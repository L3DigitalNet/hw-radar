"""hw-radar's side of the versioned Actor contract, and the run classifier (MS2-D-14).

The contract artifact is the set of committed JSON Schema (draft 2020-12) files in
the Actor's own directory, `actors/hw-radar-synthetic-collector/contract/`. The
Pydantic models below are the hw-radar end of that contract:
`tests/unit/test_apify_contract.py` requires each model's `model_json_schema()` to
equal its committed file exactly, and the Actor's own tests validate every emitted
row and `OUTPUT` record against the same files. A contract change therefore edits
this module, the schema files, and the Actor in one PR; drift in either direction
fails the gate. Regenerate a schema file from its model with
`json.dumps(Model.model_json_schema(), indent=2) + "\\n"` (see CONTRACT_SCHEMA_MODELS).

Every model is strict: the Actor and hw-radar validate the same JSON with two
different engines (jsonschema in the Actor, Pydantic here), and Pydantic's lax
coercions ("5" -> 5, 1 -> True) would accept values the committed schema rejects.
For the same reason timestamps are pattern-checked strings rather than datetimes,
so both engines apply one identical rule.

classify_run turns a terminal remote run into (RunCompleteness, reason,
TruncationReason | None). Missing, ambiguous, or self-contradictory evidence is
never COMPLETE (DR-011), and FAILED persists nothing (MS2-D-22 Reject). This
module is pure: no DB, no HTTP.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Literal, NamedTuple, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from hw_radar.acquisition.contracts import SCOPE_KEY_MAX_LENGTH, SCOPE_KEY_PATTERN
from hw_radar.catalog.models import RunCompleteness
from hw_radar.catalog.models.ops import TruncationReason
from hw_radar.matching.categories import CATEGORY_SLUG_MAX_LENGTH, CATEGORY_SLUG_RE

INPUT_SCHEMA_VERSION: Final = "hw-radar-input/v1"
LISTING_SCHEMA_VERSION: Final = "hw-radar-listing/v1"
RUN_SCHEMA_VERSION: Final = "hw-radar-run/v1"

_JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

# Apify's terminal run statuses (docs.apify.com run lifecycle, MS2-D-15). The
# transitional TIMING-OUT / ABORTING and READY / RUNNING are not classifiable.
RUN_STATUS_SUCCEEDED: Final = "SUCCEEDED"
RUN_STATUS_FAILED: Final = "FAILED"
RUN_STATUS_TIMED_OUT: Final = "TIMED-OUT"
RUN_STATUS_ABORTED: Final = "ABORTED"
TERMINAL_RUN_STATUSES: Final = frozenset(
    {RUN_STATUS_SUCCEEDED, RUN_STATUS_FAILED, RUN_STATUS_TIMED_OUT, RUN_STATUS_ABORTED}
)

# Reason codes. A classified reason is either a bare code or "<code>: <detail>".
REASON_COMPLETE: Final = "complete"
REASON_COMPLETE_EMPTY: Final = "complete_empty"
REASON_MISSING_OUTPUT: Final = "missing_output"
REASON_UNKNOWN_SCHEMA_VERSION: Final = "unknown_schema_version"
REASON_INVALID_OUTPUT: Final = "invalid_output"
REASON_SCOPE_MISMATCH: Final = "scope_mismatch"
REASON_CONTRADICTORY_REPORT: Final = "contradictory_report"
REASON_NO_USABLE_ITEMS: Final = "no_usable_items"
REASON_AMBIGUOUS_EMPTY: Final = "ambiguous_empty"
REASON_REMOTE_FAILED_WITH_ITEMS: Final = "remote_failed_with_items"
REASON_REMOTE_FAILED_EMPTY: Final = "remote_failed_empty"
REASON_ERRORS_REPORTED: Final = "errors_reported"
REASON_UNUSABLE_ITEMS: Final = "unusable_items"
REASON_TRUNCATED: Final = "truncated"
REASON_UNEXPLAINED_INCOMPLETE: Final = "unexplained_incomplete"

# Contract bounds. The listing row's per-field maxLength caps are what bound one
# row's size (the per-row byte cap of MS2-D-26); the input caps bound a run.
MAX_ITEMS_CEILING: Final = 500
MAX_PAGES_CEILING: Final = 50
MAX_REQUESTS_CEILING: Final = 100
MAX_BYTES_CEILING: Final = 10_000_000
MIN_TIME_BUDGET_SECS: Final = 10
MAX_TIME_BUDGET_SECS: Final = 900
MAX_REPORTED_COUNT: Final = 100_000
MAX_RUN_ERRORS: Final = 50
MAX_FIXTURE_PATHS: Final = 20

_DECIMAL_PATTERN: Final = r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,4})?$"
_UTC_TIMESTAMP_PATTERN: Final = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$"
)
_SLUG_KEY_PATTERN: Final = r"^[a-z0-9][a-z0-9_-]*$"
# "<site_key>:<category>:<query_id>" (MS2-D-12): owned by acquisition.contracts,
# so an imported row's scope and ParsedListing.collection_scope cannot disagree.
_SCOPE_KEY_PATTERN: Final = SCOPE_KEY_PATTERN
# Relative, lowercase, .json only, no "..": the Actor joins it to a base URL fixed
# in its code, so input can never redirect a fetch off the pinned repository.
_FIXTURE_PATH_PATTERN: Final = r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)*\.json$"

_SiteKey = Annotated[str, Field(min_length=1, max_length=100, pattern=_SLUG_KEY_PATTERN)]
_ScopeKey = Annotated[
    str, Field(min_length=5, max_length=SCOPE_KEY_MAX_LENGTH, pattern=_SCOPE_KEY_PATTERN)
]
_CategorySlug = Annotated[
    str,
    Field(min_length=1, max_length=CATEGORY_SLUG_MAX_LENGTH, pattern=CATEGORY_SLUG_RE.pattern),
]
_DecimalString = Annotated[str, Field(max_length=14, pattern=_DECIMAL_PATTERN)]
_Count = Annotated[int, Field(ge=0, le=MAX_REPORTED_COUNT)]


class _ContractModel(BaseModel):
    """Strict, frozen, closed, camelCase-on-the-wire base for every contract model."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


