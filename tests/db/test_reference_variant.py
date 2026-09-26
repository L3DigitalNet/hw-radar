"""Reference context through the resolver (review findings F1, F4): a cited
product must never supply the listing's model OR its sellable variant, and the
seller's structured condition label must still materialize the variant."""

from decimal import Decimal

import pytest

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Condition,
    DriveSpec,
    Listing,
    Manufacturer,
    MediaType,
    Packaging,
    ProductAlias,
    ProductModel,
    ProductVariant,
    RecertChannel,
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver

# An exact alias in the committed IronWolf Pro seed
# (src/hw_radar/refdata/seeds/seagate-ironwolf-pro.json), so a leak is the
# strongest rung-1 bait the production catalog offers.
_MPN = "ST12000NE0008"


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


@pytest.fixture
def ironwolf_pro_12tb(db: None) -> ProductModel:
    seagate = Manufacturer.objects.create(name="Seagate", normalized_name="seagate")
    model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number=_MPN,
        normalized_model_number=normalize_alias_text(_MPN),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model,
        media_type=MediaType.HDD,
        capacity_tb=Decimal("12"),
        interface="SATA 6Gb/s",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text(_MPN),
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


def _resolve(site: SourceSite, key: str, title: str, condition_label: str = "") -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        condition_label_raw=condition_label,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return listing


def test_parenthesized_comparison_object_is_not_resolved(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    listing = _resolve(site, "f1a", "WL 12TB comparable to (Seagate ST12000NE0008)", "New")
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_model is None
    assert not ProductVariant.objects.filter(product_model=ironwolf_pro_12tb).exists()


def test_reference_condition_text_creates_no_recertified_variant(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    # F4: 'factory recertified' outranks 'new' in the vocab condition table, so
    # reading it from the reference span filed a New listing as factory recert.
    listing = _resolve(
        site,
        "f4",
        "Seagate ST12000NE0008 12TB comparable to factory recertified drives",
        "New",
    )
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    variant = listing.product_variant
    assert variant is not None
    assert variant.product_model == ironwolf_pro_12tb
    assert variant.condition == Condition.NEW
    assert variant.recert_channel == RecertChannel.UNKNOWN
    assert not ProductVariant.objects.filter(condition=Condition.RECERTIFIED).exists()


def test_parenthetical_qualifier_condition_creates_no_recertified_variant(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    # F4 residual: the span used to end at "(factory)"'s ")", leaving
    # "recertified drives" to outrank the seller's New label.
    listing = _resolve(
        site,
        "f4paren",
        "Seagate ST12000NE0008 12TB comparable to (factory) recertified drives",
        "New",
    )
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    variant = listing.product_variant
    assert variant is not None
    assert variant.product_model == ironwolf_pro_12tb
    assert variant.condition == Condition.NEW
    assert variant.recert_channel == RecertChannel.UNKNOWN
    assert not ProductVariant.objects.filter(condition=Condition.RECERTIFIED).exists()


def test_parenthetical_qualifier_does_not_expose_the_cited_mpn(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    listing = _resolve(site, "f1paren", "WL 12TB comparable to (Seagate) ST12000NE0008", "New")
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_model is None
    assert not ProductVariant.objects.filter(product_model=ironwolf_pro_12tb).exists()


def test_condition_label_after_an_open_ended_span_still_makes_the_variant(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    # The span runs to the end of the title; the label the resolver appends must
    # sit behind a boundary or it is masked with the cited product.
    listing = _resolve(
        site, "label", "Seagate ST12000NE0008 12TB compatible with Synology DS1821+", "New"
    )
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    variant = listing.product_variant
    assert variant is not None
    assert variant.product_model == ironwolf_pro_12tb
    assert variant.condition == Condition.NEW
    assert variant.packaging == Packaging.UNKNOWN


def test_reference_only_condition_without_a_label_stays_model_grain(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    listing = _resolve(site, "nolabel", "Seagate ST12000NE0008 12TB replaces recertified units")
    assert listing.resolution_grain == ResolutionGrain.MODEL
    assert listing.product_model == ironwolf_pro_12tb
    assert listing.product_variant is None
    assert not ProductVariant.objects.exists()


def test_unclosed_paren_comma_does_not_expose_the_cited_mpn(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    # Round-3 F1 residual: the span used to end at the first comma after an
    # unclosed "(", handing the cited exact-alias MPN to rung 1.
    listing = _resolve(site, "f1unclosed", "WL 12TB comparable to (Seagate, ST12000NE0008", "New")
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_model is None
    assert not ProductVariant.objects.filter(product_model=ironwolf_pro_12tb).exists()


def test_unclosed_paren_comma_creates_no_recertified_variant(
    site: SourceSite, ironwolf_pro_12tb: ProductModel
) -> None:
    # Round-3 F4 residual: "factory recertified drives" after the comma
    # outranked the seller's New label.
    listing = _resolve(
        site,
        "f4unclosed",
        "Seagate ST12000NE0008 12TB comparable to (used, factory recertified drives",
        "New",
    )
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    variant = listing.product_variant
    assert variant is not None
    assert variant.product_model == ironwolf_pro_12tb
    assert variant.condition == Condition.NEW
    assert variant.recert_channel == RecertChannel.UNKNOWN
    assert not ProductVariant.objects.filter(condition=Condition.RECERTIFIED).exists()
