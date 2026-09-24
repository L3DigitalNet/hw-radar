"""B4c: the curated first-party GPU/RAM/CPU reference seeds validate and
import cleanly alongside the pre-existing drive corpus.

Scope: every committed document under SEED_DIR (drive included, so a future
non-drive addition that breaks validation is caught here too), plus
import-side assertions restricted to the non-drive documents — the drive
import path already has its own byte-identical snapshot coverage in
test_refdata_categories.py and is not re-asserted here."""

from __future__ import annotations

import pytest

from hw_radar.catalog.models import (
    AliasSourceKind,
    CpuSpec,
    GpuSpec,
    ProductAlias,
    ProductModel,
    RamSpec,
)
from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import SEED_DIR, load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db


def _non_drive_docs() -> list[SeedDocument]:
    return [d for d in load_seed_documents() if d.category != "drive"]


def test_every_shipped_seed_document_validates() -> None:
    # Re-parse each raw file directly against SeedDocument, rather than
    # trusting load_seed_documents() (which already does this internally):
    # this test's job is to catch a future document that fails validation,
    # independent of whether the loader's own error handling changes.
    paths = sorted(SEED_DIR.glob("*.json"))
    assert paths
    for path in paths:
        SeedDocument.model_validate_json(path.read_text(encoding="utf-8"))


def test_non_drive_import_creates_expected_per_category_counts() -> None:
    docs = _non_drive_docs()
    report = import_documents(docs)

    expected_models = {"cpu": 9, "gpu": 8, "ram": 2}
    for category, count in expected_models.items():
        docs_in_category = [d for d in docs if d.category == category]
        assert sum(len(d.models) for d in docs_in_category) == count

    assert report.models_created == sum(expected_models.values())
    assert CpuSpec.objects.count() == expected_models["cpu"]
    assert GpuSpec.objects.count() == expected_models["gpu"]
    assert RamSpec.objects.count() == expected_models["ram"]

    expected_aliases = sum(len(m.aliases) for d in docs for m in d.models)
    assert report.aliases_created == expected_aliases
    assert ProductAlias.objects.count() == expected_aliases
    # Every non-drive document is first-party (MS2-D-06): the importer stamps
    # catalog_authoritative on every alias it writes, never manual.
    assert set(ProductAlias.objects.values_list("source_kind", flat=True)) == {
        AliasSourceKind.CATALOG_AUTHORITATIVE
    }


def test_non_drive_import_is_idempotent() -> None:
    docs = _non_drive_docs()
    import_documents(docs)
    before_models = ProductModel.objects.count()
    before_aliases = ProductAlias.objects.count()

    report = import_documents(docs)

    assert report.models_created == 0
    assert report.aliases_created == 0
    assert ProductModel.objects.count() == before_models
    assert ProductAlias.objects.count() == before_aliases
