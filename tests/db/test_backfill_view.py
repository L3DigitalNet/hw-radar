"""C.3.4: the backfill queue is a VIEW over listings below model grain plus the
rung-2 decoded-but-unknown set — no second source of truth. Occurrence counts
group by decoded hypothesis so MS-1c's discovery loop has a trigger signal."""

import pytest

from hw_radar.catalog.models import (
    Listing,
    RetentionClass,
    SourceSite,
    UnknownModelBackfill,
)
from hw_radar.matching.resolver import CatalogResolver


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="BF", normalized_name="bf")


def _resolve(site: SourceSite, key: str, title: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    CatalogResolver().resolve_listing(listing.pk)
    return listing


def test_same_decoded_hypothesis_groups_across_listings(site: SourceSite) -> None:
    _resolve(site, "b1", "Seagate 20TB ST20000NM007D Recertified")
    _resolve(site, "b2", "Seagate 20TB ST20000NM007D Renewed")
    row = UnknownModelBackfill.objects.get(mpn_hypothesis="st20000nm007d")
    assert row.occurrences == 2
    assert row.family_grain_count == 2  # both attached at family grain (rung 2)
    assert row.first_seen <= row.last_seen


def test_unresolved_listing_without_hypothesis_gets_its_own_row(site: SourceSite) -> None:
    listing = _resolve(site, "b3", "mystery enterprise drive")
    # No vendor → empty vendor prefix in the composite key.
    row = UnknownModelBackfill.objects.get(hypothesis_key=f":listing:{listing.pk}")
    assert row.mpn_hypothesis is None
    assert row.occurrences == 1


def test_same_text_hypothesis_from_different_vendors_never_collapses(
    site: SourceSite,
) -> None:
    # CR-005: vendor_hint is part of the grouping key.
    from hw_radar.catalog.models import ListingResolution, ResolutionGrain

    for key, vendor in (("bv1", "seagate"), ("bv2", "toshiba")):
        listing = Listing.objects.create(
            source_site=site,
            source_listing_key=key,
            canonical_url=f"https://example.test/{key}",
            url_hash=key,
            title_raw="collision fixture",
            retention_class=RetentionClass.MERCHANT_FACT,
        )
        ListingResolution.objects.create(
            listing=listing,
            grain=ResolutionGrain.NONE,
            matcher_version="test",
            evidence={"outcome": "none", "mpn_hypothesis": "zz99zz99", "vendor_hint": vendor},
        )
    assert UnknownModelBackfill.objects.filter(mpn_hypothesis="zz99zz99").count() == 2


def test_model_grain_listings_are_not_in_the_queue(site: SourceSite) -> None:
    # A fully resolved listing must not appear (view covers BELOW model grain).
    from hw_radar.catalog.models import (
        AliasSourceKind,
        AliasType,
        Manufacturer,
        ProductAlias,
        ProductModel,
    )

    mfr = Manufacturer.objects.create(name="Seagate", normalized_name="seagate")
    model = ProductModel.objects.create(
        manufacturer=mfr,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text="st16000nm001g",
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    _resolve(site, "b4", "Seagate 16TB ST16000NM001G Recertified")
    assert not UnknownModelBackfill.objects.filter(mpn_hypothesis="st16000nm001g").exists()
