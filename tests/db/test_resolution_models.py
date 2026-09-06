"""C.3.3 resolution-state schema: grain/target coherence, append-only supersede."""

import pytest
from django.db import IntegrityError

from hw_radar.catalog.models import (
    Category,
    Condition,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductModel,
    ProductVariant,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo", normalized_name="resdemo")


@pytest.fixture
def listing(site: SourceSite) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key="k1",
        canonical_url="https://example.test/1",
        url_hash="h1",
        title_raw="Seagate Exos 16TB ST16000NM001G",
        retention_class=RetentionClass.MERCHANT_FACT,
    )


@pytest.fixture
def model(db: None) -> ProductModel:
    mfr = Manufacturer.objects.create(name="Seagate", normalized_name="seagate")
    return ProductModel.objects.create(
        manufacturer=mfr,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )


def test_model_grain_edge_holds_only_the_model_fk(listing: Listing, model: ProductModel) -> None:
    edge = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version="test",
    )
    # django-types has no stub for Django's auto-generated `<field>_id`
    # shadow attributes, hence the narrow ignores below.
    # fmt: off
    family_id = edge.product_family_id  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
    variant_id = edge.product_variant_id  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
    # fmt: on
    assert family_id is None and variant_id is None


def test_grain_target_coherence_rejects_mismatch(listing: Listing, model: ProductModel) -> None:
    with pytest.raises(IntegrityError):
        ListingResolution.objects.create(
            listing=listing,
            grain=ResolutionGrain.FAMILY,  # family grain but a model target
            product_model=model,
            method=ResolutionMethod.EXACT_ALIAS,
            confidence=0.9,
            matcher_version="test",
        )


def test_none_grain_rejects_any_target(listing: Listing, model: ProductModel) -> None:
    with pytest.raises(IntegrityError):
        ListingResolution.objects.create(
            listing=listing,
            grain=ResolutionGrain.NONE,
            product_model=model,
            matcher_version="test",
        )


def test_accepted_grain_requires_method_and_confidence(
    listing: Listing, model: ProductModel
) -> None:
    with pytest.raises(IntegrityError):
        ListingResolution.objects.create(
            listing=listing,
            grain=ResolutionGrain.MODEL,
            product_model=model,
            method="",  # accepted grain without a method: incoherent
            matcher_version="test",
        )


def test_supersede_chain_keeps_one_current_edge(listing: Listing, model: ProductModel) -> None:
    # The apply order the resolver must follow: demote-old → insert-new → link-old.
    first = ListingResolution.objects.create(
        listing=listing, grain=ResolutionGrain.NONE, matcher_version="test"
    )
    first.is_current = False
    first.save(update_fields=["is_current"])
    second = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version="test",
    )
    first.superseded_by = second
    first.save(update_fields=["superseded_by"])
    # django-types has no stub for the reverse-FK related manager (Listing has
    # no `resolutions` attribute in its stubs — related_name is runtime-only
    # Django metaclass magic), hence the narrow ignores below.
    current = listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]
        is_current=True
    )
    assert list(current) == [second]  # pyright: ignore[reportUnknownArgumentType]


def test_two_current_edges_for_one_listing_are_impossible(
    listing: Listing, model: ProductModel
) -> None:
    # CR-002: the one-current invariant is a DATABASE constraint, not query
    # discipline — concurrent resolver calls must collide here, not corrupt state.
    ListingResolution.objects.create(
        listing=listing, grain=ResolutionGrain.NONE, matcher_version="test"
    )
    with pytest.raises(IntegrityError):
        ListingResolution.objects.create(
            listing=listing,
            grain=ResolutionGrain.MODEL,
            product_model=model,
            method=ResolutionMethod.EXACT_ALIAS,
            confidence=0.98,
            matcher_version="test",
        )


def test_current_edge_cannot_be_superseded(listing: Listing) -> None:
    first = ListingResolution.objects.create(
        listing=listing, grain=ResolutionGrain.NONE, matcher_version="test"
    )
    first.is_current = False
    first.save(update_fields=["is_current"])
    second = ListingResolution.objects.create(
        listing=listing, grain=ResolutionGrain.NONE, matcher_version="test"
    )
    second.superseded_by = first  # a CURRENT edge pointing at a successor is incoherent
    with pytest.raises(IntegrityError):
        second.save(update_fields=["superseded_by"])


def test_listing_carries_denormalized_resolution_fields(listing: Listing) -> None:
    field_names = {f.name for f in Listing._meta.get_fields()}
    assert {
        "product_family",
        "product_model",
        "product_variant",
        "resolution_grain",
        "resolution_confidence",
    } <= field_names
    assert listing.resolution_grain == ResolutionGrain.NONE


def test_for_parts_condition_supports_variant_on_demand(model: ProductModel) -> None:
    variant = ProductVariant.objects.create(product_model=model, condition=Condition.FOR_PARTS)
    assert variant.condition == "for_parts"


def test_drive_category_still_seeded(db: None) -> None:
    # Rung-2 family materialization depends on the MS-0 seed surviving migration 0006.
    assert Category.objects.filter(slug="drive").exists()
