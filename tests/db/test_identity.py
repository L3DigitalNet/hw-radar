from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    Condition,
    DriveSpec,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RecertChannel,
    RetentionClass,
    SourceSite,
    SourceType,
)


@pytest.fixture
def seagate(db: None) -> Manufacturer:
    return Manufacturer.objects.create(name="Seagate", normalized_name="seagate")


@pytest.fixture
def exos_16tb(seagate: Manufacturer) -> ProductModel:
    return ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )


def test_drive_category_is_seeded(db: None) -> None:
    assert Category.objects.filter(slug="drive").exists()


def test_recert_and_new_are_one_model_two_variants(exos_16tb: ProductModel) -> None:
    # FR-003 MS-1 acceptance, resolver-driven since MS-1b: a recert and a new
    # listing of the same drive resolve to ONE product_model, TWO variants.
    from hw_radar.catalog.models import (
        AliasSourceKind,
        AliasType,
        DriveSpec,
        Listing,
        MediaType,
        ProductAlias,
        RetentionClass,
        SourceSite,
    )
    from hw_radar.matching.resolver import CatalogResolver

    DriveSpec.objects.create(
        product_model=exos_16tb,
        media_type=MediaType.HDD,
        capacity_tb="16.000",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text="st16000nm001g",
        product_model=exos_16tb,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    site = SourceSite.objects.create(name="FR3", normalized_name="fr3")
    resolver = CatalogResolver()
    for key, title in (
        ("r", "Seagate Exos 16TB ST16000NM001G Factory Recertified"),
        ("n", "Seagate Exos 16TB ST16000NM001G Brand New"),
    ):
        listing = Listing.objects.create(
            source_site=site,
            source_listing_key=key,
            canonical_url=f"https://example.test/{key}",
            url_hash=key,
            title_raw=title,
            retention_class=RetentionClass.MERCHANT_FACT,
        )
        resolver.resolve_listing(listing.pk)
    assert ProductModel.objects.count() == 1
    assert ProductVariant.objects.filter(product_model=exos_16tb).count() == 2


def test_model_identity_anchor_is_unique(seagate: Manufacturer, exos_16tb: ProductModel) -> None:
    with pytest.raises(IntegrityError):
        ProductModel.objects.create(
            manufacturer=seagate,
            model_number="ST16000NM001G (OEM)",
            normalized_model_number="st16000nm001g",
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
        )


def test_duplicate_variant_rejected(exos_16tb: ProductModel) -> None:
    ProductVariant.objects.create(product_model=exos_16tb, condition=Condition.NEW)
    with pytest.raises(IntegrityError):
        ProductVariant.objects.create(product_model=exos_16tb, condition=Condition.NEW)


def test_drive_spec_is_one_to_one_satellite(exos_16tb: ProductModel) -> None:
    DriveSpec.objects.create(
        product_model=exos_16tb,
        media_type=MediaType.HDD,
        capacity_tb="16.000",
        spec_json={"helium": True},
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    assert DriveSpec.objects.get(product_model=exos_16tb).media_type == MediaType.HDD


def test_alias_requires_exactly_one_grain(seagate: Manufacturer, exos_16tb: ProductModel) -> None:
    family = ProductFamily.objects.create(
        category=Category.objects.get(slug="drive"),
        manufacturer=seagate,
        name="Exos X16",
        normalized_name="exos x16",
    )
    ProductAlias.objects.create(
        alias_type=AliasType.OEM_PN,
        normalized_alias_text="wd-oem-0001",
        product_family=family,
        source_kind=AliasSourceKind.MANUAL,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.MPN,
            normalized_alias_text="st16000nm001g",
            product_model=exos_16tb,
            product_family=family,
            source_kind=AliasSourceKind.MANUAL,
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
        )


def test_alias_requires_at_least_one_grain(db: None) -> None:
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.GTIN,
            normalized_alias_text="0012345678905",
            source_kind=AliasSourceKind.MANUAL,
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
        )


def test_alias_supports_variant_grain(exos_16tb: ProductModel) -> None:
    variant = ProductVariant.objects.create(
        product_model=exos_16tb,
        condition=Condition.RECERTIFIED,
        recert_channel=RecertChannel.FACTORY,
    )
    alias = ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text="st16000nm001g-recert-sku",
        product_variant=variant,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    assert alias.product_variant == variant


def test_alias_is_marketplace_local(exos_16tb: ProductModel) -> None:
    amazon = SourceSite.objects.create(
        name="Amazon", normalized_name="amazon", source_type=SourceType.MARKETPLACE
    )
    # normalized_name is distinct from the "ebay" key migration 0005 seeds into
    # SourceConfig/SourceSite, so this test-local site doesn't collide with it.
    ebay = SourceSite.objects.create(
        name="eBay", normalized_name="ebay-test", source_type=SourceType.MARKETPLACE
    )
    for site in (amazon, ebay):
        ProductAlias.objects.create(
            alias_type=AliasType.ASIN,
            normalized_alias_text="b08x123456",
            product_model=exos_16tb,
            source_site=site,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
        )
    assert ProductAlias.objects.filter(normalized_alias_text="b08x123456").count() == 2
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.ASIN,
            normalized_alias_text="b08x123456",
            product_model=exos_16tb,
            source_site=amazon,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
        )


