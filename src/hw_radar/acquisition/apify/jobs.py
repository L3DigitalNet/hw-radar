"""Scheduler surface for self-owned Apify Actor runs (plan D5; MS2-D-16, -23, -26, -33).

Two jobs, both driven by the one APScheduler process (ADR 0012; there are no
Apify schedules or webhooks):

- start_provider_run is the start job. poll_source calls it instead of
  run_source for a source whose collection_provider is `apify`, after the same
  check_admission gate. It refuses, with no spend and no row, anything MS2-D-33
  forbids (a bounded-retention site, a timeout that does not fit the storage
  deadline), a FULL start while an earlier FULL run of the same scope is still
  outstanding, and anything the kill switch or budget admission denies; then it
  creates the provider_run row with `admitted_at` and the absolute storage
  deadline BEFORE the start request, sends exactly one start request, records
  the response, and verifies it (MS2-D-26 options check, MS2-D-38 version line).
- apify_poll_tick is one tick of the `apify-poll` interval job. It runs the
  four MS2-D-23 selectors over persisted state only, so a poller restart
  resumes every row: selector 1 polls active remote runs, selector 2 drives
  terminal rows with outstanding local work (the D10 importer, then storage
  cleanup, then (E4) the reconcile unit's settlement reads) and bound operator
  builds, selector 3 hands rows past their storage deadline to the overdue
  unit whatever their remote status, and selector 4 re-reads reconciled rows
  for upward corrections until a closing read commits. Each unit of work is
  claimed and committed on its own, with per-row backoff through
  `next_attempt_at` (`next_usage_read_at` for ledger rows), so one failing row
  never stalls the others. Every run poll also feeds the ledger
  (reconcile.record_run_usage). The kill switch does not stop the tick: it
  settles liability already reserved (MS2-D-17, ED-07).

Production safety. The budget admission binding is LedgerAdmission (E5): the
Slice E spend ledger, which records every denial as a ledger row and fails
closed on every unset price, cap, or configured account setting (MS2-D-17,
-40, -48). Nothing in production binds anything else. In front of it, the
kill switch settings.HW_RADAR_APIFY_ENABLED (default false) refuses every
start, an unset HW_RADAR_APIFY_ACTOR_ID refuses every start, and RUN_SPECS
holds only the synthetic proof site, whose factory returns no spec while
HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT is unset (its default), so even an
`apify` source with everything else allowed is refused `no_run_spec`. The
synthetic site also needs a SourceConfig row, which only the non-production
apify_synthetic_setup command creates (MS2-D-42). With the production defaults
no start request is ever sent, and in every configuration no Apify account
endpoint is read (MS2-D-48: the billing cycle and the account limits are
operator-verified settings). The poll tick never starts a run; with no
provider_run rows it makes no API call and constructs no client, so it is safe
to schedule with no token rendered.

SCOPE: storage deletion and the overdue abort-and-delete sequence are D11's
units of work (MS2-D-25, -33) in acquisition.apify.storage_cleanup; this module
owns their selectors and binds those units as the StorageUnit defaults. The one
abort sent here is the start-option-mismatch abort, which MS2-D-32 makes
attempt 1 of the overdue sequence: it is counted in storage_cleanup_attempts
and committed before it is sent, and it deletes nothing (deletion needs
terminal evidence, which the storage unit establishes). The ledger, the overrun latch (a mismatch, an observed
restart, and an orphaned start all trip it) and settlement live in
acquisition.apify.ledger and acquisition.apify.reconcile; this module trips
the latch only through ledger.trip_latch after the provider_run transaction
that found the condition has committed (ED-05). An API response over its byte
cap (ApifyResponseTooLargeError) trips `api_response_over_cap` for the row
whose call it was. Recovery-probe dispatch by provider is D12.

Requirements: a Django context with the catalog app, PostgreSQL (row locks via
select_for_update), and the HW_RADAR_APIFY_* settings named above; a real start
or poll also needs HW_RADAR_APIFY_TOKEN, read only when a client is built.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, cast

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import ValidationError

from hw_radar.acquisition.apify import importer, reconcile, synthetic
from hw_radar.acquisition.apify.budget import AdmissionRequest, BudgetClass, RunShape
from hw_radar.acquisition.apify.client import (
    ApifyApiError,
    ApifyClient,
    ApifyError,
    ApifyResponseTooLargeError,
    ApifyRun,
)
from hw_radar.acquisition.apify.contract import (
    INPUT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    TERMINAL_RUN_STATUSES,
    CollectorInput,
    QueryScope,
    SyntheticCollectorInput,
)
from hw_radar.acquisition.apify.importer import RejectReason, import_provider_run
from hw_radar.acquisition.apify.ledger import (
    LatchReason,
    LedgerConfig,
    load_ledger_config,
    reserve,
    take_budget_lock,
    trip_latch,
    trip_start_refusal,
)
from hw_radar.acquisition.contracts import AdapterRetention, ListingResolver
from hw_radar.acquisition.retention_policy import UnknownSourceRetention, source_retention
from hw_radar.catalog.models import (
    ApifySpendReservation,
    ProviderKind,
    ProviderRun,
    ReservationStatus,
    RunKind,
    SourceConfig,
)
from hw_radar.catalog.models.provider import AdmissionClass, ImportState, StorageState
from hw_radar.eligibility import ListingEvaluator

logger = logging.getLogger(__name__)

APIFY_POLL_SECONDS: Final = 60

# When this poller process started. A selector-4 pending marker older than
# this was left by a process that is gone and can never send its read; the
# poller resolves such markers to this instant once, at its start
# (poller.service.run, MS2-D-34, R10-03), before the first tick can run.
PROCESS_STARTED_AT: datetime = timezone.now()

# Per-row backoff after a failed unit of work: 1 min doubling to 1 h. The
# failure count lives in stage_detail["unit_failures"] and is cleared by the
# next successful poll or import.
UNIT_BACKOFF_BASE: Final = timedelta(minutes=1)
UNIT_BACKOFF_MAX: Final = timedelta(hours=1)

_TERMINAL_STATUSES: Final = sorted(TERMINAL_RUN_STATUSES)
_TERMINAL_IMPORT: Final = [ImportState.FINALIZED.value, ImportState.REJECTED.value]


def _schema_major(version: str) -> str:
    """Return the major of a contract schema version: "hw-radar-input/v1" -> "1"."""
    return version.rsplit("/v", 1)[1].split(".", 1)[0]


# MS2-D-38: an Actor's `version` is MAJOR.MINOR with the contract's major, so
# every build number of a compatible Actor starts "<major>.". Derived from the
# contract module, not a setting, so a contract bump cannot leave the check on
# the old line. The input and run contracts move together (one PR, MS2-D-14).
CONTRACT_MAJOR: Final = _schema_major(INPUT_SCHEMA_VERSION)
if _schema_major(RUN_SCHEMA_VERSION) != CONTRACT_MAJOR:
    raise RuntimeError("input and run contracts are on different majors")


# ── Budget admission (the Slice E seam) ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    """What a start asks budget admission to reserve for (MS2-D-17, -26).

    max_requests and max_bytes are the Actor input caps the transfer bound
    is priced from (budget.RunShape); max_pages is carried for the record.
    """

    site_key: str
    source_site_id: int
    scope_key: str
    run_kind: RunKind
    admission_class: AdmissionClass
    memory_mb: int
    timeout_s: int
    max_items: int
    max_pages: int
    max_requests: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """An admission answer. `reason` is the persisted DenialReason on a denial.

    reservation_id names the admitted ledger row the start job must attach
    its provider_run to; None means the binding reserved nothing (test-only
    bindings), and then nothing is attached.
    """

    admitted: bool
    reason: str = ""
    reservation_id: int | None = None


class BudgetAdmission(Protocol):
    """Paid-admission check run after check_admission and before any row exists.

    Async so the start job can await it; every database step inside runs
    through sync_to_async in its own transaction, so no transaction spans an
    await.
    """

    async def admit(self, request: BudgetRequest) -> BudgetDecision: ...


class LedgerAdmission:
    """The production binding: admission by the Slice E spend ledger (E5).

    One step: ledger.reserve under the budget lock, which materializes the
    configured billing cycle and persists the admitted row or the denial row
    (probe denials included). No Apify account endpoint is read, in any
    configuration (MS2-D-48): the cycle and the account state are
    operator-verified settings.

    `config` and `clock` default to the settings and to the reserve's own
    post-lock timestamp; tests pass both.
    """

    def __init__(self, *, config: LedgerConfig | None = None, clock: Clock | None = None) -> None:
        self._config = config
        self._clock = clock

    async def admit(self, request: BudgetRequest) -> BudgetDecision:
        config = self._config or await sync_to_async(load_ledger_config)()
        ask = AdmissionRequest(
            budget_class=BudgetClass(request.admission_class.value),
            run=RunShape(
                memory_mb=request.memory_mb,
                timeout_s=request.timeout_s,
                max_items=request.max_items,
                max_requests=request.max_requests,
                max_bytes=request.max_bytes,
            ),
        )
        outcome = await sync_to_async(reserve)(
            ask,
            source_site_id=request.source_site_id,
            config=config,
            now=self._clock() if self._clock is not None else None,
        )
        if not outcome.admitted:
            return BudgetDecision(False, outcome.reason)
        return BudgetDecision(True, reservation_id=outcome.reservation_id)


# Rejected alternative: an AllowAll binding in this module for tests. A
# permissive class importable from production code is one wrong default away
# from live spend; tests define their own (D12 names it test-only).
BUDGET_ADMISSION: Final[BudgetAdmission] = LedgerAdmission()


# ── Run specs: what an `apify` source asks its Actor to do ──────────────────


@dataclass(frozen=True, slots=True)
class ActorRunSpec:
    """The run input and run options for one start of a site's Actor.

    run_input is the wire (camelCase) input and is validated against
    input_model before anything else happens, so an invalid request never
    reaches paid execution (MS2-D-14). Its QueryScope part becomes
    provider_run.query_scope, which the importer checks the OUTPUT echo
    against (D4 hand-off). admission_class applies to FULL runs; a PROBE is
    always DISCOVERY (MS2-D-24).
    """

    input_model: type[CollectorInput]
    run_input: Mapping[str, object]
    memory_mb: int
    timeout_s: int
    admission_class: AdmissionClass = AdmissionClass.WATCH_REFRESH


# None means "no spec in this configuration" and is refused exactly like an
# unregistered site (`no_run_spec`), so a registered factory can stay inert
# until the operator supplies what its input needs.
type RunSpecFactory = Callable[[SourceConfig, RunKind], ActorRunSpec | None]


def synthetic_spec(
    fixture_commit: str,
    *,
    fault_mode: str = synthetic.DEFAULT_FAULT_MODE,
    caps: synthetic.SyntheticCaps = synthetic.DEFAULT_CAPS,
) -> ActorRunSpec:
    """Return the synthetic proof site's spec for one start (MS2-D-42, F5a).

    The admission class is the ActorRunSpec default, so a FULL start is
    admitted as `watch_refresh` and a PROBE as `discovery`, as for any site.
    """
    return ActorRunSpec(
        input_model=SyntheticCollectorInput,
        run_input=synthetic.run_input(fixture_commit, fault_mode=fault_mode, caps=caps),
        memory_mb=synthetic.MEMORY_MB,
        timeout_s=synthetic.TIMEOUT_S,
    )


def synthetic_run_spec(_config: SourceConfig, _run_kind: RunKind) -> ActorRunSpec | None:
    """The synthetic site's RUN_SPECS factory: fault mode `none`, the default caps.

    Returns None while HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT is empty. The
    commit is read per call rather than captured at import, so the pinned
    fixture is always the configured one and never a code constant.
    """
    commit: str = settings.HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT
    if not commit:
        return None
    return synthetic_spec(commit)


# SCOPE: the synthetic proof site only. No merchant source is Actor-backed in
# MS-2 until a source-admission record exists (OQ24, MS2-D-44). A site absent
# here is refused `no_run_spec` before any spend, and so is the synthetic site
# while its factory returns None (synthetic_run_spec). apify_smoke never uses
# this entry: it passes its own per-invocation spec through `run_specs`.
RUN_SPECS: Final[Mapping[str, RunSpecFactory]] = MappingProxyType(
    {synthetic.SITE_KEY: synthetic_run_spec}
)


class StartStatus(StrEnum):
    REFUSED = "refused"  # before budget admission: no row, no spend
    DENIED = "denied"  # budget admission said no: no row, no spend
    STARTED = "started"
    START_FAILED = "start_failed"  # row exists, response never recorded
    MISMATCH_ABORTED = "mismatch_aborted"


class StartRefusal(StrEnum):
    NO_RUN_SPEC = "no_run_spec"
    INVALID_INPUT = "invalid_input"
    UNKNOWN_RETENTION = "unknown_retention"
    BOUNDED_RETENTION_UNENFORCEABLE = "bounded_retention_unenforceable"
    TIMEOUT_EXCEEDS_RETENTION = "timeout_exceeds_retention"
    APIFY_DISABLED = "apify_disabled"
    ACTOR_UNCONFIGURED = "actor_unconfigured"
    CLIENT_UNAVAILABLE = "client_unavailable"
    SCOPE_RUN_OUTSTANDING = "scope_run_outstanding"


# The DENIED reason when _create_run finds its reservation already closed.
RESERVATION_NOT_OPEN: Final = "reservation_not_open"


@dataclass(frozen=True, slots=True)
class StartResult:
    status: StartStatus
    reason: str = ""
    provider_run_id: int | None = None


type ClientFactory = Callable[[], ApifyClient]


def storage_cleanup_deadline(admitted_at: datetime, retention: AdapterRetention) -> datetime:
    """Return the absolute storage deadline for a run admitted at `admitted_at`.

    MS2-D-25: admitted_at + min(STORAGE_CLEANUP_MAX, bounded_ttl / 2). The TTL
    term only binds for a bounded site, which MS2-D-33 refuses an Actor path
    today; it stays so the bound is already right if that denial is lifted.
    """
    horizon = timedelta(seconds=settings.HW_RADAR_APIFY_STORAGE_CLEANUP_MAX)
    if retention.expires_policy is not None:
        expires = retention.expires_policy(admitted_at)
        if expires is not None:
            horizon = min(horizon, (expires - admitted_at) / 2)
    return admitted_at + horizon


def start_mismatches(run: ApifyRun, *, memory_mb: int, timeout_s: int, build_tag: str) -> list[str]:
    """Return why a start response is unacceptable (empty when it is acceptable).

    The options must equal the request (MS2-D-26): an unreported memory or
    timeout is a mismatch too, because the compute bound was reserved from
    exactly those values. restartOnError is judged only when echoed (Apify's
    documented options omit it, so None means "not reported"); an observed
    restart is judged from stats.restartCount. The build must be on the
    contract's major (MS2-D-38); an unreported build number cannot be shown to
    be, so it fails closed.
    """
    found: list[str] = []
    options = run.options
    if options.memory_mbytes != memory_mb:
        found.append(f"memory {options.memory_mbytes} != {memory_mb}")
    if options.timeout_secs != timeout_s:
        found.append(f"timeout {options.timeout_secs} != {timeout_s}")
    if options.build is not None and options.build != build_tag:
        found.append(f"build tag {options.build!r} != {build_tag!r}")
    if options.restart_on_error is True:
        found.append("restartOnError true")
    if run.restart_count:
        found.append(f"restarted {run.restart_count}x")
    if run.build_number is None:
        found.append("build number unreported")
    elif run.build_number.split(".", 1)[0] != CONTRACT_MAJOR:
        found.append(f"build {run.build_number} outside contract line {CONTRACT_MAJOR}.x")
    return found


async def start_provider_run(
    config: SourceConfig,
    *,
    run_kind: RunKind = RunKind.FULL,
    admission: BudgetAdmission | None = None,
    run_specs: Mapping[str, RunSpecFactory] | None = None,
    client_factory: ClientFactory | None = None,
    build_tag: str | None = None,
) -> StartResult:
    """Start one Actor run for an `apify` source; never retries the start request.

    The caller has already passed check_admission (ADR-0017 lifecycle, back-off,
    buckets). `config` must have source_site loaded. Refusals and denials leave
    no row and send nothing. Once the row exists, exactly one start request is
    sent: any failure to get a parsed response (transport loss, non-2xx, bad
    body) is recorded in stage_detail["start_error"] and returns START_FAILED
    with the row still unstarted, because a lost response may still have
    started a billable run; selector 3 treats it as `orphaned_start` at its
    deadline (MS2-D-33). A response that fails start_mismatches rejects the
    import and sends the mismatch abort (module docstring).

    `build_tag` names the Actor build to start, defaulting to
    HW_RADAR_APIFY_ACTOR_BUILD; the scheduler never passes it, so only an
    explicit caller (apify_smoke's `candidate` run, MS2-D-43) starts another
    tag. The same tag is sent and then required of the echoed options, and the
    MS2-D-38 version-line check reads the build number, not the tag, so any
    tag still has to resolve to a build on the contract's major.
    """
    site_key = config.source_site.normalized_name
    specs = RUN_SPECS if run_specs is None else run_specs
    factory = specs.get(site_key)
    spec = None if factory is None else factory(config, run_kind)
    if spec is None:
        return StartResult(StartStatus.REFUSED, StartRefusal.NO_RUN_SPEC)
    try:
        validated = spec.input_model.model_validate(dict(spec.run_input))
    except ValidationError as exc:
        return StartResult(StartStatus.REFUSED, f"{StartRefusal.INVALID_INPUT}: {exc}")
    if validated.site_key != site_key:
        return StartResult(
            StartStatus.REFUSED,
            f"{StartRefusal.INVALID_INPUT}: siteKey {validated.site_key!r} != {site_key!r}",
        )
    try:
        retention = source_retention(site_key)
    except UnknownSourceRetention:
        return StartResult(StartStatus.REFUSED, StartRefusal.UNKNOWN_RETENTION)
    # MS2-D-33: hw-radar's own delete is the only remote expiry, and it can
    # fail; the platform fallback is days against a bounded TTL of hours.
    if retention.expires_policy is not None:
        return StartResult(StartStatus.REFUSED, StartRefusal.BOUNDED_RETENTION_UNENFORCEABLE)
    admitted_at = timezone.now()
    due_at = storage_cleanup_deadline(admitted_at, retention)
    margin = timedelta(seconds=settings.HW_RADAR_APIFY_IMPORT_MARGIN)
    if timedelta(seconds=spec.timeout_s) + margin > due_at - admitted_at:
        return StartResult(StartStatus.REFUSED, StartRefusal.TIMEOUT_EXCEEDS_RETENTION)
    if not settings.HW_RADAR_APIFY_ENABLED:
        return StartResult(StartStatus.REFUSED, StartRefusal.APIFY_DISABLED)
    actor_id: str = settings.HW_RADAR_APIFY_ACTOR_ID
    build: str = settings.HW_RADAR_APIFY_ACTOR_BUILD if build_tag is None else build_tag
    if not actor_id:
        return StartResult(StartStatus.REFUSED, StartRefusal.ACTOR_UNCONFIGURED)
    if run_kind is RunKind.FULL and await sync_to_async(_has_outstanding_full_run)(
        config, validated.collection_scope
    ):
        return StartResult(StartStatus.REFUSED, StartRefusal.SCOPE_RUN_OUTSTANDING)
    # Built before budget admission: a missing token must refuse before E
    # reserves anything, not strand a reservation behind a row that can never start.
    try:
        client = (client_factory or ApifyClient)()
    except ApifyError as exc:
        return StartResult(StartStatus.REFUSED, f"{StartRefusal.CLIENT_UNAVAILABLE}: {exc}")

    async with client:
        admission_class = (
            AdmissionClass.DISCOVERY if run_kind is RunKind.PROBE else spec.admission_class
        )
        request = BudgetRequest(
            site_key=site_key,
            source_site_id=config.source_site.pk,
            scope_key=validated.collection_scope,
            run_kind=run_kind,
            admission_class=admission_class,
            memory_mb=spec.memory_mb,
            timeout_s=spec.timeout_s,
            max_items=validated.max_items,
            max_pages=validated.max_pages,
            max_requests=validated.max_requests,
            max_bytes=validated.max_bytes,
        )
        decision = await (admission or BUDGET_ADMISSION).admit(request)
        if not decision.admitted:
            logger.info("apify start for %s denied: %s", site_key, decision.reason)
            return StartResult(StartStatus.DENIED, decision.reason)

        query_scope = QueryScope.model_validate(
            {name: getattr(validated, name) for name in QueryScope.model_fields}
        ).model_dump(mode="json")
        try:
            row = await sync_to_async(_create_run)(
                {
                    "provider_kind": ProviderKind.APIFY,
                    "source_site": config.source_site,
                    "actor_ref": actor_id,
                    "contract_schema_version": RUN_SCHEMA_VERSION,
                    "query_scope": query_scope,
                    "scope_key": validated.collection_scope,
                    "memory_mb": spec.memory_mb,
                    "timeout_s": spec.timeout_s,
                    "max_items": validated.max_items,
                    "max_pages": validated.max_pages,
                    "admission_class": admission_class,
                    "run_kind": run_kind,
                    "admitted_at": admitted_at,
                    "storage_cleanup_due_at": due_at,
                },
                decision.reservation_id,
            )
        except ReservationNotOpen as exc:
            # Refused before the start request: the reservation was released
            # (or otherwise closed) under this start, so a run started now
            # would charge against nothing in the ledger.
            logger.error("apify start for %s refused: %s", site_key, exc)
            return StartResult(StartStatus.DENIED, RESERVATION_NOT_OPEN)
        try:
            run = await client.start_run(
                actor_id,
                validated.model_dump(mode="json"),
                memory_mbytes=spec.memory_mb,
                timeout_secs=spec.timeout_s,
                build=build,
            )
        except Exception as exc:  # never retried: see the docstring
            await sync_to_async(_record_start_error)(row.pk, exc)
            # MS2-D-48 *Hard-limit refusal*: Apify documents 402 on a run start
            # when the account has exceeded its usage limit (or lacks credits).
            # Classified by the status code alone: the error.type for that
            # cause is undocumented (the docs show only an example), so
            # matching a type string could miss a real refusal. After the
            # commit above (ED-05). The row stays unstarted and its
            # reservation open for selector 3's orphaned_start, unchanged:
            # the rule for a lost response is not relaxed for a 402. A trip
            # lost to a crash here is repaired by the next reserve or tick
            # (ledger.trip_stranded_start_refusals_locked).
            if isinstance(exc, ApifyApiError) and exc.status_code == 402:
                logger.error("apify start for provider_run %s refused with HTTP 402", row.pk)
                await sync_to_async(trip_start_refusal)(row.pk)
                return StartResult(
                    StartStatus.START_FAILED, LatchReason.ACCOUNT_LIMIT_REFUSED, row.pk
                )
            logger.exception("apify start for provider_run %s lost its response", row.pk)
            return StartResult(StartStatus.START_FAILED, type(exc).__name__, row.pk)

        mismatches = start_mismatches(
            run, memory_mb=spec.memory_mb, timeout_s=spec.timeout_s, build_tag=build
        )
        await sync_to_async(_record_start)(row.pk, run, mismatches)
        if not mismatches:
            return StartResult(StartStatus.STARTED, "", row.pk)
        logger.error("apify start %s mismatched its request: %s", row.pk, "; ".join(mismatches))
        # After _record_start committed: the budget lock is never requested
        # under the provider_run lock (ED-05).
        await sync_to_async(trip_latch)(LatchReason.START_OPTION_MISMATCH, provider_run_id=row.pk)
        await sync_to_async(_reject_mismatch)(row.pk)
        try:
            aborted = await client.abort_run(run.id)
        except Exception:  # the D11 overdue unit retries the abort at the deadline
            logger.exception("mismatch abort of provider_run %s failed", row.pk)
        else:
            await sync_to_async(_record_observation)(row.pk, aborted, timezone.now(), poll=False)
        return StartResult(StartStatus.MISMATCH_ABORTED, "; ".join(mismatches), row.pk)


def _has_outstanding_full_run(config: SourceConfig, scope_key: str) -> bool:
    """Return whether a FULL run of this source and scope has an undecided import.

    Two concurrent FULL runs of one scope would pay twice for one sweep and
    race each other's continuity and delist stages; the MS2-D-30/-36 ordering
    guards keep that correct but not cheap, so the second start is refused
    before budget admission, with no ledger row.

    Outstanding means import_state is neither finalized nor rejected, the same
    test as poller.service._has_outstanding_probe, including its fail-closed
    lost-response case: an unstarted row stays outstanding until the overdue
    unit settles it. Storage state is deliberately not consulted: pending
    cleanup of a decided run cannot affect a new import, and its liability is
    already on the ledger. PROBE rows never count here, and FULL rows never
    count against a probe (D12 owns that rule).

    The check-then-start is not locked: it relies on the per-source `poll-{key}`
    job being the only FULL Actor starter (the heartbeat lane never starts an
    Actor run) and on the scheduler's max_instances=1 for that job.
    """
    return (
        ProviderRun.objects.filter(
            source_site=config.source_site, scope_key=scope_key, run_kind=RunKind.FULL
        )
        .exclude(import_state__in=_TERMINAL_IMPORT)
        .exists()
    )


class ReservationNotOpen(RuntimeError):
    """The start's reservation is no longer `reserved` and unattached; nothing was created."""


def _create_run(fields: dict[str, object], reservation_id: int | None) -> ProviderRun:
    """Create the provider_run and attach it to its reservation in one commit.

    One transaction, so a crash can never leave a run whose spend no ledger
    row tracks: selector 2 reconciles only runs that have a reservation, and
    an unattached run would be imported and cleaned up but never settled. A
    crash before this commit leaves the admitted reservation unattached: it
    counts at its estimate (the spend report lists it `no_provider_run`)
    until reconcile.release_unattached_reservations releases it after the
    grace. Lock order (MS2-D-35): the budget lock, then the new provider_run,
    then the reservation row.

    Raises ReservationNotOpen, creating nothing, when the reservation is not
    this start's own open one, e.g. released by that unit because this start
    stalled past the grace. The check is the filtered attach under the budget
    lock, which the release also holds, so the two cannot interleave.
    """
    with transaction.atomic():
        if reservation_id is not None:
            take_budget_lock()
        row = ProviderRun.objects.create(**fields)  # pyright: ignore[reportArgumentType]
        if reservation_id is not None:
            attached = ApifySpendReservation.objects.filter(
                pk=reservation_id,
                status=ReservationStatus.RESERVED,
                provider_run__isnull=True,
            ).update(provider_run=row)
            if attached != 1:
                # Rolls the row back: a run is never started against a
                # reservation that is not this start's own open one.
                raise ReservationNotOpen(f"reservation {reservation_id} is not open and unattached")
    return row


def _record_start_error(provider_run_id: int, exc: Exception) -> None:
    error: dict[str, object] = {"type": type(exc).__name__}
    if isinstance(exc, ApifyApiError):
        error["status_code"] = exc.status_code
        error["error_type"] = exc.error_type
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        row.stage_detail = {**row.stage_detail, "start_error": error}
        row.save(update_fields=["stage_detail"])


def _record_start(provider_run_id: int, run: ApifyRun, mismatches: list[str]) -> None:
    """Record the start response, and a mismatch's counted cleanup attempt, in one commit.

    The attempt is committed here, BEFORE the abort is sent (MS2-D-32 *Per-call
    bound*), so a crash between the two still spends it. The same commit marks
    the mismatch in stage_detail, which the import unit reads before importing:
    a crash before _reject_mismatch then still rejects the import rather than
    reading a run whose compute was never bounded.
    """
    now = timezone.now()
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        row.external_run_id = run.id
        row.import_idempotency_key = f"apify:{run.id}"
        row.build_id = run.build_id
        row.build_number = run.build_number
        row.dataset_id = run.default_dataset_id
        row.kv_store_id = run.default_key_value_store_id
        _apply_run_fields(row, run, now)
        fields = [
            "external_run_id",
            "import_idempotency_key",
            "build_id",
            "build_number",
            "dataset_id",
            "kv_store_id",
            *_RUN_FIELDS,
        ]
        if mismatches:
            row.stage_detail = {**row.stage_detail, "start_mismatch": mismatches}
            row.storage_cleanup_attempts += 1
            fields += ["stage_detail", "storage_cleanup_attempts"]
        row.save(update_fields=fields)


def _reject_mismatch(provider_run_id: int) -> None:
    row = ProviderRun.objects.get(pk=provider_run_id)
    detail = row.stage_detail.get("start_mismatch")
    reasons = [str(item) for item in cast(list[object], detail)] if isinstance(detail, list) else []
    importer._reject(  # pyright: ignore[reportPrivateUsage] - the one MS2-D-22 Reject transaction
        provider_run_id, RejectReason.START_OPTION_MISMATCH, "; ".join(reasons), random.random
    )


_RUN_FIELDS: Final = ["remote_status", "remote_terminal_at", "started_at", "finished_at"]


def _apply_run_fields(row: ProviderRun, run: ApifyRun, observed_at: datetime) -> None:
    """Copy the remote-status observation onto a locked row (MS2-D-23 remote axis).

    A terminal status is never overwritten by a later non-terminal one, and
    remote_terminal_at is set only once: it is an observation fact and never a
    deadline anchor (MS2-D-33).
    """
    if row.remote_status in TERMINAL_RUN_STATUSES and run.status not in TERMINAL_RUN_STATUSES:
        return
    row.remote_status = run.status
    if run.status in TERMINAL_RUN_STATUSES and row.remote_terminal_at is None:
        row.remote_terminal_at = observed_at
    if run.started_at is not None:
        row.started_at = run.started_at
    if run.finished_at is not None:
        row.finished_at = run.finished_at


def _record_observation(
    provider_run_id: int, run: ApifyRun, observed_at: datetime, *, poll: bool = True
) -> None:
    """Record a poll or abort response. Storage ids recorded at start are never replaced.

    The MS2-D-25 *404 rule* trusts a 404 only for the storage id recorded from
    this run's own start or run record, so a later response may fill a missing
    id but never swap one. Only a poll (`poll=True`) stamps usage_read_at,
    which spaces selector 1's polls; the mismatch abort's answer records the
    status alone, so it neither delays the first poll nor stands in for a
    usage read.
    """
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        _apply_run_fields(row, run, observed_at)
        row.dataset_id = row.dataset_id or run.default_dataset_id
        row.kv_store_id = row.kv_store_id or run.default_key_value_store_id
        row.build_id = row.build_id or run.build_id
        row.build_number = row.build_number or run.build_number
        if poll:
            row.usage_total_usd = run.usage_total_usd
            row.usage_read_at = observed_at
        detail = dict(row.stage_detail)
        detail.pop("unit_failures", None)
        if run.restart_count:
            # E trips the overrun latch on this (MS2-D-26); D5 only records it.
            detail["restart_count"] = run.restart_count
            logger.error("provider_run %s: platform restarted the run", provider_run_id)
        row.stage_detail = detail
        row.save(
            update_fields=[
                *_RUN_FIELDS,
                "dataset_id",
                "kv_store_id",
                "build_id",
                "build_number",
                "usage_total_usd",
                "usage_read_at",
                "stage_detail",
            ]
        )
    if run.restart_count:
        # An observed restart is a start-option mismatch (MS2-D-26, ED-20),
        # tripped after the row's transaction committed (ED-05).
        trip_latch(LatchReason.START_OPTION_MISMATCH, provider_run_id=provider_run_id)


# ── The apify-poll job: three selectors over persisted state (MS2-D-23) ─────


def _due(now: datetime) -> Q:
    return Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)


