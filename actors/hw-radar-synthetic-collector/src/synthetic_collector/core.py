"""Pure core of the synthetic collector: input -> bounded fetch plan -> rows plus OUTPUT.

The source is this public repository's own fixture pages, fetched over HTTPS from
raw.githubusercontent.com at the commit the input pins (MS2-D-42). The base URL
is fixed here; input supplies only the commit and relative page paths, both
pattern-checked by the contract, so input can never point a fetch elsewhere.

Caps (MS2-D-26) are enforced here, not trusted to the platform:
- maxRequests: HTTP requests sent; maxPages: pages attempted;
- maxItems: rows emitted; maxBytes: response-body bytes received;
- timeBudgetSecs: wall time from the start of collect(), also each request's timeout.
A limit counts as *hit* only when it stopped work that remained, so a source that
exactly fits its caps is complete, not truncated. Every limit binding at the
moment of stopping is reported, never just the first.

faultMode (synthetic Actor only) deterministically forces one MS2-D-14
classification row; see _Faults. With faultMode=none the output is the honest
account of the fetch.

This module does no Apify SDK call and holds no storage handle; the entry point
(synthetic_collector.main) decides what reaches the dataset and the KV store.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, cast

import httpx

from synthetic_collector.contract import (
    ACTOR_NAME,
    ACTOR_VERSION,
    INPUT_SCHEMA_FILE,
    LISTING_SCHEMA_FILE,
    LISTING_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    SOURCE_KIND,
    UNKNOWN_LISTING_SCHEMA_VERSION,
    UNKNOWN_RUN_SCHEMA_VERSION,
    schema_errors,
)

RAW_BASE_URL: Final = "https://raw.githubusercontent.com/L3DigitalNet/hw-radar"
SOURCE_ROOT: Final = "actors/hw-radar-synthetic-collector/fixtures/source"

# Mirrors the run schema's errors maxItems; the last slot reports the overflow.
MAX_ERRORS: Final = 50

_QUERY_SCOPE_FIELDS: Final = (
    "siteKey",
    "collectionScope",
    "categoryHint",
    "maxItems",
    "maxPages",
    "maxRequests",
    "maxBytes",
    "timeBudgetSecs",
)
# Source-listing key -> contract row key, for the optional merchant fields.
_OPTIONAL_LISTING_FIELDS: Final = (
    ("shippingPrice", "shippingPrice"),
    ("quantityAvailable", "quantityAvailable"),
    ("seller", "sellerName"),
    ("condition", "conditionLabel"),
    ("shipsFrom", "shipsFromCountry"),
    ("mpn", "mpn"),
)


class InputRejected(ValueError):
    """The run input does not satisfy hw-radar-input/v1; the Actor must not collect."""


@dataclass(frozen=True, slots=True)
class CollectorInput:
    site_key: str
    collection_scope: str
    category_hint: str
    max_items: int
    max_pages: int
    max_requests: int
    max_bytes: int
    time_budget_secs: int
    fixture_commit: str
    fixture_paths: tuple[str, ...]
    fault_mode: str
    query_scope: dict[str, Any]


def validate_input(raw: object) -> CollectorInput:
    """Validate raw Actor input against the committed contract, or raise InputRejected.

    This is the third layer (hw-radar validates before the start request, then
    Apify validates against .actor/input_schema.json); it exists so a run started
    any other way still cannot collect with unbounded or malformed caps.
    """
    data = _json_object(raw)
    if data is None:
        raise InputRejected("input must be a JSON object")
    problems = schema_errors(INPUT_SCHEMA_FILE, data)
    if problems:
        raise InputRejected("; ".join(problems))
    return CollectorInput(
        site_key=data["siteKey"],
        collection_scope=data["collectionScope"],
        category_hint=data["categoryHint"],
        max_items=data["maxItems"],
        max_pages=data["maxPages"],
        max_requests=data["maxRequests"],
        max_bytes=data["maxBytes"],
        time_budget_secs=data["timeBudgetSecs"],
        fixture_commit=data["fixtureCommit"],
        fixture_paths=tuple(data["fixturePaths"]),
        fault_mode=data["faultMode"],
        query_scope={name: data[name] for name in _QUERY_SCOPE_FIELDS},
    )


def _json_object(value: object) -> dict[str, Any] | None:
    """Return a decoded JSON object with string keys, or None for any other value."""
    if not isinstance(value, Mapping):
        return None
    mapping = cast("Mapping[object, object]", value)
    return {str(key): item for key, item in mapping.items()}


def page_url(commit: str, path: str) -> str:
    return f"{RAW_BASE_URL}/{commit}/{SOURCE_ROOT}/{path}"


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """What one run produced.

    output is None only when the Actor must write no OUTPUT at all. succeeded is
    the Actor's own exit status: False makes the entry point fail the run after
    storing whatever rows and OUTPUT exist.
    """

    rows: list[dict[str, Any]]
    output: dict[str, Any] | None
    succeeded: bool


@dataclass(slots=True)
class _Limits:
    pages: bool = False
    requests: bool = False
    items: bool = False
    time: bool = False
    bytes: bool = False

    def any(self) -> bool:
        return self.pages or self.requests or self.items or self.time or self.bytes

    def as_json(self) -> dict[str, bool]:
        return {
            "pages": self.pages,
            "requests": self.requests,
            "items": self.items,
            "time": self.time,
            "bytes": self.bytes,
        }


@dataclass(slots=True)
class _Run:
    """Mutable counters for one collect() call."""

    rows: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    errors: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    limits: _Limits = field(default_factory=_Limits)
    requests: int = 0
    pages_attempted: int = 0
    pages_fetched: int = 0
    bytes_read: int = 0
    pages_declared: int | None = None
    items_declared: int | None = None
    time_exhausted: bool = False
    stopped: bool = False

    def error(self, code: str, message: str) -> None:
        self.errors.append({"code": code, "message": message[:500]})


class _Faults:
    """faultMode semantics. Each mode forces one MS2-D-14 row, deterministically.

    The truncate_* modes lower a cap so the real enforcement path trips; they do
    not fake a flag. Report-level modes (contradictory_report, count_mismatch,
    unknown_schema) rewrite only what the Actor *says*, after an honest fetch.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def item_cap(self, cap: int) -> int:
        return min(cap, 1) if self.mode == "truncate_items" else cap

    def page_cap(self, cap: int) -> int:
        return min(cap, 1) if self.mode == "truncate_pages" else cap

    def after_first_page(self, run: _Run) -> None:
        if run.pages_fetched != 1:
            return
        if self.mode == "truncate_time":
            run.time_exhausted = True
        if self.mode == "fail":
            run.error("injected_failure", "faultMode=fail: the Actor failed after page 1")
            run.stopped = True

    def byte_cap(self, cap: int, run: _Run) -> int:
        # After page 1 the remaining byte budget becomes zero, so the next fetch
        # is refused by the ordinary byte check.
        if self.mode == "truncate_bytes" and run.pages_fetched >= 1:
            return min(cap, run.bytes_read)
        return cap

    def skips_page(self, index: int, planned: int) -> bool:
        return self.mode == "partial_failure" and index == planned - 1


