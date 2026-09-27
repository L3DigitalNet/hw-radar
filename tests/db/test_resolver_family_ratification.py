"""OQ34 family-scoped category ratification on the live resolver path.

A corpus ratifies only what it measured: the owner-audited EPYC corpus makes
AMD EPYC the one CPU family that auto-accepts, and every other CPU family —
Intel Xeon included, whatever its exact authoritative aliases — stays `review`
with `family_not_ratified`. These cases run the real CatalogResolver over the
shipped CPU seeds under the PRODUCTION rules (nothing is re-registered unless a
test says so), and pin the gate order: the AcceptancePolicy and the vetoes speak
first, so an unratified family never masks a policy or identity failure.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    Condition,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RecertChannel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import MATCHER_VERSION, categories, ladder, resolver
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.matching.types import Grain
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_EPYC_7763 = "AMD EPYC 7763 64-Core 2.45GHz SP3 Server CPU"
_XEON_6338 = "Intel Xeon Gold 6338 32-Core 2.0GHz LGA4189 CPU Processor"
_XEON_FAMILY = {"manufacturer": "intel", "family": "xeon scalable"}


@pytest.fixture
def seeded(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=key,
            url=listing.canonical_url,
            title=title,
            price=Decimal("1999.00"),
            attrs={},
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=_OBSERVED_AT,
    )
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _model(model_number: str) -> ProductModel:
    return ProductModel.objects.get(model_number=model_number)


def _lift_family_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = categories.rules_for("cpu")
    assert rules is not None
    monkeypatch.setitem(
        categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - test-only registration, as in test_resolver_categories.py
        "cpu",
        lambda: replace(rules, ratified_families=None),
    )


def _cpu_model(maker: Manufacturer, family: ProductFamily, number: str) -> ProductModel:
    model = ProductModel.objects.create(
        manufacturer=maker,
        product_family=family,
        model_number=number,
        normalized_model_number=normalize_alias_text(number),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text(number),
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


def _family(maker: Manufacturer, name: str) -> ProductFamily:
    return ProductFamily.objects.create(
        category=Category.objects.get(slug="cpu"),
        manufacturer=maker,
        name=name,
        normalized_name=canonicalize_title(name),
    )


# --- ratified family: AMD EPYC ---------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_epyc_authoritative_model_hit_auto_accepts(site: SourceSite) -> None:
    listing = _listing(site, "epyc", _EPYC_7763)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.evidence["rung"] == 1
    assert edge.method == ResolutionMethod.EXACT_ALIAS
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    assert "family_not_ratified" not in edge.evidence
    assert edge.grain == ResolutionGrain.MODEL
    assert listing.product_model == _model("EPYC 7763")


@pytest.mark.usefixtures("seeded")
@pytest.mark.parametrize(
    ("title", "condition"),
    [
        ("AMD EPYC 7763 64-Core 2.45GHz SP3 Server CPU Used", Condition.USED),
        ("AMD EPYC 9354 32-Core 3.25GHz SP5 Server Processor New", Condition.NEW),
    ],
)
def test_epyc_condition_variant_inherits_the_family_ratification(
    site: SourceSite, title: str, condition: Condition
) -> None:
    """An explicit condition materializes a variant under the accepted EPYC
    model; the variant is inside EPYC scope without a ratification of its own."""
    listing = _listing(site, f"variant-{condition}", title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert "family_not_ratified" not in edge.evidence
    assert listing.product_variant is not None
    assert listing.product_variant.condition == condition
    assert listing.product_variant.product_model.product_family is not None
    assert listing.product_variant.product_model.product_family.normalized_name == "epyc"


@pytest.mark.usefixtures("seeded")
def test_epyc_variant_grain_authoritative_alias_auto_accepts(site: SourceSite) -> None:
    model = _model("EPYC 7763")
    variant = ProductVariant.objects.create(
        product_model=model, condition=Condition.RECERTIFIED, recert_channel=RecertChannel.FACTORY
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("EPYC7763-FRC"),
        product_variant=variant,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    target = ladder.TargetRef(
        grain=Grain.VARIANT,
        family_id=model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        model_id=model.pk,
        variant_id=variant.pk,
    )
    rules = categories.rules_for("cpu")
    assert rules is not None
    # A variant is keyed by its model's family, so the EPYC scope admits it.
    assert resolver._unratified_family(rules, target) is None  # pyright: ignore[reportPrivateUsage] - the gate under test


# --- unratified families ---------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_xeon_authoritative_hit_reviews_as_family_not_ratified(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = _listing(site, "xeon", _XEON_6338)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 1
    assert edge.evidence["family_not_ratified"] == {"family": _XEON_FAMILY}
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    # It survived every identity check: no veto, no policy miss, no flag.
    for key in ("veto", "acceptance_policy", "auto_accept_disabled", "conflicting_targets"):
        assert key not in edge.evidence
    assert listing.product_model is None

    # The scope is the only thing that stopped it: lifted, the same listing
    # accepts the right Xeon model.
    _lift_family_scope(monkeypatch)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_model == _model("Xeon Gold 6338")


def test_unratified_amd_family_reviews(site: SourceSite) -> None:
    amd, _ = Manufacturer.objects.get_or_create(normalized_name="amd", defaults={"name": "AMD"})
    _cpu_model(amd, _family(amd, "Ryzen Threadripper PRO"), "Threadripper PRO 7995WX")
    listing = _listing(site, "tr", "AMD Ryzen Threadripper PRO 7995WX 96-Core sTR5 CPU")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["family_not_ratified"] == {
        "family": {"manufacturer": "amd", "family": "ryzen threadripper pro"}
    }
    assert listing.product_model is None


# --- policy and vetoes speak before the family scope ------------------------------


@pytest.mark.usefixtures("seeded")
@pytest.mark.parametrize("source_kind", [AliasSourceKind.LISTING_DERIVED, AliasSourceKind.MANUAL])
def test_non_authoritative_alias_on_epyc_is_a_policy_review(
    site: SourceSite, source_kind: AliasSourceKind
) -> None:
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("EPYC7763X"),
        product_model=_model("EPYC 7763"),
        source_kind=source_kind,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(site, f"nonauth-{source_kind}", "AMD EPYC7763X 64-Core SP3 CPU")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["acceptance_policy"] == {"source_kind": source_kind, "grain": "model"}
    assert "family_not_ratified" not in edge.evidence


@pytest.mark.usefixtures("seeded")
def test_family_grain_alias_on_epyc_is_a_policy_review(site: SourceSite) -> None:
    family = _model("EPYC 7763").product_family
    assert family is not None
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("EPYCFAM01"),
        product_family=family,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(site, "famgrain", "AMD EPYCFAM01 SP3 CPU")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["acceptance_policy"] == {
        "source_kind": "catalog_authoritative",
        "grain": "family",
    }
    assert "family_not_ratified" not in edge.evidence


@pytest.mark.usefixtures("seeded")
@pytest.mark.parametrize(
    ("title", "veto"),
    [
        ("Supermicro H12SSL-i Mother Board + AMD EPYC 7763 64-Core SP3", ["bundle"]),
        ("AMD EPYC 7763 64-Core SP3 Engineering Sample CPU", ["sample"]),
    ],
)
def test_vetoed_epyc_stays_review(site: SourceSite, title: str, veto: list[str]) -> None:
    listing = _listing(site, f"veto-{veto[0]}", title)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == veto
    assert listing.product_model is None


# --- rung 0: inheritance cannot carry an unratified family -------------------------


def _accepted_prior(listing: Listing, model: ProductModel, method: str) -> None:
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=method,
        confidence=0.95,
        matcher_version=MATCHER_VERSION,
        evidence={
            "outcome": "accept",
            "rung": 1,
            "category": "cpu",
            "alias_source_kind": "catalog_authoritative",
            "identity_identifiers": _identifiers(listing.title_raw),
        },
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=0.95
    )


def _identifiers(title: str) -> list[str]:
    rules = categories.rules_for("cpu")
    assert rules is not None
    candidates = rules.extract_candidates(canonicalize_title(title))
    return ladder.identity_identifiers(candidates, [], rules.decode)


@pytest.mark.usefixtures("seeded")
def test_automated_xeon_prior_is_not_inherited(site: SourceSite) -> None:
    """A policy-grade exact-alias accept on Xeon (as an earlier scope could have
    written) is re-reviewed at rung 0 instead of inherited forever."""
    listing = _listing(site, "xeon-prior", _XEON_6338)
    _accepted_prior(listing, _model("Xeon Gold 6338"), ResolutionMethod.EXACT_ALIAS)
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["family_not_ratified"] == {"family": _XEON_FAMILY}
    assert listing.product_model is None


@pytest.mark.usefixtures("seeded")
def test_manual_xeon_prior_still_inherits(site: SourceSite) -> None:
    listing = _listing(site, "xeon-manual", _XEON_6338)
    model = _model("Xeon Gold 6338")
    _accepted_prior(listing, model, ResolutionMethod.MANUAL)
    _resolve(listing)
    assert listing.product_model == model


@pytest.mark.usefixtures("seeded")
def test_automated_epyc_prior_inherits(site: SourceSite) -> None:
    listing = _listing(site, "epyc-prior", _EPYC_7763)
    model = _model("EPYC 7763")
    _accepted_prior(listing, model, ResolutionMethod.EXACT_ALIAS)
    _resolve(listing)
    assert listing.product_model == model
    # An unchanged inherited accept writes no edge; the prior stays current.
    assert ListingResolution.objects.filter(listing=listing).count() == 1


# --- the key lookup cannot be fooled by look-alikes --------------------------------


def _target(model: ProductModel) -> ladder.TargetRef:
    return ladder.TargetRef(
        grain=Grain.MODEL,
        family_id=model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        model_id=model.pk,
    )


def _unratified(model: ProductModel) -> dict[str, object] | None:
    rules = categories.rules_for("cpu")
    assert rules is not None
    return resolver._unratified_family(rules, _target(model))  # pyright: ignore[reportPrivateUsage] - the gate under test


def test_same_family_text_under_another_manufacturer_is_not_ratified(db: None) -> None:
    intel = Manufacturer.objects.create(name="Intel", normalized_name="intel")
    model = _cpu_model(intel, _family(intel, "EPYC"), "EPYC 1234")
    assert _unratified(model) == {"family": {"manufacturer": "intel", "family": "epyc"}}


def test_a_longer_family_name_is_not_a_prefix_match(db: None) -> None:
    amd = Manufacturer.objects.create(name="AMD", normalized_name="amd")
    model = _cpu_model(amd, _family(amd, "EPYC Embedded"), "EPYC 3451")
    assert _unratified(model) == {"family": {"manufacturer": "amd", "family": "epyc embedded"}}


def test_mixed_manufacturer_model_has_no_family_key(db: None) -> None:
    """A model whose own maker differs from its family's maker names neither."""
    amd = Manufacturer.objects.create(name="AMD", normalized_name="amd")
    intel = Manufacturer.objects.create(name="Intel", normalized_name="intel")
    model = _cpu_model(intel, _family(amd, "EPYC"), "EPYC 9999")
    assert _unratified(model) == {"family": None}