def _poll_spacing(timeout_s: int) -> timedelta:
    # MS2-D-32 *Run polls*: half the poll cap covers the whole run timeout, and
    # the other half is left for the settlement reads after termination.
    return timedelta(seconds=timeout_s / (settings.HW_RADAR_APIFY_MAX_RUN_POLLS / 2))


def select_active(now: datetime) -> list[int]:
    """Selector 1: rows with a set, non-terminal remote status that are due a poll.

    A row with a NULL status has no run id to poll; only selector 3 covers it.
    A row at the poll cap is no longer read (MS2-D-32); its termination, if
    never observed, is handled by selector 3 at the deadline.
    """
    rows = (
        ProviderRun.objects.filter(_due(now), remote_status__isnull=False)
        .exclude(remote_status__in=_TERMINAL_STATUSES)
        .exclude(remote_status="")
        .filter(run_poll_count__lt=settings.HW_RADAR_APIFY_MAX_RUN_POLLS)
        .order_by("pk")
        .values_list("pk", "timeout_s", "usage_read_at")
    )
    return [
        pk
        for pk, timeout_s, last_read in rows
        if last_read is None or last_read + _poll_spacing(timeout_s) <= now
    ]


def _storage_work_left() -> Q:
    """Storage not yet deleted and still worth an attempt.

    A row at the delete-attempt cap (left `delete_failed`, MS2-D-32) or marked
    `orphaned_start` (nothing to abort or delete, MS2-D-33) is excluded:
    re-selecting it would only re-log the same terminal state every tick. Both
    stay visible to reporting through their own columns; their latch trip
    commits with the mark (storage_cleanup), and trip_stranded_latches
    re-detects one that never did, so dropping them here cannot lose it.
    """
    return (
        ~Q(storage_state=StorageState.DELETED)
        & Q(storage_cleanup_attempts__lt=settings.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS)
        & Q(orphaned_start_at__isnull=True)
    )


