"""Pure Apify budget policy (plan E2; MS2-D-17, -26, -32, -40, -41, -46).

Two jobs, both free of database access so they can be tested exhaustively and
called under the E3 ledger's advisory lock without I/O:

- estimate_run_cost and the operator estimators turn settings plus a run's
  requested shape into a CostEstimate: the MS2-D-26 component bounds, the
  MS2-D-32 split into execution bound, post-run liability, and monitoring
  allowance, and the reserved estimate `(execution + post-run) x (1 + margin)`.
- decide_admission applies every MS2-D-17/-40/-48 admission rule to one
  request against a LedgerState that the caller (E3) computes from ledger rows
  under the budget lock. It never reads rows itself.

Account state is configuration, never an observation (MS2-D-48): the runtime
reads no Apify account endpoint. The billing cycle is derived from the
operator-verified anchor (billing_cycle_bounds), and the account limit, base
price, and data retention are operator-verified settings, all validated by the
one contract account_setting_problem, which admission and the post-admission
correction check (reconcile.invariant_breaches) share.

Callers own persistence. This module defines its own BudgetClass and
OperatorKind (string-equal to catalog.models.provider.AdmissionClass and the
E1 operator_kind choices) so it depends on no model; E3 maps between them.

Denial is fail-closed by construction. Every unset or invalid setting maps to a
named DenialReason, never to a default: settings.py parses an invalid value to
None (the account margin to NaN) precisely so this module can deny it. Money is
Decimal throughout; each reported amount is rounded UP to the ledger's 4-place
column precision, so rounding can only over-reserve.

Byte units: transfer and storage are priced per decimal GB (10^9 bytes), which
prices a byte higher than a GiB would. Wire ceilings reuse the client's code
constants (HTTP_RECEIVE_BUFFER_BYTES, MAX_API_REQUEST_BODY_BYTES) and the
contract's row bound and page sizing, so the estimate and the enforcement that
makes it a bound cannot drift apart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Final

from django.conf import settings

from hw_radar.acquisition.apify.client import (
    HTTP_RECEIVE_BUFFER_BYTES,
    MAX_API_REQUEST_BODY_BYTES,
)
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES, page_limit

__all__ = [
    "FULL_CYCLE_HOURS",
    "HTTP_READ_CHUNK_BYTES",
    "MAX_CYCLE_TARGET_USD",
    "AccountSettingProblem",
    "AccountSnapshot",
    "AdmissionDecision",
    "AdmissionRequest",
    "BudgetClass",
    "BudgetDenied",
    "BudgetSettings",
    "CostEstimate",
    "CycleDebits",
    "DenialReason",
    "LedgerState",
    "OperatorKind",
    "PerCallBounds",
    "RunShape",
    "UnitPrices",
    "account_margin_usd",
    "account_setting_problem",
    "actor_fetch_bytes",
    "api_call_bound",
    "billing_cycle_bounds",
    "dataset_page_bound",
    "decide_admission",
    "estimate_operator_cost",
    "estimate_run_cost",
    "load_budget_settings",
    "pages_per_read",
    "per_call_bounds",
    "project_allocation",
    "request_wire_overhead",
    "settle_envelope",
    "storage_hours",
    "trailing_window_warning",
]

# Code constant of the MS2-D-26 *Data transfer* bound: the most one network
# read can deliver, i.e. the Actor's largest overshoot past maxBytes at the
# stop. Cross-file contract: must cover the Actor's own HTTP_READ_CHUNK_BYTES
# (actors/hw-radar-synthetic-collector/src/synthetic_collector/core.py), which
# tests/unit/test_apify_contract.py::test_actor_transfer_constants_match_estimator
# reads from that file's source text.
HTTP_READ_CHUNK_BYTES: Final = 65536

# The owner's operating target (MS2-D-40); raising it is an owner decision, so a
# larger setting is refused rather than honoured.
MAX_CYCLE_TARGET_USD: Final = Decimal("12.00")
# One full billing cycle (31 days). storage_hours never prices less, and an
# observed cycle longer than this denies, because an undeleted storage recurs
# at one full cycle of storage per cycle (MS2-D-26, R10-07).
FULL_CYCLE_HOURS: Final = 744
# MS2-D-40 default account margin when the setting is absent.
DEFAULT_ACCOUNT_MARGIN_FRACTION: Final = Decimal("0.10")

_USD_QUANTUM: Final = Decimal("0.0001")
_BYTES_PER_GB: Final = Decimal(10**9)
_OPS_PER_PRICE_UNIT: Final = Decimal(1000)
_SECONDS_PER_HOUR: Final = Decimal(3600)
_MB_PER_GB_CU: Final = Decimal(1024)
# MS2-D-32 *Every call*: one overdue attempt is abort + confirming GET + one
# DELETE per storage (dataset, KV store); a delete is priced as the dearest
# operation of its type: max_items + 1 dataset writes, 3 KV writes (INPUT,
# OUTPUT, the store itself).
_CALLS_PER_CLEANUP_ATTEMPT: Final = 4
_KV_WRITES_PER_DELETE: Final = 3
_START_CALLS: Final = 1


class BudgetClass(StrEnum):
    """Admission class (MS2-D-17, -46); values equal catalog AdmissionClass."""

    WATCH_REFRESH = "watch_refresh"
    DISCOVERY = "discovery"
    OPERATOR = "operator"


class OperatorKind(StrEnum):
    BUILD = "build"
    INSPECT = "inspect"
    PROBE = "probe"


class DenialReason(StrEnum):
    """Why paid admission was denied; persisted as `denial_reason` by E3."""

    APIFY_DISABLED = "apify_disabled"
    OVERRUN_LATCH = "overrun_latch"
    CALL_BILLING_RESIDUAL_UNACCEPTED = "call_billing_residual_unaccepted"
    EXTERNAL_LIABILITY_UNBOUNDED = "external_liability_unbounded"
    # Not named by the plan: an allocation, ceiling, or ledger-timing setting
    # that is unset, invalid, or (the target) above the owner's 12.00. Caps and
    # component inputs use UNBOUNDED_COMPONENT instead, as the plan says.
    BUDGET_SETTING_INVALID = "budget_setting_invalid"
    PRICING_UNVERIFIED = "pricing_unverified"
    UNBOUNDED_COMPONENT = "unbounded_component"
    LEDGER_AUTHORITY_MISSING = "ledger_authority_missing"
    # MS2-D-48: the anchor is unset, invalid, in the future, or conflicts with
    # a recorded cycle, or `now` is outside the derived cycle.
    CYCLE_UNKNOWN = "cycle_unknown"
    # MS2-D-48: an operator-verified account setting is unset or invalid.
    ACCOUNT_STATE_UNOBSERVABLE = "account_state_unobservable"
    CASH_CEILING_EXCEEDED_BY_PLAN = "cash_ceiling_exceeded_by_plan"
    CLASS_CAP = "class_cap"
    OPERATOR_ALLOWANCE_EXHAUSTED = "operator_allowance_exhausted"
    ACCOUNT_HEADROOM = "account_headroom"


class BudgetDenied(Exception):
    """A setting or input makes a bound impossible; carries the denial reason."""

    def __init__(self, reason: DenialReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitPrices:
    """Verified unit prices as the pricing page lists them; None is unverified."""

    usd_per_cu: Decimal | None
    dataset_reads_per_1000: Decimal | None
    dataset_writes_per_1000: Decimal | None
    dataset_storage_per_gb_hour: Decimal | None
    kv_reads_per_1000: Decimal | None
    kv_writes_per_1000: Decimal | None
    kv_storage_per_gb_hour: Decimal | None
    # The higher of the external and internal transfer prices (ED-01).
    transfer_per_gb: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetSettings:
    """Every setting the pure policy reads. None means unset or invalid.

    account_margin_usd is the one exception: None means absent (10% of the
    configured account limit) and a NaN means present-but-invalid.
    """

    enabled: bool
    prices: UnitPrices
    margin: Decimal | None
    estimator_version: str
    max_timeout_s: int | None
    cycle_target_usd: Decimal | None
    operator_allowance_usd: Decimal | None
    watch_refresh_reserve_usd: Decimal | None
    cash_ceiling_usd: Decimal | None
    account_margin_usd: Decimal | None
    external_liability_usd: Decimal | None
    call_billing_residual_accepted: date | None
    operator_build_bound_usd: Decimal | None
    operator_inspect_max_items: int | None
    operator_inspect_max_record_reads: int | None
    operator_inspect_max_bytes: int | None
    operator_probe_max_calls: int | None
    max_dataset_reads: int | None
    max_kv_reads: int | None
    max_delete_attempts: int | None
    max_run_polls: int | None
    max_correction_reads: int | None
    max_kv_writes: int | None
    max_kv_bytes: int | None
    storage_max_lifetime_s: int | None
    api_call_overhead_bytes: int | None
    max_api_response_bytes: int | None
    max_dataset_page_bytes: int | None
    cycle_boundary_guard_s: int | None
    # Operator-verified account state (MS2-D-48); None is unset or invalid.
    billing_cycle_anchor: datetime | None
    account_limit_usd: Decimal | None
    account_base_price_usd: Decimal | None
    account_data_retention_days: int | None
    account_verified_on: date | None


def load_budget_settings() -> BudgetSettings:
    """Read BudgetSettings from Django settings (settings.py owns the parsing)."""
    s = settings
    return BudgetSettings(
        enabled=s.HW_RADAR_APIFY_ENABLED,
        prices=UnitPrices(
            usd_per_cu=s.HW_RADAR_APIFY_USD_PER_CU,
            dataset_reads_per_1000=s.HW_RADAR_APIFY_DATASET_READS_USD_PER_1000,
            dataset_writes_per_1000=s.HW_RADAR_APIFY_DATASET_WRITES_USD_PER_1000,
            dataset_storage_per_gb_hour=s.HW_RADAR_APIFY_DATASET_STORAGE_USD_PER_GB_HOUR,
            kv_reads_per_1000=s.HW_RADAR_APIFY_KV_READS_USD_PER_1000,
            kv_writes_per_1000=s.HW_RADAR_APIFY_KV_WRITES_USD_PER_1000,
            kv_storage_per_gb_hour=s.HW_RADAR_APIFY_KV_STORAGE_USD_PER_GB_HOUR,
            transfer_per_gb=s.HW_RADAR_APIFY_TRANSFER_USD_PER_GB,
        ),
        margin=s.HW_RADAR_APIFY_MARGIN,
        estimator_version=s.HW_RADAR_APIFY_ESTIMATOR_VERSION,
        max_timeout_s=s.HW_RADAR_APIFY_MAX_TIMEOUT_S,
        cycle_target_usd=s.HW_RADAR_APIFY_CYCLE_TARGET_USD,
        operator_allowance_usd=s.HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD,
        watch_refresh_reserve_usd=s.HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD,
        cash_ceiling_usd=s.HW_RADAR_APIFY_CASH_CEILING_USD,
        account_margin_usd=s.HW_RADAR_APIFY_ACCOUNT_MARGIN_USD,
        external_liability_usd=s.HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD,
        call_billing_residual_accepted=s.HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED,
        operator_build_bound_usd=s.HW_RADAR_APIFY_OPERATOR_BUILD_BOUND_USD,
        operator_inspect_max_items=s.HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_ITEMS,
        operator_inspect_max_record_reads=s.HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_RECORD_READS,
        operator_inspect_max_bytes=s.HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_BYTES,
        operator_probe_max_calls=s.HW_RADAR_APIFY_OPERATOR_PROBE_MAX_CALLS,
        max_dataset_reads=s.HW_RADAR_APIFY_MAX_DATASET_READS,
        max_kv_reads=s.HW_RADAR_APIFY_MAX_KV_READS,
        max_delete_attempts=s.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS,
        max_run_polls=s.HW_RADAR_APIFY_MAX_RUN_POLLS,
        max_correction_reads=s.HW_RADAR_APIFY_MAX_CORRECTION_READS,
        max_kv_writes=s.HW_RADAR_APIFY_MAX_KV_WRITES,
        max_kv_bytes=s.HW_RADAR_APIFY_MAX_KV_BYTES,
        storage_max_lifetime_s=s.HW_RADAR_APIFY_STORAGE_MAX_LIFETIME,
        api_call_overhead_bytes=s.HW_RADAR_APIFY_API_CALL_OVERHEAD_BYTES,
        max_api_response_bytes=s.HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES,
        max_dataset_page_bytes=s.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES,
        cycle_boundary_guard_s=s.HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S,
        billing_cycle_anchor=s.HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR,
        account_limit_usd=s.HW_RADAR_APIFY_ACCOUNT_LIMIT_USD,
        account_base_price_usd=s.HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD,
        account_data_retention_days=s.HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS,
        account_verified_on=s.HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON,
    )


def _price(value: Decimal | None, name: str) -> Decimal:
    if value is None or not value.is_finite() or value < 0:
        raise BudgetDenied(DenialReason.PRICING_UNVERIFIED, f"unit price {name} is unset")
    return value


def _cap(value: int | None, name: str, *, minimum: int = 0) -> int:
    if value is None or value < minimum:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, f"{name} is unset or below {minimum}")
    return value


def _setting(value: Decimal | None, name: str) -> Decimal:
    if value is None or not value.is_finite() or value < 0:
        raise BudgetDenied(DenialReason.BUDGET_SETTING_INVALID, f"{name} is unset or invalid")
    return value


def _margin(cfg: BudgetSettings) -> Decimal:
    margin = cfg.margin
    if margin is None or not margin.is_finite() or margin < 0:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, "reservation margin is unset")
    return margin


def _usd(amount: Decimal) -> Decimal:
    # Up, never to nearest: the ledger stores Decimal(10,4), and a reserved
    # bound rounded down could settle above itself and trip the latch falsely.
    return amount.quantize(_USD_QUANTUM, rounding=ROUND_CEILING)


def _per_op(price_per_1000: Decimal | None, name: str) -> Decimal:
    return _price(price_per_1000, name) / _OPS_PER_PRICE_UNIT


# ── Per-call bounds (MS2-D-32 *Per-call bound*) ─────────────────────────────


def request_wire_overhead(cfg: BudgetSettings) -> int:
    """`API_CALL_OVERHEAD_BYTES + 2 x HTTP_RECEIVE_BUFFER_BYTES` (Linux doubles SO_RCVBUF)."""
    overhead = _cap(cfg.api_call_overhead_bytes, "API_CALL_OVERHEAD_BYTES")
    return overhead + 2 * HTTP_RECEIVE_BUFFER_BYTES


def _transfer_per_byte(cfg: BudgetSettings) -> Decimal:
    return _price(cfg.prices.transfer_per_gb, "TRANSFER_USD_PER_GB") / _BYTES_PER_GB


def api_call_bound(cfg: BudgetSettings) -> Decimal:
    """One call: the dearest storage operation plus wire_bytes(MAX_API_RESPONSE_BYTES) of transfer.

    The docs give a no-storage call zero operations; the one operation is kept
    as margin so no revision-10 bound is lowered. The dearest of the four
    storage-operation prices this ledger uses (request-queue and key-list
    operations are never performed by a run, MS2-D-26 allowlist).
    """
    p = cfg.prices
    dearest = max(
        _per_op(p.dataset_reads_per_1000, "DATASET_READS_USD_PER_1000"),
        _per_op(p.dataset_writes_per_1000, "DATASET_WRITES_USD_PER_1000"),
        _per_op(p.kv_reads_per_1000, "KV_READS_USD_PER_1000"),
        _per_op(p.kv_writes_per_1000, "KV_WRITES_USD_PER_1000"),
    )
    response_cap = _cap(cfg.max_api_response_bytes, "MAX_API_RESPONSE_BYTES")
    wire = request_wire_overhead(cfg) + response_cap
    return dearest + wire * _transfer_per_byte(cfg)


def dataset_page_bound(cfg: BudgetSettings) -> Decimal:
    """One dataset page's transfer; its item reads are priced in the storage component."""
    page_cap = _cap(cfg.max_dataset_page_bytes, "MAX_DATASET_PAGE_BYTES")
    return (request_wire_overhead(cfg) + page_cap) * _transfer_per_byte(cfg)


