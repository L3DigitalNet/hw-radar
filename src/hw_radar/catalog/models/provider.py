# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
"""Remote collection runs and per-scope sweep continuity (MS-2 Slice D, migration 0021).

- `ProviderRun` is the durable record of one self-owned Apify Actor run
  (MS2-D-13). It is created at admission, before the start request, so the
  absolute storage deadline exists even if the start response is lost
  (MS2-D-33). It carries two independent axes (MS2-D-23): the remote execution
  status as Apify reports it, and the local outstanding work (the staged import,
  MS2-D-22; remote storage cleanup, MS2-D-25) that the poll selectors drive from
  persisted state alone. Its operation counters are incremented and committed
  before each paid call, so a crash mid-call still counts it (MS2-D-32).
- `ScopeSweepContinuity` holds the continuity and absence watermarks of one
  non-NULL collection scope (MS2-D-31, -35, -36). The legacy NULL scope keeps
  its watermarks on the FULL `SourceLaneState` row instead (ops.py), because the
  frozen eBay continuity tests pin that lane-row mechanism (MS2-D-31 rejected
  alternative (b)).

Neither table holds merchant content (only ids, counts, scope keys, and
status), so neither is retention-governed, like `ScraperRun`. Dataset items land
in `RawPayload` under the source's registered retention (MS2-D-25).

Every column a later guard reads as a bound is nullable and starts NULL, which
means "no bound": deployed rows need no backfill (plan D2).

The Apify spend ledger (MS-2 Slice E, migration 0022) lives here too, because
its runtime rows attach to `ProviderRun`:

- `ApifySpendReservation` — one row per admission decision, paid or denied,
  runtime or operator (MS2-D-17, -26, -32, -34, -41, -46, -47).
- `ApifyUsageRead` — append-only usage evidence; reconciliation state lives on
  the reservation and cites these rows (MS2-D-41).
- `ApifyBudgetLatch` — one row per overrun-latch trip and its clear (MS2-D-26).
- `ApifyBudgetCycle` — one row per observed Apify billing cycle with the latest
  account snapshot and the per-cycle account-read counter (MS2-D-40, -32).
- `ApifyCycleDiscovery` — the read counter for account reads made while no
  cycle row covers `now` (MS2-D-32 *Cycle discovery*).
- `ApifyLedgerAuthority` — this environment's paid-admission authority per
  cycle (MS2-D-45).

The schema pins only invariants a single row can prove (state coherence,
write-together pairs, non-negative money). Aggregates, the budget lock, and
write-once rules that need the previous value (`usage_finalized_usd`,
`provider_build_id`, `provider_run.final_charge_op_at`) are the ledger
service's (E3, E4). None of these tables holds merchant content, so none is
retention-governed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models

from hw_radar.catalog.models.market import SourceSite
from hw_radar.catalog.models.ops import (
    ProviderKind,
    RunCompleteness,
    RunKind,
    ScraperRun,
    TruncationReason,
)


class AdmissionClass(models.TextChoices):
    """Budget class a paid operation is admitted under (MS2-D-17, -24, -46).

    A PROBE run is admitted as DISCOVERY, so a paused source cannot spend
    watch-refresh headroom. OPERATOR exists for the Slice E ledger's operator
    reservations, which never have a provider run.
    """

    WATCH_REFRESH = "watch_refresh", "Watch refresh"
    DISCOVERY = "discovery", "Discovery"
    OPERATOR = "operator", "Operator"


class ImportState(models.TextChoices):
    """Durable stage of a provider import (MS2-D-22).

    Moves forward only, by compare-and-set under the row lock:
    PENDING → OBSERVATIONS_COMMITTED → ABSENCE_APPLIED → RESOLVED → EVALUATED →
    FINALIZED. REJECTED is the only other terminal state.
    """

    PENDING = "pending", "Pending"
    OBSERVATIONS_COMMITTED = "observations_committed", "Observations committed"
    ABSENCE_APPLIED = "absence_applied", "Absence applied"
    RESOLVED = "resolved", "Resolved"
    EVALUATED = "evaluated", "Evaluated"
    FINALIZED = "finalized", "Finalized"
    REJECTED = "rejected", "Rejected"


class StorageState(models.TextChoices):
    """Remote default-storage cleanup state (MS2-D-25)."""

    RETAINED = "retained", "Retained"
    DELETED = "deleted", "Deleted"
    DELETE_FAILED = "delete_failed", "Delete failed"


def _choice_values(choices: type[models.TextChoices]) -> list[str]:
    return [str(c.value) for c in choices]


class ProviderRun(models.Model):
    """One Actor run, from admission through import and storage cleanup (MS2-D-13).

    Identity: `(provider_kind, external_run_id)` and `import_idempotency_key`
    (`apify:<run id>`) are each unique once set. Both stay NULL on a row that
    was admitted but whose start response was never recorded, so any number of
    such rows coexist; a CHECK keeps the two set together, because a row with a
    run id but no key would import without idempotency protection.

    `scope_key` is the MS2-D-12 scope the run was admitted for and is never
    NULL or blank (revision 10, ED-03): the contract requires `collectionScope`
    on the input and on every row, so a remote run never sweeps the legacy NULL
    scope. Continuity and delist read this column, never the Actor's own claim
    (MS2-D-31).
    """

    # ── identity ──
    provider_kind = models.CharField(
        max_length=20, choices=ProviderKind.choices, default=ProviderKind.APIFY
    )
    source_site = models.ForeignKey(
        SourceSite, on_delete=models.PROTECT, related_name="provider_runs"
    )
    external_run_id = models.CharField(max_length=100, null=True, blank=True)
    import_idempotency_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    actor_ref = models.CharField(max_length=200, blank=True, default="")
    build_id = models.CharField(max_length=100, null=True, blank=True)
    # Apify reports the build number as a dotted version string ("0.1.23").
    build_number = models.CharField(max_length=50, null=True, blank=True)
    contract_schema_version = models.CharField(max_length=50, blank=True, default="")

    # ── requested scope (fixed at admission) ──
    query_scope: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    scope_key = models.CharField(max_length=100)
    memory_mb = models.PositiveIntegerField()
    timeout_s = models.PositiveIntegerField()
    max_items = models.PositiveIntegerField()
    max_pages = models.PositiveIntegerField()
    admission_class = models.CharField(max_length=20, choices=AdmissionClass.choices)
    run_kind = models.CharField(max_length=20, choices=RunKind.choices, default=RunKind.FULL)

    # ── remote execution (MS2-D-23) ──
    admitted_at = models.DateTimeField()
    # Apify's run status string verbatim (READY, RUNNING, SUCCEEDED, ...); NULL
    # until the start response is recorded. Deliberately neither `choices` nor a
    # CHECK: the vocabulary is Apify's, and a status it adds later must still be
    # recordable by the poller rather than failing the write and stalling the
    # row. The terminal set the importer trusts is
    # acquisition.apify.contract.TERMINAL_RUN_STATUSES.
    remote_status = models.CharField(max_length=20, null=True, blank=True)
    # First time the poller saw a terminal status. An observation fact only: it
    # never anchors a deadline (MS2-D-33 rejected alternative (a)).
    remote_terminal_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # "" until classify_run has run; then a RunCompleteness value.
    completeness = models.CharField(
        max_length=20, choices=RunCompleteness.choices, blank=True, default=""
    )
    completeness_reason = models.TextField(blank=True, default="")
    truncation_reason = models.CharField(
        max_length=20, choices=TruncationReason.choices, blank=True, default=""
    )
    # The run's default-KV `OUTPUT` record (MS2-D-14), stored here and never as a
    # RawItem, so the zero-record parser-rot guard still passes a proven-empty
    # sweep. Holds the record's canonical hw-radar-run/v1 JSON (camelCase wire
    # form) only when it validates against that contract; NULL when the record
    # is absent, not JSON, or not contract-valid, and completeness_reason then
    # says which (missing_output / invalid_output / unknown_schema_version).
    # Storing only validated records keeps this table free of merchant content:
    # it is not retention-governed (MS2-D-13), and a contract-valid OUTPUT holds
    # counts, scope, and errors only (MS2-D-25).
    run_output: models.JSONField[dict[str, object] | None] = models.JSONField(null=True, blank=True)
    # Wider than the Slice E ledger's (10,4) money columns: this is the
    # provider's own figure, stored before E compares or rounds it.
    usage_total_usd = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True)
    usage_read_at = models.DateTimeField(null=True, blank=True)

    # ── local work (MS2-D-22, -23, -25, -32, -33) ──
    dataset_id = models.CharField(max_length=100, null=True, blank=True)
    kv_store_id = models.CharField(max_length=100, null=True, blank=True)
    dataset_item_count = models.PositiveIntegerField(null=True, blank=True)
    import_state = models.CharField(
        max_length=30, choices=ImportState.choices, default=ImportState.PENDING
    )
    import_listing_ids = ArrayField(models.BigIntegerField(), default=list, blank=True)
    # Per-stage error counts (resolver and evaluator failures never block a stage).
    stage_detail: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    import_attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    # Created once by the import claim and reused on every retry (MS2-D-13).
    scraper_run = models.OneToOneField(
        ScraperRun,
        on_delete=models.PROTECT,
        related_name="provider_run",
        null=True,
        blank=True,
    )
    # Operation counters (MS2-D-32): each is incremented and committed BEFORE its
    # call is sent, so a crash mid-call still spends the cap. Counting after the
    # call would let a crash loop re-read a paid dataset without bound.
    dataset_read_count = models.PositiveIntegerField(default=0)
    kv_read_count = models.PositiveIntegerField(default=0)
    run_poll_count = models.PositiveIntegerField(default=0)
    correction_read_count = models.PositiveIntegerField(default=0)
    final_charge_op_at = models.DateTimeField(null=True, blank=True)
    storage_state = models.CharField(
        max_length=20, choices=StorageState.choices, default=StorageState.RETAINED
    )
    # Absolute retention deadline set at admission (MS2-D-25 formula, MS2-D-33).
    # It never moves later; selector 3 acts on it whatever the remote status.
    storage_cleanup_due_at = models.DateTimeField()
    storage_cleanup_attempts = models.PositiveIntegerField(default=0)
    storage_deleted_at = models.DateTimeField(null=True, blank=True)
    # MS2-D-33 `orphaned_start`: set when selector 3 finds the deadline passed on
    # a row whose start response was never recorded. Such a row has no run id
    # and no storage ids, so nothing can be aborted or deleted; admitted spend
    # is untracked, which Slice E's latch trips on and an operator clears by
    # hand (R21). Once set, the selectors stop handing the row to cleanup.
    orphaned_start_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "provider_run"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Postgres treats NULLs as distinct, so starting rows (run id still
            # NULL) never collide here.
            models.UniqueConstraint(
                fields=["provider_kind", "external_run_id"],
                name="provider_run_unique_external_run",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(external_run_id__isnull=True, import_idempotency_key__isnull=True)
                    | models.Q(external_run_id__isnull=False, import_idempotency_key__isnull=False)
                ),
                name="provider_run_idempotency_key_with_run_id",
            ),
            models.CheckConstraint(
                condition=~models.Q(scope_key=""), name="provider_run_scope_key_nonblank"
            ),
            models.CheckConstraint(
                condition=models.Q(provider_kind__in=_choice_values(ProviderKind)),
                name="provider_run_provider_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(run_kind__in=[RunKind.FULL.value, RunKind.PROBE.value]),
                name="provider_run_run_kind_full_or_probe",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    admission_class__in=[
                        AdmissionClass.WATCH_REFRESH.value,
                        AdmissionClass.DISCOVERY.value,
                    ]
                ),
                name="provider_run_admission_class_runtime",
            ),
            models.CheckConstraint(
                condition=models.Q(import_state__in=_choice_values(ImportState)),
                name="provider_run_import_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(storage_state__in=_choice_values(StorageState)),
                name="provider_run_storage_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(completeness__in=["", *_choice_values(RunCompleteness)]),
                name="provider_run_completeness_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(truncation_reason__in=["", *_choice_values(TruncationReason)]),
                name="provider_run_truncation_reason_valid",
            ),
            # The MS2-D-11 evidence rule for a non-local run, which every
            # provider_run is: TRUNCATED must name its cap, and nothing else
            # may carry one.
            models.CheckConstraint(
                condition=(
                    models.Q(completeness=RunCompleteness.TRUNCATED.value)
                    & ~models.Q(truncation_reason="")
                )
                | (
                    ~models.Q(completeness=RunCompleteness.TRUNCATED.value)
                    & models.Q(truncation_reason="")
                ),
                name="provider_run_truncation_reason_coherent",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_kind}:{self.external_run_id or '(not started)'} [{self.scope_key}]"


class ScopeSweepContinuity(models.Model):
    """Continuity and absence watermarks for one non-NULL collection scope.

    One row per `(source_site, collection_scope)` serves local and Actor runs of
    that scope alike, because scope keys are provider-independent (MS2-D-12).
    Gap detection reads this row's own `last_eligible_sweep_at`, never another
    scope's runs (MS2-D-31). All four watermarks are NULL until written, which
    every guard reads as "no bound":

    - continuous_since — start of the scope's current uninterrupted run of
      eligible sweeps; NULL blocks stale absence for the scope.
    - last_eligible_sweep_at — newest eligible sweep folded in; an older one
      arriving late changes nothing (MS2-D-36 record step 2).
    - continuity_broken_at — newest break event time; a sweep at or before it
      changes nothing (MS2-D-36 record step 1).
    - last_complete_sweep_at — newest gated complete FULL sweep; an older
      observation may not revive or create an active row (MS2-D-35).

    The row is created in its own committed transaction before any locking
    transaction touches it (MS2-D-35 *Row creation before locking*), which is
    safe precisely because an all-NULL row decides nothing.
    """

    source_site = models.ForeignKey(
        SourceSite, on_delete=models.PROTECT, related_name="scope_continuity"
    )
    collection_scope = models.CharField(max_length=100)
    continuous_since = models.DateTimeField(null=True, blank=True)
    last_eligible_sweep_at = models.DateTimeField(null=True, blank=True)
    continuity_broken_at = models.DateTimeField(null=True, blank=True)
    last_complete_sweep_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scope_sweep_continuity"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["source_site", "collection_scope"],
                name="scope_sweep_continuity_unique_scope",
            ),
            # The NULL scope's watermarks live on the FULL lane row; a blank key
            # here would be a second copy that the lane-row guards never read.
            models.CheckConstraint(
                condition=~models.Q(collection_scope=""),
                name="scope_sweep_continuity_scope_nonblank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_site_id}:{self.collection_scope}"  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs


# ── Apify spend ledger (MS-2 Slice E, migration 0022) ──


class ReservationStatus(models.TextChoices):
    """Ledger row lifecycle (MS2-D-32 *Settlement*, MS2-D-41 *Usage states*).

    RESERVED → USAGE_PROVISIONAL → USAGE_FINALIZED → RECONCILED; RELEASED is a
    start that never ran, DENIED a refused admission. Every state before
    RECONCILED counts at the full estimate (MS2-D-26 *Invariant*).
    """

    RESERVED = "reserved", "Reserved"
    USAGE_PROVISIONAL = "usage_provisional", "Usage provisional"
    USAGE_FINALIZED = "usage_finalized", "Usage finalized"
    RECONCILED = "reconciled", "Reconciled"
    RELEASED = "released", "Released"
    DENIED = "denied", "Denied"


class SettlementBasis(models.TextChoices):
    """How the settled run usage was reached (MS2-D-41)."""

    STABLE_READS = "stable_reads", "Stable reads"
    BOUND = "bound", "Bound"
    BOUND_UNFINALIZED = "bound_unfinalized", "Bound (unfinalized at deadline)"


class PostRunCostMode(models.TextChoices):
    """`HW_RADAR_APIFY_POST_RUN_COST_MODE` in force at reconciliation (MS2-D-41)."""

    BOUND = "bound", "Bound"
    COUNTED = "counted", "Counted"


class OperatorKind(models.TextChoices):
    """Kind of an `operator` reservation (MS2-D-46)."""

    BUILD = "build", "Build"
    INSPECT = "inspect", "Inspect"
    PROBE = "probe", "Capability probe"


class CycleDiscoveryCloseReason(models.TextChoices):
    """Why an `ApifyCycleDiscovery` row closed (MS2-D-32 *Cycle discovery*)."""

    DISCOVERED = "discovered", "Discovered"
    OWNER_RESET = "owner_reset", "Owner reset"


class LedgerAuthorityKind(models.TextChoices):
    """How this environment came to hold a cycle's authority (MS2-D-45)."""

    ORIGIN = "origin", "Origin (owner attestation)"
    HANDOFF = "handoff", "Handoff (imported record)"
    CONTINUED = "continued", "Continued at rollover"


