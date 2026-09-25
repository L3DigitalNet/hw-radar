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
"""

from __future__ import annotations

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