@dataclass(frozen=True, slots=True, kw_only=True)
class PerCallBounds:
    """The *API calls* price of each call hw-radar makes (the MS2-D-32 call table)."""

    start_run: Decimal
    run_poll: Decimal
    correction_read: Decimal
    abort: Decimal
    abort_confirm: Decimal
    delete_dataset: Decimal
    delete_kv_store: Decimal
    dataset_page: Decimal
    kv_record_read: Decimal
    build_read: Decimal


def per_call_bounds(cfg: BudgetSettings) -> PerCallBounds:
    call = api_call_bound(cfg)
    return PerCallBounds(
        start_run=call,
        run_poll=call,
        correction_read=call,
        abort=call,
        abort_confirm=call,
        delete_dataset=call,
        delete_kv_store=call,
        dataset_page=dataset_page_bound(cfg),
        kv_record_read=call,
        build_read=call,
    )


# ── Run estimate (MS2-D-26 components, MS2-D-32 split) ──────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class RunShape:
    """The caps a run start requests (Actor input and run options)."""

    memory_mb: int
    timeout_s: int
    max_items: int
    max_requests: int
    max_bytes: int
    max_item_bytes: int = MAX_LISTING_ROW_BYTES

    def __post_init__(self) -> None:
        # A malformed request is a caller bug, not a budget outcome: it must
        # never be priced (a negative cap would lower the reservation).
        if self.memory_mb <= 0 or self.timeout_s <= 0 or self.max_item_bytes <= 0:
            raise ValueError("memory_mb, timeout_s, and max_item_bytes must be positive")
        if self.max_items < 0 or self.max_requests < 0 or self.max_bytes < 0:
            raise ValueError("max_items, max_requests, and max_bytes must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class CostEstimate:
    """A reservation's bound and its recorded parts (E1 columns).

    components holds the unrounded component bounds for `component_bounds`.
    execution_bound_usd and post_run_liability_usd are pre-margin; estimate_usd
    is their (rounded) sum times (1 + margin), so a `bound`-mode settlement of
    `execution + post-run` never exceeds it. monitoring_bound_usd is held
    beside the estimate, never inside it (MS2-D-34 *Monitoring charges*).
    """

    components: Mapping[str, Decimal]
    execution_bound_usd: Decimal
    post_run_liability_usd: Decimal
    monitoring_bound_usd: Decimal
    estimate_usd: Decimal
    estimator_version: str

    @property
    def admission_usd(self) -> Decimal:
        """What admission debits: the estimate plus the monitoring allowance."""
        return self.estimate_usd + self.monitoring_bound_usd


def _with_margin(execution: Decimal, post_run: Decimal, margin: Decimal) -> Decimal:
    # Round the parts first, then the product: rounding (e + p) x (1 + m) once
    # could land 0.0001 below round(e) + round(p), and a bound-mode settlement
    # of exactly those two recorded parts would then trip the overrun latch.
    return _usd((_usd(execution) + _usd(post_run)) * (1 + margin))


def storage_hours(cfg: BudgetSettings) -> Decimal:
    """`max(STORAGE_MAX_LIFETIME, 744 h + 2 x guard)` in hours (MS2-D-26, R10-07)."""
    lifetime = _cap(cfg.storage_max_lifetime_s, "STORAGE_MAX_LIFETIME", minimum=1)
    guard = _cap(cfg.cycle_boundary_guard_s, "CYCLE_BOUNDARY_GUARD_S")
    return max(
        Decimal(lifetime) / _SECONDS_PER_HOUR,
        FULL_CYCLE_HOURS + Decimal(2 * guard) / _SECONDS_PER_HOUR,
    )


def pages_per_read(cfg: BudgetSettings, max_items: int, max_item_bytes: int) -> int:
    """`ceil(max_items / page_limit) + 1`: the + 1 is the terminating empty page."""
    page_cap = _cap(cfg.max_dataset_page_bytes, "MAX_DATASET_PAGE_BYTES")
    try:
        limit = page_limit(page_cap, max_item_bytes)
    except ValueError as exc:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, str(exc)) from exc
    return math.ceil(max_items / limit) + 1