class QueryScope(_ContractModel):
    """What a run was admitted for; the Actor echoes it verbatim in OUTPUT.queryScope."""

    site_key: _SiteKey
    collection_scope: _ScopeKey
    category_hint: _CategorySlug
    max_items: Annotated[int, Field(ge=1, le=MAX_ITEMS_CEILING)]
    max_pages: Annotated[int, Field(ge=1, le=MAX_PAGES_CEILING)]
    max_requests: Annotated[int, Field(ge=1, le=MAX_REQUESTS_CEILING)]
    max_bytes: Annotated[int, Field(ge=1, le=MAX_BYTES_CEILING)]
    time_budget_secs: Annotated[int, Field(ge=MIN_TIME_BUDGET_SECS, le=MAX_TIME_BUDGET_SECS)]


class CollectorInput(QueryScope):
    """The common part of hw-radar-input/v1 that every Hardware Radar Actor accepts.

    SCOPE: no proxy, browser, CAPTCHA, or unblocker field exists here or may be
    added (MS2-D-26, MS2-D-44); an Actor that would need one fails source admission.
    """

    schema_version: Literal["hw-radar-input/v1"]


FaultMode = Literal[
    "none",
    "truncate_items",
    "truncate_pages",
    "truncate_time",
    "truncate_bytes",
    "partial_failure",
    "contradictory_report",
    "count_mismatch",
    "unknown_schema",
    "fail",
]


class SyntheticCollectorInput(CollectorInput):
    """hw-radar-synthetic-collector's full input: the common part plus its source fields.

    fixture_commit pins the repository commit whose fixture pages the Actor fetches
    (MS2-D-42). fault_mode exists only on this model, never on CollectorInput, so a
    merchant Actor's input cannot carry it.
    """

    model_config = ConfigDict(
        title="SyntheticCollectorInput",
        json_schema_extra={
            "$schema": _JSON_SCHEMA_DIALECT,
            "$id": "urn:hw-radar:contract:input:v1",
        },
    )

    fixture_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    fixture_paths: Annotated[
        list[Annotated[str, Field(max_length=200, pattern=_FIXTURE_PATH_PATTERN)]],
        Field(min_length=1, max_length=MAX_FIXTURE_PATHS),
    ]
    fault_mode: FaultMode


# Mirrors catalog.models.market.StockStatus values; a unit test pins the equality.
StockStatusValue = Literal["in_stock", "out_of_stock", "preorder", "unknown"]


