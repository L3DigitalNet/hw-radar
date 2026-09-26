"""MS-2 Slice B4a/B4b: category-discriminated seed contract and importer.

The drive snapshot in __snapshots__/test_refdata_categories.ambr was recorded by
running test_drive_import_byte_identical against the UNMODIFIED pre-B4 importer
(contracts.py/persist.py at the Slice B base). It imports the committed drive
seeds, so it has exactly one legitimate refresh trigger: a commit that changes
drive seed documents regenerates it in that same commit, and every row the
previous snapshot held must survive verbatim unless the seed change intends
otherwise. Any other diff is a change to what the drive import writes, never a
snapshot to refresh with --snapshot-update.

Inline seed documents only: the curated GPU/RAM/CPU seeds are B4c's, and these
tests must not move when that content changes.
"""

from __future__ import annotations

import copy
from typing import Any

import pydantic
import pytest
from syrupy.assertion import SnapshotAssertion

from hw_radar.catalog.models import (
    Category,
    CpuSpec,
    DriveSpec,
    GpuSpec,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RamSpec,
)
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.refdata.contracts import SEED_SCHEMA, SeedDocument
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import UnratifiedProvenanceError, import_documents

# Minimal drive document with no `category` key anywhere: the pre-MS-2 shape.
DRIVE_DOC: dict[str, Any] = {
    "schema": SEED_SCHEMA,
    "manufacturer_name": "Seagate",
    "manufacturer_key": "seagate",
    "family_name": "Exos",
    "provenance": {
        "source_kind": "first_party_datasheet",
        "sources": [
            {"url": "https://example.com/exos.pdf", "title": "Exos", "retrieved": "2026-07-05"}
        ],
    },
    "models": [
        {
            "model_number": "ST16000NM002C",
            "spec": {"media_type": "hdd", "capacity_tb": "16"},
            "aliases": [{"alias_type": "mpn", "text": "ST16000NM002C", "is_primary": True}],
        }
    ],
}

# Fictional part numbers on example.com: the fixture exercises the contract, not
# real catalog content.
GPU_DOC: dict[str, Any] = {
    "schema": SEED_SCHEMA,
    "category": "gpu",
    "manufacturer_name": "NVIDIA",
    "manufacturer_key": "nvidia",
    "family_name": "Test Accelerator",
    "provenance": {
        "source_kind": "first_party_page",
        "sources": [
            {"url": "https://example.com/gpu", "title": "GPU page", "retrieved": "2026-09-24"}
        ],
    },
    "models": [
        {
            "model_number": "TA-100-PCIE-32G",
            "source_url": "https://example.com/gpu/ta-100",
            "retrieved_on": "2026-09-24",
            "spec": {
                "chip_vendor": "nvidia",
                "vram_gb": 32,
                "interface": "pcie",
                "cooling": "passive",
                "tdp_w": 250,
            },
            "aliases": [{"alias_type": "mpn", "text": "TA-100-PCIE-32G", "is_primary": True}],
        }
    ],
}


