"""import_refdata --category: category-scoped seeding (L15).

Production holds only the drive seeds; the F6 pilot needs CPU reference rows
without also seeding GPU/RAM. These tests pin that a category-scoped import
writes exactly the selected category's seed rows, is idempotent, and leaves a
drive-only catalog (the production shape) untouched down to its timestamps.

Expected rows are derived from the shipped seed documents rather than
hardcoded, so a seed edit moves the expectation with it."""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from hw_radar.catalog.models import (
    CpuSpec,
    DriveSpec,
    GpuSpec,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RamSpec,
)
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import load_seed_documents

pytestmark = pytest.mark.django_db


def _seed_docs(category: str) -> list[SeedDocument]:
    return [doc for doc in load_seed_documents() if doc.category == category]


def _import(*categories: str) -> dict[str, Any]:
    out = StringIO()
    args = [arg for slug in categories for arg in ("--category", slug)]
    call_command("import_refdata", *args, stdout=out)
    return json.loads(out.getvalue())


def _catalog_rows(category: str) -> dict[str, list[tuple[Any, ...]]]:
    """Every column of one category's rows, timestamps included, so a re-save
    with identical content still shows up as a difference."""
    models = ProductModel.objects.filter(product_family__category__slug=category)
    spec_model = {"drive": DriveSpec, "gpu": GpuSpec, "ram": RamSpec, "cpu": CpuSpec}[category]
    return {
        "models": sorted(models.values_list()),
        "specs": sorted(spec_model.objects.filter(product_model__in=models).values_list()),
        "aliases": sorted(
            ProductAlias.objects.filter(product_model__in=models).values_list(), key=str
        ),
        "families": sorted(
            ProductFamily.objects.filter(category__slug=category).values_list(), key=str
        ),
    }


def test_cpu_import_writes_exactly_the_cpu_seed_rows() -> None:
    docs = _seed_docs("cpu")
    assert docs  # the F6 pilot depends on shipped CPU seeds
    expected_models = {
        (doc.manufacturer_key, normalize_alias_text(m.model_number))
        for doc in docs
        for m in doc.models
    }
    expected_aliases = {a.normalized for doc in docs for m in doc.models for a in m.aliases}

    output = _import("cpu")

    stored_models = ProductModel.objects.values_list(
        "manufacturer__normalized_name", "normalized_model_number"
    )
    assert set(stored_models) == expected_models
    assert CpuSpec.objects.count() == len(expected_models)
    stored_aliases = ProductAlias.objects.values_list("normalized_alias_text", flat=True)
    assert set(stored_aliases) == expected_aliases
    assert ProductAlias.objects.count() == len(expected_aliases)
    assert not GpuSpec.objects.exists()
    assert not RamSpec.objects.exists()
    assert not DriveSpec.objects.exists()
    assert set(ProductFamily.objects.values_list("category__slug", flat=True)) == {"cpu"}
    assert set(output["by_category"]) == {"cpu"}
    cpu = output["by_category"]["cpu"]
    assert cpu["models_created"] == len(expected_models)
    assert cpu["specs_created"] == len(expected_models)
    assert cpu["aliases_created"] == len(expected_aliases)


_CPU_SPEC_CONTENT = (
    "product_model_id",
    "socket",
    "cores",
    "tdp_w",
    "retention_class",
    "expires_at",
)


def test_cpu_import_twice_is_idempotent() -> None:
    first = _import("cpu")
    rows_before = _catalog_rows("cpu")
    specs_before = sorted(CpuSpec.objects.values_list(*_CPU_SPEC_CONTENT))
    second = _import("cpu")

    created = first["by_category"]["cpu"]
    again = second["by_category"]["cpu"]
    assert again["models_created"] == again["specs_created"] == again["aliases_created"] == 0
    assert again["models_updated"] == again["specs_updated"] == again["aliases_updated"] == 0
    assert again["models_unchanged"] == created["models_created"]
    assert again["specs_unchanged"] == created["specs_created"]
    assert again["aliases_unchanged"] == created["aliases_created"]
    rows_after = _catalog_rows("cpu")
    # Models, aliases, and families are not re-saved at all. Spec satellites
    # are re-saved by update_or_create (updated_at moves) with identical
    # content — pre-existing importer behavior the per-category count reports
    # as unchanged — so compare them without their timestamp columns.
    for key in ("models", "aliases", "families"):
        assert rows_after[key] == rows_before[key]
    assert sorted(CpuSpec.objects.values_list(*_CPU_SPEC_CONTENT)) == specs_before
    assert ProductModel.objects.count() == created["models_created"]
    assert ProductAlias.objects.count() == created["aliases_created"]


def test_cpu_import_leaves_a_drive_only_catalog_untouched() -> None:
    # The production shape: drive seeds only (15 models at c2e2be5).
    _import("drive")
    drive_before = _catalog_rows("drive")
    manufacturers_before = sorted(Manufacturer.objects.values_list(), key=str)

    output = _import("cpu")

    assert _catalog_rows("drive") == drive_before
    assert set(output["by_category"]) == {"cpu"}
    # CPU manufacturers (amd, intel) are new rows; no drive manufacturer moved.
    manufacturers_after = sorted(Manufacturer.objects.values_list(), key=str)
    assert set(manufacturers_before) <= set(manufacturers_after)
    assert not GpuSpec.objects.exists()
    assert not RamSpec.objects.exists()


def test_repeated_category_flag_imports_each_selected_category() -> None:
    output = _import("drive", "cpu")
    assert set(output["by_category"]) == {"drive", "cpu"}
    assert not GpuSpec.objects.exists()
    assert not RamSpec.objects.exists()


def test_unknown_category_slug_errors_and_lists_valid_slugs() -> None:
    valid = sorted({doc.category for doc in load_seed_documents()})
    with pytest.raises(CommandError) as excinfo:
        call_command("import_refdata", "--category", "cpu", "--category", "nic")
    message = str(excinfo.value)
    assert "nic" in message
    for slug in valid:
        assert slug in message
    assert not ProductModel.objects.exists()  # rejected before anything is written


def test_category_with_refresh_is_rejected() -> None:
    with pytest.raises(CommandError, match="--refresh"):
        call_command("import_refdata", "--refresh", "--category", "cpu")
    assert not ProductModel.objects.exists()