def actor_fetch_bytes(cfg: BudgetSettings, *, max_bytes: int, max_requests: int) -> int:
    """`maxBytes + maxRequests x request_wire_overhead + HTTP_READ_CHUNK_BYTES` (R10-02).

    The Actor checks maxBytes only after a read arrives, so the stop can
    overshoot by one read; every request can also leave a full receive window
    in flight when abandoned, which request_wire_overhead covers.
    """
    return max_bytes + max_requests * request_wire_overhead(cfg) + HTTP_READ_CHUNK_BYTES


def estimate_run_cost(shape: RunShape, cfg: BudgetSettings) -> CostEstimate:
    """Return the MS2-D-26 reservation for one Actor run.

    Raises BudgetDenied with PRICING_UNVERIFIED when a unit price is unset and
    UNBOUNDED_COMPONENT when a cap, the margin, or the requested timeout has no
    enforceable bound (including page_limit < 1 and MAX_KV_BYTES above the
    control response cap, R10-08). Prices are checked first, so a missing
    price is reported as such even when a cap is also missing.
    """
    p = cfg.prices
    usd_per_cu = _price(p.usd_per_cu, "USD_PER_CU")
    ds_read = _per_op(p.dataset_reads_per_1000, "DATASET_READS_USD_PER_1000")
    ds_write = _per_op(p.dataset_writes_per_1000, "DATASET_WRITES_USD_PER_1000")
    kv_read = _per_op(p.kv_reads_per_1000, "KV_READS_USD_PER_1000")
    kv_write = _per_op(p.kv_writes_per_1000, "KV_WRITES_USD_PER_1000")
    ds_storage = _price(p.dataset_storage_per_gb_hour, "DATASET_STORAGE_USD_PER_GB_HOUR")
    kv_storage = _price(p.kv_storage_per_gb_hour, "KV_STORAGE_USD_PER_GB_HOUR")
    per_byte = _transfer_per_byte(cfg)

    margin = _margin(cfg)
    max_timeout = _cap(cfg.max_timeout_s, "MAX_TIMEOUT_S", minimum=1)
    if shape.timeout_s > max_timeout:
        raise BudgetDenied(
            DenialReason.UNBOUNDED_COMPONENT,
            f"timeout_s {shape.timeout_s} exceeds MAX_TIMEOUT_S {max_timeout}",
        )
    dataset_reads = _cap(cfg.max_dataset_reads, "MAX_DATASET_READS")
    kv_reads = _cap(cfg.max_kv_reads, "MAX_KV_READS")
    delete_attempts = _cap(cfg.max_delete_attempts, "MAX_DELETE_ATTEMPTS")
    run_polls = _cap(cfg.max_run_polls, "MAX_RUN_POLLS")
    correction_reads = _cap(cfg.max_correction_reads, "MAX_CORRECTION_READS")
    kv_writes = _cap(cfg.max_kv_writes, "MAX_KV_WRITES")
    kv_bytes = _cap(cfg.max_kv_bytes, "MAX_KV_BYTES")
    response_cap = _cap(cfg.max_api_response_bytes, "MAX_API_RESPONSE_BYTES")
    if kv_bytes > response_cap:
        # An OUTPUT record larger than the response cap could never be read
        # back, and the read attempt would trip api_response_over_cap (R10-08).
        raise BudgetDenied(
            DenialReason.UNBOUNDED_COMPONENT,
            f"MAX_KV_BYTES {kv_bytes} exceeds MAX_API_RESPONSE_BYTES {response_cap}",
        )
    pages = pages_per_read(cfg, shape.max_items, shape.max_item_bytes)
    hours = storage_hours(cfg)
    call = api_call_bound(cfg)
    page = dataset_page_bound(cfg)

    compute = Decimal(shape.memory_mb) / _MB_PER_GB_CU * shape.timeout_s / _SECONDS_PER_HOUR
    dataset_bytes = shape.max_items * shape.max_item_bytes
    fetch = actor_fetch_bytes(cfg, max_bytes=shape.max_bytes, max_requests=shape.max_requests)
    components: dict[str, Decimal] = {
        # Execution part.
        "compute": compute * usd_per_cu,
        "run_writes": shape.max_items * ds_write + kv_writes * kv_write,
        "transfer": (fetch + dataset_bytes + kv_writes * kv_bytes) * per_byte,
        "proxy": Decimal(0),
        # Post-run liability.
        "post_run_reads": dataset_reads * (shape.max_items + pages) * ds_read + kv_reads * kv_read,
        "post_run_deletes": delete_attempts
        * ((shape.max_items + 1) * ds_write + _KV_WRITES_PER_DELETE * kv_write),
        "storage": (
            dataset_bytes * ds_storage + (kv_bytes + MAX_API_REQUEST_BODY_BYTES) * kv_storage
        )
        / _BYTES_PER_GB
        * hours,
        "api_calls": (
            _START_CALLS + run_polls + _CALLS_PER_CLEANUP_ATTEMPT * delete_attempts + kv_reads
        )
        * call
        + dataset_reads * pages * page,
    }
    execution = sum(
        (components[k] for k in ("compute", "run_writes", "transfer", "proxy")), Decimal(0)
    )
    post_run = sum(
        (components[k] for k in ("post_run_reads", "post_run_deletes", "storage", "api_calls")),
        Decimal(0),
    )
    return CostEstimate(
        components=components,
        execution_bound_usd=_usd(execution),
        post_run_liability_usd=_usd(post_run),
        monitoring_bound_usd=_usd(correction_reads * call),
        estimate_usd=_with_margin(execution, post_run, margin),
        estimator_version=cfg.estimator_version,
    )


