"""MS-2 Slice C1 migration 0020 (watch, requirement satellites, watch_evaluation).

Drives MigrationExecutor directly, like test_category_migrations.py: roll catalog
back to 0019, write deployed-shape rows through the historical models, then roll
forward and back. Transactional because schema changes must commit to be visible
across executor calls; every test migrates back to the leaf in a `finally` so a
failed assertion cannot strand the shared test DB on an old schema.

serialized_rollback=True for the same reason as test_category_migrations.py: a
plain transactional flush re-emits post_migrate and recreates content types
under new primary keys, which breaks every later serialized_rollback test at
setup with a content-type unique-constraint violation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("catalog", "0019_seed_categories")
AFTER = ("catalog", "0020_watch_requirements")
NEW_TABLES = {
    "watch",
    "drive_requirement",
    "gpu_requirement",
    "ram_requirement",
    "cpu_requirement",
    "watch_evaluation",
}
EXISTING_TABLES = (
    "category",
    "product_family",
    "product_model",
    "drive_spec",
    "gpu_spec",
    "product_alias",
    "listing",
    "listing_resolution",
)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _migrate(target: tuple[str, str]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


@pytest.fixture
def at_0019() -> Iterator[Apps]:
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("catalog")[0]
    try:
        # serialized_rollback restored the deployed category rows (0001, 0019)
        # that _seed_deployed_rows and the reverse test look up by slug.
        yield _migrate(BEFORE)
    finally:
        _migrate(leaf)


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _counts() -> dict[str, int]:
    with connection.cursor() as cursor:
        counts: dict[str, int] = {}
        for table in EXISTING_TABLES:
            cursor.execute(f"SELECT count(*) FROM {table}")
            row = cursor.fetchone()
            assert row is not None
            counts[table] = int(row[0])
        return counts


def _seed_deployed_rows(apps: Apps) -> None:
    get = apps.get_model
    mfr = get("catalog", "Manufacturer").objects.create(name="Seagate", normalized_name="seagate")
    family = get("catalog", "ProductFamily").objects.create(
        category=get("catalog", "Category").objects.get(slug="drive"),
        manufacturer=mfr,
        name="Exos",
        normalized_name="exos",
    )
    model = get("catalog", "ProductModel").objects.create(
        manufacturer=mfr,
        product_family=family,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class="manufacturer_reference",
    )
    get("catalog", "DriveSpec").objects.create(
        product_model=model, media_type="hdd", retention_class="manufacturer_reference"
    )
    gpu_mfr = get("catalog", "Manufacturer").objects.create(name="NVIDIA", normalized_name="nvidia")
    gpu_model = get("catalog", "ProductModel").objects.create(
        manufacturer=gpu_mfr,
        model_number="P40",
        normalized_model_number="p40",
        retention_class="manufacturer_reference",
    )
    get("catalog", "GpuSpec").objects.create(
        product_model=gpu_model, vram_gb=24, retention_class="manufacturer_reference"
    )
    get("catalog", "ProductAlias").objects.create(
        alias_type="mpn",
        normalized_alias_text="st16000nm001g",
        product_model=model,
        source_kind="catalog_authoritative",
        retention_class="manufacturer_reference",
    )
    # A dedicated site name, so the 0005 seed rows restored by
    # serialized_rollback never collide with it.
    site = get("catalog", "SourceSite").objects.create(
        name="Watch migration demo", normalized_name="watchmigdemo"
    )
    listing = get("catalog", "Listing").objects.create(
        source_site=site,
        source_listing_key="k1",
        canonical_url="https://example.test/1",
        url_hash="h1",
        title_raw="Seagate Exos 16TB",
        retention_class="merchant_fact",
    )
    get("catalog", "ListingResolution").objects.create(
        listing=listing,
        grain="model",
        product_model=model,
        method="exact_alias",
        confidence=1.0,
        matcher_version="t",
    )


def test_forward_creates_tables_and_leaves_existing_rows_untouched(at_0019: Apps) -> None:
    _seed_deployed_rows(at_0019)
    assert not NEW_TABLES & _tables()
    before = _counts()

    apps = _migrate(AFTER)

    assert _tables() >= NEW_TABLES
    assert _counts() == before
    # The new tables start empty: nothing is backfilled from existing data.
    for name in ("Watch", "WatchEvaluation", "GpuRequirement"):
        assert not apps.get_model("catalog", name).objects.exists()


def test_forward_schema_accepts_a_watch_and_evaluation(at_0019: Apps) -> None:
    _seed_deployed_rows(at_0019)
    apps = _migrate(AFTER)
    get = apps.get_model

    watch = get("catalog", "Watch").objects.create(
        name="16TB CMR", category=get("catalog", "Category").objects.get(slug="drive")
    )
    get("catalog", "DriveRequirement").objects.create(watch=watch, media_types=["hdd"])
    listing = get("catalog", "Listing").objects.get()
    get("catalog", "WatchEvaluation").objects.create(
        watch=watch,
        listing=listing,
        verdict="unknown",
        reasons=[{"clause": "capacity", "outcome": "unknown"}],
        requirement_version=1,
        evaluator_version="1",
        catalog_fingerprint="0" * 64,
        resolution=get("catalog", "ListingResolution").objects.get(),
        retention_class="merchant_fact",
    )
    assert get("catalog", "WatchEvaluation").objects.count() == 1


def test_reverse_drops_only_the_new_tables(at_0019: Apps) -> None:
    _seed_deployed_rows(at_0019)
    before = _counts()
    apps = _migrate(AFTER)
    get = apps.get_model
    # Reverse must succeed with rows present in the new tables, too.
    watch = get("catalog", "Watch").objects.create(
        name="w", category=get("catalog", "Category").objects.get(slug="gpu")
    )
    get("catalog", "GpuRequirement").objects.create(watch=watch, min_vram_gb=24)

    _migrate(BEFORE)

    assert not NEW_TABLES & _tables()
    assert _counts() == before
