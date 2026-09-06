from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from hw_radar.catalog.models import (
    CheapSignal,
    FxRateDaily,
    LifecycleState,
    Listing,
    OfferSnapshot,
    RetentionClass,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceConfig,
    SourceSite,
    SourceTier,
    SourceType,
    VolatilityProfile,
)

pytestmark = pytest.mark.django_db


def make_site(name: str = "Demo Site", key: str = "demo-site") -> SourceSite:
    return SourceSite.objects.create(name=name, normalized_name=key, source_type=SourceType.OTHER)


def make_config(site: SourceSite, **overrides: object) -> SourceConfig:
    defaults: dict[str, object] = {
        "source_site": site,
        "tier": SourceTier.T2_SPECIALIST,
        "domain": "example.com",
        "cadence_baseline_s": 3600,
        "cadence_ceiling_s": 900,
    }
    defaults.update(overrides)
    return SourceConfig.objects.create(**defaults)


def test_source_config_defaults_are_safe() -> None:
    config = make_config(make_site())
    assert config.enabled is False  # sources ship disabled until their connector lands
    assert config.lifecycle_state == LifecycleState.ACTIVE
    assert config.heartbeat_enabled is False  # orthogonal to fast_lane (design §3 matrix)
    assert config.fast_lane is False
    assert config.volatility_profile == VolatilityProfile.STABLE
    assert config.cheap_signal == CheapSignal.NONE
    assert config.consecutive_failures == 0


def test_ceiling_must_not_be_slower_than_baseline() -> None:
    with pytest.raises(IntegrityError):
        make_config(make_site(), cadence_baseline_s=900, cadence_ceiling_s=3600)


def test_fast_lane_requires_drop_prone_and_cheap_signal() -> None:
    with pytest.raises(IntegrityError):
        make_config(make_site(), fast_lane=True)  # stable + no signal


def test_fast_lane_allowed_when_eligible() -> None:
    config = make_config(
        make_site(),
        fast_lane=True,
        volatility_profile=VolatilityProfile.DROP_PRONE,
        cheap_signal=CheapSignal.SHOPIFY_PRODUCTS_JSON,
    )
    assert config.fast_lane is True


def test_scraper_run_records_lifecycle_of_a_run() -> None:
    site = make_site()
    run = ScraperRun.objects.create(
        source_site=site, run_kind=RunKind.FULL, started_at=timezone.now()
    )
    assert run.status == RunStatus.RUNNING
    assert run.failure_class == ""
    run.status = RunStatus.SUCCESS
    run.finished_at = timezone.now()
    run.records_fetched = 3
    run.save()
    stored = ScraperRun.objects.get(pk=run.pk)
    assert stored.status == RunStatus.SUCCESS
    assert stored.records_fetched == 3


def test_fx_rate_unique_per_date_and_pair() -> None:
    day = timezone.now().date()
    FxRateDaily.objects.create(rate_date=day, base="EUR", quote="USD", rate=Decimal("1.08"))
    with pytest.raises(IntegrityError):
        FxRateDaily.objects.create(rate_date=day, base="EUR", quote="USD", rate=Decimal("1.09"))


def test_usd_item_price_generated_column() -> None:
    site = make_site()
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key="sku-1",
        canonical_url="https://example.com/sku-1",
        url_hash="a" * 64,
        title_raw="Demo 16TB drive",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    OfferSnapshot.objects.create(
        listing=listing,
        observed_at=timezone.now(),
        currency="EUR",
        item_price=Decimal("100.00"),
        fx_rate=Decimal("1.080000"),
        fx_pair="EUR/USD",
        fx_rate_date=timezone.now().date(),
        fx_source="frankfurter",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    snap = OfferSnapshot.objects.get(listing=listing)
    assert snap.usd_item_price == Decimal("108.0000")


def test_non_usd_snapshot_without_fx_stamp_is_rejected() -> None:
    # FR-004 in schema: a EUR row with no FX stamp must fail, not compute as USD.
    site = make_site("Demo Site EU", "demo-site-eu")
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key="sku-eu-1",
        canonical_url="https://example.de/sku-eu-1",
        url_hash="c" * 64,
        title_raw="Demo 16TB (EU)",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    with pytest.raises(IntegrityError):
        OfferSnapshot.objects.create(
            listing=listing,
            observed_at=timezone.now(),
            currency="EUR",
            item_price=Decimal("100.00"),
            retention_class=RetentionClass.MERCHANT_FACT,
        )


def test_listing_is_international_defaults_false() -> None:
    site = make_site()
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key="sku-2",
        canonical_url="https://example.com/sku-2",
        url_hash="b" * 64,
        title_raw="Demo 8TB drive",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    assert listing.is_international is False


def test_seeded_sources_exist_and_are_disabled() -> None:
    # Migration 0005 seeds the five MS-1 sources + demo, all disabled until
    # their connector lands (MS-1d flips each on as it ships). Migration 0011
    # flips heartbeat_enabled per-source as each connector's task lands (C1:
    # serverpartdeals, C3: WD, C4: Seagate, C5: eBay); fast_lane stays False for
    # sources that aren't drop_prone-eligible (FR-002). eBay is heartbeat-native:
    # its Browse poll IS the heartbeat, so fast_lane=True (drop_prone ∩ ebay_browse).
    expected = {
        # Each value is the tuple (tier, heartbeat_enabled, fast_lane).
        "wd-recertified": (SourceTier.T1_MANUFACTURER, True, True),
        "seagate-recertified": (SourceTier.T1_MANUFACTURER, True, True),
        "serverpartdeals": (SourceTier.T2_SPECIALIST, True, False),
        "goharddrive": (SourceTier.T2_SPECIALIST, False, False),
        "ebay": (SourceTier.T0_OFFICIAL_API, True, True),
        "demo": (SourceTier.T2_SPECIALIST, False, False),
    }
    for key, (tier, heartbeat_enabled, fast_lane) in expected.items():
        config = SourceConfig.objects.get(source_site__normalized_name=key)
        assert config.tier == tier, key
        assert config.enabled is False, key
        assert config.heartbeat_enabled is heartbeat_enabled, key
        assert config.fast_lane is fast_lane, key  # FR-002: drop_prone-only (WD flipped C3)
