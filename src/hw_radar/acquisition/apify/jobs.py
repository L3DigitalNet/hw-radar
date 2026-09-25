"""Scheduler surface for self-owned Apify Actor runs (plan D5; MS2-D-16, -23, -26, -33).

Two jobs, both driven by the one APScheduler process (ADR 0012; there are no
Apify schedules or webhooks):

- start_provider_run is the start job. poll_source calls it instead of
  run_source for a source whose collection_provider is `apify`, after the same
  check_admission gate. It refuses, with no spend and no row, anything MS2-D-33
  forbids (a bounded-retention site, a timeout that does not fit the storage
  deadline) and anything the kill switch or budget admission denies; then it
  creates the provider_run row with `admitted_at` and the absolute storage
  deadline BEFORE the start request, sends exactly one start request, records
  the response, and verifies it (MS2-D-26 options check, MS2-D-38 version line).
- apify_poll_tick is one tick of the `apify-poll` interval job. It runs the
  three MS2-D-23 selectors over persisted state only, so a poller restart
  resumes every row: selector 1 polls active remote runs, selector 2 drives
  terminal rows with outstanding local work (the D10 importer, then storage
  cleanup), and selector 3 hands rows past their storage deadline to the
  overdue unit whatever their remote status. Each unit of work is claimed and
  committed on its own, with per-row backoff through `next_attempt_at`, so one
  failing row never stalls the others.

Production safety. The budget admission binding is DenyAllAdmission until
Slice E wires the ledger (E5), so no start request can be sent: DenyAll is the
module default and nothing in production passes another binding. The kill
switch settings.HW_RADAR_APIFY_ENABLED (default false) is checked before budget
admission as well, and an unset HW_RADAR_APIFY_ACTOR_ID refuses every start. No
site has an ActorRunSpec registered in RUN_SPECS, so even an `apify` source
with everything else allowed is refused `no_run_spec`. The poll tick never
starts a run; with no provider_run rows it makes no API call and constructs no
client, so it is safe to schedule with no token rendered.

SCOPE: storage deletion and the overdue abort-and-delete sequence are D11's
units of work (MS2-D-25, -33); this module owns their selectors and binds each
to a StorageUnit hook whose default only logs. The one abort sent here is the
start-option-mismatch abort, which MS2-D-32 makes attempt 1 of the overdue
sequence: it is counted in storage_cleanup_attempts and committed before it is
sent, and it deletes nothing (deletion needs terminal evidence, which the D11
unit establishes). The ledger, the overrun latch (a mismatch, an observed
restart, and an orphaned start all trip it), reconciliation, and selector 4 are
Slice E. Recovery-probe dispatch by provider is D12.

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

from hw_radar.acquisition.apify import importer
from hw_radar.acquisition.apify.client import ApifyApiError, ApifyClient, ApifyError, ApifyRun
from hw_radar.acquisition.apify.contract import (
    INPUT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    TERMINAL_RUN_STATUSES,
    CollectorInput,
    QueryScope,
)
from hw_radar.acquisition.apify.importer import RejectReason, import_provider_run
from hw_radar.acquisition.contracts import AdapterRetention, ListingResolver
from hw_radar.acquisition.retention_policy import UnknownSourceRetention, source_retention
from hw_radar.catalog.models import ProviderKind, ProviderRun, RunKind, SourceConfig
from hw_radar.catalog.models.provider import AdmissionClass, ImportState, StorageState
from hw_radar.eligibility import ListingEvaluator

logger = logging.getLogger(__name__)

APIFY_POLL_SECONDS: Final = 60

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
    """What a start asks budget admission to reserve for (MS2-D-17, -26)."""

    site_key: str
    scope_key: str
    run_kind: RunKind
    admission_class: AdmissionClass
    memory_mb: int
    timeout_s: int
    max_items: int
    max_pages: int


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    admitted: bool
    reason: str = ""


class BudgetAdmission(Protocol):
    """Paid-admission check run after check_admission and before any row exists.

    Synchronous: the Slice E ledger takes a Postgres advisory lock inside a
    transaction, so the start job calls it through sync_to_async.
    """

    def admit(self, request: BudgetRequest) -> BudgetDecision: ...


class DenyAllAdmission:
    """The production binding until E5: every paid start is denied."""

    reason: Final = "budget_admission_unavailable"

    def admit(self, request: BudgetRequest) -> BudgetDecision:
        return BudgetDecision(False, self.reason)


# Rejected alternative: an AllowAll binding in this module for tests. A
# permissive class importable from production code is one wrong default away
# from live spend; tests define their own (D12 names it test-only).
BUDGET_ADMISSION: Final[BudgetAdmission] = DenyAllAdmission()


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


type RunSpecFactory = Callable[[SourceConfig, RunKind], ActorRunSpec]

# SCOPE: empty on purpose. No production source is Actor-backed in MS-2 until a
# source-admission record exists (OQ24, MS2-D-44); F5a registers the synthetic
# site's spec. A site absent here is refused `no_run_spec` before any spend.
RUN_SPECS: Final[Mapping[str, RunSpecFactory]] = MappingProxyType({})


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
    """
    site_key = config.source_site.normalized_name
    specs = RUN_SPECS if run_specs is None else run_specs
    factory = specs.get(site_key)
    if factory is None:
        return StartResult(StartStatus.REFUSED, StartRefusal.NO_RUN_SPEC)
    spec = factory(config, run_kind)
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
    build_tag: str = settings.HW_RADAR_APIFY_ACTOR_BUILD
    if not actor_id:
        return StartResult(StartStatus.REFUSED, StartRefusal.ACTOR_UNCONFIGURED)
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
            scope_key=validated.collection_scope,
            run_kind=run_kind,
            admission_class=admission_class,
            memory_mb=spec.memory_mb,
            timeout_s=spec.timeout_s,
            max_items=validated.max_items,
            max_pages=validated.max_pages,
        )
        decision = await sync_to_async((admission or BUDGET_ADMISSION).admit)(request)
        if not decision.admitted:
            logger.info("apify start for %s denied: %s", site_key, decision.reason)
            return StartResult(StartStatus.DENIED, decision.reason)

        query_scope = QueryScope.model_validate(
            {name: getattr(validated, name) for name in QueryScope.model_fields}
        ).model_dump(mode="json")
        row = await sync_to_async(ProviderRun.objects.create)(
            provider_kind=ProviderKind.APIFY,
            source_site=config.source_site,
            actor_ref=actor_id,
            contract_schema_version=RUN_SCHEMA_VERSION,
            query_scope=query_scope,
            scope_key=validated.collection_scope,
            memory_mb=spec.memory_mb,
            timeout_s=spec.timeout_s,
            max_items=validated.max_items,
            max_pages=validated.max_pages,
            admission_class=admission_class,
            run_kind=run_kind,
            admitted_at=admitted_at,
            storage_cleanup_due_at=due_at,
        )
        try:
            run = await client.start_run(
                actor_id,
                validated.model_dump(mode="json"),
                memory_mbytes=spec.memory_mb,
                timeout_secs=spec.timeout_s,
                build=build_tag,
            )
        except Exception as exc:  # never retried: see the docstring
            await sync_to_async(_record_start_error)(row.pk, exc)
            logger.exception("apify start for provider_run %s lost its response", row.pk)
            return StartResult(StartStatus.START_FAILED, type(exc).__name__, row.pk)

        mismatches = start_mismatches(
            run, memory_mb=spec.memory_mb, timeout_s=spec.timeout_s, build_tag=build_tag
        )
        await sync_to_async(_record_start)(row.pk, run, mismatches)
        if not mismatches:
            return StartResult(StartStatus.STARTED, "", row.pk)
        logger.error("apify start %s mismatched its request: %s", row.pk, "; ".join(mismatches))
        await sync_to_async(_reject_mismatch)(row.pk)
        try:
            aborted = await client.abort_run(run.id)
        except Exception:  # the D11 overdue unit retries the abort at the deadline
            logger.exception("mismatch abort of provider_run %s failed", row.pk)
        else:
            await sync_to_async(_record_observation)(row.pk, aborted, timezone.now(), poll=False)
        return StartResult(StartStatus.MISMATCH_ABORTED, "; ".join(mismatches), row.pk)


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


