"""Resolver behavior for the non-drive categories (MS2-D-05, MS2-D-21, plan B3).

Covers the three category gates the resolver applies after `ladder.decide` —
cross-category guard, acceptance policy, auto-accept flag — plus server
no-variant, the per-category veto on the live path, and the category-change edge.

Auto-accept is OFF for gpu/ram/cpu at merge, so every test that needs to see the
acceptance policy (not the flag) decide an outcome enables it through a test-only
registration (`auto_accept_on`), exactly as the plan's F-09 tests prescribe.
Fixtures are local so the frozen resolver test files stay untouched."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    CpuSpec,
    DriveSpec,
    GpuSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RamSpec,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import categories
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver

_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
_A100_TITLE = "NVIDIA A100 80GB PCIe Passive Used"
# No condition word: a model-grain accept stays at model grain, so rung-0 tests
# can compare the listing's model directly.
_A100_BARE = "NVIDIA A100 80GB PCIe Passive"
_RAM_TITLE = "Samsung 32GB DDR4-3200 ECC RDIMM 2Rx4 M393A4K40DB3-CWE Used"
_CPU_TITLE = "Intel Xeon Gold 6448Y 32-Core FCLGA4677 Used"

_RETENTION_FOR_KIND = {
    AliasSourceKind.CATALOG_AUTHORITATIVE: RetentionClass.MANUFACTURER_REFERENCE,
    AliasSourceKind.MANUAL: RetentionClass.MANUFACTURER_REFERENCE,
    AliasSourceKind.LISTING_DERIVED: RetentionClass.LISTING_DERIVED_ALIAS,
}


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _family(category: str, manufacturer: Manufacturer, name: str) -> ProductFamily:
    return ProductFamily.objects.create(
        category=Category.objects.get(slug=category),
        manufacturer=manufacturer,
        name=name,
        normalized_name=canonicalize_title(name),
    )


def _manufacturer(key: str) -> Manufacturer:
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name=key, defaults={"name": key.title()}
    )
    return manufacturer


def _model(family: ProductFamily, model_number: str) -> ProductModel:
    return ProductModel.objects.create(
        manufacturer=family.manufacturer,
        product_family=family,
        model_number=model_number,
        normalized_model_number=normalize_alias_text(model_number),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )


def _alias(
    text: str,
    *,
    model: ProductModel | None = None,
    family: ProductFamily | None = None,
    source_kind: str = AliasSourceKind.CATALOG_AUTHORITATIVE,
    alias_type: str = AliasType.MPN,
) -> ProductAlias:
    return ProductAlias.objects.create(
        alias_type=alias_type,
        normalized_alias_text=normalize_alias_text(text),
        product_model=model,
        product_family=family,
        source_kind=source_kind,
        retention_class=_RETENTION_FOR_KIND[AliasSourceKind(source_kind)],
    )


@pytest.fixture
def a100(db: None) -> ProductModel:
    model = _model(_family("gpu", _manufacturer("nvidia"), "Data Center GPU"), "A100 80GB PCIe")
    GpuSpec.objects.create(
        product_model=model,
        chip_vendor="nvidia",
        vram_gb=80,
        interface="pcie",
        cooling="passive",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


@pytest.fixture
def samsung_rdimm(db: None) -> ProductModel:
    model = _model(_family("ram", _manufacturer("samsung"), "DDR4 RDIMM"), "M393A4K40DB3-CWE")
    RamSpec.objects.create(
        product_model=model,
        generation="ddr4",
        module_type="rdimm",
        ecc=True,
        module_capacity_gb=32,
        speed_mts=3200,
        ranks=2,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


@pytest.fixture
def xeon(db: None) -> ProductModel:
    model = _model(_family("cpu", _manufacturer("intel"), "Xeon Scalable"), "Xeon Gold 6448Y")
    CpuSpec.objects.create(
        product_model=model,
        socket="fclga4677",
        cores=32,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


@pytest.fixture
def auto_accept_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Test-only registration: gpu/ram/cpu with auto_accept=True and everything
    else — including the acceptance policy — exactly as registered."""
    for slug in ("gpu", "ram", "cpu"):
        rules = categories.rules_for(slug)
        assert rules is not None and rules.acceptance is not None
        monkeypatch.setitem(
            categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - the test-only registration the plan prescribes
            slug,
            lambda rules=rules: replace(rules, auto_accept=True),
        )
    yield


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _snapshot(
    listing: Listing,
    *,
    category_hint: str | None,
    attrs: dict[str, object] | None = None,
    observed_at: datetime = _OBSERVED_AT,
) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("999.00"),
            attrs=attrs or {},
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
            category_hint=category_hint,
        ),
        observed_at=observed_at,
    )


