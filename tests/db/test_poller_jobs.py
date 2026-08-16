# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
import pytest

from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import SourceConfig
from hw_radar.poller.service import build_scheduler, load_schedules

# transaction=True: recovery_probe_job writes via sync_to_async threads;
# serialized_rollback preserves the migration-0005 seed (see test_pipeline.py).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_disabled_sources_get_no_jobs() -> None:
    # Seed ships everything disabled; the scheduler must reflect that.
    registry = BucketRegistry()
    configs = list(SourceConfig.objects.select_related("source_site").filter(enabled=True))
    scheduler = build_scheduler(registry, load_schedules(configs))
    assert scheduler.get_job("poll-demo") is None


def test_recovery_probe_reactivates_paused_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-0017 end-to-end: paused source + passing daily probe → active again.
    # A fake (non-Scrapy) adapter keeps the Scrapy reactor confined to
    # test_pipeline_demo.py's module loop (the single-loop rule).
    import asyncio
    from datetime import UTC, datetime
    from decimal import Decimal

    from hw_radar.acquisition import sources
    from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
    from hw_radar.catalog.models import LifecycleState, RunKind
    from hw_radar.poller.service import recovery_probe_job

    class ProbeAdapter:
        name = "demo"
        site_key = "demo"
        run_kind = RunKind.FULL
        expects_json = True

        async def fetch(self) -> RawBatch:
            return RawBatch(
                source="demo",
                fetched_at=datetime(2026, 7, 5, tzinfo=UTC),
                items=[RawItem(url="https://demo.invalid/p", payload_json={"sku": "p"})],
            )

        def parse(self, batch: RawBatch) -> list[ParsedListing]:
            return [
                ParsedListing(
                    source_listing_key="probe-sku",
                    url="https://demo.invalid/p",
                    title="Probe 8TB",
                    price=Decimal("99.99"),
                )
            ]

    monkeypatch.setitem(sources.ADAPTERS, "demo", ProbeAdapter)
    SourceConfig.objects.filter(source_site__normalized_name="demo").update(
        enabled=True, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX
    )
    registry = BucketRegistry()
    registry.configure_source("demo", rate_per_min=60.0, burst=3, now_s=0.0)
    asyncio.run(recovery_probe_job(registry))
    config = SourceConfig.objects.get(source_site__normalized_name="demo")
    assert config.lifecycle_state == LifecycleState.ACTIVE


def test_poller_wires_the_catalog_resolver() -> None:
    # MS-1b: scheduled polls must resolve through the ADR-0019 resolver, not the
    # MS-1a NullResolver stub. Import-level tripwire + type check.
    from hw_radar.matching.resolver import CatalogResolver
    from hw_radar.poller import service

    assert service.CatalogResolver is CatalogResolver
    assert not hasattr(service, "NullResolver")


def test_enabled_source_gets_job_with_config_cadence_and_bucket() -> None:
    SourceConfig.objects.filter(source_site__normalized_name="demo").update(enabled=True)
    registry = BucketRegistry()
    configs = list(SourceConfig.objects.select_related("source_site").filter(enabled=True))
    scheduler = build_scheduler(registry, load_schedules(configs))
    job = scheduler.get_job("poll-demo")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 3600  # seeded baseline
    assert job.misfire_grace_time == 60
    assert "demo" in registry.source_buckets