async def collect(
    actor_input: CollectorInput,
    client: httpx.AsyncClient,
    *,
    clock: Callable[[], float],
    started_at: str,
) -> CollectionResult:
    """Fetch the pinned fixture pages within every cap and build rows plus OUTPUT.

    started_at (UTC, second precision) stamps every row's observedAt. clock is a
    monotonic seconds source for the time budget. Never raises for source or
    network trouble: each becomes an OUTPUT error or a hit limit, because a
    crash would lose the completeness evidence hw-radar needs to refuse absence.
    """
    faults = _Faults(actor_input.fault_mode)
    run = _Run()
    deadline = clock() + actor_input.time_budget_secs
    max_items = faults.item_cap(actor_input.max_items)
    max_pages = faults.page_cap(actor_input.max_pages)
    max_bytes = actor_input.max_bytes
    planned = len(actor_input.fixture_paths)

    index = 0
    while index < planned and not run.stopped:
        path = actor_input.fixture_paths[index]
        if faults.skips_page(index, planned):
            run.pages_attempted += 1
            run.error("injected_fetch_error", f"faultMode=partial_failure: page {path} not fetched")
            index += 1
            continue
        if _stop_for_limits(
            run, clock() >= deadline, max_pages, actor_input.max_requests, max_items, max_bytes
        ):
            break
        run.pages_attempted += 1
        page = await _fetch_page(
            client, run, page_url(actor_input.fixture_commit, path), deadline - clock(), max_bytes
        )
        index += 1
        if page is None:
            continue
        if run.pages_fetched == 0:
            run.pages_declared, run.items_declared = _declared_counts(page)
            if run.pages_declared is not None:
                planned = min(planned, run.pages_declared)
        run.pages_fetched += 1
        _emit_rows(
            run,
            actor_input,
            page,
            page_url(actor_input.fixture_commit, path),
            max_items,
            started_at,
        )
        max_bytes = faults.byte_cap(max_bytes, run)
        faults.after_first_page(run)
        if run.limits.items:
            break

    return _finish(run, actor_input, faults, planned)