def test_identifier_alias_cannot_point_at_two_targets(
    seagate: Manufacturer, exos_16tb: ProductModel
) -> None:
    amazon = SourceSite.objects.create(
        name="Amazon2", normalized_name="amazon2", source_type=SourceType.MARKETPLACE
    )
    other_model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST18000NM000J",
        normalized_model_number="st18000nm000j",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.ASIN,
        normalized_alias_text="b09y654321",
        product_model=exos_16tb,
        source_site=amazon,
        source_kind=AliasSourceKind.LISTING_DERIVED,
        retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
    )
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.ASIN,
            normalized_alias_text="b09y654321",
            product_model=other_model,
            source_site=amazon,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
        )


def test_oem_alias_may_map_to_multiple_models(
    seagate: Manufacturer, exos_16tb: ProductModel
) -> None:
    other_model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST16000NM002G",
        normalized_model_number="st16000nm002g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    for model in (exos_16tb, other_model):
        ProductAlias.objects.create(
            alias_type=AliasType.OEM_PN,
            normalized_alias_text="dell-0f1w2x3",
            product_model=model,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
        )
    assert ProductAlias.objects.filter(normalized_alias_text="dell-0f1w2x3").count() == 2


def test_oem_alias_rejected_at_variant_grain(exos_16tb: ProductModel) -> None:
    variant = ProductVariant.objects.create(product_model=exos_16tb, condition=Condition.NEW)
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.OEM_PN,
            normalized_alias_text="hp-mb016000gwxyz",
            product_variant=variant,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
        )


def test_reference_tables_carry_retention_columns(exos_16tb: ProductModel) -> None:
    for model_cls in (ProductModel, DriveSpec, ProductAlias):
        field_names = {f.name for f in model_cls._meta.get_fields()}
        assert {"retention_class", "expires_at"} <= field_names, model_cls.__name__


def test_identity_row_without_a_retention_class_is_rejected(seagate: Manufacturer) -> None:
    # DR-001 at the database, not by convention (OQ22, migration 0017). Before
    # this CHECK the identity tables accepted classless rows, which the hourly
    # purge_expired sweep can neither retire nor report — a silent retention gap.
    with pytest.raises(IntegrityError):
        ProductModel.objects.create(
            manufacturer=seagate,
            model_number="ST0000NOCLASS",
            normalized_model_number="st0000noclass",
        )


def test_learned_alias_class_rejects_an_expiry(exos_16tb: ProductModel) -> None:
    # listing_derived_alias is indefinite: a TTL on a learned alias would let the
    # sweep delete it and re-open the ADR-0019 rule 7 review queue it settled.
    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(
            alias_type=AliasType.OEM_PN,
            normalized_alias_text="learned-with-ttl",
            product_model=exos_16tb,
            source_kind=AliasSourceKind.LISTING_DERIVED,
            retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
            expires_at=timezone.now() + timedelta(hours=6),
        )
