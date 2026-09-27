"""Codex s8 round-1 finding 8 on the live resolver: an explicitly different
model name must not be overridden by a seeded OPN, on a fresh listing, on the
redecision after the title changes, and on every later poll.

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
_CPU_0082 = "AMD EPYC GENOA SP5 ZEN4 9354 32-Core 3.25GHz Processor CPU 100-000000798Open"
# cpu-0082 with only the model number changed: 9354P is unseeded, while the
# fused OPN still belongs to the seeded EPYC 9354.
_P_OVER_798_FUSED = "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz Processor CPU 100-000000798Open"
_P_OVER_798 = "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz Processor CPU 100-000000798"


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


@pytest.mark.parametrize("title", [_P_OVER_798_FUSED, _P_OVER_798])
def test_fresh_p_name_over_the_9354_opn_is_reviewed(
    site: SourceSite, seeded_cpus: None, title: str
) -> None:
    listing = _listing(site, "fresh-" + title[-12:], title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["model"]
    assert listing.product_model is None


def test_cpu_0082_still_accepts_9354(site: SourceSite, seeded_cpus: None) -> None:
    listing = _listing(site, "cpu-0082", _CPU_0082)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == ProductModel.objects.get(model_number="EPYC 9354")


def test_p_variant_with_its_own_opn_is_still_not_9354(site: SourceSite, seeded_cpus: None) -> None:
    listing = _listing(
        site, "p-own-opn", "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz CPU 100-000000805"
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] != "accept"
    assert listing.product_model is None


@pytest.mark.parametrize("new_title", [_P_OVER_798_FUSED, _P_OVER_798])
def test_accepted_cpu_0082_retitled_to_9354p_reviews_and_stays_reviewed(
    site: SourceSite, seeded_cpus: None, new_title: str
) -> None:
    """The stale-decision class: the accepted 9354 must not be carried onto a
    title that names 9354P, neither by the redecision the identifier change
    triggers nor by rung-0 inheritance on the polls after it."""
    listing = _listing(site, "retitle-" + new_title[-12:], _CPU_0082)
    assert _resolve(listing).evidence["outcome"] == "accept"

    _retitle(listing, new_title, _OBSERVED_AT + timedelta(hours=1))
    redecided = _resolve(listing)
    assert redecided.evidence["outcome"] == "review"
    assert redecided.evidence["veto"] == ["model"]
    assert listing.product_model is None
    assert listing.resolution_grain == ResolutionGrain.NONE

    _observe(listing, _OBSERVED_AT + timedelta(hours=2))
    next_poll = _resolve(listing)
    assert next_poll.evidence["outcome"] == "review"
    assert listing.product_model is None


def test_rung0_prior_is_vetoed_when_only_the_named_model_changes(
    site: SourceSite, seeded_cpus: None
) -> None:
    """Pure rung 0: '9354P' placed after the OPN is not an 'EPYC <number>'
    phrase, so the edited title emits exactly the accepted title's one
    candidate (the OPN). No identifier changes, nothing is reconsidered, and
    only the veto rung 0 re-runs can see the named model."""
    listing = _listing(site, "rung0", "AMD EPYC 100-000000798 Processor SP5")
    assert _resolve(listing).evidence["outcome"] == "accept"

    _retitle(
        listing, "AMD EPYC 100-000000798 9354P Processor SP5", _OBSERVED_AT + timedelta(hours=1)
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 0
    assert "reconsidered_prior" not in edge.evidence
    assert edge.evidence["veto"] == ["model"]
    assert listing.product_model is None

    _observe(listing, _OBSERVED_AT + timedelta(hours=2))
    assert _resolve(listing).evidence["outcome"] == "review"
    assert listing.product_model is None
