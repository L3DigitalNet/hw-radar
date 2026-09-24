"""MS-2 Slice B1/B2 migrations 0018 (satellites) and 0019 (category rows).

These tests drive MigrationExecutor directly: they roll catalog back to 0017,
write rows through the historical models of that state, and roll forward. They
are transactional because the schema changes must commit to be observable across
executor calls, and every test ends by migrating back to the leaf in a `finally`
so a failed assertion cannot leave the shared test DB on an old schema for the
tests that run after it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("catalog", "0017_identity_retention_checks")
SATELLITES = ("catalog", "0018_category_spec_satellites")
CATEGORY_ROWS = ("catalog", "0019_seed_categories")
NEW_SLUGS = {"gpu", "ram", "cpu", "nic", "hba", "motherboard", "server"}
DRIVE_TABLES = ("category", "product_family", "product_model", "drive_spec", "product_alias")

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(target: tuple[str, str]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


@pytest.fixture
def at_0017() -> Iterator[Apps]:
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("catalog")[0]
    try:
        apps = _migrate(BEFORE)
        # A deployed 0017 DB always holds the drive row (0001). pytest-django's
        # post-test flush of transactional tests deletes it, so re-establish it
        # rather than depend on this test running first.
        apps.get_model("catalog", "Category").objects.get_or_create(
            slug="drive", defaults={"name": "Drive"}
        )
        yield apps
    finally:
        _migrate(leaf)


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _counts() -> dict[str, int]:
    with connection.cursor() as cursor:
        counts: dict[str, int] = {}
        for table in DRIVE_TABLES:
            cursor.execute(f"SELECT count(*) FROM {table}")
            row = cursor.fetchone()
            assert row is not None
            counts[table] = int(row[0])
        return counts


def _seed_drive_rows(apps: Apps) -> None:
    category = apps.get_model("catalog", "Category").objects.get(slug="drive")
    mfr = apps.get_model("catalog", "Manufacturer").objects.create(
        name="Seagate", normalized_name="seagate"
    )
    family = apps.get_model("catalog", "ProductFamily").objects.create(
        category=category, manufacturer=mfr, name="Exos", normalized_name="exos"
    )
    model = apps.get_model("catalog", "ProductModel").objects.create(
        manufacturer=mfr,
        product_family=family,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class="manufacturer_reference",
    )
    apps.get_model("catalog", "DriveSpec").objects.create(
        product_model=model, media_type="hdd", retention_class="manufacturer_reference"
    )
    apps.get_model("catalog", "ProductAlias").objects.create(
        alias_type="mpn",
        normalized_alias_text="st16000nm001g",
        product_model=model,
        source_kind="catalog_authoritative",
        retention_class="manufacturer_reference",
    )


def test_forward_from_empty_catalog_creates_satellites_and_rows(at_0017: Apps) -> None:
    assert not {"gpu_spec", "ram_spec", "cpu_spec"} & _tables()
    slugs_before = set(
        at_0017.get_model("catalog", "Category").objects.values_list("slug", flat=True)
    )
    assert slugs_before == {"drive"}

    apps = _migrate(CATEGORY_ROWS)

    assert {"gpu_spec", "ram_spec", "cpu_spec"} <= _tables()
    slugs = set(apps.get_model("catalog", "Category").objects.values_list("slug", flat=True))
    assert slugs == NEW_SLUGS | {"drive"}


def test_forward_over_drive_rows_leaves_drive_counts_unchanged(at_0017: Apps) -> None:
    _seed_drive_rows(at_0017)
    before = _counts()

    _migrate(CATEGORY_ROWS)

    after = _counts()
    # Only the category table grows, by exactly the seven new rows.
    assert after == {**before, "category": before["category"] + len(NEW_SLUGS)}


def test_forward_adopts_a_preexisting_row_without_renaming(at_0017: Apps) -> None:
    at_0017.get_model("catalog", "Category").objects.create(slug="gpu", name="Operator GPU")

    apps = _migrate(CATEGORY_ROWS)

    names = apps.get_model("catalog", "Category").objects.filter(slug="gpu")
    assert list(names.values_list("name", flat=True)) == ["Operator GPU"]


def test_reverse_removes_rows_and_tables_but_keeps_drive(at_0017: Apps) -> None:
    _seed_drive_rows(at_0017)
    before = _counts()
    _migrate(CATEGORY_ROWS)

    apps = _migrate(BEFORE)

    assert not {"gpu_spec", "ram_spec", "cpu_spec"} & _tables()
    slugs = set(apps.get_model("catalog", "Category").objects.values_list("slug", flat=True))
    assert slugs == {"drive"}
    assert _counts() == before


def test_reverse_keeps_a_category_that_has_families(at_0017: Apps) -> None:
    # product_family.category is PROTECT: deleting a referenced row would abort
    # the rollback, so the reverse must skip it and leave its catalog data alone.
    apps = _migrate(CATEGORY_ROWS)
    gpu = apps.get_model("catalog", "Category").objects.get(slug="gpu")
    mfr = apps.get_model("catalog", "Manufacturer").objects.create(
        name="NVIDIA", normalized_name="nvidia"
    )
    apps.get_model("catalog", "ProductFamily").objects.create(
        category=gpu, manufacturer=mfr, name="Tesla", normalized_name="tesla"
    )

    apps = _migrate(SATELLITES)

    slugs = set(apps.get_model("catalog", "Category").objects.values_list("slug", flat=True))
    assert slugs == {"drive", "gpu"}
    assert apps.get_model("catalog", "ProductFamily").objects.filter(category__slug="gpu").exists()
