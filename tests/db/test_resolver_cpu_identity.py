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
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

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


def test_spaced_mother_board_goes_to_review(site: SourceSite, epyc_7763: ProductModel) -> None:
    """Round-3 R3-E: "Mother Board" is the ordinary spelling of a board listing."""
    listing = _cpu_listing(
        site, "mother-board", "Supermicro H12SSL-i Mother Board + AMD EPYC 7763 64-Core SP3"
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["bundle"]
    assert listing.product_model is None


@pytest.fixture
def seeded_cpus(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped CPU seeds (OPN and bare-number aliases included) with CPU
    auto-accept forced on, as in the epyc_7763 fixture."""
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])
    rules = categories.rules_for("cpu")
    assert rules is not None
    monkeypatch.setitem(
        categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - test-only registration, as in test_resolver_categories.py
        "cpu",
        lambda: replace(rules, auto_accept=True),
    )


def test_two_opns_of_different_models_do_not_inherit_the_prior(
    site: SourceSite, seeded_cpus: None
) -> None:
    """Round-3 N3 residual: two OPNs (9354's and 9654's) name no EPYC model
    number, so the multi_model marker stays empty; rung 0 must still refuse."""
    listing = _cpu_listing(site, "opn-pair", "AMD EPYC 9354 32-Core SP5")
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == ProductModel.objects.get(model_number="EPYC 9354")
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="AMD 100-000000798 / 100-000000789 SP5 processors"
    )
    listing.refresh_from_db()
    _observe(listing, observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["conflicting_alias_models"] == ["100000000789", "100000000798"]
    assert listing.product_model is None


def test_near_model_xeon_number_does_not_reach_the_seeded_model(
    site: SourceSite, seeded_cpus: None
) -> None:
    """Round-3 R3-D: 'Gold 63380' is not 'Gold 6338'."""
    listing = _cpu_listing(site, "xeon-63380", "Intel Xeon Gold 63380 32-Core LGA4189")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] != "accept"
    assert listing.product_model is None
    control = _cpu_listing(site, "xeon-6338", "Intel Xeon Gold 6338 32-Core LGA4189")
    assert _resolve(control).evidence["outcome"] == "accept"


def test_reobserved_title_naming_another_epyc_does_not_inherit(
    site: SourceSite, seeded_cpus: None
) -> None:
    """Round-3 tail T2: the title switched from 7763 to 7742. Its only alias
    hits name 7742, so nothing conflicts among the current identifiers and
    both are 64-core SP3 parts (no veto); the 7763 prior must not be kept."""
    listing = _cpu_listing(site, "7763-then-7742", _BARE_7763)
    assert _resolve(listing).evidence["outcome"] == "accept"
    prior_model = ProductModel.objects.get(model_number="EPYC 7763")
    assert listing.product_model == prior_model
    Listing.objects.filter(pk=listing.pk).update(title_raw="AMD EPYC 7742 64-Core SP3")
    listing.refresh_from_db()
    _observe(listing, observed_at=_OBSERVED_AT + timedelta(hours=1))
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["prior_model_not_named"] == {
        "prior_model_id": prior_model.pk,
        "alias_model_ids": [ProductModel.objects.get(model_number="EPYC 7742").pk],
        "identifiers": ["epyc7742"],
    }
    assert listing.product_model is None
