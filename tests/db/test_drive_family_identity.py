"""One WD product line, one family identity, end to end (s7 D1).

Before the fix, a rung-2 decode of an unseeded Ultrastar MPN created the
provisional family (western_digital, 'ultrastar') while the seeded Ultrastar
models sat under 'ultrastar dc hc560': two identities for one line, so a
family-grain listing never met the seeded models' listings. The seeds now
name the family the grammar decodes, and both paths land on one row."""

import pytest

from hw_radar.catalog.models import (
    Listing,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def test_unseeded_ultrastar_decode_lands_on_the_seeded_family() -> None:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])
    site = SourceSite.objects.create(name="S7 D1 Site", normalized_name="s7d1site")
    # WUH722020BLE601 is not seeded (no first-party table), so only the rung-2
    # grammar decode can place it.
    listing = _listing(
        site, "hc560-unseeded", 'WD DC HC560 20TB WUH722020BLE601 7.2K RPM SATA 6Gb/s 3.5" HDD'
    )
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    seeded = ProductModel.objects.get(normalized_model_number="wuh722020ble6l4")
    assert listing.product_family == seeded.product_family
    assert (
        ProductFamily.objects.filter(
            manufacturer__normalized_name="western_digital", normalized_name__startswith="ultrastar"
        ).count()
        == 1
    )
