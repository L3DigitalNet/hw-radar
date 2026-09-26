"""Manual harvest of unlabeled staging listings for the MS-1e validation corpus (design §4).

Drives each connector adapter directly — `await adapter.fetch()` → `adapter.parse(batch)` —
deliberately bypassing the poller scheduler and the `SourceConfig.enabled` go-live flag: this
is an owner-run tool for building the ratification corpus, not a scheduled job, and it must be
able to harvest a source whose production ingest is still blocked (eBay, CR-004). Each adapter's
own fetch is reused verbatim, so the robots preflight and Scrapy's ROBOTSTXT_OBEY apply exactly
as they do in production.

Output is *unlabeled* staging: `id`, `source`, `title`, `listing`, plus an `oem_dual_label`
heuristic pre-fill. The command never writes a `label` block — drafting labels is the separate
owner-in-the-loop step (design §6), and a machine-invented label would silently become the
ground truth the ratification gate is measured against.

Writes `<out>/staging.jsonl` (one entry per line) and `<out>/staging.meta.json` (per-source
`harvested` / `skipped_malformed` counts and per-source status), and prints the same summary.
A multi-scope source (eBay's category sweeps) also records one `scopes` entry per swept
collection scope — pages, completeness and its reason, distinct listings seen — which is the
evidence that a harvested scope was enumerated completely.

`--category <slug>` (eBay only) narrows the harvest to that category's sweeps, with the legacy
drive search only for `drive`, so a focused corpus spends no Browse quota on other categories.

Corpus-expansion options (harvest-only; nothing here is reachable from the poller, the
registries, or CATEGORY_SWEEPS, so production collection is unchanged):

- `--ebay-query [CATEGORY_ID:]QUERY` (repeatable, eBay only) replaces the eBay sweep set with
  one single-page, fixed-price Browse search per query, capped by `--ebay-query-limit`. Rows
  are unhinted drive listings, exactly like the legacy drive search's. The manifest records
  per-query `total` / `returned` / `parsed` / `new` counts under `queries`.
- `--ghd-url URL` (repeatable, goHardDrive only) adds goHardDrive category pages to the
  production category, and the harvest follows each category's Volusion pagination until
  `--ghd-max-pages` page fetches in total. The manifest records the fetched page URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import httpx
from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.admission import MATRIX_CATEGORIES, RETIRED_REASON, is_retired
from hw_radar.acquisition.contracts import (
    MultiScopeDelistDetector,
    ParsedListing,
    RawBatch,
    RawItem,
    ScopeSweepReport,
    SourceAdapter,
)
from hw_radar.acquisition.sources import HARVEST_ADAPTERS
from hw_radar.acquisition.sources.ebay import DEFAULT_API_BASE as DEFAULT_EBAY_API_BASE
from hw_radar.acquisition.sources.ebay import (
    FIXED_PRICE_FILTER,
    PAGE_LIMIT,
    EbayAdapter,
    category_sweep_adapter,
)
from hw_radar.acquisition.sources.ebay import SITE_KEY as EBAY_SITE_KEY
from hw_radar.acquisition.sources.goharddrive import CATEGORY_URL as GHD_CATEGORY_URL
from hw_radar.acquisition.sources.goharddrive_harvest import (
    MAX_GHD_PAGES,
    GoHardDrivePagingAdapter,
    validate_ghd_url,
)
from hw_radar.matching.mpn import extract_candidates
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.types import TokenKind

# The five real registry keys of SA-002. `demo` and `synthetic` are in ADAPTERS
# but are fixture sources (FIXTURE_SOURCE_KEYS), and corpus schema validation
# rejects any key outside this tuple — a fixture entry would be unloadable by
# the evaluator. The OQ31-retired keys stay listed so `--source <retired>`
# reaches handle()'s explicit refusal instead of argparse's bare "invalid
# choice"; `--all` harvests ACTIVE_HARVEST_SOURCES only.
HARVEST_SOURCES: tuple[str, ...] = (
    "serverpartdeals",
    "goharddrive",
    "wd-recertified",
    "seagate-recertified",
    "ebay",
)
ACTIVE_HARVEST_SOURCES: tuple[str, ...] = tuple(k for k in HARVEST_SOURCES if not is_retired(k))

DEFAULT_OUT = Path(".harvest")

# eBay's adapter reads these at mint time (acquisition/sources/ebay.py) and would
# raise KeyError mid-fetch without them; the command skips the source instead so a
# credential-less run still harvests the other four.
EBAY_ENV_VARS = ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")


def _ebay_credentials_present() -> bool:
    return all(os.environ.get(name) for name in EBAY_ENV_VARS)


def _oem_dual_label(title: str, source_key: str) -> bool:
    """Heuristic pre-fill: the title prints both an OEM part number and a manufacturer MPN.

    A hint for the human labeling pass (ADR-0019 rule 7 spot check), never an
    assertion of ground truth — the labeler is free to overwrite it.
    """
    kinds = {c.kind for c in extract_candidates(canonicalize_title(title), source_key=source_key)}
    return TokenKind.OEM_PN in kinds and TokenKind.MANUFACTURER_MPN in kinds


def _staging_entry(source_key: str, listing: ParsedListing) -> dict[str, Any]:
    # Exactly the ParsedListing fields the eval harness rebuilds from (design §3);
    # `attrs` is the connector's real dict, copied verbatim so the resolver sees
    # production-identical input (SA-004).
    fields: dict[str, Any] = {
        "source_listing_key": listing.source_listing_key,
        "url": listing.url,
        "price": str(listing.price),
        "currency": listing.currency,
        "condition_label": listing.condition_label,
        "attrs": listing.attrs,
    }
    # MS2-D-27: written only when the collector asserted a category, so every
    # unhinted (drive-only MS-1) listing stages byte-identically to before. Its
    # counterpart is corpus.ListingFields.category_hint, which evaluate._ingest
    # replays into ParsedListing; omitting a real hint would replay a non-drive
    # listing through the drive rules.
    if listing.category_hint is not None:
        fields["category_hint"] = listing.category_hint
    return {
        "id": f"{source_key}:{listing.source_listing_key}",
        "source": source_key,
        "title": listing.title,
        "listing": fields,
        "oem_dual_label": _oem_dual_label(listing.title, source_key),
    }


def _requires_repo_opt_in(out_dir: Path) -> bool:
    """Report whether `out_dir` lands on git-tracked ground and so needs `--allow-repo-output`.

    "Tracked ground" is decided by git itself: inside a work tree and not matched by
    an ignore rule. A `rev-parse` failure whose stderr says "not a git repository" means
    we are genuinely outside any work tree — no tracked path can be involved, so no
    opt-in is needed. Any OTHER `rev-parse` failure (e.g. exit 128 "detected dubious
    ownership" from a safe.directory refusal) or an unparseable success (empty stdout)
    means git could not actually answer the question, and is treated as needing the
    opt-in — as is a check-ignore call that itself errors inside a work tree — because
    the failure this guard exists to prevent — raw, unaudited staging output committed
    into the public repo — is far worse than an extra flag on a legitimate run.
    """
    anchor = out_dir
    while not anchor.exists():
        parent = anchor.parent
        if parent == anchor:
            return False
        anchor = parent
    root = subprocess.run(
        ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if root.returncode != 0:
        return "not a git repository" not in root.stderr
    root_dir = root.stdout.strip()
    if not root_dir:
        return True
    if not out_dir.resolve().is_relative_to(Path(root_dir).resolve()):
        return False
    # Trailing separator is load-bearing: check-ignore reads a bare path as a FILE
    # when it does not exist on disk, and a directory-only rule (`.harvest/`) then
    # reports "not ignored" — which would demand --allow-repo-output for the very
    # default output directory this guard is meant to wave through.
    ignored = subprocess.run(
        ["git", "-C", str(anchor), "check-ignore", "-q", "--", f"{out_dir.resolve()}/"],
        capture_output=True,
        check=False,
    )
    return ignored.returncode != 0


async def _fetch_parse(
    adapter: SourceAdapter,
) -> tuple[list[ParsedListing], int, list[dict[str, Any]] | None]:
    """Return parsed listings, the malformed records parse() dropped, and scope reports.

    The skip count must be read from the same adapter instance immediately after
    parse() — `last_parse_skipped` describes only the most recent call. Scope
    reports are None for a single-scope adapter; for a multi-scope one they are
    the adapter's own delist_scopes() verdicts over this very batch, so the
    manifest's `complete` is the production completeness rule, not a re-derivation.
    """
    batch = await adapter.fetch()
    parsed = adapter.parse(batch)
    skipped = adapter.last_parse_skipped
    if not isinstance(adapter, MultiScopeDelistDetector):
        return parsed, skipped, None
    scopes = [
        {
            "scope_key": report.scope_key,
            "pages": report.pages,
            "complete": report.scope is not None and report.scope.complete,
            "reason": report.reason,
            "seen": 0 if report.scope is None else len(report.scope.seen_keys),
        }
        for report in adapter.delist_scopes(batch, parsed)
    ]
    return parsed, skipped, scopes


@runtime_checkable
class HarvestReporting(Protocol):
    """A harvest-only adapter that adds its own evidence to the source's manifest entry.

    Called after parse(); the returned keys are merged into the per-source report.
    """

    def harvest_report(self) -> dict[str, Any]: ...


# ── Harvest-only eBay drive queries (corpus expansion) ──

# Each query is ONE Browse call (plus one on a 401 re-mint), and the Browse
# quota is shared with the production poller (5,000/day, ebay.py). The cap keeps
# a single harvest well inside a day's headroom whatever the caller passes.
MAX_EBAY_QUERIES = 20
DEFAULT_EBAY_QUERY_LIMIT = 40


@dataclass(frozen=True)
class EbayQuery:
    """One harvest-only Browse search: free-text `q`, optionally inside one leaf category."""

    q: str
    category_id: str | None = None

    def params(self, limit: int) -> dict[str, str]:
        # Fixed-price only, as the category sweeps do (FIXED_PRICE_FILTER): an
        # auction's current bid is not an acquirable price. Single page, no
        # offset: the corpus wants a sample per query, not an enumeration.
        params = {"q": self.q, "filter": FIXED_PRICE_FILTER, "limit": str(limit)}
        if self.category_id is not None:
            params["category_ids"] = self.category_id
        return params


def parse_ebay_query(value: str) -> EbayQuery:
    """Parse `[CATEGORY_ID:]QUERY`; a numeric prefix before the first colon is a category id.

    Raises ValueError for a blank query, including a bare `CATEGORY_ID:`.
    """
    head, sep, tail = value.strip().partition(":")
    category_id, q = (head, tail.strip()) if sep and head.isdigit() else (None, value.strip())
    if not q:
        raise ValueError(f"empty eBay query: {value!r}")
    return EbayQuery(q=q, category_id=category_id)


def _arg_type[T](parse: Callable[[str], T]) -> Callable[[str], T]:
    """Wrap a ValueError-raising parser so argparse shows its message.

    argparse replaces a plain ValueError with a generic "invalid <name> value";
    only ArgumentTypeError keeps the parser's explanation in the usage error.
    """

    @functools.wraps(parse)
    def wrapped(value: str) -> T:
        try:
            return parse(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return wrapped


class EbayQueryHarvestAdapter(EbayAdapter):
    """eBay adapter that runs explicit drive queries instead of the production sweeps.

    SCOPE: harvest-only. It is built only by harvest_corpus from `--ebay-query`
    and is absent from every registry, so the poller can never schedule it, and it
    never reads or alters CATEGORY_SWEEPS. It reuses EbayAdapter's token handling
    and per-summary parsing so staged rows are byte-identical in shape to what the
    production legacy drive search would ingest.

    Every page is treated as an unhinted drive page (category_hint None), even
    when the query carries a category id: EbayAdapter's own page routing would
    drop a category page that matches no configured CategorySweep.
    """

    def __init__(
        self,
        queries: Sequence[EbayQuery],
        limit: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, drive_sweep=False)
        self._queries = tuple(queries)
        self._limit = limit
        # One dict per query, in order, filled by fetch() and parse(); becomes
        # the manifest's `queries` list.
        self.query_reports: list[dict[str, Any]] = []

    async def fetch(self) -> RawBatch:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        base = os.environ.get("EBAY_API_BASE", DEFAULT_EBAY_API_BASE)
        self.query_reports = []
        items: list[RawItem] = []
        try:
            for query in self._queries:
                report: dict[str, Any] = {"q": query.q, "category_id": query.category_id}
                self.query_reports.append(report)
                try:
                    resp = await self._search(client, base, query.params(self._limit))
                except Exception as exc:
                    # One query's transport or token failure must not discard
                    # the pages earlier queries already paid quota for. Type
                    # name only, as in _harvest: a repr can carry request detail.
                    report["error"] = type(exc).__name__
                    continue
                report["http_status"] = resp.status_code
                if resp.status_code == 429:
                    # The quota is per application and shared with production:
                    # stop at the first throttle instead of burning more calls.
                    report["error"] = "throttled; remaining queries not sent"
                    break
                if resp.status_code != 200:
                    continue
                payload = cast("object", resp.json())
                if not isinstance(payload, dict):
                    report["error"] = "not_json"
                    continue
                data = cast("dict[str, object]", payload)
                report["total"] = data.get("total")
                summaries = data.get("itemSummaries")
                report["returned"] = (
                    len(cast("list[object]", summaries)) if isinstance(summaries, list) else 0
                )
                items.append(
                    RawItem(
                        url=str(resp.url),
                        http_status=200,
                        content_type=resp.headers.get("content-type", "application/json"),
                        payload_json=data,
                        payload_text=resp.text,
                    )
                )
        finally:
            if owns:
                await client.aclose()
        return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=items)

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        """Parse every query page as a drive page; one malformed page never sinks the rest.

        `new` per query counts keys no earlier query returned — the dedupe the
        command's first-wins id filter applies — so the manifest shows how much
        each query actually added.
        """
        self.last_parse_skipped = 0
        by_query = {(r["category_id"], r["q"]): r for r in self.query_reports if "returned" in r}
        seen: set[str] = set()
        out: list[ParsedListing] = []
        for item in batch.items:
            params = httpx.URL(item.url).params
            report = by_query.get((params.get("category_ids"), params.get("q", "")), {})
            try:
                listings, skipped = self._parse_page(item, None)
            except Exception as exc:
                # The legacy page path re-raises a malformed summary (MS-1 run
                # semantics); for a harvest that would discard every other query.
                report["error"] = f"parse_failed:{type(exc).__name__}"
                self.last_parse_skipped += int(report.get("returned", 0) or 1)
                continue
            self.last_parse_skipped += skipped
            fresh = [p for p in listings if p.source_listing_key not in seen]
            seen.update(p.source_listing_key for p in listings)
            report["parsed"] = len(listings)
            report["new"] = len(fresh)
            out.extend(listings)
        return out

    def delist_scopes(self, batch: RawBatch, parsed: list[ParsedListing]) -> list[ScopeSweepReport]:
        # A capped search sample proves nothing about which listings ended, and
        # the harvest never delists; reporting the legacy NULL scope here would
        # present these pages as the production drive sweep in the manifest.
        return []

    def harvest_report(self) -> dict[str, Any]:
        return {"queries": self.query_reports}


class Command(BaseCommand):
    help = "Harvest unlabeled staging listings for the MS-1e validation corpus."

    def add_arguments(self, parser: CommandParser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--source", choices=HARVEST_SOURCES, help="harvest one source")
        group.add_argument(
            "--all", action="store_true", help="harvest every source that is not retired"
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="cap parsed listings per source"
        )
        parser.add_argument(
            "--category",
            choices=MATRIX_CATEGORIES,
            default=None,
            help="with --source ebay: harvest only this category's sweeps",
        )
        parser.add_argument(
            "--ebay-query",
            dest="ebay_queries",
            action="append",
            type=_arg_type(parse_ebay_query),
            default=None,
            metavar="[CATEGORY_ID:]QUERY",
            help="with --source ebay: run this drive query instead of the sweeps (repeatable)",
        )
        parser.add_argument(
            "--ebay-query-limit",
            type=int,
            default=None,
            help=f"listings per --ebay-query, one page (1..{PAGE_LIMIT}; "
            f"default {DEFAULT_EBAY_QUERY_LIMIT})",
        )
        parser.add_argument(
            "--ghd-url",
            dest="ghd_urls",
            action="append",
            type=_arg_type(validate_ghd_url),
            default=None,
            help="with --source goharddrive: also harvest this category URL (repeatable)",
        )
        parser.add_argument(
            "--ghd-max-pages",
            type=int,
            default=None,
            help=f"with --source goharddrive: follow pagination up to this many page "
            f"fetches in total (1..{MAX_GHD_PAGES})",
        )
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
        parser.add_argument(
            "--allow-repo-output",
            action="store_true",
            help="permit writing under a git-tracked path (curated corpus only)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # This command bypasses `enabled` on purpose, but OQ31 retirement bars
        # collection itself, harvest included — refused before any request.
        source: str | None = options["source"]
        if source is not None and is_retired(source):
            raise CommandError(f"{source} is retired: {RETIRED_REASON}")
        category: str | None = options["category"]
        if category is not None and source != EBAY_SITE_KEY:
            # Only eBay collects more than one category; on any other source the
            # flag would silently harvest everything or nothing.
            raise CommandError("--category requires --source ebay")
        ebay_queries: list[EbayQuery] | None = options["ebay_queries"]
        query_limit: int | None = options["ebay_query_limit"]
        if ebay_queries is not None:
            if source != EBAY_SITE_KEY:
                raise CommandError("--ebay-query requires --source ebay")
            if category is not None:
                # --category selects production sweeps; queries replace them.
                raise CommandError("--ebay-query and --category are mutually exclusive")
            if len(set(ebay_queries)) != len(ebay_queries):
                # A repeat spends quota on rows the first-wins dedupe drops, and
                # the per-query report could not tell the two apart.
                raise CommandError("duplicate --ebay-query values")
            if len(ebay_queries) > MAX_EBAY_QUERIES:
                raise CommandError(f"at most {MAX_EBAY_QUERIES} --ebay-query values per run")
        elif query_limit is not None:
            raise CommandError("--ebay-query-limit requires --ebay-query")
        if query_limit is not None and not 1 <= query_limit <= PAGE_LIMIT:
            raise CommandError(f"--ebay-query-limit must be 1..{PAGE_LIMIT}")
        ghd_urls: list[str] | None = options["ghd_urls"]
        ghd_max_pages: int | None = options["ghd_max_pages"]
        if (ghd_urls is not None or ghd_max_pages is not None) and source != "goharddrive":
            raise CommandError("--ghd-url and --ghd-max-pages require --source goharddrive")
        ghd_pages = ghd_max_pages if ghd_max_pages is not None else MAX_GHD_PAGES
        if not 1 <= ghd_pages <= MAX_GHD_PAGES:
            raise CommandError(f"--ghd-max-pages must be 1..{MAX_GHD_PAGES}")
        extra_ghd: list[str] = ghd_urls or []
        # The production category always leads, so an extra URL widens the
        # harvest rather than replacing the page the scheduled source reads.
        ghd_start = list(dict.fromkeys([GHD_CATEGORY_URL, *extra_ghd]))
        if len(ghd_start) > ghd_pages:
            raise CommandError("--ghd-max-pages is smaller than the number of category URLs")
        out_dir: Path = options["out"]
        limit: int | None = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be >= 1")
        if _requires_repo_opt_in(out_dir) and not options["allow_repo_output"]:
            raise CommandError(
                f"{out_dir} is a git-tracked path; raw staging output does not belong in the "
                "repository. Pass --allow-repo-output only for a curated, labeled corpus."
            )

        sources = ACTIVE_HARVEST_SOURCES if source is None else (source,)
        entries: list[dict[str, Any]] = []
        report: dict[str, dict[str, Any]] = {}
        for key in sources:
            adapter: SourceAdapter | None = None
            if key == EBAY_SITE_KEY and ebay_queries is not None:
                adapter = EbayQueryHarvestAdapter(
                    ebay_queries,
                    query_limit if query_limit is not None else DEFAULT_EBAY_QUERY_LIMIT,
                )
            elif key == "goharddrive" and (ghd_urls is not None or ghd_max_pages is not None):
                adapter = GoHardDrivePagingAdapter(ghd_start, ghd_pages)
            source_entries, status = self._harvest(key, limit, category, adapter)
            entries.extend(source_entries)
            report[key] = status

        summary = {
            "harvested_at": datetime.now(UTC).isoformat(),
            "out": str(out_dir),
            "sources": report,
            "total_harvested": len(entries),
        }
        if category is not None:
            summary["category"] = category
        self._write(out_dir, entries, summary)
        self.stdout.write(json.dumps(summary, indent=2))

    def _harvest(
        self,
        key: str,
        limit: int | None,
        category: str | None = None,
        adapter: SourceAdapter | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Harvest one source, absorbing its failures.

        Never raises: a source that is unreachable, throttled, or newly
        incompatible reports `status: error` and the sweep continues (NFR-001).
        A harvest is a best-effort snapshot, and losing four healthy sources to
        one broken one would waste the whole manual run.
        """
        if key == "ebay" and not _ebay_credentials_present():
            self.stderr.write(
                f"skipping ebay: {' / '.join(EBAY_ENV_VARS)} not set in the environment"
            )
            return [], {"status": "skipped_no_credentials", "harvested": 0, "skipped_malformed": 0}
        try:
            if adapter is None:
                adapter = (
                    HARVEST_ADAPTERS[key]()
                    if category is None
                    else category_sweep_adapter(category)
                )
            parsed, dropped_in_parse, scopes = asyncio.run(_fetch_parse(adapter))
        except Exception as exc:
            self.stderr.write(f"{key} failed: {exc!r}")
            return [], {
                "status": "error",
                "harvested": 0,
                "skipped_malformed": 0,
                # Type name only: a full repr can carry request URLs or
                # token-exchange detail, and this manifest may be written under
                # a tracked path via --allow-repo-output. Full repr goes to
                # stderr above, which is never persisted.
                "error": type(exc).__name__,
            }

        # Truncate BEFORE the staging-validity filter: --limit caps parsed listings
        # (design §4), so a run is reproducible from the same batch. Truncation is
        # NOT malformed and is never counted — dropping the tail of a healthy parse
        # would otherwise read as source rot in the harvest-quality report.
        if limit is not None:
            parsed = parsed[:limit]
        usable = [p for p in parsed if p.title.strip() and p.source_listing_key.strip()]
        # One entry per id, first wins: the corpus loader rejects a duplicate
        # id (matching.eval.corpus), and a source may return one listing twice
        # (eBay: the same item in two sweeps or across pages). The count is
        # recorded only when non-zero, so manifests of duplicate-free runs are
        # unchanged.
        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for listing in usable:
            entry = _staging_entry(key, listing)
            if entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                entries.append(entry)
        report: dict[str, Any] = {
            "status": "ok",
            "harvested": len(entries),
            # Both halves of the malformed picture (B3): records the adapter itself
            # discarded inside parse(), plus listings it returned that carry nothing
            # the matcher could resolve. The adapter half is unaffected by --limit,
            # so `harvested + skipped_malformed` need not equal the capped slice.
            "skipped_malformed": dropped_in_parse + (len(parsed) - len(usable)),
        }
        if len(usable) > len(entries):
            report["duplicates_dropped"] = len(usable) - len(entries)
        if scopes is not None:
            report["scopes"] = scopes
        if isinstance(adapter, HarvestReporting):
            report.update(adapter.harvest_report())
        return entries, report

    def _write(self, out_dir: Path, entries: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "staging.jsonl").open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        (out_dir / "staging.meta.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
