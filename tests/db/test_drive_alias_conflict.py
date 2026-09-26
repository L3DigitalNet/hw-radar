"""Drive identity conflicts on the live resolver path (round-3 review R3-A, R3-B).

Every case imports the shipped drive seeds, so the alias table is the
production one: WD40EFPX and both WUH722424ALE6L* models with their WD retail
part numbers (0F62795 -> ALE6L1, 0F62796 -> ALE6L4) are catalog-authoritative."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Listing,
    ListingResolution,
    ProductModel,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def site(db: None) -> SourceSite:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(listing: Listing, *, attrs: dict[str, object], observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("499.00"),
            attrs=attrs,
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
        ),
        observed_at=observed_at,
    )


def _listing(
    site: SourceSite, key: str, title: str, *, attrs: dict[str, object] | None = None
) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _observe(listing, attrs=attrs or {}, observed_at=_OBSERVED_AT)
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _model(mpn: str) -> ProductModel:
    return ProductModel.objects.get(normalized_model_number=mpn)


def test_structured_mpn_does_not_hide_the_second_title_mpn(site: SourceSite) -> None:
    # R3-A bypass 1: dedup kept only the structured WD40EFPX occurrence, the
    # D3 guard saw one title MPN (WD40EFZX), and the EFPX exact alias accepted.
    listing = _listing(
        site, "efpx-efzx", "WD Red Plus WD40EFPX/WD40EFZX 4TB", attrs={"mpn": "WD40EFPX"}
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["multiple_mpns"] == ["wd40efpx", "wd40efzx"]
    assert listing.product_model is None


def test_retail_pn_of_another_model_blocks_rung_zero(site: SourceSite) -> None:
    # R3-A bypass 2: re-observed naming ALE6L4's retail PN, the listing kept
    # inheriting its ALE6L1 prior because rung 0 never looked at alias hits.
    listing = _listing(site, "ale6l1", "WD Ultrastar WUH722424ALE6L1 24TB SATA")
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == _model("wuh722424ale6l1")
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="WD Ultrastar WUH722424ALE6L1 / 0F62796 24TB SATA"
    )
    listing.refresh_from_db()
    _observe(listing, attrs={}, observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["conflicting_alias_models"] == ["0f62796", "wuh722424ale6l1"]
    assert listing.product_model is None


def test_same_model_retail_pn_still_accepts(site: SourceSite) -> None:
    listing = _listing(site, "ale6l1-own", "WD Ultrastar WUH722424ALE6L1 / 0F62795 24TB SATA")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("wuh722424ale6l1")


def test_retail_pn_alone_does_not_accept(site: SourceSite) -> None:
    # Retail PNs are review-only evidence: MS-1e ebay-0409's title resolved to
    # nothing before they became candidates, and still does.
    listing = _listing(
        site, "pn-only", "NEW SEALED WD Ultrastar DC HC580 0F62796 - Enterprise HDD - 24 TB"
    )
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.product_model is None


def test_for_sale_preamble_resolves(site: SourceSite) -> None:
    # R3-B: the leading-"for" reference rule masked the whole sales title.
    listing = _listing(site, "for-sale", "For sale: Seagate ST12000NE0008 12TB")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("st12000ne0008")


def test_leading_for_compatible_part_stays_unresolved(site: SourceSite) -> None:
    listing = _listing(
        site, "for-compat", 'FOR Seagate Exos X14 12TB 7200 RPM SATA 3.5" HDD ST12000NM0008 NEW'
    )
    _resolve(listing)
    assert listing.product_model is None


def test_leading_compatible_part_stays_unresolved(site: SourceSite) -> None:
    # Round-3 tail T1: a first-token "Compatible" sells a look-alike of the
    # cited drive, like a leading "FOR"; before it was masked the cited
    # ST12000NE0008 exact alias accepted.
    listing = _listing(site, "compat-lead", "Compatible Seagate ST12000NE0008 12TB")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] != "accept"
    assert listing.product_model is None


def test_mid_title_compatible_still_resolves(site: SourceSite) -> None:
    listing = _listing(site, "compat-mid", "Seagate ST12000NE0008 12TB NAS compatible")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("st12000ne0008")


def test_reobserved_title_naming_another_model_does_not_inherit(site: SourceSite) -> None:
    # Round-3 tail T2: one identifier, one other catalog model. Nothing
    # conflicts among the CURRENT identifiers, so conflicting_alias_models is
    # silent; rung 0 must still refuse to keep the ST12000NE0008 prior once
    # the title names only ST12000NM0008 (same capacity and interface, so no
    # hard-attribute veto fires either).
    listing = _listing(site, "ne-then-nm", "Seagate ST12000NE0008 12TB")
    assert _resolve(listing).evidence["outcome"] == "accept"
    prior_model = _model("st12000ne0008")
    assert listing.product_model == prior_model
    Listing.objects.filter(pk=listing.pk).update(title_raw="Seagate ST12000NM0008 12TB")
    listing.refresh_from_db()
    _observe(listing, attrs={}, observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["prior_model_not_named"] == {
        "prior_model_id": prior_model.pk,
        "alias_model_ids": [_model("st12000nm0008").pk],
        "identifiers": ["st12000nm0008"],
    }
    assert listing.product_model is None


def test_reobserved_same_model_still_inherits(site: SourceSite) -> None:
    listing = _listing(site, "ne-twice", "Seagate ST12000NE0008 12TB")
    assert _resolve(listing).evidence["outcome"] == "accept"
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="Seagate IronWolf Pro ST12000NE0008 12TB"
    )
    listing.refresh_from_db()
    _observe(listing, attrs={}, observed_at=_OBSERVED_AT + timedelta(hours=1))
    # An unchanged accept writes no new edge, so the current edge is still
    # the rung-1 original; the kept denorm is the observable inheritance.
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == _model("st12000ne0008")
