"""Rung 0 inherits an automated accept only for the identifiers it was decided on
(round-4 review R4-A; resolver._prior_reconsideration).

Every case imports the shipped drive seeds: ST12000NE0008 is a seeded IronWolf
Pro model, ST12000NM0008 a seeded Exos model, and ST12000NE0009 is unseeded, so
it resolves only through the `ne` grammar decode to the IronWolf Pro family."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Listing,
    ListingResolution,
    ProductFamily,
    ProductModel,
    ProductVariant,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import MATCHER_VERSION
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def site(db: None) -> SourceSite:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(listing: Listing, observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("199.00"),
            attrs={},
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
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


def _retitle(listing: Listing, title: str, *, hours: int = 1) -> None:
    Listing.objects.filter(pk=listing.pk).update(title_raw=title)
    listing.refresh_from_db()
    _observe(listing, _OBSERVED_AT + timedelta(hours=hours))


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _edges(listing: Listing) -> int:
    return ListingResolution.objects.filter(listing=listing).count()


def _model(mpn: str) -> ProductModel:
    return ProductModel.objects.get(normalized_model_number=mpn)


def _family(name: str) -> ProductFamily:
    return ProductFamily.objects.get(manufacturer__normalized_name="seagate", normalized_name=name)


def test_automated_accept_records_its_identifiers(site: SourceSite) -> None:
    listing = _listing(site, "rec", "Seagate IronWolf Pro ST12000NE0008 12TB")
    edge = _resolve(listing)
    assert edge.evidence["rung"] == 1
    assert edge.evidence["identity_identifiers"] == ["st12000ne0008"]


def test_unchanged_and_cosmetically_edited_titles_still_inherit(site: SourceSite) -> None:
    # No flapping: the same identifier set inherits at rung 0 and writes no edge.
    listing = _listing(site, "same", "Seagate ST12000NE0008 12TB")
    first = _resolve(listing)
    assert _resolve(listing).pk == first.pk
    _retitle(listing, "Seagate IronWolf Pro ST12000NE0008 12TB NAS HDD")
    assert _resolve(listing).pk == first.pk
    assert _edges(listing) == 1
    assert listing.product_model == _model("st12000ne0008")


def test_edited_to_unseeded_near_model_drops_the_seeded_model(site: SourceSite) -> None:
    # R4-A case 1: rung 0 kept the seeded ST12000NE0008 model after the title
    # changed to the unseeded ST12000NE0009, because the new title hit no alias.
    listing = _listing(site, "ne8-ne9", "Seagate ST12000NE0008 12TB")
    assert _resolve(listing).evidence["outcome"] == "accept"
    _retitle(listing, "Seagate ST12000NE0009 12TB")
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "identifiers_changed",
        "prior_identifiers": ["st12000ne0008"],
    }
    # Re-decided as a fresh listing would be: the grammar family, not the model.
    assert edge.evidence["rung"] == 2
    assert listing.product_model is None
    assert listing.product_family == _family("ironwolf pro")
    assert edge.evidence["identity_identifiers"] == ["st12000ne0009"]
    # The re-decision recorded the new set, so the next poll inherits it.
    assert _resolve(listing).pk == edge.pk


def test_family_prior_edited_to_another_familys_alias_is_redecided(site: SourceSite) -> None:
    # R4-A case 2: a family-grain prior has no model, so prior_model_not_named
    # skipped it and the IronWolf Pro decode survived an edit to Exos' MPN.
    listing = _listing(site, "ne9-nm8", "Seagate ST12000NE0009 12TB")
    assert _resolve(listing).evidence["rung"] == 2
    assert listing.product_family == _family("ironwolf pro")
    _retitle(listing, "Seagate ST12000NM0008 12TB")
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "identifiers_changed",
        "prior_identifiers": ["st12000ne0009"],
    }
    assert edge.evidence["rung"] == 1
    assert listing.product_model == _model("st12000nm0008")


def test_removed_identifier_is_redecided(site: SourceSite) -> None:
    listing = _listing(site, "ne8-gone", "Seagate ST12000NE0008 12TB")
    assert _resolve(listing).evidence["outcome"] == "accept"
    _retitle(listing, "Seagate IronWolf Pro 12TB NAS")
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "identifiers_changed",
        "prior_identifiers": ["st12000ne0008"],
    }
    # Nothing identity-bearing is left, so nothing may be asserted.
    assert edge.evidence["outcome"] == "none"
    assert listing.resolution_grain == ResolutionGrain.NONE


def test_unrecorded_same_version_prior_is_redecided_once(site: SourceSite) -> None:
    # An automated edge written before identifiers were recorded (pre-fix
    # 2026.09.2) is re-decided once; the re-decision records its set, even when
    # it confirms the same target, and later polls inherit.
    model = _model("st12000ne0008")
    listing = _listing(site, "legacy", "Seagate ST12000NE0008 12TB")
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "rung": 1, "category": "drive"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=0.98
    )
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {"reason": "identifiers_unrecorded"}
    assert edge.evidence["rung"] == 1
    assert listing.product_model == model
    assert _edges(listing) == 2
    assert _resolve(listing).pk == edge.pk
    assert _edges(listing) == 2


def test_manual_prior_is_never_redecided(site: SourceSite) -> None:
    model = _model("st12000ne0008")
    listing = _listing(site, "manual", "Seagate ST12000NE0009 12TB")
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "category": "drive"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=1.0
    )
    edge = _resolve(listing)
    assert edge.method == ResolutionMethod.MANUAL
    assert listing.product_model == model


# R5-A: a variant-grain prior is only valid for the variant attributes the
# listing asserts. "New" materializes the new-condition variant at rung 1.
_NEW_TITLE = "New Seagate ST12000NE0008 12TB HDD"


def test_condition_edit_to_spares_rematerializes_the_variant(site: SourceSite) -> None:
    # The identifiers stay [st12000ne0008], so R4-A's check kept the prior and
    # rung 0 re-accepted the new-condition variant forever.
    listing = _listing(site, "new-spares", _NEW_TITLE)
    first = _resolve(listing)
    assert first.evidence["variant_attributes"] == {"condition": "new"}
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "new"
    _retitle(listing, "For spares or repair: Seagate ST12000NE0008 12TB HDD")
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": "new"},
    }
    assert edge.evidence["outcome"] == "accept"
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "for_parts"
    assert listing.product_variant.product_model == _model("st12000ne0008")
    assert edge.evidence["variant_attributes"] == {"condition": "for_parts"}
    # The re-decision recorded what it decided on, so the next poll inherits.
    assert _resolve(listing).pk == edge.pk
    assert _edges(listing) == 2


def test_unchanged_and_cosmetic_variant_titles_still_inherit(site: SourceSite) -> None:
    listing = _listing(site, "new-same", _NEW_TITLE)
    first = _resolve(listing)
    assert _resolve(listing).pk == first.pk
    _retitle(listing, "NEW Seagate IronWolf Pro ST12000NE0008 12TB NAS HDD!")
    assert _resolve(listing).pk == first.pk
    assert _edges(listing) == 1
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "new"


def test_dropped_condition_word_still_inherits(site: SourceSite) -> None:
    # An unasserted condition is not evidence against the "new" variant.
    listing = _listing(site, "new-unsaid", _NEW_TITLE)
    first = _resolve(listing)
    _retitle(listing, "Seagate ST12000NE0008 12TB HDD")
    assert _resolve(listing).pk == first.pk
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "new"


def _variant_edge(listing: Listing, condition: str, evidence: dict[str, object]) -> None:
    variant = ProductVariant.objects.create(
        product_model=_model("st12000ne0008"), condition=condition
    )
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.VARIANT,
        product_variant=variant,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version=MATCHER_VERSION,
        evidence={
            "outcome": "accept",
            "rung": 1,
            "category": "drive",
            "identity_identifiers": ["st12000ne0008"],
            **evidence,
        },
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.VARIANT,
        product_variant=variant,
        resolution_confidence=0.98,
    )


def test_variant_decided_on_the_same_assertions_is_not_redecided(site: SourceSite) -> None:
    # A variant-grain alias can land a listing on a variant whose tuple differs
    # from what the title asserts; the origin recorded those assertions, so the
    # difference was already decided and must not re-decide on every poll.
    listing = _listing(site, "alias-variant", "Used Seagate ST12000NE0008 12TB HDD")
    _variant_edge(listing, "new", {"variant_attributes": {"condition": "used"}})
    edge = _resolve(listing)
    assert "reconsidered_prior" not in edge.evidence
    assert _edges(listing) == 1
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "new"


def test_unrecorded_contradicted_variant_is_redecided_once(site: SourceSite) -> None:
    listing = _listing(site, "legacy-variant", "Used Seagate ST12000NE0008 12TB HDD")
    _variant_edge(listing, "new", {})
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": "new"},
    }
    assert listing.product_variant is not None
    assert listing.product_variant.condition == "used"
    assert _resolve(listing).pk == edge.pk
    assert _edges(listing) == 2
