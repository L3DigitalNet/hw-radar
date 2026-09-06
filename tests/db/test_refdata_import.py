"""ADR-0018 importer semantics: DR-009 stamping, idempotence, provisional-family
adoption, discontinued retention, conflict fail-into-review, SanDisk/WD
cross-manufacturer collision detection."""

from decimal import Decimal

import pytest

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    DriveSpec,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RetentionClass,
)
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import ImportConflictError, import_documents

pytestmark = pytest.mark.django_db


@pytest.fixture
def docs() -> list[SeedDocument]:  # the real repo corpus — the fixtures ARE the seed
    return load_seed_documents()


def test_import_writes_the_full_corpus_with_dr009_stamps(docs: list[SeedDocument]) -> None:
    report = import_documents(docs)
    assert report.models_created == 15
    assert report.aliases_created == 17
    assert ProductModel.objects.count() == 15
    assert DriveSpec.objects.count() == 15
    for model in ProductModel.objects.all():
        assert model.retention_class == RetentionClass.MANUFACTURER_REFERENCE
        assert model.expires_at is None
    for alias in ProductAlias.objects.all():
        assert alias.retention_class == RetentionClass.MANUFACTURER_REFERENCE
        assert alias.source_kind == AliasSourceKind.CATALOG_AUTHORITATIVE
        assert alias.source_site is None
    spec = DriveSpec.objects.get(product_model__model_number="WUH721818ALE6L4")
    assert spec.capacity_tb == Decimal("18")
    assert spec.cache_mb == 512


def test_import_is_idempotent(docs: list[SeedDocument]) -> None:
    import_documents(docs)
    report = import_documents(docs)
    assert report.models_created == 0
    assert report.aliases_created == 0
    assert ProductModel.objects.count() == 15
    assert ProductAlias.objects.count() == 17


def test_import_adopts_rung2_provisional_family(docs: list[SeedDocument]) -> None:
    # Simulate MS-1b: rung 2 materialized a provisional broad 'exos' family.
    seagate = Manufacturer.objects.create(name="Seagate", normalized_name="seagate")
    category = Category.objects.get_or_create(slug="drive", defaults={"name": "Drive"})[0]
    provisional = ProductFamily.objects.create(
        manufacturer=seagate, category=category, name="Exos", normalized_name="exos"
    )
    report = import_documents(docs)
    assert "exos" in report.families_adopted
    adopted = ProductFamily.objects.get(manufacturer=seagate, normalized_name="exos")
    assert adopted.pk == provisional.pk  # same row, no duplicate
    assert adopted.models.count() == 6  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub


def test_unadopted_provisional_families_are_reported_not_touched(docs: list[SeedDocument]) -> None:
    seagate = Manufacturer.objects.create(name="Seagate", normalized_name="seagate")
    category = Category.objects.get_or_create(slug="drive", defaults={"name": "Drive"})[0]
    ProductFamily.objects.create(
        manufacturer=seagate, category=category, name="Barracuda", normalized_name="barracuda"
    )
    report = import_documents(docs)
    assert "barracuda" in report.unreconciled_families
    assert ProductFamily.objects.filter(normalized_name="barracuda").exists()


def test_discontinued_model_is_retained_on_refresh(docs: list[SeedDocument]) -> None:
    import_documents(docs)
    # Simulate the vendor dropping the 12TB IronWolf Pro from a later datasheet.
    trimmed = [
        doc.model_copy(
            update={"models": tuple(m for m in doc.models if m.model_number != "ST12000NE0008")}
        )
        if doc.family_name == "IronWolf Pro"
        else doc
        for doc in docs
    ]
    import_documents(trimmed)
    assert ProductModel.objects.filter(model_number="ST12000NE0008").exists()  # DR-009


def test_existing_listing_derived_alias_same_target_is_adopted(docs: list[SeedDocument]) -> None:
    report = import_documents(docs)
    model = ProductModel.objects.get(model_number="ST16000NE000")
    alias = ProductAlias.objects.get(normalized_alias_text=normalize_alias_text("ST16000NE000"))
    # Pre-existing listing_derived alias at the same target upgrades in place —
    # simulate by downgrading, then re-importing. The downgrade uses the real
    # resolver-written shape (OQ22: listing_derived_alias, expires_at NULL), and
    # this import is the ONE promotion to manufacturer_reference the class
    # permits: a first-party datasheet corroborating the learned token.
    alias.source_kind = AliasSourceKind.LISTING_DERIVED
    alias.retention_class = RetentionClass.LISTING_DERIVED_ALIAS
    alias.save(update_fields=["source_kind", "retention_class"])
    report = import_documents(docs)
    alias.refresh_from_db()
    assert alias.source_kind == AliasSourceKind.CATALOG_AUTHORITATIVE
    assert alias.retention_class == RetentionClass.MANUFACTURER_REFERENCE
    assert alias.product_model_id == model.pk  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
    assert report.aliases_adopted >= 1


def test_alias_pointing_at_a_different_target_fails_into_review(docs: list[SeedDocument]) -> None:
    # Generic cross-manufacturer collision: a same-key global alias under a
    # DIFFERENT manufacturer/model aborts the whole import — nothing written.
    samsung = Manufacturer.objects.create(name="Samsung", normalized_name="samsung")
    stranger = ProductModel.objects.create(
        manufacturer=samsung,
        model_number="NOT-A-WD-DRIVE",
        normalized_model_number=normalize_alias_text("NOT-A-WD-DRIVE"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("WUH721818ALE6L4"),
        product_model=stranger,
        source_kind=AliasSourceKind.LISTING_DERIVED,
        retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
    )
    before = ProductModel.objects.count()
    with pytest.raises(ImportConflictError) as excinfo:
        import_documents(docs)
    assert any("wuh721818ale6l4" in c for c in excinfo.value.conflicts)
    assert any("cross-brand" in c for c in excinfo.value.conflicts)
    assert ProductModel.objects.count() == before  # transaction rolled back


def test_brand_equivalent_collision_is_flagged_as_such(docs: list[SeedDocument]) -> None:
    # D7: the SanDisk↔WD lineage path — the collision still aborts (different
    # target), but the descriptor says 'brand-equivalent' so review knows this
    # is the Optimus-rebrand case, not a random cross-brand clash. Real-corpus
    # SanDisk↔WD verification stays deferred to the first SSD seed.
    sandisk = Manufacturer.objects.create(name="SanDisk", normalized_name="sandisk")
    rebrand = ProductModel.objects.create(
        manufacturer=sandisk,
        model_number="WUH721818ALE6L4-SD",
        normalized_model_number=normalize_alias_text("WUH721818ALE6L4-SD"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("WUH721818ALE6L4"),
        product_model=rebrand,
        source_kind=AliasSourceKind.LISTING_DERIVED,
        retention_class=RetentionClass.LISTING_DERIVED_ALIAS,
    )
    with pytest.raises(ImportConflictError) as excinfo:
        import_documents(docs)
    assert any("brand-equivalent" in c and "wuh721818ale6l4" in c for c in excinfo.value.conflicts)