def select_outstanding(now: datetime) -> list[int]:
    """Selector 2: terminal rows whose import is unfinished, or finished with storage left.

    Rows backing off (`next_attempt_at` in the future) wait; the D10 importer
    writes that backoff itself on retry exhaustion and relies on this filter.
    (E adds the unreconciled-reservation clause.)
    """
    return list(
        ProviderRun.objects.filter(_due(now), remote_status__in=_TERMINAL_STATUSES)
        .filter(
            ~Q(import_state__in=_TERMINAL_IMPORT)
            | (Q(import_state__in=_TERMINAL_IMPORT) & ~Q(storage_state=StorageState.DELETED))
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
        ProviderRun.objects.filter(_due(now), storage_cleanup_due_at__lte=now)
        .exclude(storage_state=StorageState.DELETED)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


type StorageUnit = Callable[[int, ApifyClient], Awaitable[None]]


async def pending_storage_unit(provider_run_id: int, client: ApifyClient) -> None:
    """Default binding for the D11 cleanup and overdue units: select, log, do nothing."""
    logger.debug("provider_run %s awaits storage cleanup (D11)", provider_run_id)


@dataclass(slots=True)
class TickReport:
    """Which rows each unit handled this tick, and which failed (then backed off)."""

    polled: list[int] = field(default_factory=list)
    imported: list[int] = field(default_factory=list)
    # Handed to the cleanup unit, which is a logging no-op until D11 binds one.
    cleanup: list[int] = field(default_factory=list)
    overdue: list[int] = field(default_factory=list)
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


async def _poll_unit(provider_run_id: int, client: ApifyClient) -> None:
    run_id = await sync_to_async(_count_poll)(provider_run_id)
    if run_id is None:
        return
    run = await client.get_run(run_id)
    await sync_to_async(_record_observation)(provider_run_id, run, timezone.now())


async def _outstanding_unit(
    provider_run_id: int,
    client: ApifyClient,
    *,
    resolver: ListingResolver,
    evaluator: ListingEvaluator | None,
    cleanup: StorageUnit,
) -> str:
    row = await sync_to_async(ProviderRun.objects.get)(pk=provider_run_id)
    if row.import_state in _TERMINAL_IMPORT:
        await cleanup(provider_run_id, client)
        return "cleanup"
    if row.stage_detail.get("start_mismatch"):
        # The start job normally rejected already; this resumes a crash between
        # recording the mismatch and the rejection. Never import such a run.
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
    cleanup: StorageUnit = pending_storage_unit,
    overdue: StorageUnit = pending_storage_unit,
) -> TickReport:
    """Run the three selectors once; each row's unit commits and fails on its own.

    Selector 1 runs first and selector 2 is queried after it, so a run whose
    termination this tick observed is imported in the same tick. A unit that
    raises an Exception is logged and backed off; a BaseException (process
    loss, cancellation) propagates with every earlier unit already committed.
    The client is built only when a selector returns a row, so an idle tick
    needs no token; a failed construction is recorded in `error` and ends the
    tick without backing any row off.
    """
    report = TickReport()
    client: ApifyClient | None = None

    async def run_unit(pk: int, unit: Callable[[ApifyClient], Awaitable[str]]) -> str | None:
        nonlocal client
        if client is None:
            client = (client_factory or ApifyClient)()
        try:
            return await unit(client)
        except ApifyError as exc:  # the client already scrubs the token from these
            logger.warning("apify-poll unit for provider_run %s failed: %s", pk, exc)
        except Exception:
            logger.exception("apify-poll unit for provider_run %s failed", pk)
        report.failed.append(pk)
        await sync_to_async(_back_off)(pk)
        return None

    def poll(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await _poll_unit(pk, c)
            return "poll"

        return unit

    def outstanding(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            return await _outstanding_unit(
                pk, c, resolver=resolver, evaluator=evaluator, cleanup=cleanup
            )

        return unit

    def overdue_unit(pk: int) -> Callable[[ApifyClient], Awaitable[str]]:
        async def unit(c: ApifyClient) -> str:
            await overdue(pk, c)
            return "overdue"

        return unit

    try:
        for pk in await sync_to_async(select_active)(timezone.now()):
            if await run_unit(pk, poll(pk)) is not None:
                report.polled.append(pk)
        for pk in await sync_to_async(select_outstanding)(timezone.now()):
            kind = await run_unit(pk, outstanding(pk))
            if kind == "cleanup":
                report.cleanup.append(pk)
            elif kind is not None:
                report.imported.append(pk)
        for pk in await sync_to_async(select_overdue)(timezone.now()):
            if await run_unit(pk, overdue_unit(pk)) is not None:
                report.overdue.append(pk)
    except ApifyError as exc:
        # Only client construction raises out of run_unit (e.g. no token).
        report.error = str(exc)
        logger.error("apify-poll tick skipped: %s", exc)
    finally:
        if client is not None:
            await client.aclose()
    return report
