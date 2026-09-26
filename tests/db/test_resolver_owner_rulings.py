"""Owner rulings of 2026-09-26 on the live resolver and eligibility paths, over
the shipped drive seeds: Q4 (a lot never auto-accepts, rung 0 included), Q6
(the WD recertified store proves a factory recert), Q7 ("90%NEW" stays at
model grain) and Q8 (a WD model with its own retail PN is one identity)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    Condition,
    EligibilityVerdict,
    Listing,
    ListingResolution,
    ProductAlias,
    ProductModel,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility.evaluate import evaluate_listing
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.matching import vocab
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
# MS-1e wd-0057's store title: no part number, condition "Recertified" only.
_WD_STORE_TITLE = 'WD Red Plus Internal NAS HDD 3.5" - Recertified'
_Q8_TITLE = 'WD Ultrastar DC HC550 18TB HDD SATA 3.5" Enterprise WUH721818ALE6L4 0F38467'


@pytest.fixture
def seeded(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])


@pytest.fixture
def market(seeded: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


@pytest.fixture
def wd_store(seeded: None) -> SourceSite:
    # The migration-0005 row; get_or_create so the test owns no assumption about
    # whether the test DB kept the seed.
    site, _ = SourceSite.objects.get_or_create(
        normalized_name="wd-recertified", defaults={"name": "WD Recertified"}
    )
    return site


def _observe(listing: Listing, *, attrs: dict[str, object], observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("199.00"),
            shipping_price=Decimal(0),
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


def _retitle(listing: Listing, title: str, *, attrs: dict[str, object] | None = None) -> None:
    Listing.objects.filter(pk=listing.pk).update(title_raw=title)
    listing.refresh_from_db()
    _observe(listing, attrs=attrs or {}, observed_at=_OBSERVED_AT + timedelta(hours=1))


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _model(mpn: str) -> ProductModel:
    return ProductModel.objects.get(normalized_model_number=mpn)


def _variant_tuple(listing: Listing) -> tuple[str, str, str, str]:
    variant = listing.product_variant
    assert variant is not None
    return (variant.condition, variant.packaging, variant.recert_channel, variant.warranty_channel)


# ── Q4: lot veto ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "quantity"),
    [
        # MS-1e ebay-0199 (was a for_parts variant accept) and ebay-0282.
        ('Lot of 2 Seagate Exos X14 ST10000NM0478 10TB SATA 3.5" For Parts Low Health', 2),
        ('Lot 10 Supermicro Seagate EXOS AF 7E2000 ST2000NX0253 2TB 7.2K SATA III 2.5" HDD', 10),
    ],
)
def test_lot_listing_reviews_at_rung_one(market: SourceSite, title: str, quantity: int) -> None:
    listing = _listing(market, f"lot-{quantity}", title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 1
    assert edge.evidence["lot"]["quantity"] == quantity  # pyright: ignore[reportIndexIssue] - evidence is a JSON object
    assert listing.product_model is None and listing.product_variant is None


def test_lot_of_one_still_accepts(market: SourceSite) -> None:
    listing = _listing(market, "lot-1", "Lot of 1 Seagate Exos X14 ST10000NM0478 10TB SATA")
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == _model("st10000nm0478")


def test_single_unit_prior_is_not_inherited_by_a_lot(market: SourceSite) -> None:
    listing = _listing(market, "relot", "Seagate Exos X14 ST10000NM0478 10TB SATA")
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == _model("st10000nm0478")

    # Same identifiers, so the identifier check lets rung 0 run; the lot
    # review must stop the inheritance itself.
    _retitle(listing, "Lot of 3 Seagate Exos X14 ST10000NM0478 10TB SATA")
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["lot"]["quantity"] == 3  # pyright: ignore[reportIndexIssue] - evidence is a JSON object
    assert listing.product_model is None

    # And back to a single unit: rung 1 accepts again.
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="Seagate Exos X14 ST10000NM0478 10TB SATA"
    )
    listing.refresh_from_db()
    assert _resolve(listing).evidence["outcome"] == "accept"
    assert listing.product_model == _model("st10000nm0478")


# ── Q7: "90%NEW" ─────────────────────────────────────────────────────────────


def test_percentage_new_resolves_at_model_grain(market: SourceSite) -> None:
    # MS-1e ebay-0261: previously a new-condition variant.
    listing = _listing(
        market,
        "pct-new",
        '90%NEW Seagate Exos 7E10 ST10000NM017B 10TB 7200 RPM 256MB Cache SATA 3.5"',
    )
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.grain == "model"
    assert listing.product_model == _model("st10000nm017b")
    assert listing.product_variant is None


# ── Q6: WD recertified store ─────────────────────────────────────────────────


@pytest.mark.parametrize(("sku", "mpn"), [("RWD20EFPX", "WD20EFPX"), ("RWD60EFPX", "WD60EFPX")])
def test_wd_store_listing_is_a_factory_recert_variant(
    wd_store: SourceSite, sku: str, mpn: str
) -> None:
    listing = _listing(wd_store, sku, _WD_STORE_TITLE, attrs={"mpn": mpn})
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_variant is not None
    assert listing.product_variant.product_model == _model(mpn.lower())
    assert _variant_tuple(listing) == ("recertified", "unknown", "factory", "unknown")


def test_marketplace_recertified_keeps_an_unknown_channel(market: SourceSite) -> None:
    listing = _listing(market, "mkt-recert", "WD Red Plus WD20EFPX 2TB - Recertified")
    _resolve(listing)
    assert _variant_tuple(listing) == ("recertified", "unknown", "unknown", "unknown")


def test_wd_store_title_contradicting_the_store_keeps_its_condition(wd_store: SourceSite) -> None:
    listing = _listing(
        wd_store, "RWD20EFPX-used", "WD Red Plus 2TB - Used", attrs={"mpn": "WD20EFPX"}
    )
    _resolve(listing)
    assert _variant_tuple(listing) == ("used", "unknown", "unknown", "unknown")


def test_wd_store_unknown_channel_prior_is_re_decided(
    wd_store: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A variant decided before the store's provenance was declared: same
    # matcher version and identifiers, so only the variant check can move it.
    with monkeypatch.context() as patch:
        patch.delitem(vocab.SOURCE_OFFER_PROVENANCE, "wd-recertified")
        listing = _listing(wd_store, "RWD20EFPX", _WD_STORE_TITLE, attrs={"mpn": "WD20EFPX"})
        _resolve(listing)
    assert _variant_tuple(listing) == ("recertified", "unknown", "unknown", "unknown")

    _retitle(listing, _WD_STORE_TITLE, attrs={"mpn": "WD20EFPX"})
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"recert_channel": "unknown"},
    }
    assert edge.evidence["outcome"] == "accept"
    assert _variant_tuple(listing) == ("recertified", "unknown", "factory", "unknown")

    # Settled: the next poll inherits without a new edge.
    _resolve(listing)
    assert ListingResolution.objects.filter(listing=listing).count() == 2


def test_eligibility_reads_the_store_condition(wd_store: SourceSite) -> None:
    # The store title states no condition; only the source provenance does.
    listing = _listing(
        wd_store, "RWD20EFPX-bare", 'WD Red Plus Internal NAS HDD 3.5"', attrs={"mpn": "WD20EFPX"}
    )
    _resolve(listing)
    assert _variant_tuple(listing) == ("recertified", "unknown", "factory", "unknown")
    watch = Watch.objects.create(
        name="recert drive", category=Category.objects.get(slug="drive"), enabled=True
    )
    save_requirement(watch, DriveRequirementSpec(allowed_conditions=(Condition.RECERTIFIED,)))

    evaluate_listing(listing.pk)

    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    reasons = {str(r["clause"]): r for r in row.reasons}
    assert reasons["offer.condition"]["outcome"] == "match"
    assert row.verdict == EligibilityVerdict.MATCH


# ── Q8: model MPN + its own retail PN ────────────────────────────────────────


def test_model_with_unseeded_retail_pn_accepts(market: SourceSite) -> None:
    listing = _listing(market, "q8", _Q8_TITLE)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("wuh721818ale6l4")


def test_model_with_its_own_seeded_retail_pn_accepts(market: SourceSite) -> None:
    # A 0F token seeded to a DIFFERENT model reviews instead:
    # test_drive_alias_conflict.test_retail_pn_of_another_model_blocks_rung_zero.
    ProductAlias.objects.create(
        alias_type=AliasType.RETAIL_PN,
        normalized_alias_text=normalize_alias_text("0F38467"),
        product_model=_model("wuh721818ale6l4"),
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(market, "q8-seeded", _Q8_TITLE)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("wuh721818ale6l4")
