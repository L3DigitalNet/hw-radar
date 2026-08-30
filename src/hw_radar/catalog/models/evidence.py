# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
# See identity.py header: future annotations keep JSONField[...] a resolvable
# string for the type-checker without subscripting the class at runtime.
from __future__ import annotations

from typing import ClassVar

from django.db import models

from hw_radar.catalog.models.base import (
    RetentionGoverned,
    retention_constraints,
    retention_indexes,
)
from hw_radar.catalog.models.identity import DriveUnit
from hw_radar.catalog.models.market import SourceSite


class RawPayload(RetentionGoverned):
    """Cold raw evidence kept re-parseable when source terms allow it."""

    provider = models.CharField(max_length=50)
    endpoint = models.CharField(max_length=255)
    fetched_at = models.DateTimeField()
    request_json: models.JSONField[dict[str, object] | None] = models.JSONField(
        null=True, blank=True
    )
    response_json: models.JSONField[dict[str, object] | None] = models.JSONField(
        null=True, blank=True
    )
    response_text = models.TextField(null=True, blank=True)
    content_hash = models.CharField(max_length=64)
    http_status = models.PositiveSmallIntegerField()
    parse_version = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "raw_payload"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["content_hash"], name="raw_payload_content_hash"),
            *retention_indexes("raw_payload_expires"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [*retention_constraints("raw_payload")]


class SearchObservation(RetentionGoverned):
    """IR-006-minimal discovery evidence: discovered URL plus our query metadata only."""

    provider = models.CharField(max_length=50)
    query_text = models.TextField()
    query_params_json: models.JSONField[dict[str, object]] = models.JSONField(
        default=dict, blank=True
    )
    observed_at = models.DateTimeField()
    result_rank = models.PositiveIntegerField(null=True, blank=True)
    result_url = models.URLField(max_length=1000)
    matched_listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.SET_NULL,
        related_name="search_observations",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "search_observation"
        indexes: ClassVar[list[models.Index]] = [*retention_indexes("search_obs_expires")]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("search_observation")
        ]


class VerificationEvent(RetentionGoverned):
    """Warranty-lookup cache for a physical drive unit."""

    drive_unit = models.ForeignKey(
        DriveUnit, on_delete=models.CASCADE, related_name="verifications"
    )
    provider = models.CharField(max_length=50)
    checked_at = models.DateTimeField()
    result_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "verification_event"
        indexes: ClassVar[list[models.Index]] = [*retention_indexes("verif_event_expires")]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("verification_event")
        ]


class HeartbeatDecision(models.TextChoices):
    """ADR-0015 heartbeat decision outcomes."""

    UNCHANGED = "unchanged", "Unchanged"
    TRANSITION_DETECTED = "transition_detected", "Transition detected"
    AMBIGUOUS = "ambiguous", "Ambiguous"
    FAILED = "failed", "Failed"


class AvailabilityHeartbeatObservation(RetentionGoverned):
    """ADR-0015 cheap variant/SKU-grain availability poll (hypertable, 30d raw).

    One row per probe reading. `unchanged` rows stay here and never touch
    offer_snapshot; non-`unchanged` rows are ALSO written to
    AvailabilityHeartbeatEvent (365d) by the poller (OQ17 split-table pattern —
    chunk-granular retention cannot keep two classes at two TTLs in one table)."""

    # CR-001: TimescaleDB requires every unique index — including the PK — to
    # contain the partitioning column (observed_at). Django's default `id` PK
    # would make create_hypertable() FAIL. Mirror OfferSnapshot's
    # CompositePrimaryKey (market.py:137). (source_site_id, source_sku,
    # observed_at) is unique per probe — heartbeats are written serially per source.
    pk = models.CompositePrimaryKey("source_site_id", "source_sku", "observed_at")
    source_site = models.ForeignKey(SourceSite, on_delete=models.PROTECT, related_name="heartbeats")
    source_sku = models.CharField(max_length=255)  # the source's variant/SKU key
    observed_at = models.DateTimeField()
    decision = models.CharField(max_length=20, choices=HeartbeatDecision.choices)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    stock_status = models.CharField(max_length=20, blank=True, default="")
    shipping_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fingerprint = models.CharField(max_length=64)  # sha256 of price+stock+shipping
    http_status = models.PositiveSmallIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    endpoint = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "availability_heartbeat_observation"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["source_site", "source_sku", "-observed_at"], name="hb_obs_sku_time"
            ),
            *retention_indexes("avail_hb_obs_expires"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("availability_heartbeat_observation")
        ]


class AvailabilityHeartbeatEvent(RetentionGoverned):
    """OQ17 small plain (non-hypertable) table: the rare non-`unchanged` rows,
    retained 365d, read exclusively by fingerprint-tuning diagnostics."""

    source_site = models.ForeignKey(
        SourceSite, on_delete=models.PROTECT, related_name="heartbeat_events"
    )
    source_sku = models.CharField(max_length=255)
    observed_at = models.DateTimeField()
    decision = models.CharField(max_length=20, choices=HeartbeatDecision.choices)
    prev_fingerprint = models.CharField(max_length=64, blank=True, default="")
    fingerprint = models.CharField(max_length=64)
    detail_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "availability_heartbeat_event"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["source_site", "-observed_at"], name="hb_event_site_time"),
            *retention_indexes("avail_hb_event_expires"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("availability_heartbeat_event")
        ]