def _hinted(
    site: SourceSite, key: str, title: str, category: str, *, attrs: dict[str, object] | None = None
) -> Listing:
    listing = _listing(site, key, title)
    _snapshot(listing, category_hint=category, attrs=attrs)
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _edge_count(listing: Listing) -> int:
    return ListingResolution.objects.filter(listing=listing).count()


# --- auto-accept flag (MS2-D-05) -------------------------------------------------


@pytest.mark.parametrize(
    ("category", "fixture", "alias_text", "title"),
    [
        ("gpu", "a100", "A100", _A100_TITLE),
        ("ram", "samsung_rdimm", "M393A4K40DB3-CWE", _RAM_TITLE),
        ("cpu", "xeon", "Xeon Gold 6448Y", _CPU_TITLE),
    ],
)
def test_rung1_authoritative_hit_is_review_while_auto_accept_off(
    site: SourceSite,
    request: pytest.FixtureRequest,
    category: str,
    fixture: str,
    alias_text: str,
    title: str,
) -> None:
    model = request.getfixturevalue(fixture)
    _alias(alias_text, model=model)
    listing = _hinted(site, f"{category}-1", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 1
    assert edge.evidence["auto_accept_disabled"] is True
    assert edge.evidence["category"] == category
    assert "acceptance_policy" not in edge.evidence  # the hit itself was acceptable
    assert edge.grain == ResolutionGrain.NONE
    assert listing.product_model is None
    assert listing.product_variant is None


def test_registered_acceptance_settings() -> None:
    for slug in ("gpu", "ram", "cpu"):
        rules = categories.rules_for(slug)
        assert rules is not None
        assert rules.auto_accept is False
        assert rules.acceptance == categories.NEW_CATEGORY_ACCEPTANCE
    for slug in ("nic", "hba", "motherboard", "server"):
        rules = categories.rules_for(slug)
        assert rules is not None
        assert rules.acceptance == categories.NEW_CATEGORY_ACCEPTANCE
        assert rules.variant_on_demand is (slug != "server")


# --- cross-category guard (MS2-D-05, FR-003) -------------------------------------


@pytest.mark.usefixtures("auto_accept_on")
def test_cross_category_alias_goes_to_review(site: SourceSite) -> None:
    # A CPU box code that a drive-family model also carries (a seeding mistake or a
    # genuine collision): a cpu listing must not merge into the drive catalog even
    # with auto-accept on and an authoritative alias.
    drive_model = _model(_family("drive", _manufacturer("intel"), "Optane"), "P5800X")
    _alias("PK8071305074801", model=drive_model)
    listing = _hinted(site, "x-1", "Intel Xeon Platinum PK8071305074801", "cpu")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["cross_category"] == {"target_category": "drive"}
    assert listing.product_model is None


def test_cross_category_guard_also_protects_drive(site: SourceSite) -> None:
    # The reverse direction on the unhinted legacy path: a drive listing that hits
    # a CPU-family alias is reviewed, not merged into cpu price history.
    cpu_model = _model(_family("cpu", _manufacturer("intel"), "Xeon Scalable"), "Xeon Gold 6448Y")
    _alias("PK8071305074801", model=cpu_model)
    listing = _listing(site, "x-2", "Intel PK8071305074801")
    edge = _resolve(listing)
    assert edge.evidence["category"] == "drive"
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["cross_category"] == {"target_category": "cpu"}


def test_gpu_hint_on_drive_titled_listing_never_reaches_drive_model(site: SourceSite) -> None:
    seagate = _manufacturer("seagate")
    drive_model = _model(_family("drive", seagate, "Exos X16"), "ST16000NM001G")
    DriveSpec.objects.create(
        product_model=drive_model,
        media_type=MediaType.HDD,
        capacity_tb=Decimal(16),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    _alias("ST16000NM001G", model=drive_model)
    listing = _hinted(site, "x-3", "Seagate Exos 16TB ST16000NM001G", "gpu")
    edge = _resolve(listing)
    assert edge.evidence["category"] == "gpu"
    assert edge.product_model_id is None  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs
    assert listing.product_model is None


@pytest.mark.usefixtures("auto_accept_on")
def test_cross_category_prior_goes_to_review(site: SourceSite) -> None:
    # A listing accepted as a drive earlier, now hinted gpu: its drive prior is
    # foreign to the dispatch category and must not be inherited.
    drive_model = _model(_family("drive", _manufacturer("seagate"), "Exos"), "ST4000NM000A")
    listing = _listing(site, "x-4", _A100_BARE)
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=drive_model,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version="test",
        evidence={"outcome": "accept", "rung": 1, "category": "drive"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=drive_model
    )
    _snapshot(listing, category_hint="gpu")
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["cross_category"] == {"target_category": "drive"}
    assert listing.product_model is None


# --- acceptance policy (MS2-D-21) ------------------------------------------------


@pytest.mark.usefixtures("auto_accept_on")
@pytest.mark.parametrize(
    "source_kind",
    [
        AliasSourceKind.CATALOG_AUTHORITATIVE,
        AliasSourceKind.MANUAL,
        AliasSourceKind.LISTING_DERIVED,
    ],
)
def test_non_authoritative_alias_never_auto_accepts_new_category(
    site: SourceSite, a100: ProductModel, source_kind: str
) -> None:
    _alias("A100", model=a100, source_kind=source_kind)
    listing = _hinted(site, f"p-{source_kind}", _A100_TITLE, "gpu")
    edge = _resolve(listing)
    if source_kind == AliasSourceKind.CATALOG_AUTHORITATIVE.value:
        assert edge.evidence["outcome"] == "accept"
        assert edge.method == ResolutionMethod.EXACT_ALIAS
        assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
        # Condition was extracted, so the model-grain hit materialized a variant.
        assert edge.grain == ResolutionGrain.VARIANT
        assert listing.product_variant is not None
        assert listing.product_variant.product_model_id == a100.pk  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs
    else:
        assert edge.evidence["outcome"] == "review"
        assert edge.evidence["acceptance_policy"] == {"source_kind": source_kind, "grain": "model"}
        assert listing.product_model is None
        assert listing.product_variant is None


@pytest.mark.usefixtures("auto_accept_on")
def test_alias_learned_from_earlier_observation_goes_to_review(
    site: SourceSite, samsung_rdimm: ProductModel
) -> None:
    _alias("M393A4K40DB3-CWE", model=samsung_rdimm)
    # Dual-labeled listing: the authoritative Samsung part accepts, and the HPE
    # spares number beside it is learned as a listing_derived OEM alias.
    first = _hinted(
        site, "l-1", "HPE 815100-B21 32GB DDR4-3200 RDIMM Samsung M393A4K40DB3-CWE", "ram"
    )
    assert _resolve(first).evidence["outcome"] == "accept"
    learned = ProductAlias.objects.get(normalized_alias_text="815100b21")
    assert learned.source_kind == AliasSourceKind.LISTING_DERIVED

    # A later listing carrying only the HPE number (asserted in its structured
    # MPN field, so brand evidence is present) hits that learned alias: it must
    # surface as reviewable, never accept and never vanish into `none`. The title
    # names no brand, because 'HPE' would brand-gate away a Samsung-model alias.
    second = _hinted(
        site, "l-2", "815100-B21 32GB DDR4 Server Memory", "ram", attrs={"mpn": "815100-B21"}
    )
    edge = _resolve(second)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["acceptance_policy"] == {
        "source_kind": "listing_derived",
        "grain": "model",
    }
    assert second.product_model is None


@pytest.mark.usefixtures("auto_accept_on")
def test_family_grain_fanout_is_review_for_new_categories(site: SourceSite) -> None:
    hpe = _manufacturer("hpe")
    family = _family("ram", hpe, "HPE SmartMemory DDR4")
    for number in ("P00924-B21-A", "P00924-B21-B"):
        _alias("P00924-B21", model=_model(family, number), alias_type=AliasType.OEM_PN)
    listing = _hinted(site, "f-1", "HPE P00924-B21 32GB DDR4 RDIMM", "ram")
    edge = _resolve(listing)
    # The drive ladder would accept this fan-out at family grain; for a new
    # category the grain is not approved, even with authoritative aliases.
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["oem_fanout"] == 2
    assert edge.evidence["acceptance_policy"] == {
        "source_kind": "catalog_authoritative",
        "grain": "family",
    }
    assert listing.product_family is None


def _accepted_prior(
    listing: Listing, model: ProductModel, *, method: str, evidence: dict[str, object]
) -> None:
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=method,
        confidence=0.95,
        matcher_version="test",
        evidence={"outcome": "accept", "category": "gpu", **evidence},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=0.95
    )


@pytest.mark.parametrize(
    ("method", "evidence"),
    [
        (ResolutionMethod.EXACT_ALIAS, {"rung": 1, "alias_source_kind": "manual"}),
        (ResolutionMethod.EXACT_ALIAS, {"rung": 1, "alias_source_kind": "listing_derived"}),
        # An accept recorded before the policy existed carries no source kind.
        (ResolutionMethod.EXACT_ALIAS, {"rung": 1}),
    ],
)
def test_prior_from_non_authoritative_edge_is_review(
    site: SourceSite, a100: ProductModel, method: str, evidence: dict[str, object]
) -> None:
    listing = _listing(site, "r0-1", _A100_BARE)
    _accepted_prior(listing, a100, method=method, evidence=evidence)
    _snapshot(listing, category_hint="gpu")
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    policy = edge.evidence["acceptance_policy"]
    assert isinstance(policy, dict)
    assert cast("dict[str, object]", policy)["prior_method"] == "exact_alias"
    assert listing.product_model is None


@pytest.mark.parametrize(
    ("method", "evidence"),
    [
        (ResolutionMethod.EXACT_ALIAS, {"rung": 1, "alias_source_kind": "catalog_authoritative"}),
        (ResolutionMethod.MANUAL, {}),
    ],
)
def test_prior_from_authoritative_or_manual_edge_is_inherited(
    site: SourceSite, a100: ProductModel, method: str, evidence: dict[str, object]
) -> None:
    # Rung 0 is not gated by auto_accept (it is off here): inheriting an owner
    # decision or a policy-approved accept is not a new automatic accept.
    listing = _listing(site, "r0-2", _A100_BARE)
    _accepted_prior(listing, a100, method=method, evidence=evidence)
    _snapshot(listing, category_hint="gpu")
    _resolve(listing)
    assert listing.product_model == a100
    assert _edge_count(listing) == 1  # unchanged rung-0 accept: no edge spam


def test_rung0_edge_carries_its_basis_forward(site: SourceSite, a100: ProductModel) -> None:
    # A rung-0 accept that has to write an edge (here: superseding an error edge)
    # records what it inherited, so the NEXT re-observation still finds an
    # authoritative basis instead of a bare `source_alias` method.
    listing = _listing(site, "r0-3", _A100_BARE)
    _accepted_prior(
        listing,
        a100,
        method=ResolutionMethod.EXACT_ALIAS,
        evidence={"rung": 1, "alias_source_kind": "catalog_authoritative"},
    )
    ListingResolution.objects.filter(listing=listing).update(is_current=False)
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.NONE,
        matcher_version="test",
        evidence={"error": "RuntimeError('boom')"},
    )
    _snapshot(listing, category_hint="gpu")
    edge = _resolve(listing)
    assert edge.method == ResolutionMethod.SOURCE_ALIAS
    assert edge.evidence["acceptance_basis"] == "exact_alias"
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.product_model == a100


