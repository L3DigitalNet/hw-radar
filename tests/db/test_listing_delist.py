"""CR-004 Listing-grain soft delete: terminal marks, evidence bounding, and the
guarantee that delisting never touches the DR-010 resolution audit trail."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from hw_radar.catalog.models import (
    Category,
    DelistReason,
    Listing,
    ListingResolution,
    Manufacturer,
    OfferSnapshot,
    ProductFamily,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    SourceType,
    StockStatus,
)

T0 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
TTL = T0 + timedelta(hours=6)  # the eBay DR-008 freshness bound


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(
        name="eBay (test)",
        normalized_name="ebay-delist-test",
        source_type=SourceType.MARKETPLACE,
    )


def _listing(
    site: SourceSite,
    key: str,
    *,
    retention_class: RetentionClass = RetentionClass.EBAY_LISTING_OBSERVATION,
    expires_at: datetime | None = TTL,
) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://www.ebay.com/itm/{key}",
        url_hash=key.ljust(64, "0"),
        title_raw="Seagate Exos X18 18TB Recertified",
        retention_class=retention_class,
        expires_at=expires_at,
    )


def test_mark_delisted_stamps_time_and_reason(site: SourceSite) -> None:
    listing = _listing(site, "ebay-1")
    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0) is True

    listing.refresh_from_db()
    assert listing.delisted_at == T0
    assert listing.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    # Second call is a no-op: the first delist instant is the one DR-008 cares
    # about, so a later sweep must not overwrite it.
    assert listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0 + timedelta(hours=1)) is False
    listing.refresh_from_db()
    assert listing.delisted_at == T0


def test_delist_does_not_reset_last_seen(site: SourceSite) -> None:
    # last_seen is auto_now, so a careless full save() here would silently reset
    # the absence clock the ABSENT_STALE heuristic reads.
    listing = _listing(site, "ebay-lastseen")
    stale = T0 - timedelta(days=2)
    Listing.objects.filter(pk=listing.pk).update(last_seen=stale)
    listing.refresh_from_db()

    listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0)

    listing.refresh_from_db()
    assert listing.last_seen == stale


def test_delist_pulls_bounded_evidence_ttl_forward(site: SourceSite) -> None:
    listing = _listing(site, "ebay-2")
    snapshot = OfferSnapshot.objects.create(
        listing=listing,
        observed_at=T0 - timedelta(hours=1),
        item_price=Decimal("199.99"),
        stock_status=StockStatus.IN_STOCK,
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=TTL,
    )

    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0)

    listing.refresh_from_db()
    snapshot.refresh_from_db()
    # DR-008: the offer evidence becomes due for the retention sweeper at the
    # delist instant instead of riding out the rest of the freshness window.
    assert listing.expires_at == T0
    assert snapshot.expires_at == T0


def test_delist_leaves_indefinite_retention_untouched(site: SourceSite) -> None:
    # merchant_fact rows must keep expires_at NULL (DR-001 CHECK); delisting an
    # offer is not licence to retire a merchant fact.
    listing = _listing(site, "spd-1", retention_class=RetentionClass.MERCHANT_FACT, expires_at=None)

    listing.mark_delisted(DelistReason.MANUAL, when=T0)

    listing.refresh_from_db()
    assert listing.delisted_at == T0
    assert listing.expires_at is None


def test_delist_reason_requires_a_timestamp(site: SourceSite) -> None:
    listing = _listing(site, "ebay-3")
    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.filter(pk=listing.pk).update(delist_reason=DelistReason.MANUAL)


def test_delisted_at_requires_a_reason(site: SourceSite) -> None:
    listing = _listing(site, "ebay-4")
    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.filter(pk=listing.pk).update(delisted_at=T0)


def test_active_and_delisted_managers_partition_the_rows(site: SourceSite) -> None:
    live = _listing(site, "ebay-live")
    gone = _listing(site, "ebay-gone")
    gone.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0)

    assert list(Listing.objects.active().filter(source_site=site)) == [live]
    assert list(Listing.objects.delisted().filter(source_site=site)) == [gone]
    assert Listing.objects.filter(source_site=site).count() == 2  # nothing was deleted


def test_delist_preserves_the_resolution_audit_trail(site: SourceSite) -> None:
    # The whole reason delisting is a soft delete: a hard delete would cascade
    # into listing_resolution, where superseded_by is PROTECT (DR-010).
    listing = _listing(site, "ebay-5")
    seagate = Manufacturer.objects.create(name="Seagate", normalized_name="seagate-delist-test")
    category = Category.objects.get_or_create(slug="drive", defaults={"name": "Drive"})[0]
    family = ProductFamily.objects.create(
        manufacturer=seagate, category=category, name="Exos X18", normalized_name="exos-x18"
    )
    old = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.9,
        matcher_version="1.0.0",
        is_current=False,
    )
    current = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.95,
        matcher_version="1.0.1",
        is_current=True,
    )
    old.superseded_by = current
    old.save(update_fields=["superseded_by"])

    with pytest.raises(ProtectedError), transaction.atomic():
        listing.delete()  # the unavailable alternative, pinned so it stays unavailable

    listing.refresh_from_db()
    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0) is True

    edges = ListingResolution.objects.filter(listing=listing).order_by("pk")
    assert [e.pk for e in edges] == [old.pk, current.pk]
    old.refresh_from_db()
    current.refresh_from_db()
    assert old.superseded_by_id == current.pk  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
    assert current.is_current is True  # a delisted listing keeps its current edge


def test_mark_relisted_restores_the_listing(site: SourceSite) -> None:
    listing = _listing(site, "ebay-6")
    listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0)

    assert listing.mark_relisted() is True
    listing.refresh_from_db()
    assert listing.delisted_at is None
    assert listing.delist_reason == ""
    assert list(Listing.objects.active().filter(source_site=site)) == [listing]
    assert listing.mark_relisted() is False  # already live