def _with(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    doc.update(overrides)
    return doc


def _row(doc: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = doc["models"][0]
    return row


# ── B4a: contract ──────────────────────────────────────────────────────────


def test_drive_documents_validate_unchanged() -> None:
    doc = SeedDocument.model_validate(DRIVE_DOC)
    assert doc.category == "drive"
    assert doc.models[0].spec.category == "drive"
    assert doc.models[0].source_url is None
    # The shipped Seagate/WD seeds carry no category tag (the pre-MS-2 shape)
    # and must all still load as drive.
    shipped = load_seed_documents()
    assert shipped
    shipped_drive = [d for d in shipped if d.manufacturer_key in {"seagate", "western_digital"}]
    assert len(shipped_drive) == 15
    assert {d.category for d in shipped_drive} == {"drive"}


def test_non_drive_row_without_source_url_rejected() -> None:
    doc = _with(GPU_DOC)
    del _row(doc)["source_url"]
    del _row(doc)["retrieved_on"]
    with pytest.raises(pydantic.ValidationError, match="source_url and retrieved_on"):
        SeedDocument.model_validate(doc)


def test_row_provenance_fields_go_together() -> None:
    doc = _with(GPU_DOC)
    del _row(doc)["retrieved_on"]
    with pytest.raises(pydantic.ValidationError, match="go together"):
        SeedDocument.model_validate(doc)


def test_spec_variant_must_match_document_category() -> None:
    explicit_mismatch = _with(GPU_DOC)
    _row(explicit_mismatch)["spec"] = {"category": "ram", "generation": "ddr5"}
    with pytest.raises(pydantic.ValidationError, match="ram spec in a gpu document"):
        SeedDocument.model_validate(explicit_mismatch)

    # Untagged GPU fields in an (implicitly) drive document validate as a drive
    # spec and fail on the drive variant's forbidden extras.
    gpu_fields_in_drive_doc = _with(DRIVE_DOC)
    _row(gpu_fields_in_drive_doc)["spec"] = dict(_row(GPU_DOC)["spec"])
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(gpu_fields_in_drive_doc)


def test_untagged_spec_takes_the_document_category() -> None:
    doc = SeedDocument.model_validate(GPU_DOC)
    assert doc.category == "gpu"
    assert doc.models[0].spec.category == "gpu"
    # The caller's dict is not mutated by the tag injection.
    assert "category" not in _row(GPU_DOC)["spec"]


def test_ram_and_cpu_specs_parse() -> None:
    ram = _with(GPU_DOC, category="ram")
    _row(ram)["spec"] = {
        "generation": "ddr4",
        "module_type": "rdimm",
        "ecc": True,
        "module_capacity_gb": 32,
        "speed_mts": 3200,
        "ranks": 2,
    }
    ram_spec = SeedDocument.model_validate(ram).models[0].spec
    assert ram_spec.category == "ram"
    assert ram_spec.model_dump()["modules_per_kit"] == 1

    cpu = _with(GPU_DOC, category="cpu")
    _row(cpu)["spec"] = {"socket": "lga4677", "cores": 32, "tdp_w": 225}
    assert SeedDocument.model_validate(cpu).models[0].spec.category == "cpu"

    _row(cpu)["spec"] = {"socket": "LGA 4677"}
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(cpu)  # socket must be a lowercase token


def test_non_first_party_row_maps_to_manual() -> None:
    manual = _with(GPU_DOC)
    manual["provenance"] = {**manual["provenance"], "source_kind": "non_first_party"}
    manual_doc = SeedDocument.model_validate(manual)
    assert not manual_doc.provenance.is_first_party
    assert manual_doc.provenance.alias_source_kind == "manual"

    for kind in ("first_party_datasheet", "first_party_manual", "first_party_page"):
        first = _with(GPU_DOC)
        first["provenance"] = {**first["provenance"], "source_kind": kind}
        assert SeedDocument.model_validate(first).provenance.alias_source_kind == (
            "catalog_authoritative"
        )


# ── B4b: importer ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_gpu_document_imports_to_gpu_spec_and_gpu_category() -> None:
    report = import_documents([SeedDocument.model_validate(GPU_DOC)])

    model = ProductModel.objects.select_related("product_family__category").get(
        normalized_model_number=normalize_alias_text("TA-100-PCIE-32G")
    )
    assert model.product_family is not None
    assert model.product_family.category.slug == "gpu"
    spec = GpuSpec.objects.get(product_model=model)
    assert (spec.chip_vendor, spec.vram_gb, spec.interface, spec.cooling, spec.tdp_w) == (
        "nvidia",
        32,
        "pcie",
        "passive",
        250,
    )
    assert spec.retention_class == "manufacturer_reference"
    assert spec.expires_at is None
    assert not DriveSpec.objects.filter(product_model=model).exists()
    alias = ProductAlias.objects.get(normalized_alias_text=normalize_alias_text("TA-100-PCIE-32G"))
    assert alias.source_kind == "catalog_authoritative"
    assert alias.retention_class == "manufacturer_reference"
    assert report.specs_written == 1


@pytest.mark.django_db
def test_ram_and_cpu_documents_import_to_their_satellites() -> None:
    ram = _with(GPU_DOC, category="ram", manufacturer_name="Micron", manufacturer_key="micron")
    _row(ram)["spec"] = {"generation": "ddr5", "module_type": "rdimm", "module_capacity_gb": 64}
    cpu = _with(GPU_DOC, category="cpu", manufacturer_name="Intel", manufacturer_key="intel")
    _row(cpu)["model_number"] = "TX-6448"
    _row(cpu)["aliases"] = [{"alias_type": "mpn", "text": "TX-6448", "is_primary": True}]
    _row(cpu)["spec"] = {"socket": "lga4677", "cores": 32}

    import_documents([SeedDocument.model_validate(ram), SeedDocument.model_validate(cpu)])

    ram_spec = RamSpec.objects.get(product_model__manufacturer__normalized_name="micron")
    assert (ram_spec.generation, ram_spec.module_capacity_gb, ram_spec.modules_per_kit) == (
        "ddr5",
        64,
        1,
    )
    assert ram_spec.ecc is None  # not stated by the seed: unknown, never guessed
    cpu_spec = CpuSpec.objects.get(product_model__manufacturer__normalized_name="intel")
    assert (cpu_spec.socket, cpu_spec.cores, cpu_spec.tdp_w) == ("lga4677", 32, None)
    assert set(
        ProductFamily.objects.filter(manufacturer__normalized_name__in=["micron", "intel"])
        .values_list("category__slug", flat=True)
        .distinct()
    ) == {"ram", "cpu"}


@pytest.mark.django_db
def test_non_first_party_document_is_refused_before_any_write() -> None:
    manual = _with(GPU_DOC)
    manual["provenance"] = {**manual["provenance"], "source_kind": "non_first_party"}
    with pytest.raises(UnratifiedProvenanceError) as excinfo:
        import_documents(
            [SeedDocument.model_validate(DRIVE_DOC), SeedDocument.model_validate(manual)]
        )
    assert "non_first_party" in excinfo.value.conflicts[0]
    # Validate-then-write: the valid drive document in the same batch is not
    # written either.
    assert not ProductModel.objects.exists()


@pytest.mark.django_db
def test_drive_import_byte_identical(snapshot: SnapshotAssertion) -> None:
    docs = [d for d in load_seed_documents() if d.category == "drive"]
    first = import_documents(docs).as_json()
    second = import_documents(docs).as_json()
    assert snapshot == {
        "first_report": first,
        "second_report": second,
        "catalog": _drive_catalog_dump(),
    }


def _drive_catalog_dump() -> dict[str, object]:
    """Every column the importer writes, keyed by natural keys (no pks or
    timestamps), so the snapshot pins content and not insertion order."""
    categories = sorted(
        Category.objects.filter(families__isnull=False).distinct().values_list("slug", "name")
    )
    families = sorted(
        ProductFamily.objects.values_list(
            "manufacturer__normalized_name",
            "manufacturer__name",
            "normalized_name",
            "name",
            "category__slug",
        )
    )
    models = sorted(
        ProductModel.objects.values_list(
            "manufacturer__normalized_name",
            "normalized_model_number",
            "model_number",
            "product_family__normalized_name",
            "retention_class",
            "expires_at",
        )
    )
    spec_fields = [
        f.name
        for f in DriveSpec._meta.concrete_fields
        if f.name not in {"product_model", "created_at", "updated_at"}
    ]
    specs = sorted(
        (
            (
                row.product_model.normalized_model_number,
                {name: repr(getattr(row, name)) for name in spec_fields},
            )
            for row in DriveSpec.objects.select_related("product_model")
        ),
        key=lambda item: item[0],
    )
    aliases = sorted(
        ProductAlias.objects.values_list(
            "alias_type",
            "normalized_alias_text",
            "product_model__normalized_model_number",
            "product_family__normalized_name",
            "product_variant",
            "source_site",
            "source_kind",
            "is_primary",
            "retention_class",
            "expires_at",
        ),
        key=repr,
    )
    return {
        "categories": categories,
        "families": families,
        "models": models,
        "drive_specs": specs,
        "aliases": aliases,
    }
