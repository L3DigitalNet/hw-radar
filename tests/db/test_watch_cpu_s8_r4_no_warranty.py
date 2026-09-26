"""Codex s8 round-4 finding 1 at watch level: a negator-owning phrase ("No
Warranty", "No Reserve", ...) between an unlock and a later lock assertion
must not negate the lock, so the contradictory title never satisfies
`require_vendor_unlocked` after an edit of a matching listing.

The listing is accepted as EPYC 7763 through a MANUAL model-grain edge (the
test_watch_cpu_s8_r3_negated_unlock.py fixture), so every catalog clause
matches and the lock clause alone decides the verdict.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Category,
    EligibilityVerdict,
    Listing,
    ListingResolution,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility.evaluate import evaluate_listing
from hw_radar.eligibility.requirements import CpuRequirementSpec, save_requirement
from hw_radar.eligibility.shortlist import shortlist
from hw_radar.matching import MATCHER_VERSION
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db

M, U = EligibilityVerdict.MATCH, EligibilityVerdict.UNKNOWN
_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_BASE = "AMD EPYC 7763 64-Core 2.45GHz 280W SP3 CPU 100-000000312"


@pytest.fixture
def seeded(db: None) -> None:
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
            shipping_price=Decimal(0),
            stock_status="in_stock",
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=observed_at,
    )


def _accepted_listing(site: SourceSite, key: str, title: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _observe(listing, _OBSERVED_AT)
    model = ProductModel.objects.get(model_number="EPYC 7763")
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "category": "test"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model
    )
    return listing


def _watch() -> Watch:
    watch = Watch.objects.create(name="epyc", category=Category.objects.get(slug="cpu"))
    save_requirement(watch, CpuRequirementSpec(sockets=("SP3",), require_vendor_unlocked=True))
    return watch


# Codex s8 r4 finding 1: the "no" of each phrase is the phrase's own meaning,
# so the later lock wording stands and contradicts the earlier unlock.
_CONTRADICTORY = [
    " Unlocked No Warranty Dell Locked",
    " Unlocked No Reserve Dell Locked",
    " Unlocked Not Tested Dell Locked",
    " Unlocked No Returns Lenovo Locked",
    # Codex s8 r5: phrases outside the registry still borrow the window's
    # negator; the denied lock that results blocks the unlocked reading.
    " Unlocked No Heatsink Dell Locked",
    " Unlocked No Fan PSB Locked",
]


@pytest.mark.parametrize("suffix", _CONTRADICTORY)
def test_matching_listing_edited_to_a_contradiction_loses_the_match(
    seeded: None, site: SourceSite, suffix: str
) -> None:
    listing = _accepted_listing(site, "r4-" + suffix.strip()[:40], _BASE + " Unlocked")
    watch = _watch()
    evaluate_listing(listing.pk)
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == M.value

    Listing.objects.filter(pk=listing.pk).update(title_raw=_BASE + suffix)
    listing.refresh_from_db()
    _observe(listing, _OBSERVED_AT + timedelta(hours=1))
    evaluate_listing(listing.pk)
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == U.value
    assert shortlist(watch.pk) == []

    _observe(listing, _OBSERVED_AT + timedelta(hours=2))
    evaluate_listing(listing.pk)
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == U.value
    assert shortlist(watch.pk) == []


def test_owning_phrase_before_a_plain_unlock_still_matches(seeded: None, site: SourceSite) -> None:
    # The barrier's other half: "No Warranty" must not deny the unlock after it.
    listing = _accepted_listing(site, "r4-ok", _BASE + " No Warranty Unlocked")
    watch = _watch()
    assert evaluate_listing(listing.pk).verdicts == {watch.pk: M}