# ── Operator estimates (MS2-D-46) ────────────────────────────────────────────


def estimate_operator_cost(kind: OperatorKind, cfg: BudgetSettings) -> CostEstimate:
    """Return the bound of one operator reservation of `kind`.

    build: `BUILD_BOUND + MAX_RUN_POLLS x api_call_bound` reserved, plus
    `MAX_CORRECTION_READS x api_call_bound` held as its monitoring allowance.
    The plan's build_reservation formula carries no (1 + margin), and none is
    applied here. inspect and probe are envelopes x (1 + margin), with no
    monitoring allowance. Raises BudgetDenied like estimate_run_cost.
    """
    call = api_call_bound(cfg)
    per_byte = _transfer_per_byte(cfg)
    if kind is OperatorKind.BUILD:
        bound = cfg.operator_build_bound_usd
        if bound is None or not bound.is_finite() or bound < 0:
            raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, "OPERATOR_BUILD_BOUND_USD unset")
        polls = _cap(cfg.max_run_polls, "MAX_RUN_POLLS")
        corrections = _cap(cfg.max_correction_reads, "MAX_CORRECTION_READS")
        components = {"build": bound, "api_calls": polls * call}
        execution = bound + polls * call
        return CostEstimate(
            components=components,
            execution_bound_usd=_usd(execution),
            post_run_liability_usd=Decimal("0.0000"),
            monitoring_bound_usd=_usd(corrections * call),
            estimate_usd=_usd(execution),
            estimator_version=cfg.estimator_version,
        )
    margin = _margin(cfg)
    if kind is OperatorKind.INSPECT:
        ds_read = _per_op(cfg.prices.dataset_reads_per_1000, "DATASET_READS_USD_PER_1000")
        items = _cap(cfg.operator_inspect_max_items, "OPERATOR_INSPECT_MAX_ITEMS")
        records = _cap(cfg.operator_inspect_max_record_reads, "OPERATOR_INSPECT_MAX_RECORD_READS")
        out_bytes = _cap(cfg.operator_inspect_max_bytes, "OPERATOR_INSPECT_MAX_BYTES")
        components = {
            "items": items * ds_read,
            "record_reads": records * call,
            "transfer": out_bytes * per_byte,
        }
    else:
        ds_write = _per_op(cfg.prices.dataset_writes_per_1000, "DATASET_WRITES_USD_PER_1000")
        ds_storage = _price(
            cfg.prices.dataset_storage_per_gb_hour, "DATASET_STORAGE_USD_PER_GB_HOUR"
        )
        calls = _cap(cfg.operator_probe_max_calls, "OPERATOR_PROBE_MAX_CALLS")
        components = {
            "calls": calls * (call + ds_write),
            "storage": MAX_LISTING_ROW_BYTES * ds_storage / _BYTES_PER_GB * storage_hours(cfg),
        }
    envelope = sum(components.values(), Decimal(0))
    return CostEstimate(
        components=components,
        execution_bound_usd=_usd(envelope),
        post_run_liability_usd=Decimal("0.0000"),
        monitoring_bound_usd=Decimal("0.0000"),
        estimate_usd=_with_margin(envelope, Decimal(0), margin),
        estimator_version=cfg.estimator_version,
    )