def _stop_for_limits(
    run: _Run, out_of_time: bool, max_pages: int, max_requests: int, max_items: int, max_bytes: int
) -> bool:
    """Record every cap that forbids fetching another page; True when any does."""
    limits = run.limits
    limits.time = limits.time or out_of_time or run.time_exhausted
    limits.bytes = limits.bytes or run.bytes_read >= max_bytes
    limits.requests = limits.requests or run.requests >= max_requests
    limits.pages = limits.pages or run.pages_attempted >= max_pages
    # Rows already fill the item cap and another page remains: more items may
    # exist, so completeness cannot be claimed.
    limits.items = limits.items or len(run.rows) >= max_items
    return limits.any()


async def _fetch_page(
    client: httpx.AsyncClient, run: _Run, url: str, remaining_secs: float, max_bytes: int
) -> dict[str, Any] | None:
    """GET one page within the remaining time and byte budget; None when unusable.

    Identity encoding is requested, so the counted body bytes are the transfer
    the cap bounds. If a server compresses anyway, the decoded count exceeds the
    transfer, which only makes the cap stricter, never looser.
    An over-budget body is discarded whole: a partial page could silently drop
    listings while still looking parseable.
    """
    run.requests += 1
    body = bytearray()
    try:
        async with client.stream(
            "GET", url, headers={"Accept-Encoding": "identity"}, timeout=max(remaining_secs, 0.001)
        ) as response:
            if response.status_code != 200:
                run.error("http_status", f"{url} answered HTTP {response.status_code}")
                return None
            async for chunk in response.aiter_bytes():
                run.bytes_read += len(chunk)
                if run.bytes_read > max_bytes:
                    run.limits.bytes = True
                    run.stopped = True
                    return None
                body.extend(chunk)
    except httpx.TimeoutException:
        run.limits.time = True
        run.stopped = True
        return None
    except httpx.HTTPError as exc:
        run.error("fetch_error", f"{url}: {type(exc).__name__}")
        return None
    try:
        page: object = json.loads(bytes(body))
    except ValueError:
        run.error("invalid_page", f"{url} is not JSON")
        return None
    parsed = _json_object(page)
    if parsed is None or not isinstance(parsed.get("listings"), list):
        run.error("invalid_page", f"{url} has no listings array")
        return None
    return parsed


def _declared_counts(page: Mapping[str, Any]) -> tuple[int | None, int | None]:
    def count(key: str) -> int | None:
        value = page.get(key)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    return count("pagesDeclared"), count("itemsDeclared")