def test_accepted_manual_alias_still_accepts_for_drive(site: SourceSite) -> None:
    # test_drive_acceptance_unchanged is the A0 baseline oracle
    # (tests/db/test_ms2_decision_baseline.py); this pins the one policy fact it
    # cannot see: drive has no acceptance policy, so a manual alias accepts.
    seagate = _manufacturer("seagate")
    model = _model(_family("drive", seagate, "Exos X16"), "ST16000NM001G")
    _alias("ST16000NM001G", model=model, source_kind=AliasSourceKind.MANUAL)
    listing = _listing(site, "d-1", "Seagate Exos ST16000NM001G Used")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert "alias_source_kind" not in edge.evidence
    assert "acceptance_policy" not in edge.evidence


# --- per-category veto and missing identity evidence -----------------------------


@pytest.mark.usefixtures("auto_accept_on")
@pytest.mark.parametrize(
    ("category", "fixture", "alias_text", "title", "vetoed"),
    [
        ("gpu", "a100", "A100", "NVIDIA A100 40GB PCIe", ["vram_gb"]),
        (
            "ram",
            "samsung_rdimm",
            "M393A4K40DB3-CWE",
            "Samsung M393A4K40DB3-CWE 32GB DDR5 non-ECC",
            ["generation", "ecc"],
        ),
        (
            "cpu",
            "xeon",
            "Xeon Gold 6448Y",
            "Intel Xeon Gold 6448Y 24-Core LGA4189",
            ["socket", "cores"],
        ),
    ],
)
def test_hard_contradiction_vetoes_exact_hit(
    site: SourceSite,
    request: pytest.FixtureRequest,
    category: str,
    fixture: str,
    alias_text: str,
    title: str,
    vetoed: list[str],
) -> None:
    _alias(alias_text, model=request.getfixturevalue(fixture))
    listing = _hinted(site, f"v-{category}", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == vetoed
    assert listing.product_model is None


@pytest.mark.usefixtures("auto_accept_on")
def test_missing_identity_evidence_never_accepts(site: SourceSite, a100: ProductModel) -> None:
    # The alias exists and auto-accept is on, but the title never names the chip
    # vendor or product line, so 'A100' is not a candidate: attribute evidence
    # (80GB, PCIe) alone never produces a target.
    _alias("A100", model=a100)
    listing = _hinted(site, "m-1", "A100 80GB PCIe accelerator", "gpu")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "none"
    assert listing.product_model is None


# --- basic-watch -----------------------------------------------------------------


def test_server_accepts_at_model_grain_without_a_variant(site: SourceSite) -> None:
    model = _model(_family("server", _manufacturer("dell"), "PowerEdge"), "PowerEdge R740xd")
    _alias("R740XD", model=model)
    listing = _hinted(site, "s-1", "Dell PowerEdge R740xd 2x Gold 6148 256GB Used", "server")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    # Condition 'used' was extracted, which would create a variant for any
    # other category; a configured server stays at model grain.
    assert edge.grain == ResolutionGrain.MODEL
    assert listing.product_model == model
    assert not ProductVariant.objects.filter(product_model=model).exists()
    assert "variant_on_demand" not in edge.evidence


def test_basic_watch_variant_control(site: SourceSite) -> None:
    # Same shape for a NIC: variant-on-demand still applies, which proves the
    # server behavior above is its registration and not a basic-watch default.
    model = _model(_family("nic", _manufacturer("mellanox"), "ConnectX-5"), "MCX516A-CCAT")
    _alias("MCX516A-CCAT", model=model)
    listing = _hinted(site, "n-1", "Mellanox ConnectX-5 MCX516A-CCAT 100GbE Used", "nic")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.grain == ResolutionGrain.VARIANT


def test_basic_watch_non_authoritative_alias_is_review(site: SourceSite) -> None:
    model = _model(_family("hba", _manufacturer("broadcom"), "HBA 9400"), "9400-16i")
    _alias("LSI00462", model=model, source_kind=AliasSourceKind.MANUAL)
    listing = _hinted(site, "h-1", "LSI Broadcom 9400-16i LSI00462 HBA", "hba")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["acceptance_policy"] == {"source_kind": "manual", "grain": "model"}


# --- category change -------------------------------------------------------------


def test_category_change_writes_new_edge_even_when_outcome_unchanged(site: SourceSite) -> None:
    listing = _listing(site, "c-1", "Mystery accelerator card 80GB")
    first = _resolve(listing)
    assert first.evidence["outcome"] == "none"
    assert first.evidence["category"] == "drive"

    _snapshot(listing, category_hint="gpu", observed_at=_OBSERVED_AT + timedelta(hours=1))
    second = _resolve(listing)
    assert second.pk != first.pk
    assert second.evidence["outcome"] == "none"
    assert second.evidence["category"] == "gpu"
    assert second.evidence["category_source"] == "hint"
    assert _edge_count(listing) == 2

    # Same category again: the ordinary no-spam rule applies.
    _resolve(listing)
    assert _edge_count(listing) == 2
