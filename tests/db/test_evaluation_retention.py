"""MS2-D-09 retention: a WatchEvaluation mirrors its listing's retention,
`Listing.mark_delisted` pulls a bounded evaluation's expiry forward exactly as
it does the snapshots', and the retention sweeper deletes it (DR-001/DR-008:
eBay-derived reasons quote prices and must not outlive the observation).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.management.commands.purge_expired import (
    retention_governed_models,
    sweep_expired,
)
from hw_radar.catalog.models import (
    Category,
    DelistReason,
    Listing,
    OfferSnapshot,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility.evaluate import evaluate_listing
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement

pytestmark = pytest.mark.django_db


@pytest.fixture
def watch(db: None) -> Watch:
    w = Watch.objects.create(name="drive", category=Category.objects.get(slug="drive"))
    save_requirement(w, DriveRequirementSpec())
    return w


def _listing(key: str, retention: RetentionClass, expires_at: datetime | None) -> Listing:
    listing = Listing.objects.create(
        source_site=SourceSite.objects.get(normalized_name="demo"),
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=f"Drive {key}",
        retention_class=retention,
        expires_at=expires_at,
    )
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("100.00"),
            shipping_price=Decimal(0),
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
        ),
        observed_at=timezone.now(),
    )
    evaluate_listing(listing.pk)
    return listing


def test_watch_evaluation_is_in_the_purge_registry() -> None:
    assert WatchEvaluation in retention_governed_models()


def test_mark_delisted_pulls_bounded_evaluation_expiry_forward(watch: Watch) -> None:
    ttl = timezone.now() + timedelta(hours=6)
    listing = _listing("ebay-1", RetentionClass.EBAY_LISTING_OBSERVATION, ttl)
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    assert (row.retention_class, row.expires_at) == (RetentionClass.EBAY_LISTING_OBSERVATION, ttl)

    # Delist evidence runs on the observation clock (MS2-D-39): the stamp is
    # the sweep's observed_at, not the processing time of the delist call.
    stamp = timezone.now() - timedelta(minutes=10)
    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=stamp)

    row.refresh_from_db()
    snapshot = OfferSnapshot.objects.get(listing=listing)
    assert row.expires_at == stamp
    assert snapshot.expires_at == stamp  # mirrored exactly, like the snapshot


def test_mark_delisted_never_extends_an_earlier_evaluation_expiry(watch: Watch) -> None:
    ttl = timezone.now() + timedelta(hours=6)
    listing = _listing("ebay-2", RetentionClass.EBAY_LISTING_OBSERVATION, ttl)
    earlier = timezone.now() - timedelta(hours=1)
    WatchEvaluation.objects.filter(listing=listing).update(expires_at=earlier)

    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=timezone.now())

    assert WatchEvaluation.objects.get(listing=listing).expires_at == earlier


def test_mark_delisted_leaves_indefinite_evaluations_alone(watch: Watch) -> None:
    listing = _listing("fact-1", RetentionClass.MERCHANT_FACT, None)

    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP)

    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    assert (row.retention_class, row.expires_at) == (RetentionClass.MERCHANT_FACT, None)


def test_sweeper_deletes_a_delisted_bounded_evaluation(watch: Watch) -> None:
    listing = _listing(
        "ebay-3", RetentionClass.EBAY_LISTING_OBSERVATION, timezone.now() + timedelta(hours=6)
    )
    stamp = timezone.now() - timedelta(minutes=1)
    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=stamp)

    report = sweep_expired(now=timezone.now())

    assert not WatchEvaluation.objects.filter(listing=listing).exists()
    assert report.counts[WatchEvaluation._meta.label] == 1  # pyright: ignore[reportPrivateUsage] - Django's public _meta API