def _emit_rows(
    run: _Run,
    actor_input: CollectorInput,
    page: Mapping[str, Any],
    url: str,
    max_items: int,
    started_at: str,
) -> None:
    for listing in page["listings"]:
        if len(run.rows) >= max_items:
            run.limits.items = True
            return
        row = _row(actor_input, listing, url, started_at)
        # Only contract-valid rows leave the Actor, so the schema's maxLength caps
        # really do bound every stored row (MS2-D-26).
        if row is None or schema_errors(LISTING_SCHEMA_FILE, row):
            run.error("invalid_source_listing", f"{url}: listing {len(run.rows) + 1} skipped")
            continue
        run.rows.append(row)


def _row(
    actor_input: CollectorInput, listing: object, url: str, started_at: str
) -> dict[str, Any] | None:
    source = _json_object(listing)
    if source is None:
        return None
    listing_id = source.get("id")
    if not isinstance(listing_id, str):
        return None
    row: dict[str, Any] = {
        "schemaVersion": LISTING_SCHEMA_VERSION,
        "siteKey": actor_input.site_key,
        "sourceListingKey": listing_id,
        "url": f"{url}#{listing_id}",
        "title": source.get("title"),
        "price": source.get("price"),
        "currency": source.get("currency"),
        "stockStatus": source.get("stockStatus", "unknown"),
        "categoryHint": actor_input.category_hint,
        "collectionScope": actor_input.collection_scope,
        "observedAt": started_at,
    }
    for source_key, row_key in _OPTIONAL_LISTING_FIELDS:
        if source.get(source_key) is not None:
            row[row_key] = source[source_key]
    return row


def _finish(
    run: _Run, actor_input: CollectorInput, faults: _Faults, planned: int
) -> CollectionResult:
    limits = run.limits
    if not limits.any() and not run.errors:
        if run.pages_fetched < planned or (
            run.pages_declared is not None and run.pages_fetched < run.pages_declared
        ):
            run.error("source_pages_unlisted", "the source declares pages the input did not list")
        elif run.items_declared is not None and len(run.rows) != run.items_declared:
            run.error("declared_item_count_mismatch", "rows emitted differ from the declared total")
    complete = not limits.any() and not run.errors
    items_emitted = len(run.rows)
    rows = run.rows

    if faults.mode == "contradictory_report":
        # Claims completeness while reporting a hit cap: MS2-D-14 contradiction.
        limits.pages = True
    if faults.mode == "count_mismatch":
        items_emitted += 1
    if faults.mode == "unknown_schema":
        rows = [{**row, "schemaVersion": UNKNOWN_LISTING_SCHEMA_VERSION} for row in rows]

    completeness: dict[str, Any] = {
        "complete": complete,
        "truncated": limits.any() and faults.mode != "contradictory_report",
        "limitsHit": limits.as_json(),
    }
    # Undeclared counts are omitted, never guessed: their absence is what keeps
    # an empty run ambiguous rather than proven complete-empty.
    if run.pages_declared is not None:
        completeness["pagesDeclared"] = run.pages_declared
    completeness["pagesFetched"] = run.pages_fetched
    if run.items_declared is not None:
        completeness["itemsDeclared"] = run.items_declared
    completeness["itemsEmitted"] = items_emitted

    errors = run.errors
    if len(errors) > MAX_ERRORS:
        errors = [
            *errors[: MAX_ERRORS - 1],
            {"code": "errors_truncated", "message": f"{len(errors)} errors"},
        ]
    failed = faults.mode == "fail"
    output: dict[str, Any] = {
        "schemaVersion": UNKNOWN_RUN_SCHEMA_VERSION
        if faults.mode == "unknown_schema"
        else RUN_SCHEMA_VERSION,
        "status": "failed" if failed else "succeeded",
        "completeness": completeness,
        "queryScope": dict(actor_input.query_scope),
        "provider": {
            "actorName": ACTOR_NAME,
            "actorVersion": ACTOR_VERSION,
            "sourceKind": SOURCE_KIND,
        },
        "errors": errors,
    }
    return CollectionResult(rows=rows, output=output, succeeded=not failed)
