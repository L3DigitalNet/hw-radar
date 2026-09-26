# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportAttributeAccessIssue=false
# django-types has no reverse-FK manager stubs (ProductFamily.models,
# ProductModel.aliases/drive_spec) — same gap as test_refdata_import.py and
# test_resolution_models.py, hoisted to file level here given the density of
# reverse-relation traversal in these acceptance assertions.
"""MS-1c exit criteria (design doc §MS-1c + ADR-0018 Confirmation): one family
lands with its full per-MPN fan-out; a listing matching a seeded alias inherits
authoritative drive_spec; DR-009 stamping holds; ingest writes no observation
rows."""

from decimal import Decimal

import pytest

from hw_radar.catalog.models import (
    Condition,
    Listing,
    OfferSnapshot,
    ProductFamily,
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def seeded() -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def site() -> SourceSite:
    # "demo" collides with migration 0005's seeded demo row; use a distinct
    # normalized_name (same fix Task 6 applied with "rr-demo").
    return SourceSite.objects.create(name="Demo", normalized_name="acc-demo")


def _listing(site: SourceSite, key: str, title: str, condition: str = "") -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        condition_label_raw=condition,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def test_exos_recertified_lands_as_one_family_with_full_datasheet_fanout() -> None:
    # D2a: the FULL-fan-out acceptance family. The Seagate Exos Recertified
    # data sheet's published ladder (4-28TB, two literals per capacity except
    # 18TB) is exactly these SKUs — complete first-party coverage, unlike HC550
    # (a deliberate subset, tested below). The X-series and 7E documents share
    # the 'Exos' family, so the recert ladder is picked out by its spec flag.
    family = ProductFamily.objects.get(normalized_name="exos")
    recertified = family.models.filter(drive_spec__spec_json__seagate_recertified_sku=True)
    numbers = set(recertified.values_list("model_number", flat=True))
    assert numbers == {
        "ST4000NM000C",
        "ST4000NM001C",
        "ST6000NM000C",
        "ST6000NM001C",
        "ST8000NM000C",
        "ST8000NM001C",
        "ST10000NM000C",
        "ST10000NM001C",
        "ST12000NM002C",
        "ST12000NM003C",
        "ST14000NM002C",
        "ST14000NM003C",
        "ST16000NM002C",
        "ST16000NM003C",
        "ST18000NM002C",
        "ST20000NM002C",
        "ST20000NM004C",
        "ST22000NM000C",
        "ST22000NM001C",
        "ST24000NM000C",
        "ST24000NM001C",
        "ST26000NM000C",
        "ST26000NM001C",
        "ST28000NM000C",
        "ST28000NM001C",
    }
    for model in family.models.all():
        assert model.retention_class == RetentionClass.MANUFACTURER_REFERENCE
        assert model.aliases.filter(is_primary=True).count() == 1


def test_hc550_starter_subset_spans_sata_and_sas_with_retail_pn_aliases() -> None:
    # D2a: HC550 is a BOUNDED STARTER SUBSET of WD's much larger first-party
    # matrix (14/16/18TB, 6 SATA + 9 SAS rows) — never call it full fan-out.
    family = ProductFamily.objects.get(normalized_name="ultrastar dc hc550")
    models = list(family.models.all())
    assert len(models) == 4  # research-evidenced recert-market rows only
    interfaces = set(family.models.values_list("drive_spec__interface", flat=True))
    assert interfaces == {"SATA 6Gb/s", "SAS 12Gb/s"}  # per-MPN interface fan-out
    retail_pns = sum(m.aliases.filter(alias_type="retail_pn").count() for m in models)
    assert retail_pns == 2  # both WD 0F… orderable part numbers


def test_listing_matching_seeded_alias_inherits_authoritative_drive_spec(
    site: SourceSite,
) -> None:
    listing = _listing(site, "acc-1", "Seagate Exos ST20000NM002C 20TB SATA Enterprise HDD")
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.MODEL
    assert listing.product_model is not None
    spec = listing.product_model.drive_spec
    assert spec.capacity_tb == Decimal("20")
    assert spec.sector_format == "512e"
    assert spec.retention_class == RetentionClass.MANUFACTURER_REFERENCE


def test_recert_listing_reaches_variant_grain_with_inherited_spec(
    site: SourceSite,
) -> None:
    listing = _listing(
        site,
        "acc-2",
        "Seagate IronWolf Pro ST20000NE000 20TB SATA NAS HDD",
        condition="Manufacturer Recertified",
    )
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert listing.product_variant is not None
    assert listing.product_variant.condition == Condition.RECERTIFIED
    spec = listing.product_variant.product_model.drive_spec
    assert spec.capacity_tb == Decimal("20")


def test_reference_ingest_writes_no_observation_rows() -> None:
    # ADR-0018 rule 1: catalog ingest stops at persist — the seeded fixture
    # (autouse) must have produced zero snapshots/listings.
    assert OfferSnapshot.objects.count() == 0
    assert Listing.objects.count() == 0