def _money() -> models.DecimalField[Decimal | None]:
    """Ledger money column: hw-radar's own figures, (10,4) per plan E1."""
    return models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)


def _provider_money() -> models.DecimalField[Decimal | None]:
    # Apify's own figures keep ProviderRun.usage_total_usd's width: they are
    # stored as reported, before the ledger compares or rounds them, so a
    # rounding step can never make a debit smaller than the provider's number.
    return models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True)


def _non_negative(prefix: str, *fields: str) -> list[models.CheckConstraint]:
    return [
        models.CheckConstraint(
            condition=models.Q(**{f"{field}__isnull": True}) | models.Q(**{f"{field}__gte": 0}),
            name=f"{prefix}_{field}_non_negative",
        )
        for field in fields
    ]


_RESERVATION_MONEY = (
    "estimate_usd",
    "actual_usd",
    "execution_bound_usd",
    "post_run_liability_usd",
    "monitoring_bound_usd",
    "usage_provisional_usd",
    "usage_finalized_usd",
    "post_run_cost_usd",
    "settled_run_usage_usd",
)
_RUNTIME_CLASSES = [AdmissionClass.WATCH_REFRESH.value, AdmissionClass.DISCOVERY.value]


class ApifySpendReservation(models.Model):
    """One admission decision in the Apify spend ledger (MS2-D-17, -32, -41, -46).

    Runtime rows (`watch_refresh`, `discovery`) attach to the `ProviderRun`
    they admitted; denials and every `operator` row have no run. A build row is
    identified instead by `provider_build_id`, bound once by `--settle
    --build-id`. An unreconciled row counts at `estimate_usd` in every cycle
    from its admission onward; a reconciled row counts at `actual_usd` in every
    cycle its charge interval `[reserved_at, last_charge_at]` touches, and
    `monitoring_bound_usd` separately by the monitoring interval (MS2-D-34).

    `reserved_at` is provenance only and never changes; `last_charge_at` is the
    window anchor, set at reconciliation.
    """

    # ── identity ──
    provider_run = models.OneToOneField(
        ProviderRun,
        on_delete=models.PROTECT,
        related_name="spend_reservation",
        null=True,
        blank=True,
    )
    source_site = models.ForeignKey(
        SourceSite,
        on_delete=models.PROTECT,
        related_name="spend_reservations",
        null=True,
        blank=True,
    )
    admission_class = models.CharField(max_length=20, choices=AdmissionClass.choices)
    operator_kind = models.CharField(
        max_length=10, choices=OperatorKind.choices, blank=True, default=""
    )
    # Apify's build id for an operator build row; written once by the settle
    # command's binding transaction (MS2-D-46 *Build identity*).
    provider_build_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.RESERVED
    )
    # Free text, not a closed vocabulary: the plan names most reasons
    # (class_cap, account_headroom, overrun_latch, cycle_unknown, ...), but
    # the policy function that emits them is E2's, and a reason it adds must
    # still be recordable rather than failing the denial row it explains.
    denial_reason = models.CharField(max_length=60, blank=True, default="")
    # The operator's `--reason` for an operator row (MS2-D-46), kept with the
    # row so the report and the handoff record can say why the spend happened.
    # On a runtime row it is empty, or the fixed code
    # reconcile.UNATTACHED_RESERVATION once that row is released.
    reason = models.TextField(blank=True, default="")
    # A capability probe's throwaway dataset id, recorded by `--settle` with
    # its verified deletion; a probe cannot settle without it (MS2-D-46).
    probe_dataset_id = models.CharField(max_length=100, null=True, blank=True)

    # ── admission-time bounds (MS2-D-26, -32) ──
    # NULL only on a denial, which may be refused before an estimate exists
    # (pricing_unverified).
    estimate_usd = _money()
    execution_bound_usd = _money()
    post_run_liability_usd = _money()
    # The selector-4 read allowance, held outside the settled amount and debited
    # by its own interval (MS2-D-34 *Monitoring charges*); 0 for inspect/probe.
    monitoring_bound_usd = _money()
    component_bounds: models.JSONField[dict[str, object]] = models.JSONField(
        default=dict, blank=True
    )
    estimator_version = models.CharField(max_length=50, blank=True, default="")
    # Operator envelope limits (MS2-D-46): the inspection envelope's three
    # limits, and the capability probe's call limit.
    envelope_max_items = models.PositiveIntegerField(null=True, blank=True)
    envelope_max_record_reads = models.PositiveIntegerField(null=True, blank=True)
    envelope_max_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    envelope_max_calls = models.PositiveIntegerField(null=True, blank=True)

    # ── usage and settlement (MS2-D-41) ──
    usage_provisional_usd = _money()
    # The first finalized figure, recorded once; later reads are evidence in
    # ApifyUsageRead and raise settled_run_usage_usd instead.
    usage_finalized_usd = _money()
    usage_finalized_at = models.DateTimeField(null=True, blank=True)
    settlement_basis = models.CharField(
        max_length=20, choices=SettlementBasis.choices, blank=True, default=""
    )
    # Raised by upward corrections, never lowered (MS2-D-47). On a build row it
    # is the settled build usage.
    settled_run_usage_usd = _money()
    post_run_cost_usd = _money()
    post_run_cost_mode = models.CharField(
        max_length=10, choices=PostRunCostMode.choices, blank=True, default=""
    )
    actual_usd = _money()

    # ── clocks (MS2-D-34, -39) ──
    reserved_at = models.DateTimeField()
    reconciled_at = models.DateTimeField(null=True, blank=True)
    last_charge_at = models.DateTimeField(null=True, blank=True)

    # ── correction monitoring (MS2-D-23 selector 4, MS2-D-41, -47) ──
    correction_monitor_until = models.DateTimeField(null=True, blank=True)
    next_usage_read_at = models.DateTimeField(null=True, blank=True)
    correction_monitor_closed_at = models.DateTimeField(null=True, blank=True)
    correction_closing_read = models.ForeignKey(
        "ApifyUsageRead",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    # Stamped by the commit that completes a selector-4 read (MS2-D-34).
    monitoring_charge_last_at = models.DateTimeField(null=True, blank=True)
    # Set in the same commit as the counter increment, before the read is sent,
    # and cleared when it completes: while set, the monitoring interval stays
    # open, so a worker suspended past a cycle boundary cannot end the interval
    # before its counted call runs (MS2-D-34, R10-03).
    monitoring_call_pending_since = models.DateTimeField(null=True, blank=True)

    # ── operator build call counters (MS2-D-32, -46) ──
    # A build row has no provider_run, so its GET-build polls and correction
    # reads are counted here, incremented and committed before each call.
    run_poll_count = models.PositiveIntegerField(default=0)
    correction_read_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "apify_spend_reservation"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["reserved_at"], name="apify_resv_reserved_at"),
            models.Index(fields=["status"], name="apify_resv_status"),
            models.Index(fields=["status", "last_charge_at"], name="apify_resv_status_last_charge"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(admission_class__in=_choice_values(AdmissionClass)),
                name="apify_resv_admission_class_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=_choice_values(ReservationStatus)),
                name="apify_resv_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(operator_kind__in=["", *_choice_values(OperatorKind)]),
                name="apify_resv_operator_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(settlement_basis__in=["", *_choice_values(SettlementBasis)]),
                name="apify_resv_settlement_basis_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(post_run_cost_mode__in=["", *_choice_values(PostRunCostMode)]),
                name="apify_resv_post_run_cost_mode_valid",
            ),
            # operator_kind is set exactly on operator rows (MS2-D-46).
            models.CheckConstraint(
                condition=(
                    models.Q(admission_class=AdmissionClass.OPERATOR.value)
                    & ~models.Q(operator_kind="")
                )
                | (
                    ~models.Q(admission_class=AdmissionClass.OPERATOR.value)
                    & models.Q(operator_kind="")
                ),
                name="apify_resv_operator_kind_iff_operator",
            ),
            # Operator rows never have a provider_run, whatever their kind
            # (MS2-D-46, revision 8); a denial admitted nothing to attach.
            models.CheckConstraint(
                condition=models.Q(provider_run__isnull=True)
                | (
                    models.Q(admission_class__in=_RUNTIME_CLASSES)
                    & ~models.Q(status=ReservationStatus.DENIED.value)
                ),
                name="apify_resv_run_only_on_admitted_runtime",
            ),
            # Usage can only have been read from a run the row is attached to;
            # without the run, reconciliation could never check the import and
            # deletion barriers.
            models.CheckConstraint(
                condition=~models.Q(admission_class__in=_RUNTIME_CLASSES)
                | ~models.Q(
                    status__in=[
                        ReservationStatus.USAGE_PROVISIONAL.value,
                        ReservationStatus.USAGE_FINALIZED.value,
                        ReservationStatus.RECONCILED.value,
                    ]
                )
                | models.Q(provider_run__isnull=False),
                name="apify_resv_runtime_usage_needs_run",
            ),
            # Every runtime row names its source, for attribution and for the
            # budget_paused freshness state (MS2-D-17).
            models.CheckConstraint(
                condition=~models.Q(admission_class__in=_RUNTIME_CLASSES)
                | models.Q(source_site__isnull=False),
                name="apify_resv_runtime_has_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ReservationStatus.DENIED.value) & ~models.Q(denial_reason="")
                )
                | (~models.Q(status=ReservationStatus.DENIED.value) & models.Q(denial_reason="")),
                name="apify_resv_denial_reason_iff_denied",
            ),
            # Fail closed: a non-denied row without an estimate or a monitoring
            # allowance would be counted as $0 by every aggregate.
            models.CheckConstraint(
                condition=models.Q(status=ReservationStatus.DENIED.value)
                | models.Q(estimate_usd__isnull=False, monitoring_bound_usd__isnull=False),
                name="apify_resv_admitted_has_bounds",
            ),
            # Inspection and probe envelopes have no provider figure to re-read,
            # so they carry no monitoring allowance (MS2-D-32, -46).
            models.CheckConstraint(
                condition=~models.Q(
                    operator_kind__in=[OperatorKind.INSPECT.value, OperatorKind.PROBE.value]
                )
                | models.Q(monitoring_bound_usd__isnull=True)
                | models.Q(monitoring_bound_usd=0),
                name="apify_resv_envelope_rows_no_monitoring",
            ),
            # Denied rows are exempt: a denial caused by an unset envelope limit
            # must still be recorded (MS2-D-17 *Denials are recorded*), and it
            # carries whatever limits were set. Only an admitted envelope row is
            # priced from, and counted at, its limits.
            models.CheckConstraint(
                condition=(
                    models.Q(status=ReservationStatus.DENIED.value)
                    | models.Q(
                        operator_kind=OperatorKind.INSPECT.value,
                        envelope_max_items__isnull=False,
                        envelope_max_record_reads__isnull=False,
                        envelope_max_bytes__isnull=False,
                        envelope_max_calls__isnull=True,
                    )
                    | models.Q(
                        operator_kind=OperatorKind.PROBE.value,
                        envelope_max_items__isnull=True,
                        envelope_max_record_reads__isnull=True,
                        envelope_max_bytes__isnull=True,
                        envelope_max_calls__isnull=False,
                    )
                    | (
                        ~models.Q(
                            operator_kind__in=[
                                OperatorKind.INSPECT.value,
                                OperatorKind.PROBE.value,
                            ]
                        )
                        & models.Q(
                            envelope_max_items__isnull=True,
                            envelope_max_record_reads__isnull=True,
                            envelope_max_bytes__isnull=True,
                            envelope_max_calls__isnull=True,
                        )
                    )
                ),
                name="apify_resv_envelope_limits_by_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(probe_dataset_id__isnull=True)
                | models.Q(operator_kind=OperatorKind.PROBE.value),
                name="apify_resv_probe_dataset_only_on_probe",
            ),
            # A build id belongs only to a build row, and a build row cannot
            # reconcile unbound: without the id nothing can re-read or monitor
            # the build (MS2-D-46, revision 8).
            models.CheckConstraint(
                condition=models.Q(provider_build_id__isnull=True)
                | models.Q(operator_kind=OperatorKind.BUILD.value),
                name="apify_resv_build_id_only_on_build",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    operator_kind=OperatorKind.BUILD.value,
                    status=ReservationStatus.RECONCILED.value,
                )
                | models.Q(provider_build_id__isnull=False),
                name="apify_resv_reconciled_build_has_build_id",
            ),
            # A reconciled row must carry its settled amount and its charge
            # interval, or MS2-D-34's cycle predicate cannot place it.
            models.CheckConstraint(
                condition=~models.Q(status=ReservationStatus.RECONCILED.value)
                | models.Q(
                    actual_usd__isnull=False,
                    reconciled_at__isnull=False,
                    last_charge_at__isnull=False,
                ),
                name="apify_resv_reconciled_has_settlement",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(usage_finalized_usd__isnull=True, usage_finalized_at__isnull=True)
                    | models.Q(usage_finalized_usd__isnull=False, usage_finalized_at__isnull=False)
                ),
                name="apify_resv_finalized_usage_pair",
            ),
            # Closure commits with its closing read or not at all (MS2-D-47
            # step 5), and only on a row that has a monitoring deadline.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        correction_monitor_closed_at__isnull=True,
                        correction_closing_read__isnull=True,
                    )
                    | models.Q(
                        correction_monitor_closed_at__isnull=False,
                        correction_closing_read__isnull=False,
                        correction_monitor_until__isnull=False,
                    )
                ),
                name="apify_resv_closure_with_closing_read",
            ),
            *_non_negative("apify_resv", *_RESERVATION_MONEY),
        ]

    def __str__(self) -> str:
        kind = f"/{self.operator_kind}" if self.operator_kind else ""
        return f"reservation {self.pk} {self.admission_class}{kind} [{self.status}]"


