"""MS-2 Slice D task D2 migration 0021 (provider_run, scope_sweep_continuity, and
the scope/ordering watermark columns).

Drives MigrationExecutor directly, like test_watch_migration.py: roll catalog
back to 0020, write deployed-shape rows through the historical models, then roll
forward and back. Transactional because schema changes must commit to be visible
across executor calls; every test migrates back to the leaf in a `finally` so a
failed assertion cannot strand the shared test DB on an old schema.
serialized_rollback=True for the content-type reason test_watch_migration.py
documents.

What the upgrade must prove (plan D2): every revision-4 column is nullable with
no backfill, so a deployed row upgrades with the column NULL, which each later
guard reads as "no bound"; and every deployed source keeps the local provider,
including heartbeat and fast-lane sources, which the new CHECK would otherwise
refuse.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("catalog", "0020_watch_requirements")
AFTER = ("catalog", "0021_provider_runs")
NEW_TABLES = {"provider_run", "scope_sweep_continuity"}
NEW_COLUMNS = {
    "listing": {"collection_scope", "last_observed_at", "last_absence_at"},
    "source_config": {"collection_provider"},
    "source_lane_state": {
        "last_eligible_sweep_at",
        "continuity_broken_at",
        "last_complete_sweep_at",
    },
}
EXISTING_TABLES = ("listing", "source_config", "source_lane_state", "scraper_runs")
T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _migrate(target: tuple[str, str]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


@pytest.fixture
def at_0020() -> Iterator[Apps]:
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("catalog")[0]
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate(leaf)


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


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
    # A dedicated site key, so the 0005 seed rows restored by serialized_rollback
    # never collide with it (conventions #9).
    site = get("catalog", "SourceSite").objects.create(
        name="Provider migration demo", normalized_name="d2migdemo"
    )
    # A heartbeat-enabled source: the upgrade must default it to `local`, the
    # only provider the new CHECK allows with a heartbeat lane.
    config = get("catalog", "SourceConfig").objects.create(
        source_site=site,
        tier="t2",
        domain="example.test",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        heartbeat_enabled=True,
    )
    for lane in ("full", "heartbeat"):
        get("catalog", "SourceLaneState").objects.create(
            source_config=config, lane=lane, current_interval_s=3600, continuous_since=T0
        )
    get("catalog", "Listing").objects.create(
        source_site=site,
        source_listing_key="k1",
        canonical_url="https://example.test/1",
        url_hash="h1",
        title_raw="Seagate Exos 16TB",
        retention_class="merchant_fact",
    )
    get("catalog", "ScraperRun").objects.create(source_site=site, started_at=T0, status="success")


def test_forward_adds_nullable_columns_without_backfill(at_0020: Apps) -> None:
    _seed_deployed_rows(at_0020)
    assert not NEW_TABLES & _tables()
    before = _counts()

    apps = _migrate(AFTER)
    get = apps.get_model

    assert _tables() >= NEW_TABLES
    for table, columns in NEW_COLUMNS.items():
        assert _columns(table) >= columns
    assert _counts() == before
    # .values() rather than attributes: historical models carry no field stubs.
    listing = get("catalog", "Listing").objects.values(
        "collection_scope", "last_observed_at", "last_absence_at"
    )
    assert list(listing.filter(source_listing_key="k1")) == [
        {"collection_scope": None, "last_observed_at": None, "last_absence_at": None}
    ]
    config = get("catalog", "SourceConfig").objects.filter(source_site__normalized_name="d2migdemo")
    assert list(config.values_list("collection_provider", flat=True)) == ["local"]
    lanes = get("catalog", "SourceLaneState").objects.filter(source_config__in=config)
    assert sorted(
        lanes.values_list(
            "lane",
            "continuous_since",
            "last_eligible_sweep_at",
            "continuity_broken_at",
            "last_complete_sweep_at",
        )
    ) == [("full", T0, None, None, None), ("heartbeat", T0, None, None, None)]
    assert not get("catalog", "ProviderRun").objects.exists()
    assert not get("catalog", "ScopeSweepContinuity").objects.exists()


def test_reverse_drops_only_the_new_schema_and_forward_reapplies(at_0020: Apps) -> None:
    _seed_deployed_rows(at_0020)
    before = _counts()
    apps = _migrate(AFTER)
    get = apps.get_model
    # Reverse must succeed with rows present in the new tables and columns, too.
    site = get("catalog", "SourceSite").objects.get(normalized_name="d2migdemo")
    get("catalog", "ProviderRun").objects.create(
        source_site=site,
        provider_kind="apify",
        scope_key="d2migdemo:drive:q1",
        memory_mb=512,
        timeout_s=600,
        max_items=50,
        max_pages=5,
        admission_class="discovery",
        run_kind="full",
        admitted_at=T0,
        storage_cleanup_due_at=T0,
    )
    get("catalog", "ScopeSweepContinuity").objects.create(
        source_site=site, collection_scope="d2migdemo:drive:q1", last_complete_sweep_at=T0
    )
    get("catalog", "Listing").objects.filter(source_listing_key="k1").update(
        collection_scope="d2migdemo:drive:q1", last_observed_at=T0
    )

    _migrate(BEFORE)

    assert not NEW_TABLES & _tables()
    for table, columns in NEW_COLUMNS.items():
        assert not _columns(table) & columns
    assert _counts() == before

    _migrate(AFTER)
    assert _tables() >= NEW_TABLES
