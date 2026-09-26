"""CPU identity on the live resolver path for the s8 owner EPYC audit.

Runs the real CatalogResolver over the shipped CPU seeds with CPU auto-accept
forced on (as in test_resolver_cpu_identity.py), so an ACCEPT here is what a
ratified flip would write. Pins cpu-0082 (qualifier words plus a fused OPN),
that vendor-lock wording never changes identity on rungs 0-2, and the owner's
cpu-0283 ruling that the listing price is not identity evidence.
"""

from __future__ import annotations

from dataclasses import replace
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
from hw_radar.matching import categories
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_CPU_0082 = "AMD EPYC GENOA SP5 ZEN4 9354 32-Core 3.25GHz Processor CPU 100-000000798Open"
_CPU_0283 = "AMD EPYC 7763 Processor 64-Core 2.45GHz 256MB 280W CPU 100-000000312"


@pytest.fixture
def seeded_cpus(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])
    rules = categories.rules_for("cpu")
    assert rules is not None
    monkeypatch.setitem(
        categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - test-only registration, as in test_resolver_categories.py
        "cpu",
        lambda: replace(rules, auto_accept=True),
    )


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(listing: Listing, *, price: Decimal, observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=price,
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=observed_at,
    )


def _listing(
    site: SourceSite, key: str, title: str, *, price: Decimal = Decimal("2499.00")
) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _observe(listing, price=price, observed_at=_OBSERVED_AT)
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _model(number: str) -> ProductModel:
    return ProductModel.objects.get(model_number=number)


@pytest.mark.parametrize(
    "title",
    [_CPU_0082, "AMD EPYC 9354 32-Core SP5 CPU 100-000000798", "AMD 100-000000798 Processor"],
)
def test_cpu_0082_and_plain_opn_accept_epyc_9354(
    site: SourceSite, seeded_cpus: None, title: str
) -> None:
    listing = _listing(site, "k-" + title.replace(" ", "")[-20:], title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("EPYC 9354")


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz CPU 100-000000805",
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz CPU 100-000000805Open",
    ],
)
def test_p_variant_never_resolves_to_9354(site: SourceSite, seeded_cpus: None, title: str) -> None:
    listing = _listing(site, "p-" + title.replace(" ", "")[-20:], title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] != "accept"
    assert listing.product_model is None


def test_sample_opn_with_qualifier_words_is_vetoed(site: SourceSite, seeded_cpus: None) -> None:
    listing = _listing(site, "qs", "100-000000798-04 AMD EPYC GENOA SP5 ZEN4 9354 32-Core CPU")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["sample"]
    assert listing.product_model is None


def test_dell_locked_epyc_still_resolves_to_its_model(site: SourceSite, seeded_cpus: None) -> None:
    listing = _listing(site, "dell-7742", "AMD EPYC 7742 2.25GHz 64-Core Dell Locked CPU")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("EPYC 7742")


@pytest.mark.parametrize("new_title_suffix", [" Dell Locked", " *UNLOCKED*"])
def test_lock_wording_added_on_reobservation_keeps_the_rung0_prior(
    site: SourceSite, seeded_cpus: None, new_title_suffix: str
) -> None:
    """Lock wording is not identity: a title edit that only adds it leaves the
    identifiers unchanged, so rung 0 inherits the accept (neither a veto nor a
    reconsideration). Its eligibility effect is re-judged separately, because
    the new observation moves the evaluation binding."""
    base = "AMD EPYC 7763 64-Core SP3"
    listing = _listing(site, "lock-edit", base)
    assert _resolve(listing).evidence["outcome"] == "accept"
    Listing.objects.filter(pk=listing.pk).update(title_raw=base + new_title_suffix)
    listing.refresh_from_db()
    _observe(listing, price=Decimal("2499.00"), observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert "reconsidered_prior" not in edge.evidence
    assert listing.product_model == _model("EPYC 7763")


@pytest.mark.parametrize("price", [Decimal("399.00"), Decimal("2499.00"), Decimal("0.99")])
def test_price_never_changes_the_identity_decision(
    site: SourceSite, seeded_cpus: None, price: Decimal
) -> None:
    """cpu-0283 (owner): $399 is implausible for a retail 7763 but the title
    and OPN 100-000000312 identify it; price anomalies belong to eligibility,
    trust and deal review, never to the resolver."""
    listing = _listing(site, f"price-{price}", _CPU_0283, price=price)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("EPYC 7763")


def test_price_change_on_reobservation_keeps_the_decision(
    site: SourceSite, seeded_cpus: None
) -> None:
    listing = _listing(site, "price-drop", _CPU_0283, price=Decimal("2499.00"))
    first = _resolve(listing)
    _observe(listing, price=Decimal("399.00"), observed_at=_OBSERVED_AT + timedelta(hours=1))
    second = _resolve(listing)
    assert second.evidence["outcome"] == first.evidence["outcome"] == "accept"
    assert listing.product_model == _model("EPYC 7763")