class UsageReadImmutableError(Exception):
    """An update or delete of an `ApifyUsageRead`, which is append-only evidence."""


class _AppendOnlyQuerySet(models.QuerySet["ApifyUsageRead"]):
    # Queryset-level update()/delete() bypass Model.save/delete, so the
    # append-only rule has to refuse them here as well; otherwise one bulk
    # UPDATE could rewrite the evidence a settlement or handoff cites.
    def update(self, **kwargs: object) -> int:
        raise UsageReadImmutableError("ApifyUsageRead rows are append-only")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise UsageReadImmutableError("ApifyUsageRead rows are append-only")


class ApifyUsageRead(models.Model):
    """One usage read of a run or build, kept as immutable evidence (MS2-D-41).

    Rows are appended and never updated or deleted: settlement cites them, and
    the handoff drain check compares post-reconciliation reads against the
    settled usage (MS2-D-45). A build read has a null `provider_run` and
    identifies its build through `reservation.provider_build_id`.

    Service rules the schema cannot express (E4): `save()` inserts only and
    refuses a read whose `provider_run` is not its reservation's, and the
    manager's queryset refuses `update()` and `delete()`. Raw SQL and a
    TRUNCATE (test teardown) are outside this guard by design.
    """

    objects = _AppendOnlyQuerySet.as_manager()

    provider_run = models.ForeignKey(
        ProviderRun,
        on_delete=models.PROTECT,
        related_name="usage_reads",
        null=True,
        blank=True,
    )
    reservation = models.ForeignKey(
        ApifySpendReservation, on_delete=models.PROTECT, related_name="usage_reads"
    )
    read_at = models.DateTimeField()
    # NULL when the record carried no dollar usage; the read is still evidence.
    usage_total_usd = _provider_money()
    usage_usd: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    finished_at_reported = models.DateTimeField(null=True, blank=True)
    price_settings_version = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        db_table = "apify_usage_read"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["reservation", "read_at"], name="apify_usage_read_resv_time"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *_non_negative("apify_usage_read", "usage_total_usd"),
        ]

    def save(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        if not self._state.adding:
            raise UsageReadImmutableError("ApifyUsageRead rows are append-only")
        # A read must describe the run its reservation settles: a read filed
        # under another run's reservation would settle or correct the wrong
        # spend (both are null for a build read).
        expected = ApifySpendReservation.objects.values_list("provider_run_id", flat=True).get(
            pk=self.reservation_id  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <fk>_id stubs
        )
        if expected != self.provider_run_id:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            raise ValueError(
                f"usage read for provider_run {self.provider_run_id} filed under a "  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                f"reservation of provider_run {expected}"
            )
        super().save(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:  # type: ignore[override]
        raise UsageReadImmutableError("ApifyUsageRead rows are append-only")

    def __str__(self) -> str:
        return f"usage read {self.pk} @ {self.read_at:%Y-%m-%dT%H:%M:%SZ}"


class ApifyBudgetLatch(models.Model):
    """One overrun-latch trip and, once cleared, its clear (MS2-D-26).

    The latch is tripped while any row has a NULL `cleared_at`. A clear comes
    only from the owner's `apify_budget_reset --reason` or an estimator-version
    bump, recorded as `cleared_reason`.
    """

    tripped_at = models.DateTimeField()
    provider_run = models.ForeignKey(
        ProviderRun,
        on_delete=models.PROTECT,
        related_name="budget_latch_trips",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=60)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_reason = models.TextField(blank=True, default="")
    estimator_version = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        db_table = "apify_budget_latch"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["tripped_at"],
                name="apify_latch_open",
                condition=models.Q(cleared_at__isnull=True),
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~models.Q(reason=""), name="apify_latch_reason_nonblank"
            ),
            # An unexplained clear would reopen paid admission with no record
            # of who decided the overrun was resolved.
            models.CheckConstraint(
                condition=models.Q(cleared_at__isnull=True, cleared_reason="")
                | (models.Q(cleared_at__isnull=False) & ~models.Q(cleared_reason="")),
                name="apify_latch_clear_has_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(cleared_at__isnull=True)
                | models.Q(cleared_at__gte=models.F("tripped_at")),
                name="apify_latch_cleared_after_trip",
            ),
        ]

    def __str__(self) -> str:
        state = "open" if self.cleared_at is None else "cleared"
        return f"latch {self.reason} [{state}]"