def test_family_less_model_is_not_ratified(db: None) -> None:
    amd = Manufacturer.objects.create(name="AMD", normalized_name="amd")
    model = ProductModel.objects.create(
        manufacturer=amd,
        model_number="EPYC 0000",
        normalized_model_number=normalize_alias_text("EPYC 0000"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    assert _unratified(model) == {"family": None}


@pytest.mark.usefixtures("seeded")
def test_seeded_epyc_and_xeon_keys(db: None) -> None:
    assert _unratified(_model("EPYC 9654")) is None
    assert _unratified(_model("Xeon Platinum 8480+")) == {"family": _XEON_FAMILY}


# --- Codex s9 round-1 findings -----------------------------------------------------


def _refile(model: ProductModel, family: ProductFamily) -> None:
    ProductModel.objects.filter(pk=model.pk).update(product_family=family)


@pytest.mark.usefixtures("seeded")
def test_family_that_is_not_a_valid_key_reviews_instead_of_erroring(site: SourceSite) -> None:
    """#1: a stored family whose normalized name is empty (a row the importer
    now rejects) names no ratifiable family. Fresh and previously accepted
    listings both review; neither becomes an error edge that would keep the
    old accepted state."""
    model = _model("EPYC 7763")
    accepted = _listing(site, "was-accepted", _EPYC_7763)
    assert _resolve(accepted).evidence["outcome"] == "accept"
    amd = Manufacturer.objects.get(normalized_name="amd")
    blank = ProductFamily.objects.create(
        category=Category.objects.get(slug="cpu"), manufacturer=amd, name=" ", normalized_name=""
    )
    _refile(model, blank)

    fresh = _listing(site, "fresh", _EPYC_7763)
    edge = _resolve(fresh)
    assert "error" not in edge.evidence
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["family_not_ratified"] == {"family": None}

    edge = _resolve(accepted)
    assert "error" not in edge.evidence
    assert edge.evidence["rung"] == 0
    assert edge.evidence["family_not_ratified"] == {"family": None}
    assert accepted.product_model is None


@pytest.mark.usefixtures("seeded")
def test_refiled_unratified_family_writes_one_new_review(site: SourceSite) -> None:
    """#2: the named family is part of the review fingerprint. A re-poll with
    nothing changed writes no edge; re-filing the model under another
    unratified family writes exactly one edge naming the new family."""
    listing = _listing(site, "refile", _XEON_6338)
    assert _resolve(listing).evidence["family_not_ratified"] == {"family": _XEON_FAMILY}
    _resolve(listing)
    assert ListingResolution.objects.filter(listing=listing).count() == 1

    intel = Manufacturer.objects.get(normalized_name="intel")
    _refile(_model("Xeon Gold 6338"), _family(intel, "Xeon W"))
    edge = _resolve(listing)
    assert edge.evidence["family_not_ratified"] == {
        "family": {"manufacturer": "intel", "family": "xeon w"}
    }
    _resolve(listing)
    assert ListingResolution.objects.filter(listing=listing).count() == 2


@pytest.mark.usefixtures("seeded")
def test_family_grain_automated_prior_is_a_policy_review(site: SourceSite) -> None:
    """#3: rung 0 checks every policy condition, grain included. A family-grain
    exact-alias prior (rung 1 can no longer write one) is not inherited, even
    on the ratified EPYC family; the review is the policy's, not the scope's."""
    listing = _listing(site, "fam-prior", _EPYC_7763)
    family = _model("EPYC 7763").product_family
    assert family is not None
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.95,
        matcher_version=MATCHER_VERSION,
        evidence={
            "outcome": "accept",
            "rung": 1,
            "category": "cpu",
            "alias_source_kind": "catalog_authoritative",
            "identity_identifiers": _identifiers(listing.title_raw),
        },
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.FAMILY, product_family=family, resolution_confidence=0.95
    )
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["acceptance_policy"] == {
        "source_kind": "catalog_authoritative",
        "grain": "family",
        "prior_method": "exact_alias",
    }
    assert "family_not_ratified" not in edge.evidence
