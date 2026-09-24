"""AC-2 (MS-2 Slice C3): persisted eligibility verdicts per (watch, listing) for
drive, gpu, ram and cpu, built on Slice B's curated first-party seeds.

Accepted resolutions are written as MANUAL model-grain edges (the plan allows a
manual edge or a test-only auto-accept registration): gpu/ram/cpu ship with
auto_accept off, so the live resolver would stop at `review` — which is itself
pinned below as `unknown`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    CpuSpec,
    DelistReason,
    DriveSpec,
    EligibilityVerdict,
    GpuCooling,
    Listing,
    ListingResolution,
    OfferSnapshot,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import EVALUATOR_VERSION, catalog_fingerprint, catalog_inputs
from hw_radar.eligibility.evaluate import evaluate_listing
from hw_radar.eligibility.requirements import (
    BasicRequirementSpec,
    CpuRequirementSpec,
    DriveRequirementSpec,
    GpuRequirementSpec,
    RamRequirementSpec,
    RequirementSpec,
    save_requirement,
)
from hw_radar.matching import MATCHER_VERSION
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db

M, N, U = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH, EligibilityVerdict.UNKNOWN
_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(db: None) -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _listing(
    site: SourceSite,
    key: str,
    title: str,
    category: str | None,
    *,
    price: Decimal = Decimal("100.00"),
    stock: str = "in_stock",
    is_international: bool = False,
    observed_at: datetime = _OBSERVED_AT,
) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        is_international=is_international,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _snapshot(listing, category, price=price, stock=stock, observed_at=observed_at)
    return listing


def _snapshot(
    listing: Listing,
    category: str | None,
    *,
    price: Decimal = Decimal("100.00"),
    stock: str = "in_stock",
    observed_at: datetime = _OBSERVED_AT,
) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=price,
            stock_status=stock,
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=listing.is_international,
            category_hint=category,
        ),
        observed_at=observed_at,
    )


def _accept(listing: Listing, model: ProductModel) -> ListingResolution:
    """A manual model-grain accept, applied the way the resolver applies one:
    demote the current edge, insert the new one, point the denorm at it."""
    ListingResolution.objects.filter(listing=listing, is_current=True).update(is_current=False)
    edge = ListingResolution.objects.create(
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
    return edge


def _accept_family(listing: Listing, family: ProductFamily) -> ListingResolution:
    ListingResolution.objects.filter(listing=listing, is_current=True).update(is_current=False)
    return ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept"},
    )


def _watch(category: str, spec: RequirementSpec, *, enabled: bool = True) -> Watch:
    watch = Watch.objects.create(
        name=f"{category} watch", category=Category.objects.get(slug=category), enabled=enabled
    )
    save_requirement(watch, spec)
    return watch


def _model(number: str) -> ProductModel:
    return ProductModel.objects.get(model_number=number)


def _verdict(watch: Watch, listing: Listing) -> EligibilityVerdict:
    return EligibilityVerdict(WatchEvaluation.objects.get(watch=watch, listing=listing).verdict)


def _reasons(watch: Watch, listing: Listing) -> dict[str, dict[str, object]]:
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return {str(r["clause"]): r for r in row.reasons}


# (category, seed model number, title, satisfied spec, contradicted spec)
_CASES: dict[str, tuple[str, str, RequirementSpec, RequirementSpec]] = {
    "drive": (
        "ST16000NM002C",
        "Seagate Exos X16 16TB ST16000NM002C SATA 3.5in CMR",
        DriveRequirementSpec(
            min_capacity_tb=Decimal(16),
            media_types=("hdd",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            interfaces=("sata",),
            form_factors=("3.5",),
            recording_techs=("cmr",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            max_unit_price_usd=Decimal(150),
        ),
        DriveRequirementSpec(min_capacity_tb=Decimal(18)),
    ),
    "gpu": (
        "A100 80GB PCIe",
        "NVIDIA A100 80GB PCIe",
        GpuRequirementSpec(
            min_vram_gb=40,
            chip_vendors=("nvidia",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            interfaces=("pcie",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            coolings=("passive",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            max_tdp_w=300,
        ),
        GpuRequirementSpec(coolings=("active",)),  # pyright: ignore[reportArgumentType] - str coerces to the enum
    ),
    "ram": (
        "MTA36ASF8G72PZ-3G2F2",
        "Micron 64GB DDR4 RDIMM MTA36ASF8G72PZ-3G2F2",
        RamRequirementSpec(
            generations=("ddr4",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            module_types=("rdimm",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            require_ecc=True,
            min_total_capacity_gb=64,
            min_speed_mts=3200,
        ),
        RamRequirementSpec(generations=("ddr5",)),  # pyright: ignore[reportArgumentType] - str coerces to the enum
    ),
    "cpu": (
        "Xeon Gold 6448Y",
        "Intel Xeon Gold 6448Y",
        CpuRequirementSpec(sockets=("LGA4677",), min_cores=32, max_tdp_w=250),
        CpuRequirementSpec(min_cores=48),
    ),
}

# Per category: a constrained requirement whose catalog value is NULL in the
# seed (or is made NULL / absent by `_blank`), so the clause has no evidence.
_MISSING: dict[str, tuple[str, str, RequirementSpec]] = {
    "drive": (
        "ST16000NM002C",
        "Seagate Exos 16TB ST16000NM002C",
        DriveRequirementSpec(recording_techs=("cmr",)),  # pyright: ignore[reportArgumentType] - str coerces to the enum
    ),
    # Seed V100 states no cooling.
    "gpu": (
        "Tesla V100 PCIe (32GB)",
        "NVIDIA Tesla V100 32GB",
        GpuRequirementSpec(coolings=(GpuCooling.PASSIVE,)),
    ),
    # Seed DDR5 module states no speed.
    "ram": (
        "MTC20F2085S1RC52BA1",
        "Micron 32GB DDR5 RDIMM",
        RamRequirementSpec(min_speed_mts=4800),
    ),
    "cpu": ("Xeon Gold 6448Y", "Intel Xeon Gold 6448Y", CpuRequirementSpec(min_cores=16)),
}


def _blank(category: str, model: ProductModel) -> None:
    if category == "drive":
        # A NULL value on an existing spec row.
        DriveSpec.objects.filter(product_model=model).update(recording_tech=None)
    elif category == "cpu":
        # No spec row at all.
        CpuSpec.objects.filter(product_model=model).delete()


@pytest.mark.parametrize("category", list(_CASES))
@pytest.mark.parametrize("expected", [M, N, U])
def test_ac2_verdict_matrix(
    seeded: None, site: SourceSite, category: str, expected: EligibilityVerdict
) -> None:
    number, title, satisfied, contradicted = _CASES[category]
    listing = _listing(site, f"{category}-1", title, category)
    if expected is not U:
        _accept(listing, _model(number))
    watch = _watch(category, contradicted if expected is N else satisfied)

    result = evaluate_listing(listing.pk)

    assert result.verdicts == {watch.pk: expected}
    assert _verdict(watch, listing) is expected


@pytest.mark.parametrize("category", list(_MISSING))
def test_missing_required_catalog_attribute_is_unknown(
    seeded: None, site: SourceSite, category: str
) -> None:
    number, title, spec = _MISSING[category]
    model = _model(number)
    _blank(category, model)
    listing = _listing(site, f"{category}-missing", title, category)
    _accept(listing, model)
    watch = _watch(category, spec)

    evaluate_listing(listing.pk)

    assert _verdict(watch, listing) is U
    unknown = [r for r in _reasons(watch, listing).values() if r["outcome"] == "unknown"]
    assert len(unknown) == 1
    assert unknown[0]["evidence_tier"] == "none"


@pytest.mark.parametrize("category", list(_CASES))
def test_hard_catalog_contradiction_is_no_match(
    seeded: None, site: SourceSite, category: str
) -> None:
    number, title, _, contradicted = _CASES[category]
    listing = _listing(site, f"{category}-contra", title, category)
    _accept(listing, _model(number))
    watch = _watch(category, contradicted)

    evaluate_listing(listing.pk)

    assert _verdict(watch, listing) is N
    failing = [r for r in _reasons(watch, listing).values() if r["outcome"] == "no_match"]
    assert [r["evidence_tier"] for r in failing] == ["catalog"]


def test_soft_threshold_never_changes_the_verdict(seeded: None, site: SourceSite) -> None:
    number, title, satisfied, _ = _CASES["gpu"]
    listing = _listing(site, "gpu-soft", title, "gpu", price=Decimal("9000.00"))
    _accept(listing, _model(number))
    watch = _watch("gpu", satisfied)
    evaluate_listing(listing.pk)
    before = WatchEvaluation.objects.get(watch=watch, listing=listing)

    # A target far below the listed price: the soft threshold is written, the
    # version does not move, and re-evaluation produces the same verdict and
    # reasons (nothing in the reasons mentions the soft threshold).
    save_requirement(watch, satisfied.model_copy(update={"target_unit_price_usd": Decimal("1.00")}))
    evaluate_listing(listing.pk)
    after = WatchEvaluation.objects.get(watch=watch, listing=listing)

    assert before.verdict == after.verdict == M
    assert before.reasons == after.reasons
    assert after.requirement_version == before.requirement_version
    assert all("target_unit_price" not in str(r) for r in after.reasons)


def test_live_resolver_review_is_unknown(seeded: None, site: SourceSite) -> None:
    # gpu ships with auto_accept off: the authoritative seed alias hit becomes
    # a `review` edge, which supplies no catalog evidence. The seed's own
    # aliases are full product names; a short authoritative alias gives the
    # title a rung-1 hit.
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("A100"),
        product_model=_model("A100 80GB PCIe"),
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(site, "gpu-review", "NVIDIA A100 80GB PCIe", "gpu")
    CatalogResolver().resolve_listing(listing.pk)
    edge = ListingResolution.objects.get(listing=listing, is_current=True)
    assert edge.evidence["outcome"] == "review"
    watch = _watch("gpu", _CASES["gpu"][2])

    evaluate_listing(listing.pk)

    assert _verdict(watch, listing) is U
    reasons = _reasons(watch, listing)
    assert reasons["gpu.coolings"]["outcome"] == "unknown"
    assert reasons["gpu.coolings"]["source"]["resolution_outcome"] == "review"  # pyright: ignore[reportIndexIssue] - source is a JSON object


def test_live_resolver_drive_accept_matches(seeded: None, site: SourceSite) -> None:
    listing = _listing(site, "drive-live", _CASES["drive"][1], None)
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.product_model == _model("ST16000NM002C")
    watch = _watch("drive", _CASES["drive"][2])

    evaluate_listing(listing.pk)

    assert _verdict(watch, listing) is M


def test_listing_tier_contradiction_without_catalog_is_no_match(
    seeded: None, site: SourceSite
) -> None:
    listing = _listing(site, "gpu-title", "NVIDIA RTX 4090 24GB PCIe", "gpu")
    watch = _watch("gpu", GpuRequirementSpec(min_vram_gb=40))

    evaluate_listing(listing.pk)

    assert _verdict(watch, listing) is N
    assert _reasons(watch, listing)["gpu.min_vram_gb"]["evidence_tier"] == "listing"


def test_offer_clauses_read_the_latest_snapshot(seeded: None, site: SourceSite) -> None:
    number, title, satisfied, _ = _CASES["gpu"]
    listing = _listing(site, "gpu-offer", title + " Used", "gpu", price=Decimal("1200.00"))
    _accept(listing, _model(number))
    watch = _watch(
        "gpu",
        satisfied.model_copy(
            update={
                "max_unit_price_usd": Decimal("1000.00"),
                "allowed_conditions": ("used",),
                "require_in_stock": True,
            }
        ),
    )
    evaluate_listing(listing.pk)
    assert _verdict(watch, listing) is N
    assert _reasons(watch, listing)["offer.max_unit_price_usd"]["outcome"] == "no_match"

    _snapshot(
        listing, "gpu", price=Decimal("900.00"), observed_at=_OBSERVED_AT + timedelta(hours=1)
    )
    evaluate_listing(listing.pk)
    assert _verdict(watch, listing) is M
    reasons = _reasons(watch, listing)
    assert reasons["offer.condition"]["outcome"] == "match"
    assert reasons["offer.in_stock"]["outcome"] == "match"


def test_unstated_condition_against_allow_list_is_unknown(seeded: None, site: SourceSite) -> None:
    number, title, satisfied, _ = _CASES["gpu"]
    listing = _listing(site, "gpu-cond", title, "gpu")
    _accept(listing, _model(number))
    watch = _watch("gpu", satisfied.model_copy(update={"allowed_conditions": ("new",)}))
    evaluate_listing(listing.pk)
    assert _verdict(watch, listing) is U
    assert _reasons(watch, listing)["offer.condition"]["outcome"] == "unknown"


def test_international_offer_is_no_match_when_disallowed(seeded: None, site: SourceSite) -> None:
    number, title, satisfied, _ = _CASES["cpu"]
    listing = _listing(site, "cpu-intl", title, "cpu", is_international=True)
    _accept(listing, _model(number))
    watch = _watch("cpu", satisfied.model_copy(update={"allow_international": False}))
    evaluate_listing(listing.pk)
    assert _verdict(watch, listing) is N


def test_family_grain_uses_the_agreement_set(seeded: None, site: SourceSite) -> None:
    family = _model("GeForce RTX 4090").product_family
    assert family is not None
    listing = _listing(site, "gpu-family", "NVIDIA GeForce RTX", "gpu")
    _accept_family(listing, family)
    # Seeded GeForce RTX members agree on 24 GB and active cooling but differ
    # on TDP (350 W vs 450 W).
    agreed = _watch("gpu", GpuRequirementSpec(min_vram_gb=24, coolings=("active",)))  # pyright: ignore[reportArgumentType] - str coerces to the enum
    disagreed = _watch("gpu", GpuRequirementSpec(max_tdp_w=500))
    contradicted = _watch("gpu", GpuRequirementSpec(coolings=("passive",)))  # pyright: ignore[reportArgumentType] - str coerces to the enum

    evaluate_listing(listing.pk)

    assert _verdict(agreed, listing) is M
    # Both members would pass, but a disagreeing field is unknown by the
    # agreement-set rule: the listing could be either member.
    assert _verdict(disagreed, listing) is U
    assert _verdict(contradicted, listing) is N


def test_basic_watch_target_clause(seeded: None, site: SourceSite) -> None:
    nic = Category.objects.get(slug="nic")
    family = ProductFamily.objects.create(
        category=nic,
        manufacturer=_model("Xeon Gold 6448Y").manufacturer,
        name="Ethernet",
        normalized_name="ethernet",
    )
    target, other = (
        ProductModel.objects.create(
            manufacturer=family.manufacturer,
            product_family=family,
            model_number=number,
            normalized_model_number=number.lower(),
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
        )
        for number in ("X710-DA2", "E810-CQDA2")
    )
    hit = _listing(site, "nic-hit", "Intel X710-DA2", "nic")
    miss = _listing(site, "nic-miss", "Intel E810-CQDA2", "nic")
    unresolved = _listing(site, "nic-none", "Intel NIC", "nic")
    _accept(hit, target)
    _accept(miss, other)
    watch = _watch("nic", BasicRequirementSpec(category="nic", target_model_id=target.pk))

    for listing in (hit, miss, unresolved):
        evaluate_listing(listing.pk)

    assert _verdict(watch, hit) is M
    assert _verdict(watch, miss) is N
    assert _verdict(watch, unresolved) is U


def test_only_enabled_watches_of_the_dispatch_category(seeded: None, site: SourceSite) -> None:
    number, title, satisfied, _ = _CASES["gpu"]
    listing = _listing(site, "gpu-scope", title, "gpu")
    _accept(listing, _model(number))
    enabled = _watch("gpu", satisfied)
    disabled = _watch("gpu", satisfied, enabled=False)
    other_category = _watch("cpu", CpuRequirementSpec())

    result = evaluate_listing(listing.pk)

    assert set(result.verdicts) == {enabled.pk}
    assert not WatchEvaluation.objects.filter(watch__in=[disabled, other_category]).exists()


def test_no_watches_writes_nothing(seeded: None, site: SourceSite) -> None:
    listing = _listing(site, "ram-lonely", "Micron 64GB DDR4", "ram")
    result = evaluate_listing(listing.pk)
    assert result.skipped is None
    assert result.verdicts == {}
    assert not WatchEvaluation.objects.exists()


def test_delisted_and_expired_listings_are_not_evaluated(seeded: None, site: SourceSite) -> None:
    watch = _watch("gpu", GpuRequirementSpec())
    delisted = _listing(site, "gpu-gone", "NVIDIA A100", "gpu")
    delisted.mark_delisted(DelistReason.ABSENT_FROM_SWEEP)
    expired = _listing(site, "gpu-old", "NVIDIA A100", "gpu")
    Listing.objects.filter(pk=expired.pk).update(
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert evaluate_listing(delisted.pk).skipped == "delisted"
    assert evaluate_listing(expired.pk).skipped == "expired"
    assert not WatchEvaluation.objects.filter(watch=watch).exists()


def test_row_binds_inputs_mirrors_retention_and_references_evidence(
    seeded: None, site: SourceSite
) -> None:
    number, title, satisfied, _ = _CASES["gpu"]
    listing = _listing(site, "gpu-bind", title, "gpu")
    expires = timezone.now() + timedelta(hours=6)
    Listing.objects.filter(pk=listing.pk).update(
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION, expires_at=expires
    )
    edge = _accept(listing, _model(number))
    watch = _watch("gpu", satisfied)

    evaluate_listing(listing.pk)
    evaluate_listing(listing.pk)  # re-evaluation overwrites in place

    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    assert WatchEvaluation.objects.filter(watch=watch, listing=listing).count() == 1
    assert row.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert row.expires_at == expires
    assert row.requirement_version == watch.requirement_version
    assert row.evaluator_version == EVALUATOR_VERSION
    assert row.snapshot_observed_at == _OBSERVED_AT
    assert row.resolution == edge
    assert row.catalog_fingerprint == catalog_fingerprint(catalog_inputs(listing))
    reasons = _reasons(watch, listing)
    assert reasons["offer.max_unit_price_usd"]["source"] == {
        "snapshot_observed_at": _OBSERVED_AT.isoformat()
    }
    product_source = reasons["gpu.coolings"]["source"]
    assert product_source["resolution_id"] == edge.pk  # pyright: ignore[reportIndexIssue] - source is a JSON object
    assert product_source["model_id"] == _model(number).pk  # pyright: ignore[reportIndexIssue] - source is a JSON object
    assert product_source["catalog_fingerprint"] == row.catalog_fingerprint  # pyright: ignore[reportIndexIssue] - source is a JSON object


def test_listing_without_snapshot_has_unknown_offer_evidence(
    seeded: None, site: SourceSite
) -> None:
    listing = _listing(site, "drive-bare", _CASES["drive"][1], None)
    OfferSnapshot.objects.filter(listing=listing).delete()
    _accept(listing, _model("ST16000NM002C"))
    watch = _watch("drive", _CASES["drive"][2])

    evaluate_listing(listing.pk)

    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    assert row.verdict == U
    assert row.snapshot_observed_at is None
    assert _reasons(watch, listing)["offer.max_unit_price_usd"]["outcome"] == "unknown"


def test_malformed_category_hint_raises_and_writes_nothing(seeded: None, site: SourceSite) -> None:
    _watch("drive", DriveRequirementSpec())
    listing = _listing(site, "drive-garbled", "Seagate 16TB", None)
    OfferSnapshot.objects.filter(listing=listing).update(attrs_json={"category_hint": 7})

    with pytest.raises(ValueError, match="category hint"):
        evaluate_listing(listing.pk)
    assert not WatchEvaluation.objects.exists()