class ApifyBudgetCycle(models.Model):
    """One observed Apify billing cycle and its latest account snapshot (MS2-D-40).

    Consumed usage, outstanding reservations, and remaining budget are always
    computed from reservation rows under the budget lock and never stored
    here, so they cannot drift from the ledger. The account figures are Apify's
    as last observed; NULL until the read that carries each one.
    """

    cycle_start = models.DateTimeField(unique=True)
    cycle_end = models.DateTimeField()
    allocation_usd = _money()
    account_prepaid_credit_usd = _provider_money()
    account_base_price_usd = _provider_money()
    account_limit_usd = _provider_money()
    account_usage_usd = _provider_money()
    account_observed_at = models.DateTimeField(null=True, blank=True)
    account_data_retention_days = models.PositiveIntegerField(null=True, blank=True)
    # Incremented and committed before each account read in this cycle
    # (MS2-D-32 *Account reads*); the cap's full bound is a standing debit.
    account_read_count = models.PositiveIntegerField(default=0)
    opened_at = models.DateTimeField()

    class Meta:
        db_table = "apify_budget_cycle"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(cycle_end__gt=models.F("cycle_start")),
                name="apify_cycle_end_after_start",
            ),
            *_non_negative(
                "apify_cycle",
                "allocation_usd",
                "account_prepaid_credit_usd",
                "account_base_price_usd",
                "account_limit_usd",
                "account_usage_usd",
            ),
        ]

    def __str__(self) -> str:
        return f"cycle {self.cycle_start:%Y-%m-%d} → {self.cycle_end:%Y-%m-%d}"


