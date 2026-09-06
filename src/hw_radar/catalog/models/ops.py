# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
"""Scraper-ops grain (spec §9 "scraper-ops" group): source registry + run records.

SourceConfig is the C.2 per-source scheduling registry as settings rows
(ADR-0016 pattern): every number here is an OQ9-provisional tunable, changed
by UPDATE, not deploy. It holds identity, policy and source-wide health only —
the mutable per-lane cadence state lives in SourceLaneState (ADR-0020).
ScraperRun is the §18.5 observability substrate. FxRateDaily is the ADR-0008
daily-rate cache. SchedulerCheckpoint persists in-memory admission state for
ERR-007 crash recovery.
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from hw_radar.catalog.models.base import TimeStamped
from hw_radar.catalog.models.market import SourceSite


class SourceTier(models.TextChoices):
    T0_OFFICIAL_API = "t0", "T0 — official API"
    T1_MANUFACTURER = "t1", "T1 — manufacturer-direct"
    T2_SPECIALIST = "t2", "T2 — specialist/VAR"
    T3_ANTI_BOT = "t3", "T3 — anti-bot-exposed"
    T4_REFURB_REGIONAL = "t4", "T4 — refurb/regional"


class VolatilityProfile(models.TextChoices):
    DROP_PRONE = "drop_prone", "Drop-prone"
    CHURNING = "churning", "Churning"
    STABLE = "stable", "Stable"


class CheapSignal(models.TextChoices):
    SHOPIFY_PRODUCTS_JSON = "shopify_products_json", "Shopify /products.json"
    WOOCOMMERCE_STORE_API = "woocommerce_store_api", "WooCommerce Store API"
    EBAY_BROWSE = "ebay_browse", "eBay Browse API"
    OCC_JSON = "occ_json", "SAP Commerce OCC JSON"
    BOOTSTRAP_JSON = "bootstrap_json", "Category-page bootstrap JSON"
    NONE = "none", "None"


class LifecycleState(models.TextChoices):
    """ADR-0017 source lifecycle. SKIP is terminal pending human re-review."""

    ACTIVE = "active", "Active"
    BACKING_OFF = "backing_off", "Backing off"
    PAUSED_PENDING_FIX = "paused_pending_fix", "Paused pending fix"
    SKIP = "skip", "Skip (permanent)"


class SchedulingLane(models.TextChoices):
    """The two independently-scheduled paths of one source (ADR-0020).

    Values mirror the RunKind vocabulary on purpose: a RunKind.FULL or
    RunKind.PROBE run belongs to FULL (a probe replays the full pipeline),
    a RunKind.HEARTBEAT run belongs to HEARTBEAT. RunKind.REFERENCE has no
    SourceConfig and therefore no lane.
    """

    FULL = "full", "Full-pipeline lane"
    HEARTBEAT = "heartbeat", "Availability-heartbeat lane"


class RunKind(models.TextChoices):
    FULL = "full", "Full pipeline"
    HEARTBEAT = "heartbeat", "Availability heartbeat"
    REFERENCE = "reference", "Reference-catalog ingest"
    PROBE = "probe", "Recovery probe"


class RunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class RunFailureClass(models.TextChoices):
    """§12.1 classification tree, evaluated in this order."""

    TRANSIENT = "transient", "Transient"
    ANTI_BOT = "anti_bot", "Anti-bot / soft block"
    PARSER_ROT = "parser_rot", "Parser rot"
    DEGRADATION = "degradation", "Degradation"
    UNKNOWN = "unknown", "Unknown (escalate)"


class SourceConfig(TimeStamped):
    source_site = models.OneToOneField(SourceSite, on_delete=models.PROTECT, related_name="config")
    enabled = models.BooleanField(default=False)
    tier = models.CharField(max_length=2, choices=SourceTier.choices)
    domain = models.CharField(max_length=255)
    cadence_baseline_s = models.PositiveIntegerField()
    cadence_ceiling_s = models.PositiveIntegerField()
    volatility_profile = models.CharField(
        max_length=20, choices=VolatilityProfile.choices, default=VolatilityProfile.STABLE
    )
    cheap_signal = models.CharField(
        max_length=30, choices=CheapSignal.choices, default=CheapSignal.NONE
    )
    # heartbeat_enabled = "a cheap probe gates the full pipeline" (any verified signal);
    # fast_lane = FR-002's drop-prone AND cheap-signal intersection ONLY. Separate on
    # purpose: ServerPartDeals is heartbeat-enabled at T2 cadence but never fast-laned.
    heartbeat_enabled = models.BooleanField(default=False)
    fast_lane = models.BooleanField(default=False)
    lifecycle_state = models.CharField(
        max_length=20, choices=LifecycleState.choices, default=LifecycleState.ACTIVE
    )
    # Source-wide health axis (ADR-0020): both lanes poll the same site, so a
    # failure anywhere is evidence about the SOURCE. These counters and the
    # lifecycle stay shared; only cadence state is per-lane. Splitting them too
    # would let each lane fail just under the escalation threshold forever.
    consecutive_failures = models.PositiveIntegerField(default=0)
    consecutive_parser_rot = models.PositiveIntegerField(default=0)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    bucket_rate_per_min = models.FloatField(default=6.0)
    bucket_burst = models.PositiveIntegerField(default=3)
    misfire_grace_s = models.PositiveIntegerField(default=60)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "source_config"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(cadence_ceiling_s__lte=models.F("cadence_baseline_s")),
                name="source_config_ceiling_lte_baseline",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(fast_lane=False)
                    | (
                        models.Q(volatility_profile=VolatilityProfile.DROP_PRONE)
                        & ~models.Q(cheap_signal=CheapSignal.NONE)
                    )
                ),
                name="source_config_fast_lane_eligible",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_site.normalized_name} [{self.tier}]"

    def lane_state(self, lane: SchedulingLane) -> SourceLaneState:
        """Return this source's scheduling state for `lane`, creating it if absent.

        Creating on read is deliberate: flipping heartbeat_enabled on a live
        source must not require a migration to materialize its second lane. A
        fresh row starts at cadence_baseline_s — the slowest sanctioned cadence,
        which auto-ramp then earns down.
        """
        state, _created = SourceLaneState.objects.get_or_create(
            source_config=self,
            lane=lane,
            defaults={"current_interval_s": self.cadence_baseline_s},
        )
        return state


class SourceLaneState(TimeStamped):
    """Mutable scheduling state for ONE lane of one source (ADR-0020).

    The fast (heartbeat) and slow (full-pipeline) lanes of a heartbeat-enabled
    source are separate APScheduler jobs with separate cadences, so they need
    separate ramp/back-off state: with a single shared row, a slow-lane repair
    crawl failing reset the fast lane's earned interval and imposed its back-off
    window on the heartbeat, and vice versa. Rows exist for both lanes of every
    source; the heartbeat row of a non-heartbeat source is inert (never read,
    never mutated) and becomes live the moment heartbeat_enabled is flipped on.

    Policy (cadence_baseline_s, cadence_ceiling_s) stays on SourceConfig and is
    shared by both lanes; only the state that a run outcome mutates lives here.
    """

    source_config = models.ForeignKey(
        SourceConfig, on_delete=models.CASCADE, related_name="lane_states"
    )
    lane = models.CharField(max_length=20, choices=SchedulingLane.choices)
    current_interval_s = models.PositiveIntegerField()
    clean_polls = models.PositiveIntegerField(default=0)
    backoff_until = models.DateTimeField(null=True, blank=True)
    # Start of this lane's current UNINTERRUPTED run of successful full sweeps
    # (ADR-0020 amendment 2026-08-30), maintained by the pipeline's delist stage.
    # It is the polling-time clock the CR-004 absence grace is measured against:
    # a listing may only be marked ABSENT_STALE once the lane has actually been
    # polling for the whole grace window, so a downtime, back-off or deploy pause
    # longer than the cadence tolerance cannot mass-delist a source. NULL means
    # "no continuous run established yet" and blocks stale-absence delisting
    # outright. Never read by the scheduler: it carries evidence, not cadence.
    continuous_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "source_lane_state"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["source_config", "lane"], name="source_lane_state_unique_lane"
            )
        ]


class ScraperRun(models.Model):
    source_site = models.ForeignKey(SourceSite, on_delete=models.PROTECT, related_name="runs")
    run_kind = models.CharField(max_length=20, choices=RunKind.choices, default=RunKind.FULL)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.RUNNING)
    failure_class = models.CharField(
        max_length=20, choices=RunFailureClass.choices, blank=True, default=""
    )
    records_fetched = models.PositiveIntegerField(default=0)
    records_valid = models.PositiveIntegerField(default=0)
    listings_upserted = models.PositiveIntegerField(default=0)
    snapshots_appended = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    detail_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "scraper_runs"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["source_site", "-started_at"], name="scraper_runs_site_started")
        ]


class FxRateDaily(models.Model):
    """One row per (date, base→quote); ADR-0008 daily Frankfurter rate cache."""

    rate_date = models.DateField()
    base = models.CharField(max_length=3)
    quote = models.CharField(max_length=3, default="USD")
    rate = models.DecimalField(max_digits=12, decimal_places=6)
    source = models.CharField(max_length=50, default="frankfurter")
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fx_rate_daily"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["rate_date", "base", "quote"], name="fx_rate_daily_unique_pair_date"
            )
        ]


class SchedulerCheckpoint(TimeStamped):
    """ERR-007: in-memory admission state (token buckets) checkpointed for restart."""

    key = models.CharField(max_length=50, unique=True)
    state_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "scheduler_checkpoint"


class RefdataConfig(TimeStamped):
    """ADR-0016 settings row for ADR-0018 reference ingest: the C.3.4 discovery
    occurrence threshold and the refresh kill-switch, changed by UPDATE not
    deploy. Single row (pk=1) via current(); refdata.refresh stamps the last
    run's report here (no ScraperRun row — reference ingest has no SourceSite)."""

    enabled = models.BooleanField(default=True)
    discovery_occurrence_threshold = models.PositiveIntegerField(default=3)
    last_refresh_at = models.DateTimeField(null=True, blank=True)
    last_report_json: models.JSONField[dict[str, object]] = models.JSONField(
        default=dict, blank=True
    )

    class Meta:
        db_table = "refdata_config"

    @classmethod
    def current(cls) -> RefdataConfig:
        row, _ = cls.objects.get_or_create(pk=1)
        return row