class ListingRow(_ContractModel):
    """hw-radar-listing/v1: one default-dataset row, and nothing but merchant facts.

    Every string field carries maxLength, so a row's serialized size is bounded
    by the schema alone (MS2-D-26 per-row byte cap); import rejects over-cap rows
    because they fail this model.
    """

    model_config = ConfigDict(
        title="ListingRow",
        json_schema_extra={
            "$schema": _JSON_SCHEMA_DIALECT,
            "$id": "urn:hw-radar:contract:listing:v1",
        },
    )

    schema_version: Literal["hw-radar-listing/v1"]
    site_key: _SiteKey
    source_listing_key: Annotated[str, Field(min_length=1, max_length=255)]
    url: Annotated[str, Field(max_length=1000, pattern=r"^https://")]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    price: _DecimalString
    currency: Annotated[str, Field(max_length=3, pattern=r"^[A-Z]{3}$")]
    shipping_price: _DecimalString | None = None
    stock_status: StockStatusValue
    quantity_available: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    seller_name: Annotated[str, Field(max_length=200)] | None = None
    condition_label: Annotated[str, Field(max_length=255)] | None = None
    ships_from_country: Annotated[str, Field(max_length=2, pattern=r"^[A-Z]{2}$")] | None = None
    category_hint: _CategorySlug
    collection_scope: _ScopeKey
    mpn: Annotated[str, Field(max_length=100)] | None = None
    observed_at: Annotated[str, Field(max_length=32, pattern=_UTC_TIMESTAMP_PATTERN)]


class LimitsHit(_ContractModel):
    """Which caps stopped the run while work remained; any True means truncated."""

    pages: bool
    requests: bool
    items: bool
    time: bool
    bytes: bool


class CompletenessReport(_ContractModel):
    """The Actor's own completeness evidence.

    The declared counts are what the source said exists; they are absent when the
    source declares nothing. Absent itemsDeclared is exactly what makes an empty
    run ambiguous rather than proven complete-empty.
    """

    complete: bool
    truncated: bool
    limits_hit: LimitsHit
    pages_declared: _Count | None = None
    pages_fetched: _Count
    items_declared: _Count | None = None
    items_emitted: _Count


class ProviderInfo(_ContractModel):
    """Which Actor produced the run; actor_name is checked against the admitted Actor."""

    actor_name: Annotated[str, Field(max_length=64, pattern=r"^hw-radar-[a-z0-9-]+$")]
    actor_version: Annotated[str, Field(max_length=16, pattern=r"^[0-9]+\.[0-9]+$")]
    source_kind: Annotated[str, Field(min_length=1, max_length=32, pattern=_SLUG_KEY_PATTERN)]