class ApifyCycleDiscovery(models.Model):
    """Read counter for account reads made while no cycle row covers `now`.

    An empty ledger or a rollover has no `ApifyBudgetCycle` row to count on,
    so those reads count here (MS2-D-32 *Cycle discovery*). At most one row is
    open; the read that finds a covering cycle closes it with `discovered`, and
    the owner's `apify_budget_reset --discovery` closes an exhausted one with
    `owner_reset`.
    """

    opened_at = models.DateTimeField()
    read_count = models.PositiveIntegerField(default=0)
    last_read_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    cycle_start = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(
        max_length=20, choices=CycleDiscoveryCloseReason.choices, blank=True, default=""
    )
    # The owner's `apify_budget_reset --discovery --reason` text for an
    # `owner_reset` close: a reset is a spend decision, so its why is kept.
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "apify_cycle_discovery"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Every open row has close_reason "" (next CHECK), so uniqueness of
            # that column among open rows admits exactly one open row. A unique
            # index on closed_at alone would not: Postgres treats NULLs as
            # distinct, and open rows are exactly the NULL ones.
            models.UniqueConstraint(
                fields=["close_reason"],
                condition=models.Q(closed_at__isnull=True),
                name="apify_discovery_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    close_reason__in=["", *_choice_values(CycleDiscoveryCloseReason)]
                ),
                name="apify_discovery_close_reason_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(closed_at__isnull=True, close_reason="")
                | (models.Q(closed_at__isnull=False) & ~models.Q(close_reason="")),
                name="apify_discovery_closed_with_reason",
            ),
            # A discovered close records the cycle it found in the same commit.
            models.CheckConstraint(
                condition=~models.Q(close_reason=CycleDiscoveryCloseReason.DISCOVERED.value)
                | models.Q(cycle_start__isnull=False),
                name="apify_discovery_discovered_has_cycle",
            ),
            models.CheckConstraint(
                condition=~models.Q(close_reason=CycleDiscoveryCloseReason.OWNER_RESET.value)
                | ~models.Q(reason=""),
                name="apify_discovery_owner_reset_has_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"cycle discovery {self.pk} [{self.close_reason or 'open'}]"


class ApifyLedgerAuthority(models.Model):
    """This environment's paid-admission authority for one billing cycle (MS2-D-45).

    Paid admission of every class needs the current cycle's row with
    `handed_off_at` NULL. An export marks the row handed off to exactly one
    destination, irrevocably, storing the record and its digest; an import
    creates a `handoff` row keyed by `imported_record_digest`, so re-importing
    the same record is a no-op even after this row is exported onward.
    """

    cycle_start = models.DateTimeField(unique=True)
    kind = models.CharField(max_length=10, choices=LedgerAuthorityKind.choices)
    ledger_id = models.CharField(max_length=100)
    carried_consumption_usd = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal(0)
    )
    attested_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField()
    handed_off_at = models.DateTimeField(null=True, blank=True)
    handed_off_to = models.CharField(max_length=100, null=True, blank=True)
    # The EXPORT side: the digest and the full record this row was handed off
    # with. The record is stored, not rebuilt, so a retried export to the same
    # destination returns byte-identical evidence (MS2-D-45 idempotent retry).
    handoff_record_digest = models.CharField(max_length=128, unique=True, null=True, blank=True)
    handoff_record: models.JSONField[dict[str, object] | None] = models.JSONField(
        null=True, blank=True
    )
    # The IMPORT side: the digest of the record that created a `handoff` row,
    # its idempotency key. Kept apart from handoff_record_digest because a
    # handoff row can itself be exported onward (B imports from A, then hands
    # off to C); one shared column would overwrite the import key at that
    # export, and a re-import of A's record would then be refused instead of
    # being the no-op MS2-D-45 requires.
    imported_record_digest = models.CharField(max_length=128, unique=True, null=True, blank=True)

    class Meta:
        db_table = "apify_ledger_authority"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(kind__in=_choice_values(LedgerAuthorityKind)),
                name="apify_authority_kind_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(ledger_id=""), name="apify_authority_ledger_id_nonblank"
            ),
            # Origin is the one row whose truth is an attestation (R35), so it
            # must say who attested.
            models.CheckConstraint(
                condition=~models.Q(kind=LedgerAuthorityKind.ORIGIN.value)
                | ~models.Q(attested_by=""),
                name="apify_authority_origin_attested",
            ),
            # A handoff row exists only because a record was imported; the
            # imported digest is its idempotency key, and no other kind has one.
            models.CheckConstraint(
                condition=(
                    models.Q(kind=LedgerAuthorityKind.HANDOFF.value)
                    & models.Q(imported_record_digest__isnull=False)
                )
                | (
                    ~models.Q(kind=LedgerAuthorityKind.HANDOFF.value)
                    & models.Q(imported_record_digest__isnull=True)
                ),
                name="apify_authority_handoff_has_digest",
            ),
            # The export binds destination and record together with the
            # hand-off itself; a handed-off row without them could be exported
            # again to a second destination (MS2-D-45 *Exclusive destination*).
            models.CheckConstraint(
                condition=models.Q(
                    handed_off_at__isnull=True,
                    handed_off_to__isnull=True,
                    handoff_record_digest__isnull=True,
                    handoff_record__isnull=True,
                )
                | models.Q(
                    handed_off_at__isnull=False,
                    handed_off_to__isnull=False,
                    handoff_record_digest__isnull=False,
                    handoff_record__isnull=False,
                ),
                name="apify_authority_handoff_binds_destination",
            ),
            *_non_negative("apify_authority", "carried_consumption_usd"),
        ]

    def __str__(self) -> str:
        state = f" → {self.handed_off_to}" if self.handed_off_at else ""
        return f"authority {self.cycle_start:%Y-%m-%d} {self.kind} {self.ledger_id}{state}"
