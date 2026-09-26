"""Codex s8 round-2 finding C on the live resolver: a pin count beside the
socket ('SP3 4094-pin') is not a named model, so it neither blocks a fresh
OPN acceptance nor demotes an accepted OPN-only listing on the rung-0 polls
after the seller adds it. The 9354P-over-OPN-798 review stays (r1 #8).

Runs the real CatalogResolver over the shipped CPU seeds under the production
CPU rules (AMD EPYC ratified, OQ34), so an ACCEPT here is what production
writes.
"""

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
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
# 100-000000312 is the seeded EPYC 7763's OPN; 4094 is the SP3 pin count.
_OPN_ONLY = "AMD EPYC 100-000000312 SP3 CPU"
_PIN_TITLES = [
    "AMD EPYC 100-000000312 SP3 4094-pin CPU",
    "AMD EPYC 100-000000312 SP3 4094 pin CPU",
    "AMD EPYC 100-000000312 Socket SP3 4094 CPU",
]


@pytest.fixture
def seeded_cpus(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(listing: Listing, observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("1999.00"),
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=observed_at,
    )


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _observe(listing, _OBSERVED_AT)
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _retitle(listing: Listing, title: str, observed_at: datetime) -> None:
    Listing.objects.filter(pk=listing.pk).update(title_raw=title)
    listing.refresh_from_db()
    _observe(listing, observed_at)


_EPYC_7763 = "EPYC 7763"


@pytest.mark.parametrize("title", _PIN_TITLES)
def test_fresh_opn_title_with_pin_count_accepts_7763(
    site: SourceSite, seeded_cpus: None, title: str
) -> None:
    listing = _listing(site, "fresh-" + title[24:40], title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == ProductModel.objects.get(model_number=_EPYC_7763)


@pytest.mark.parametrize("new_title", _PIN_TITLES)
def test_accepted_opn_listing_keeps_its_prior_when_a_pin_count_is_added(
    site: SourceSite, seeded_cpus: None, new_title: str
) -> None:
    """The edited title emits the same single OPN candidate, so this is pure
    rung 0: only the re-run veto could demote the prior, and a pin count must
    not give it a model to disagree with."""
    listing = _listing(site, "rung0-" + new_title[24:40], _OPN_ONLY)
    assert _resolve(listing).evidence["outcome"] == "accept"
    model = ProductModel.objects.get(model_number=_EPYC_7763)
    assert listing.product_model == model

    _retitle(listing, new_title, _OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.evidence.get("veto", []) == []
    assert listing.product_model == model
    assert listing.resolution_grain == ResolutionGrain.MODEL

    _observe(listing, _OBSERVED_AT + timedelta(hours=2))
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == model


def test_named_9354p_over_opn_798_with_pin_count_still_reviews(
    site: SourceSite, seeded_cpus: None
) -> None:
    listing = _listing(site, "p-pins", "AMD EPYC 100-000000798 9354P SP5 6096-pin Processor")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["model"]
    assert listing.product_model is None