def settle_envelope(estimate: CostEstimate, observed_usd: Sequence[Decimal] = ()) -> Decimal:
    """Settle an inspection or probe envelope: its full bound, never below (MS2-D-46).

    Per-read attribution in a shared account is not separable, so nothing the
    operator observes can lower it; an observation above it still counts.
    """
    return max((estimate.estimate_usd, *observed_usd))


# ── Allocation (MS2-D-40) ────────────────────────────────────────────────────


def project_allocation(cfg: BudgetSettings) -> Decimal:
    """`A = CYCLE_TARGET_USD - OPERATOR_ALLOWANCE_USD`; raises for an invalid setting."""
    target = _setting(cfg.cycle_target_usd, "CYCLE_TARGET_USD")
    if target > MAX_CYCLE_TARGET_USD:
        raise BudgetDenied(
            DenialReason.BUDGET_SETTING_INVALID,
            f"CYCLE_TARGET_USD {target} is above the owner's {MAX_CYCLE_TARGET_USD}",
        )
    allowance = _setting(cfg.operator_allowance_usd, "OPERATOR_ALLOWANCE_USD")
    if allowance > target:
        raise BudgetDenied(
            DenialReason.BUDGET_SETTING_INVALID, "OPERATOR_ALLOWANCE_USD exceeds the target"
        )
    return target - allowance