class RunError(_ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
    message: Annotated[str, Field(max_length=500)]


class RunOutput(_ContractModel):
    """hw-radar-run/v1: the default-KV `OUTPUT` record.

    It holds counts, scope, and errors only, never merchant content (MS2-D-25), and
    is stored on provider_run, never as a RawItem (MS2-D-14 raw batch shape).
    """

    model_config = ConfigDict(
        title="RunOutput",
        json_schema_extra={"$schema": _JSON_SCHEMA_DIALECT, "$id": "urn:hw-radar:contract:run:v1"},
    )

    schema_version: Literal["hw-radar-run/v1"]
    status: Literal["succeeded", "failed"]
    completeness: CompletenessReport
    query_scope: QueryScope
    provider: ProviderInfo
    errors: Annotated[list[RunError], Field(max_length=MAX_RUN_ERRORS)]


# Committed file name -> the model whose JSON Schema it must equal (the drift guard).
CONTRACT_SCHEMA_MODELS: Final[Mapping[str, type[BaseModel]]] = {
    "hw-radar-input-v1.schema.json": SyntheticCollectorInput,
    "hw-radar-listing-v1.schema.json": ListingRow,
    "hw-radar-run-v1.schema.json": RunOutput,
}


# ── Dataset page sizing (MS2-D-32 *Dataset page size*, revision 11 R10-08) ──
#
# Cross-file contract: the importer (acquisition.apify.provider) sends
# page_limit(settings.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES, MAX_LISTING_ROW_BYTES)
# as every dataset page's `limit`, and the D3 follow-up's client reads each
# dataset page body up to the same setting. A contract-valid dataset therefore
# never yields an over-cap page; an over-cap page means the Actor broke the
# contract, which is an error for that call, not a reason to shrink the page.

# Fixed allowance for the JSON array brackets and separators around one page's
# rows (a code constant in the plan, not a setting).
DATASET_PAGE_ENVELOPE_BYTES: Final = 1024

# Per-element costs of the serialized-row bound. Each string code point is
# priced as a JSON-escaped astral code point (a surrogate pair, "😀",
# 12 bytes) because maxLength counts code points and the response may use
# ensure_ascii escaping; each property pays its quoted key, colon, and comma
# plus a whitespace allowance, because Apify's pretty-printing of dataset items
# is undocumented.
_STRING_QUOTES_BYTES: Final = 2
_ESCAPED_CODE_POINT_BYTES: Final = 12
_NULL_BYTES: Final = 4
_PROPERTY_OVERHEAD_BYTES: Final = 4
_PROPERTY_WHITESPACE_BYTES: Final = 32
_OBJECT_OVERHEAD_BYTES: Final = 34


def _value_bound(name: str, prop: Mapping[str, object]) -> int:
    branches_raw = prop.get("anyOf")
    if isinstance(branches_raw, list):
        bounds: list[int] = []
        for branch in cast(list[object], branches_raw):
            if not isinstance(branch, Mapping):
                raise ValueError(f"property {name!r} has a non-object anyOf branch")
            bounds.append(_value_bound(name, cast(Mapping[str, object], branch)))
        return max(bounds)
    const = prop.get("const")
    enum = prop.get("enum")
    if isinstance(const, str):
        return _STRING_QUOTES_BYTES + _ESCAPED_CODE_POINT_BYTES * len(const)
    if isinstance(enum, list):
        literals = [str(v) for v in cast(list[object], enum)]
        return _STRING_QUOTES_BYTES + _ESCAPED_CODE_POINT_BYTES * max(map(len, literals))
    kind = prop.get("type")
    if kind == "string":
        max_length = prop.get("maxLength")
        if not isinstance(max_length, int):
            raise ValueError(f"string property {name!r} has no maxLength: the row is unbounded")
        return _STRING_QUOTES_BYTES + _ESCAPED_CODE_POINT_BYTES * max_length
    if kind == "integer":
        minimum, maximum = prop.get("minimum"), prop.get("maximum")
        if not isinstance(minimum, int) or not isinstance(maximum, int):
            raise ValueError(f"integer property {name!r} lacks minimum/maximum: unbounded")
        return max(len(str(minimum)), len(str(maximum)))
    if kind == "null":
        return _NULL_BYTES
    raise ValueError(f"property {name!r} has a type the row bound cannot price: {kind!r}")


def max_serialized_row_bytes(schema: Mapping[str, object]) -> int:
    """Return the most bytes one schema-valid row can serialize to (`max_item_bytes`).

    Computed from the listing schema alone (MS2-D-32): each string
    `2 + 12 x maxLength` (a const or enum prices its longest literal), each
    integer its longest decimal form, null 4, each property `len(key) + 4` plus
    a 32-byte whitespace allowance, and 34 bytes for the object. Every property
    is priced as present, optional ones included. Raises ValueError for a
    property it cannot bound (a string without maxLength, an unbounded integer,
    or an unpriced type): an unbounded row makes the page size and the MS2-D-26
    transfer bound meaningless, so it must fail loudly, never default.
    """
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("schema has no properties to bound")
    total = _OBJECT_OVERHEAD_BYTES
    for name, prop in cast(Mapping[str, object], properties).items():
        if not isinstance(prop, Mapping):
            raise ValueError(f"property {name!r} is not a schema object")
        total += len(name) + _PROPERTY_OVERHEAD_BYTES + _PROPERTY_WHITESPACE_BYTES
        total += _value_bound(name, cast(Mapping[str, object], prop))
    return total


def page_limit(max_dataset_page_bytes: int, max_item_bytes: int) -> int:
    """Return how many rows one dataset page may request so its body fits the page cap.

    `floor((max_dataset_page_bytes - DATASET_PAGE_ENVELOPE_BYTES) / max_item_bytes)`.
    Raises ValueError when not even one worst-case row fits: MS2-D-32 admission
    denies that configuration (`unbounded_component`), and a page of zero rows
    would make pagination spin without progress.
    """
    if max_item_bytes <= 0:
        raise ValueError("max_item_bytes must be positive")
    limit = (max_dataset_page_bytes - DATASET_PAGE_ENVELOPE_BYTES) // max_item_bytes
    if limit < 1:
        raise ValueError(
            f"page_limit < 1: a {max_dataset_page_bytes}-byte page cannot hold one "
            f"{max_item_bytes}-byte row"
        )
    return limit


# The v1 listing row's bound, 32,735 bytes (MS2-D-32's worked figure; pinned by
# tests/unit/test_apify_contract.py against the committed schema file).
MAX_LISTING_ROW_BYTES: Final = max_serialized_row_bytes(ListingRow.model_json_schema())


class ContractViolation(ValueError):
    """A value hw-radar was about to send, or received, does not satisfy the contract."""


def prepare_run_input(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the validated JSON body for a run start, or raise ContractViolation.

    This must run before any start request is built, so an invalid input never
    reaches paid execution (MS2-D-14); Apify's own input-schema check is only a
    second layer.
    """
    try:
        model = SyntheticCollectorInput.model_validate(dict(payload))
    except ValidationError as exc:
        raise ContractViolation(f"run input violates {INPUT_SCHEMA_VERSION}: {exc}") from exc
    return model.model_dump(mode="json")


def usable_rows(rows: Sequence[object]) -> list[ListingRow]:
    """Return the dataset rows that satisfy hw-radar-listing/v1, dropping the rest.

    Never raises for a bad row: the count of survivors is classify_run's
    usable_count, and a shortfall is itself classified, not an exception.
    """
    usable: list[ListingRow] = []
    for row in rows:
        try:
            usable.append(ListingRow.model_validate(row))
        except ValidationError:
            continue
    return usable


@dataclass(frozen=True, slots=True)
class AdmittedRun:
    """What hw-radar admitted the run for; the OUTPUT echo must match it exactly."""

    query_scope: QueryScope
    actor_name: str


class RunClassification(NamedTuple):
    completeness: RunCompleteness
    reason: str
    truncation_reason: TruncationReason | None


# Fixed reporting precedence (MS2-D-14, revision 5): the first hit limit in this
# order names the TruncationReason; every hit limit is still listed in the reason.
_LIMIT_PRECEDENCE: Final[tuple[tuple[str, TruncationReason], ...]] = (
    ("time", TruncationReason.TIME_LIMIT),
    ("bytes", TruncationReason.RESOURCE_LIMIT),
    ("requests", TruncationReason.REQUEST_LIMIT),
    ("pages", TruncationReason.PAGE_LIMIT),
    ("items", TruncationReason.ITEM_LIMIT),
)


def _failed(reason: str) -> RunClassification:
    return RunClassification(RunCompleteness.FAILED, reason, None)


def _hit_limits(limits: LimitsHit) -> list[str]:
    return [name for name, _ in _LIMIT_PRECEDENCE if getattr(limits, name)]


def _contradictions(remote_status: str, run: RunOutput, dataset_count: int) -> list[str]:
    report = run.completeness
    hit = _hit_limits(report.limits_hit)
    found: list[str] = []
    if report.complete and hit:
        found.append("complete_with_limit_hit")
    if report.complete and report.truncated:
        found.append("complete_and_truncated")
    if report.truncated and not hit and remote_status != RUN_STATUS_TIMED_OUT:
        found.append("truncated_without_limit_hit")
    if remote_status == RUN_STATUS_SUCCEEDED and run.status != "succeeded":
        found.append("status_mismatch")
    # The declared-count checks below mirror the Actor's own refusal to claim
    # completeness (synthetic_collector.core._finish) and are repeated here on
    # purpose: only a complete run may authorize absence/delist evidence, so a
    # report claiming completeness while the source declared more (or fewer)
    # items or pages than were collected must not be trusted merely because the
    # Actor that wrote it was supposed to have checked.
    if report.items_emitted != dataset_count:
        found.append("count_mismatch")
    elif (
        report.complete
        and report.items_declared is not None
        and report.items_emitted != report.items_declared
    ):
        # elif: once itemsEmitted disagrees with the dataset it is already void,
        # and comparing the source's declaration against it adds no evidence.
        found.append("complete_with_items_declared_mismatch")
    if report.pages_declared is not None and report.pages_fetched > report.pages_declared:
        found.append("pages_fetched_exceed_declared")
    if (
        report.complete
        and report.pages_declared is not None
        and report.pages_fetched < report.pages_declared
    ):
        found.append("complete_with_pages_unfetched")
    return found


def classify_run(
    remote_status: str,
    output: object,
    dataset_count: int,
    usable_count: int,
    *,
    admitted: AdmittedRun,
) -> RunClassification:
    """Classify one terminal remote run per the MS2-D-14 completeness mapping.

    output is the decoded `OUTPUT` record, or None when the record is absent.
    dataset_count is the run's dataset item count; usable_count is how many of
    those rows pass usable_rows. admitted is what the run was started for: the
    plan's four-argument signature cannot express scope_mismatch without it.

    Raises ValueError for a non-terminal remote status or impossible counts: those
    are caller bugs, not remote evidence. Everything the remote side supplied is
    classified, never raised.

    Precedence, most conservative first:
    1. failed — missing OUTPUT, unknown schemaVersion, invalid OUTPUT, scope
       mismatch, a self-contradictory report, or a non-empty dataset with no
       usable row;
    2. partial_failure — FAILED/ABORTED with items (FAILED/ABORTED without items
       is failed), then any reported error;
    3. truncated — any hit limit or TIMED-OUT, with the TruncationReason;
    4. failed — an empty dataset without complete-empty evidence, or an
       incomplete report that names no cause;
    5. partial_failure — some rows unusable;
    6. complete, or complete_empty.
    Errors outrank truncation: both refuse remote absence, and a run that failed
    part of its work is the less trustworthy description.
    """
    if remote_status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"cannot classify a non-terminal run status: {remote_status!r}")
    if not 0 <= usable_count <= dataset_count:
        raise ValueError(f"usable_count {usable_count} outside 0..{dataset_count}")

    if output is None:
        return _failed(REASON_MISSING_OUTPUT)
    if not isinstance(output, Mapping):
        return _failed(f"{REASON_INVALID_OUTPUT}: OUTPUT is not an object")
    version = output.get("schemaVersion")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if version != RUN_SCHEMA_VERSION:
        return _failed(f"{REASON_UNKNOWN_SCHEMA_VERSION}: {version!r}")
    try:
        run = RunOutput.model_validate(output)
    except ValidationError as exc:
        return _failed(f"{REASON_INVALID_OUTPUT}: {exc.error_count()} contract error(s)")

    if run.provider.actor_name != admitted.actor_name or run.query_scope != admitted.query_scope:
        return _failed(REASON_SCOPE_MISMATCH)

    contradictions = _contradictions(remote_status, run, dataset_count)
    if contradictions:
        # Fails closed: nothing is persisted and a FULL run breaks continuity
        # (MS2-D-22 Reject), because a report that disagrees with itself cannot
        # be trusted in either direction.
        return _failed(f"{REASON_CONTRADICTORY_REPORT}: {', '.join(contradictions)}")
    if dataset_count > 0 and usable_count == 0:
        return _failed(REASON_NO_USABLE_ITEMS)

    if remote_status in (RUN_STATUS_FAILED, RUN_STATUS_ABORTED):
        if dataset_count > 0:
            return RunClassification(
                RunCompleteness.PARTIAL_FAILURE,
                f"{REASON_REMOTE_FAILED_WITH_ITEMS}: {remote_status}",
                None,
            )
        return _failed(f"{REASON_REMOTE_FAILED_EMPTY}: {remote_status}")
    if run.errors:
        codes = ", ".join(sorted({error.code for error in run.errors}))
        return RunClassification(
            RunCompleteness.PARTIAL_FAILURE, f"{REASON_ERRORS_REPORTED}: {codes}", None
        )

    hit = _hit_limits(run.completeness.limits_hit)
    if hit or remote_status == RUN_STATUS_TIMED_OUT:
        causes = list(hit)
        if remote_status == RUN_STATUS_TIMED_OUT and "time" not in causes:
            causes.insert(0, "time")
        # TIMED-OUT is a platform time limit even when the Actor flagged nothing.
        reason = next(cause for name, cause in _LIMIT_PRECEDENCE if name in causes)
        return RunClassification(
            RunCompleteness.TRUNCATED, f"{REASON_TRUNCATED}: {', '.join(causes)}", reason
        )

    report = run.completeness
    if dataset_count == 0:
        proven_empty = (
            report.complete
            and report.pages_fetched >= 1
            and report.items_declared == 0
            and report.items_emitted == 0
        )
        if proven_empty:
            return RunClassification(RunCompleteness.COMPLETE, REASON_COMPLETE_EMPTY, None)
        return _failed(REASON_AMBIGUOUS_EMPTY)
    if not report.complete:
        # Not truncated, no error, yet not complete: the report names no cause, so
        # it is ambiguous evidence and, like ambiguous-empty, persists nothing.
        return _failed(REASON_UNEXPLAINED_INCOMPLETE)
    if usable_count < dataset_count:
        return RunClassification(
            RunCompleteness.PARTIAL_FAILURE,
            f"{REASON_UNUSABLE_ITEMS}: {dataset_count - usable_count} of {dataset_count}",
            None,
        )
    return RunClassification(RunCompleteness.COMPLETE, REASON_COMPLETE, None)
