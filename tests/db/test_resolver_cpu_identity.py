"""CPU identity vetoes on the live resolver path (s7 EPYC audit; Codex N3/N4).

Each case runs the real CatalogResolver against a seeded EPYC 7763 with an
authoritative alias and CPU auto-accept forced on, so an ACCEPT here is what a
ratified flip would write. Pins the four fixes made after the first EPYC
measurement: board/bundle listings, codename titles, multi-model re-observation
at rung 0 (N3), and a sample marking carried only in the structured MPN (N4).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    CpuSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import categories
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
# No condition word, so an accept stays at model grain.
_BARE_7763 = "AMD EPYC 7763 64-Core SP3"


@pytest.fixture
def epyc_7763(db: None, monkeypatch: pytest.MonkeyPatch) -> ProductModel:
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name="amd", defaults={"name": "AMD"}
    )
    family = ProductFamily.objects.create(
        category=Category.objects.get(slug="cpu"),
        manufacturer=manufacturer,
        name="EPYC",
        normalized_name=canonicalize_title("EPYC"),
    )
    model = ProductModel.objects.create(
        manufacturer=manufacturer,
        product_family=family,
        model_number="EPYC 7763",
        normalized_model_number=normalize_alias_text("EPYC 7763"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    CpuSpec.objects.create(
        product_model=model,
        socket="sp3",
        cores=64,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("EPYC 7763"),
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    # With auto-accept off every hit is review for the flag alone, which would
    # hide whether the veto fired; these cases need the ratified-flip behavior.
    rules = categories.rules_for("cpu")
    assert rules is not None
    monkeypatch.setitem(
        categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - test-only registration, as in test_resolver_categories.py
        "cpu",
        lambda: replace(rules, auto_accept=True),
    )
    return model


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(
    listing: Listing, *, attrs: dict[str, object] | None = None, observed_at: datetime
) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("2499.00"),
            attrs=attrs or {},
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=observed_at,
    )


def _cpu_listing(
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
    _observe(listing, attrs=attrs, observed_at=_OBSERVED_AT)
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def test_bare_title_accepts_the_seeded_model(site: SourceSite, epyc_7763: ProductModel) -> None:
    # Control: the fixture really accepts, so the review cases below are vetoes.
    listing = _cpu_listing(site, "bare", _BARE_7763)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == epyc_7763


def test_codename_title_accepts_the_seeded_model(site: SourceSite, epyc_7763: ProductModel) -> None:
    listing = _cpu_listing(site, "milan", "AMD EPYC Milan 7763 CPU 64 Cores SP3 Server Processor")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.evidence["rung"] == 1
    assert listing.product_model == epyc_7763


def test_motherboard_bundle_goes_to_review(site: SourceSite, epyc_7763: ProductModel) -> None:
    listing = _cpu_listing(
        site, "bundle", "Supermicro H12DSi-N6 Motherboard With 2x AMD EPYC 7763 64 Core 2.45GHz CPU"
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["bundle"]
    assert listing.product_model is None


def test_multi_model_reobservation_does_not_inherit_the_prior(
    site: SourceSite, epyc_7763: ProductModel
) -> None:
    """Codex N3: the candidate guard alone let rung 0 inherit a current-version
    accept once the title started naming a second model."""
    listing = _cpu_listing(site, "n3", _BARE_7763)
    assert _resolve(listing).evidence["outcome"] == "accept"
    Listing.objects.filter(pk=listing.pk).update(title_raw="AMD EPYC 7763 / 7742 64-Core SP3")
    listing.refresh_from_db()
    _observe(listing, observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["multi_model"]
    assert listing.product_model is None


def test_structured_mpn_sample_marking_goes_to_review(
    site: SourceSite, epyc_7763: ProductModel
) -> None:
    """Codex N4: a retail title over a QS structured MPN must not accept."""
    listing = _cpu_listing(site, "n4", _BARE_7763, attrs={"mpn": "100-000000314-04"})
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["sample"]
    assert listing.product_model is None