_UNRECONCILED: Final = [
    ReservationStatus.RESERVED.value,
    ReservationStatus.USAGE_PROVISIONAL.value,
    ReservationStatus.USAGE_FINALIZED.value,
]


def select_outstanding(now: datetime) -> list[int]:
    """Selector 2: terminal rows whose import is unfinished, or finished with work left.

    Work left is storage not yet deleted or (E4, MS2-D-32) a reservation not
    yet reconciled once the import is terminal and storage verified deleted:
    the reconcile unit's settlement reads. Rows backing off (`next_attempt_at`
    in the future) wait; the D10 importer, the storage unit, and the reconcile
    unit write that backoff themselves and rely on this filter.
    """
    return list(
        ProviderRun.objects.filter(_due(now), remote_status__in=_TERMINAL_STATUSES)
        .filter(
            ~Q(import_state__in=_TERMINAL_IMPORT)
            | (Q(import_state__in=_TERMINAL_IMPORT) & _storage_work_left())
            | (
                Q(import_state__in=_TERMINAL_IMPORT)
                & Q(storage_state=StorageState.DELETED)
                & Q(spend_reservation__status__in=_UNRECONCILED)
            )
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def select_overdue(now: datetime) -> list[int]:
    """Selector 3: storage not deleted and past its deadline, whatever the remote status.

    Independent of selector 1 ever observing termination (MS2-D-33): NULL
    (start response never recorded), non-terminal, and terminal rows all qualify.
    """
    return list(
        ProviderRun.objects.filter(_due(now), _storage_work_left(), storage_cleanup_due_at__lte=now)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


type StorageUnit = Callable[[int, ApifyClient], Awaitable[None]]
type Clock = Callable[[], datetime]


@dataclass(slots=True)
class TickReport:
    """Which rows each unit handled this tick, and which failed (then backed off)."""

    polled: list[int] = field(default_factory=list)
    imported: list[int] = field(default_factory=list)
    # Handed to the storage units. A failed delete attempt is recorded and
    # backed off by the unit itself, so it lands here, not in `failed`.
    cleanup: list[int] = field(default_factory=list)
    overdue: list[int] = field(default_factory=list)
    # E4: provider_run ids given a reconcile unit; reservation ids of bound
    # operator builds given a build read; reservation ids given a selector-4 read.
    reconciled: list[int] = field(default_factory=list)
    builds: list[int] = field(default_factory=list)
    monitored: list[int] = field(default_factory=list)
    # The two ledger sweeps that precede the selectors: reservation ids
    # released as unattached, and provider_run ids whose lost latch trip was
    # re-detected and tripped.
    released: list[int] = field(default_factory=list)
    latch_repaired: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    error: str = ""


def _count_poll(provider_run_id: int) -> str | None:
    """Spend one run poll, committed before the GET is sent (MS2-D-32 *Run polls*).

    Returns the run id to poll, or None when the row turned terminal or hit
    the cap since it was selected.
    """
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        if (
            row.external_run_id is None
            or row.remote_status in TERMINAL_RUN_STATUSES
            or row.run_poll_count >= settings.HW_RADAR_APIFY_MAX_RUN_POLLS
        ):
            return None
        row.run_poll_count += 1
        row.save(update_fields=["run_poll_count"])
        return row.external_run_id


def _back_off(provider_run_id: int) -> None:
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        prior = row.stage_detail.get("unit_failures", 0)
        failures = (prior if isinstance(prior, int) else 0) + 1
        delay = min(UNIT_BACKOFF_BASE * 2 ** (failures - 1), UNIT_BACKOFF_MAX)
        row.stage_detail = {**row.stage_detail, "unit_failures": failures}
        row.next_attempt_at = timezone.now() + delay
        row.save(update_fields=["stage_detail", "next_attempt_at"])


def _clear_failures(provider_run_id: int) -> None:
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        if "unit_failures" in row.stage_detail:
            detail = dict(row.stage_detail)
            del detail["unit_failures"]
            row.stage_detail = detail
            row.save(update_fields=["stage_detail"])


async def _poll_unit(
    provider_run_id: int,
    client: ApifyClient,
    ledger: LedgerConfig | None = None,
    clock: Clock | None = None,
) -> None:
    run_id = await sync_to_async(_count_poll)(provider_run_id)
    if run_id is None:
        return
    run = await client.get_run(run_id)
    at = clock() if clock is not None else timezone.now()
    ledger = ledger or await sync_to_async(load_ledger_config)()
    await sync_to_async(_record_observation)(provider_run_id, run, at)
    # A separate, budget-locked transaction: the observation's own row lock is
    # released before the ledger takes the budget lock (MS2-D-35 order).
    await sync_to_async(reconcile.record_run_usage)(provider_run_id, run, read_at=at, config=ledger)


async def _settlement_unit(
    provider_run_id: int, client: ApifyClient, ledger: LedgerConfig, clock: Clock
) -> None:
    """Selector 2's reconcile unit (E4): settle, or make one counted settlement read."""
    run_id = await sync_to_async(reconcile.plan_settlement_read)(provider_run_id, clock(), ledger)
    if run_id is None:
        return
    run = await client.get_run(run_id)
    at = clock()
    await sync_to_async(_record_observation)(provider_run_id, run, at)
    await sync_to_async(reconcile.record_run_usage)(provider_run_id, run, read_at=at, config=ledger)


async def _build_unit(
    reservation_id: int, client: ApifyClient, ledger: LedgerConfig, clock: Clock
) -> None:
    """Selector 2 for an operator build (MS2-D-46): settle, or one counted `GET` build."""
    build_id = await sync_to_async(reconcile.plan_build_read)(reservation_id, clock(), ledger)
    if build_id is None:
        return
    build = await client.get_build(build_id)
    await sync_to_async(reconcile.record_build_usage)(
        reservation_id, build, read_at=clock(), config=ledger
    )


async def _monitoring_unit(
    reservation_id: int, client: ApifyClient, ledger: LedgerConfig, clock: Clock
) -> bool:
    """Selector 4 (MS2-D-23): one counted read, completed in its own locked commit.

    Every outcome completes the read (clears the pending marker); a failure
    closes nothing. A BaseException (process loss) leaves the marker for the
    next process to resolve at its start. Returns whether monitoring closed.
    """
    call = await sync_to_async(reconcile.begin_monitoring_read)(reservation_id, clock(), ledger)
    if call is None:
        return False
    result: reconcile.MonitorResult | None
    try:
        if call.kind == "run":
            record = await client.get_run(call.remote_id)
            result = reconcile.MonitorResult(
                total=record.usage_total_usd,
                usage_usd=record.usage_usd,
                usage=record.usage,
                unparseable=record.unparseable_usage,
                finished_at=record.finished_at,
            )
        else:
            build = await client.get_build(call.remote_id)
            result = reconcile.MonitorResult(
                total=build.usage_total_usd,
                usage_usd=build.usage_usd,
                usage=build.usage,
                unparseable=build.unparseable_usage,
                finished_at=build.finished_at,
            )
    except ApifyResponseTooLargeError:
        result = reconcile.MonitorResult(total=None, over_cap=True)
    except ApifyError as exc:
        logger.warning("selector-4 read for reservation %s failed: %s", reservation_id, exc)
        result = None
    return await sync_to_async(reconcile.complete_monitoring_read)(
        reservation_id, result, clock(), ledger
    )


async def _outstanding_unit(
    provider_run_id: int,
    client: ApifyClient,
    *,
    resolver: ListingResolver,
    evaluator: ListingEvaluator | None,
    cleanup: StorageUnit,
    ledger: LedgerConfig,
    clock: Clock,
) -> str:
    row = await sync_to_async(ProviderRun.objects.get)(pk=provider_run_id)
    if row.import_state in _TERMINAL_IMPORT:
        if row.storage_state == StorageState.DELETED.value:
            await _settlement_unit(provider_run_id, client, ledger, clock)
            return "reconcile"
        await cleanup(provider_run_id, client)
        return "cleanup"
    if row.stage_detail.get("start_mismatch"):
        # The start job normally rejected already; this resumes a crash between
        # recording the mismatch and the rejection. Never import such a run.
        # The trip is idempotent, so a crash before the start job's trip is covered.
        await sync_to_async(trip_latch)(
            LatchReason.START_OPTION_MISMATCH, provider_run_id=provider_run_id
        )
        await sync_to_async(_reject_mismatch)(provider_run_id)
        return "import"
    await import_provider_run(
        provider_run_id,
        client=client,
        actor_name=settings.HW_RADAR_APIFY_ACTOR_NAME,
        resolver=resolver,
        evaluator=evaluator,
    )
    await sync_to_async(_clear_failures)(provider_run_id)
    return "import"


async def apify_poll_tick(
    *,
    resolver: ListingResolver,
    evaluator: ListingEvaluator | None = None,
    client_factory: ClientFactory | None = None,
    cleanup: StorageUnit | None = None,
    overdue: StorageUnit | None = None,
    ledger_config: LedgerConfig | None = None,
    clock: Clock | None = None,
) -> TickReport:
    """Run the four selectors once; each row's unit commits and fails on its own.

    Selector 1 runs first and selector 2 is queried after it, so a run whose
    termination this tick observed is imported in the same tick. A unit that
    raises an Exception is logged and backed off; a BaseException (process
    loss, cancellation) propagates with every earlier unit already committed.
    The client is built only when a selector returns a row, so an idle tick
    needs no token; a failed construction is recorded in `error` and ends the
    tick without backing any row off. `cleanup` and `overdue` default to the
    D11 storage units; tests substitute their own. `ledger_config` defaults to
    the settings, and `clock` (the ledger's time source for selectors 2 and 4
    and every usage read) to timezone.now; tests pass both. A unit whose call
    answers over its byte cap trips `api_response_over_cap` and backs off.
    Pending selector-4 markers left by an earlier process are NOT resolved
    here but once at poller start (poller.service.run): a marker this
    process stamped must stay pending while its read is in flight.

    Before the selectors, two database-only sweeps repair state a process
    loss can strand: storage_cleanup.trip_stranded_latches trips the latch
    for an orphaned or delete_failed row that never tripped it, and
    reconcile.release_unattached_reservations releases runtime reservations
    that never got a provider_run. Either one failing is logged and does not
    stop the selectors: the rows they would repair keep failing closed.
    """
    # Imported here, not at module top: storage_cleanup builds on this module's
    # observation writer, so a top-level import would be circular.
    from hw_radar.acquisition.apify import storage_cleanup

    cleanup_unit = cleanup or storage_cleanup.cleanup_storage_unit
    overdue_unit_fn = overdue or storage_cleanup.overdue_storage_unit
    ledger = ledger_config or await sync_to_async(load_ledger_config)()
    now = clock or timezone.now
    report = TickReport()
    client: ApifyClient | None = None

    async def run_unit(
        pk: int, unit: Callable[[ApifyClient], Awaitable[str]], *, ledger_row: bool = False
    ) -> str | None:
        """Run one unit; `ledger_row` means `pk` is a reservation, not a provider_run."""
        nonlocal client
        if client is None:
            client = (client_factory or ApifyClient)()
        try:
            return await unit(client)
        except ApifyResponseTooLargeError as exc:
            # Valid content never exceeds a cap, so this is a latch trip, not a
            # retry (MS2-D-32 *Response caps*). Outside every transaction here.
            logger.error("apify-poll unit for %s: %s", pk, exc)
            await sync_to_async(trip_latch)(
                LatchReason.API_RESPONSE_OVER_CAP, provider_run_id=None if ledger_row else pk
            )
        except ApifyError as exc:  # the client already scrubs the token from these
            logger.warning("apify-poll unit for %s failed: %s", pk, exc)
        except Exception:
            logger.exception("apify-poll unit for %s failed", pk)
        report.failed.append(pk)
        if not ledger_row:
            await sync_to_async(_back_off)(pk)
        return None

    def poll(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await _poll_unit(pk, c, ledger, now)
            return "poll"

        return unit

    def outstanding(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            return await _outstanding_unit(
                pk,
                c,
                resolver=resolver,
                evaluator=evaluator,
                cleanup=cleanup_unit,
                ledger=ledger,
                clock=now,
            )

        return unit

    def build(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await _build_unit(pk, c, ledger, now)
            return "build"

        return unit

    def monitor(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await _monitoring_unit(pk, c, ledger, now)
            return "monitor"

        return unit

    def overdue_unit(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await overdue_unit_fn(pk, c)
            return "overdue"

        return unit

    # Every tick, not once at start: the conditions arise while the process
    # runs (a crash-restart is only one way in), and both sweeps are one
    # lock-free exists() when there is nothing to do.
    try:
        report.latch_repaired = await sync_to_async(storage_cleanup.trip_stranded_latches)(now())
    except Exception:
        logger.exception("apify-poll: stranded-latch sweep failed")
    try:
        report.released = await sync_to_async(reconcile.release_unattached_reservations)(
            now(), settings.HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S
        )
    except Exception:
        logger.exception("apify-poll: unattached-reservation release failed")

    try:
        for pk in await sync_to_async(select_active)(now()):
            if await run_unit(pk, poll(pk)) is not None:
                report.polled.append(pk)
        for pk in await sync_to_async(select_outstanding)(now()):
            kind = await run_unit(pk, outstanding(pk))
            if kind == "cleanup":
                report.cleanup.append(pk)
            elif kind == "reconcile":
                report.reconciled.append(pk)
            elif kind is not None:
                report.imported.append(pk)
        for pk in await sync_to_async(select_overdue)(now()):
            if await run_unit(pk, overdue_unit(pk)) is not None:
                report.overdue.append(pk)
        for pk in await sync_to_async(reconcile.select_build_settlement)(now()):
            if await run_unit(pk, build(pk), ledger_row=True) is not None:
                report.builds.append(pk)
        for pk in await sync_to_async(reconcile.select_monitoring)(now(), ledger):
            if await run_unit(pk, monitor(pk), ledger_row=True) is not None:
                report.monitored.append(pk)
    except ApifyError as exc:
        # Only client construction raises out of run_unit (e.g. no token).
        report.error = str(exc)
        logger.error("apify-poll tick skipped: %s", exc)
    finally:
        if client is not None:
            await client.aclose()
    return report
