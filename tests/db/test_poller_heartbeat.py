"""ADR-0015 Confirmation criterion (D1): the heartbeat probe fires the full
pipeline ONLY on a real transition, so an unchanged run writes no offer_snapshot
and no event, while an OOS->in_stock transition writes exactly one of each.

The fake adapter is both a HeartbeatProbe (probe()) and a SourceAdapter
(fetch/parse) so run_heartbeat can drive the fired FULL run without network.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition.contracts import NullResolver, ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.heartbeat import HeartbeatReading, run_heartbeat
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import (
    AvailabilityHeartbeatEvent,
    AvailabilityHeartbeatObservation,
    HeartbeatDecision,
    LifecycleState,
    OfferSnapshot,
    RetentionClass,
    SourceConfig,
)
from hw_radar.poller.service import build_scheduler, poll_heartbeat

# transaction=True: run_heartbeat/run_source write from sync_to_async threads.
# serialized_rollback preserves the migration-0005 seed (see test_pipeline.py).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _reading(sku: str, stock: str, price: str = "100.00") -> HeartbeatReading:
    return HeartbeatReading(
        source_sku=sku,
        price=Decimal(price),
        currency="USD",
        stock_status=stock,
        shipping_price=None,
        http_status=200,
        latency_ms=None,
        endpoint="https://fake.invalid/probe",
    )


class FakeHeartbeatAdapter:
    """probe() + fetch()/parse() derived from the same controllable readings."""

    name = "fake-hb"
    expects_json = True
    # SourceAdapter conformance only: the heartbeat path never reads it.
    last_parse_skipped = 0

    def __init__(
        self,
        site_key: str,
        readings: list[HeartbeatReading],
        *,
        retention_class: RetentionClass | None = None,
        expires_policy: object | None = None,
    ) -> None:
        from hw_radar.catalog.models import RunKind

        self.site_key = site_key
        self.run_kind = RunKind.FULL
        self._readings = readings
        if retention_class is not None:
            self.retention_class = retention_class
        if expires_policy is not None:
            self.expires_policy = expires_policy

    def set_readings(self, readings: list[HeartbeatReading]) -> None:
        self._readings = readings

    async def probe(self) -> list[HeartbeatReading]:
        return list(self._readings)

    async def fetch(self) -> RawBatch:
        return RawBatch(
            source=self.name,
            fetched_at=datetime.now(UTC),
            items=[
                RawItem(
                    url=f"https://fake.invalid/{r.source_sku}", payload_json={"sku": r.source_sku}
                )
                for r in self._readings
            ],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [
            ParsedListing(
                source_listing_key=r.source_sku,
                url=f"https://fake.invalid/{r.source_sku}",
                title=f"Fake {r.source_sku}",
                price=r.price or Decimal("100.00"),
                currency=r.currency,
                shipping_price=r.shipping_price,
                stock_status=r.stock_status,
                raw_url=f"https://fake.invalid/{r.source_sku}",
            )
            for r in self._readings
        ]


def _spd_config() -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="serverpartdeals"
    )


def test_identical_probes_write_no_snapshot_or_event() -> None:
    config = _spd_config()
    adapter = FakeHeartbeatAdapter("serverpartdeals", [_reading("SKU-A", "in_stock")])
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # baseline sighting fires once
    snaps = OfferSnapshot.objects.count()
    events = AvailabilityHeartbeatEvent.objects.count()

    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # identical -> unchanged
    assert OfferSnapshot.objects.count() == snaps
    assert AvailabilityHeartbeatEvent.objects.count() == events
    obs = AvailabilityHeartbeatObservation.objects.filter(source_sku="SKU-A").order_by(
        "observed_at"
    )
    assert obs.count() == 2
    latest = obs.last()
    assert latest is not None
    assert latest.decision == HeartbeatDecision.UNCHANGED


def test_oos_to_instock_transition_writes_exactly_one_snapshot_and_event() -> None:
    config = _spd_config()
    adapter = FakeHeartbeatAdapter("serverpartdeals", [_reading("SKU-B", "out_of_stock")])
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # baseline OOS
    snaps = OfferSnapshot.objects.filter(listing__source_listing_key="SKU-B").count()
    events = AvailabilityHeartbeatEvent.objects.filter(source_sku="SKU-B").count()

    adapter.set_readings([_reading("SKU-B", "in_stock")])
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # transition -> fire once
    assert OfferSnapshot.objects.filter(listing__source_listing_key="SKU-B").count() == snaps + 1
    assert AvailabilityHeartbeatEvent.objects.filter(source_sku="SKU-B").count() == events + 1
    evt = (
        AvailabilityHeartbeatEvent.objects.filter(source_sku="SKU-B").order_by("observed_at").last()
    )
    assert evt is not None
    assert evt.decision == HeartbeatDecision.TRANSITION_DETECTED


def test_ebay_heartbeat_rows_carry_ebay_listing_observation_class() -> None:
    # CR-NEW-001: the eBay carve-out caps BOTH heartbeat tables at the 6h class;
    # neither row may land on the 30d/365d availability_heartbeat* path.
    from hw_radar.acquisition.sources.ebay import (
        _expires_in_6h,  # pyright: ignore[reportPrivateUsage]
    )

    config = SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="ebay"
    )
    adapter = FakeHeartbeatAdapter(
        "ebay",
        [_reading("EBAY-1", "out_of_stock")],
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_policy=_expires_in_6h,
    )
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # baseline -> transition -> event

    obs = AvailabilityHeartbeatObservation.objects.filter(source_sku="EBAY-1").first()
    evt = AvailabilityHeartbeatEvent.objects.filter(source_sku="EBAY-1").first()
    assert obs is not None and evt is not None
    assert obs.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert evt.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert obs.expires_at is not None and (obs.expires_at - obs.observed_at) <= timedelta(hours=6)
    assert evt.expires_at is not None and (evt.expires_at - evt.observed_at) <= timedelta(hours=6)


def test_poll_heartbeat_admits_records_and_applies_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    # poll_heartbeat mirrors poll_source: admission (run_kind=heartbeat) -> run_heartbeat
    # -> apply_run_outcome -> reschedule on interval change (CR-006 residual).
    from hw_radar.acquisition import sources

    adapter = FakeHeartbeatAdapter("serverpartdeals", [_reading("HB-1", "in_stock")])
    monkeypatch.setitem(sources.ADAPTERS, "serverpartdeals", lambda: adapter)
    SourceConfig.objects.filter(source_site__normalized_name="serverpartdeals").update(
        enabled=True,
        lifecycle_state=LifecycleState.ACTIVE,
        current_interval_s=900,
        cadence_baseline_s=3600,
        cadence_ceiling_s=300,
    )
    registry = BucketRegistry()
    registry.configure_source("serverpartdeals", rate_per_min=60.0, burst=3, now_s=0.0)
    configs = list(
        SourceConfig.objects.select_related("source_site").filter(
            source_site__normalized_name="serverpartdeals"
        )
    )
    scheduler = build_scheduler(registry, configs)
    asyncio.run(poll_heartbeat("serverpartdeals", registry, scheduler))

    config = SourceConfig.objects.get(source_site__normalized_name="serverpartdeals")
    assert config.last_run_at is not None
    assert AvailabilityHeartbeatObservation.objects.filter(source_sku="HB-1").count() == 1