# ── Billing cycle (MS2-D-48 *Cycle*) ─────────────────────────────────────────

# A cycle ends this long before its successor starts, matching Apify's own
# `...T23:59:59.999Z` end stamps (and ledger._CYCLE_END_EPSILON's clamp).
_CYCLE_END_EPSILON: Final = timedelta(milliseconds=1)


def _cycle_start(anchor: datetime, k: int) -> datetime:
    """UTC midnight on the anchor's day, `k` months after the anchor's month."""
    months = anchor.month - 1 + k
    return datetime(anchor.year + months // 12, months % 12 + 1, anchor.day, tzinfo=UTC)


def billing_cycle_bounds(anchor: datetime, now: datetime) -> tuple[datetime, datetime] | None:
    """The configured billing cycle `[start, end]` containing `now`, or None before the anchor.

    `anchor` is settings' validated value (UTC midnight, day 1-28). The end is
    the next cycle's start minus 1 ms. A `now` in the sub-millisecond gap after
    that end still maps to this cycle, and admission's `start <= now <= end`
    test then denies it `cycle_unknown`, which fails closed.
    """
    # Every start is computed from the anchor directly, never by stepping from
    # the previous cycle: stepping would carry any one-off error into every
    # later boundary, while this form cannot drift however far `now` is.
    at = now.astimezone(UTC)
    if at < anchor:
        return None
    k = 12 * (at.year - anchor.year) + (at.month - anchor.month)
    if _cycle_start(anchor, k) > at:
        k -= 1
    return _cycle_start(anchor, k), _cycle_start(anchor, k + 1) - _CYCLE_END_EPSILON


def account_margin_usd(cfg: BudgetSettings, limit_usd: Decimal) -> Decimal:
    """The configured account margin, or 10% of the configured account limit when absent."""
    if cfg.account_margin_usd is None:
        return limit_usd * DEFAULT_ACCOUNT_MARGIN_FRACTION
    return _setting(cfg.account_margin_usd, "ACCOUNT_MARGIN_USD")


@dataclass(frozen=True, slots=True)
class AccountSettingProblem:
    """The first invalid account setting: its short name and the denial it causes."""

    setting: str
    reason: DenialReason


def _bad_money(value: Decimal | None, *, positive: bool) -> bool:
    if value is None or not value.is_finite():
        return True
    return value <= 0 if positive else value < 0


def account_setting_problem(cfg: BudgetSettings, now: datetime) -> AccountSettingProblem | None:
    """The first invalid MS2-D-48 account setting, in contract order, or None when all are valid.

    The one validation contract that admission (decide_admission) and the
    post-admission correction check (reconcile.invariant_breaches) share
    (R12-04), so a setting can never be valid to one and invalid to the other.
    Setting validity only: the cash-ceiling, retention-versus-lifetime, and
    744 h checks compare settings with each other or with the cycle and stay
    admission guards, because a correction cannot change them. `now` bounds
    ACCOUNT_VERIFIED_ON, evaluated at the caller's own instant.
    """
    anchor = cfg.billing_cycle_anchor
    if anchor is None:
        return AccountSettingProblem("BILLING_CYCLE_ANCHOR", DenialReason.CYCLE_UNKNOWN)
    unobservable = DenialReason.ACCOUNT_STATE_UNOBSERVABLE
    if _bad_money(cfg.account_limit_usd, positive=True):
        return AccountSettingProblem("ACCOUNT_LIMIT_USD", unobservable)
    if _bad_money(cfg.account_base_price_usd, positive=False):
        return AccountSettingProblem("ACCOUNT_BASE_PRICE_USD", unobservable)
    retention = cfg.account_data_retention_days
    if retention is None or retention < 1:
        return AccountSettingProblem("ACCOUNT_DATA_RETENTION_DAYS", unobservable)
    verified = cfg.account_verified_on
    # Evidence, not an expiry (owner, R39): only a date the verification
    # could not have happened on is invalid, never an old one.
    if verified is None or verified > now.astimezone(UTC).date() or verified < anchor.date():
        return AccountSettingProblem("ACCOUNT_VERIFIED_ON", unobservable)
    margin = cfg.account_margin_usd
    if margin is not None and _bad_money(margin, positive=False):
        return AccountSettingProblem("ACCOUNT_MARGIN_USD", DenialReason.BUDGET_SETTING_INVALID)
    return None


def trailing_window_warning(trailing_31d_usd: Decimal, cfg: BudgetSettings) -> bool:
    """Whether the report should warn: the trailing-31-day total exceeds A.

    Report-only (MS2-D-17): decide_admission never consults a trailing window.
    """
    return trailing_31d_usd > project_allocation(cfg)


# ── Admission (MS2-D-17, -26, -40, -41, -45, -46) ───────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountSnapshot:
    """The configured account state for the current cycle (MS2-D-48); None is unset.

    Not an observation, despite the name (kept to keep the revision-12 diff
    small): the bounds are the recorded cycle row's, derived from the
    configured anchor, and the account figures are the operator-verified
    settings as ledger._snapshot copied them. decide_admission validates the
    settings themselves through account_setting_problem, so a snapshot can
    never admit on a value the contract refuses.
    """

    cycle_start: datetime | None
    cycle_end: datetime | None
    account_limit_usd: Decimal | None
    base_price_usd: Decimal | None
    data_retention_days: int | None
    verified_on: date | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CycleDebits:
    """Hardware Radar's own debits for one cycle, summed by E3 from ledger rows.

    runtime_committed_usd and operator_committed_usd are each class group's
    settled rows touching the cycle plus unreconciled rows at full estimate
    plus their monitoring allowances (MS2-D-34).
    """

    runtime_committed_usd: Decimal = Decimal(0)
    operator_committed_usd: Decimal = Decimal(0)
    carried_handoff_usd: Decimal = Decimal(0)
    # Report-only trend metric; carried here to prove it never decides.
    trailing_31d_usd: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerState:
    now: datetime
    latch_tripped: bool
    authority_held: bool
    snapshot: AccountSnapshot | None
    current: CycleDebits = field(default_factory=CycleDebits)
    # Set by E3 when the request's charge interval can reach the next cycle.
    # Only the external-liability invariant is checked against it, with the
    # configured P, because the next cycle's own class debits start at zero.
    next_cycle: CycleDebits | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AdmissionRequest:
    budget_class: BudgetClass
    run: RunShape | None = None
    operator_kind: OperatorKind | None = None

    def __post_init__(self) -> None:
        is_operator = self.budget_class is BudgetClass.OPERATOR
        if is_operator != (self.operator_kind is not None) or is_operator == (self.run is not None):
            raise ValueError("an operator request has an operator_kind; a runtime one a run")


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admitted: bool
    reason: DenialReason | None = None
    detail: str = ""
    estimate: CostEstimate | None = None


def _hr_cycle(debits: CycleDebits) -> Decimal:
    """MS2-D-48 `HR_cycle`: runtime committed + operator committed + carried handoff."""
    return debits.runtime_committed_usd + debits.operator_committed_usd + debits.carried_handoff_usd


def _deny(
    reason: DenialReason, detail: str, estimate: CostEstimate | None = None
) -> AdmissionDecision:
    return AdmissionDecision(False, reason, detail, estimate)


def decide_admission(
    request: AdmissionRequest, cfg: BudgetSettings, ledger: LedgerState
) -> AdmissionDecision:
    """Decide one paid admission; never raises for a settings or state problem.

    Denial precedence is the order below: blanket stops (kill switch, latch,
    R38, external-liability bound, allocation settings), then the estimate,
    then the MS2-D-48 *Admission* order: ledger authority, `cycle_unknown`
    (no cycle, or `now` outside it), `account_state_unobservable` for an
    invalid account setting (account_setting_problem), the retention check,
    the 744 h check, the cash-ceiling guard on the configured base price, the
    class caps, and the external-liability check. A reservation exactly equal
    to the remaining amount is admitted.

    Budget checks, with `new = estimate + monitoring allowance`:
    - class cap: runtime classes count every runtime debit (plus carried
      handoff) against A for watch_refresh and `A - WATCH_REFRESH_RESERVE_USD`
      for discovery; the operator class counts operator debits against
      OPERATOR_ALLOWANCE_USD;
    - external-liability check: `HR_cycle + new + E <= P`, for the current
      cycle and, when given, the next;
    where `P = ACCOUNT_LIMIT_USD - account margin`. Nothing replaces the
    retired observed-usage check: other workloads' spend is not observed by
    the runtime, only bounded by E and Apify's own hard limit (MS2-D-48).
    """
    if not cfg.enabled:
        return _deny(DenialReason.APIFY_DISABLED, "HW_RADAR_APIFY_ENABLED is not true")
    if ledger.latch_tripped:
        return _deny(DenialReason.OVERRUN_LATCH, "the overrun latch is tripped")
    if cfg.call_billing_residual_accepted is None:
        return _deny(
            DenialReason.CALL_BILLING_RESIDUAL_UNACCEPTED,
            "CALL_BILLING_RESIDUAL_ACCEPTED is empty or not a date",
        )
    external = cfg.external_liability_usd
    if external is None or not external.is_finite() or external < 0:
        return _deny(
            DenialReason.EXTERNAL_LIABILITY_UNBOUNDED, "EXTERNAL_LIABILITY_USD is empty or invalid"
        )
    try:
        allocation = project_allocation(cfg)
        reserve = _setting(cfg.watch_refresh_reserve_usd, "WATCH_REFRESH_RESERVE_USD")
        if reserve > allocation:
            raise BudgetDenied(
                DenialReason.BUDGET_SETTING_INVALID, "WATCH_REFRESH_RESERVE_USD exceeds A"
            )
        ceiling = _setting(cfg.cash_ceiling_usd, "CASH_CEILING_USD")
        if request.run is not None:
            estimate = estimate_run_cost(request.run, cfg)
        else:
            assert request.operator_kind is not None  # __post_init__ guarantees it
            estimate = estimate_operator_cost(request.operator_kind, cfg)
        lifetime_s = _cap(cfg.storage_max_lifetime_s, "STORAGE_MAX_LIFETIME", minimum=1)
    except BudgetDenied as denied:
        return _deny(denied.reason, denied.detail)

    if not ledger.authority_held:
        return _deny(
            DenialReason.LEDGER_AUTHORITY_MISSING, "no ledger authority for this cycle", estimate
        )
    snap = ledger.snapshot
    if snap is None or snap.cycle_start is None or snap.cycle_end is None:
        return _deny(
            DenialReason.CYCLE_UNKNOWN, "no billing cycle derivable from the anchor", estimate
        )
    if not snap.cycle_start <= ledger.now <= snap.cycle_end:
        return _deny(DenialReason.CYCLE_UNKNOWN, "now is outside the recorded cycle", estimate)
    problem = account_setting_problem(cfg, ledger.now)
    if problem is not None:
        return _deny(problem.reason, f"{problem.setting} is unset or invalid", estimate)
    # account_setting_problem returned None, so each of these is set and valid.
    limit = cfg.account_limit_usd
    base_price = cfg.account_base_price_usd
    retention_days = cfg.account_data_retention_days
    assert limit is not None and base_price is not None and retention_days is not None
    if lifetime_s < retention_days * 86400:
        return _deny(
            DenialReason.UNBOUNDED_COMPONENT,
            "STORAGE_MAX_LIFETIME is shorter than ACCOUNT_DATA_RETENTION_DAYS",
            estimate,
        )
    if snap.cycle_end - snap.cycle_start > timedelta(hours=FULL_CYCLE_HOURS):
        return _deny(
            DenialReason.UNBOUNDED_COMPONENT,
            f"recorded cycle is longer than {FULL_CYCLE_HOURS} h",
            estimate,
        )
    if base_price > ceiling:
        return _deny(
            DenialReason.CASH_CEILING_EXCEEDED_BY_PLAN,
            f"plan base price {base_price} exceeds the cash ceiling {ceiling}",
            estimate,
        )

    usable = limit - account_margin_usd(cfg, limit)
    debits = ledger.current
    new = estimate.admission_usd
    if request.budget_class is BudgetClass.OPERATOR:
        if debits.operator_committed_usd + new > _setting(
            cfg.operator_allowance_usd, "OPERATOR_ALLOWANCE_USD"
        ):
            return _deny(
                DenialReason.OPERATOR_ALLOWANCE_EXHAUSTED,
                "the operator allowance cannot fit this reservation",
                estimate,
            )
    else:
        cap = (
            allocation
            if request.budget_class is BudgetClass.WATCH_REFRESH
            else allocation - reserve
        )
        runtime = debits.runtime_committed_usd + debits.carried_handoff_usd
        if runtime + new > cap:
            return _deny(
                DenialReason.CLASS_CAP, f"{request.budget_class} cap {cap} exceeded", estimate
            )

    for cycle_debits in (debits, ledger.next_cycle):
        if cycle_debits is None:
            continue
        if _hr_cycle(cycle_debits) + new + external > usable:
            return _deny(
                DenialReason.ACCOUNT_HEADROOM,
                "external-liability check: Hardware Radar's share exceeded",
                estimate,
            )
    return AdmissionDecision(True, None, "", estimate)
