"""MS-2 Slice E task E1 migration 0022 (the Apify spend ledger tables).

Drives MigrationExecutor directly, like test_provider_run_migration.py: roll
catalog back to 0021, write a deployed-shape provider_run through the
historical models, then roll forward and back. Transactional because schema
changes must commit to be visible across executor calls; every test migrates
back to the leaf in a `finally` so a failed assertion cannot strand the shared
test DB on an old schema. serialized_rollback=True for the content-type reason
test_watch_migration.py documents.

What the upgrade must prove: 0022 only adds tables, so an existing
provider_run keeps every value, and 0022 reverses cleanly even with ledger rows
present, including the reservation ↔ usage-read FK cycle that
`correction_closing_read` creates.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("catalog", "0021_provider_runs")
AFTER = ("catalog", "0022_apify_spend_ledger")
NEW_TABLES = {
    "apify_spend_reservation",
    "apify_usage_read",
    "apify_budget_latch",
    "apify_budget_cycle",
    "apify_cycle_discovery",
    "apify_ledger_authority",
}
T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _migrate(target: tuple[str, str]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


@pytest.fixture
def at_0021() -> Iterator[Apps]:
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("catalog")[0]
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate(leaf)


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _seed_run(apps: Apps) -> int:
    get = apps.get_model
    # A dedicated site key, so the 0005 seed rows restored by serialized_rollback
    # never collide with it (conventions #9).
    site = get("catalog", "SourceSite").objects.create(
        name="Ledger migration demo", normalized_name="e1migdemo"
    )
    run = get("catalog", "ProviderRun").objects.create(
        source_site=site,
        provider_kind="apify",
        external_run_id="r1",
        import_idempotency_key="apify:r1",
        scope_key="e1migdemo:gpu:q1",
        memory_mb=512,
        timeout_s=600,
        max_items=50,
        max_pages=5,
        admission_class="watch_refresh",
        run_kind="full",
        admitted_at=T0,
        storage_cleanup_due_at=T0 + timedelta(hours=24),
        run_poll_count=3,
        final_charge_op_at=T0 + timedelta(hours=1),
    )
    return int(run.pk)


def _run_snapshot(apps: Apps, pk: int) -> dict[str, object]:
    return (
        apps.get_model("catalog", "ProviderRun")
        .objects.values(
            "external_run_id", "scope_key", "run_poll_count", "final_charge_op_at", "storage_state"
        )
        .get(pk=pk)
    )


def test_forward_adds_only_ledger_tables_and_keeps_runs(at_0021: Apps) -> None:
    pk = _seed_run(at_0021)
    before = _run_snapshot(at_0021, pk)
    assert not NEW_TABLES & _tables()

    apps = _migrate(AFTER)

    assert _tables() >= NEW_TABLES
    assert _run_snapshot(apps, pk) == before
    for name in (
        "ApifySpendReservation",
        "ApifyUsageRead",
        "ApifyBudgetLatch",
        "ApifyBudgetCycle",
        "ApifyCycleDiscovery",
        "ApifyLedgerAuthority",
    ):
        assert not apps.get_model("catalog", name).objects.exists()


def test_reverse_drops_ledger_with_rows_present_and_forward_reapplies(at_0021: Apps) -> None:
    pk = _seed_run(at_0021)
    before = _run_snapshot(at_0021, pk)
    apps = _migrate(AFTER)
    get = apps.get_model
    run = get("catalog", "ProviderRun").objects.get(pk=pk)
    reservation = get("catalog", "ApifySpendReservation").objects.create(
        provider_run=run,
        source_site=get("catalog", "SourceSite").objects.get(normalized_name="e1migdemo"),
        admission_class="watch_refresh",
        status="reconciled",
        estimate_usd=Decimal("0.5"),
        monitoring_bound_usd=Decimal("0.002"),
        actual_usd=Decimal("0.4"),
        reserved_at=T0,
        reconciled_at=T0 + timedelta(hours=2),
        last_charge_at=T0 + timedelta(hours=2),
        correction_monitor_until=T0 + timedelta(days=7),
    )
    read = get("catalog", "ApifyUsageRead").objects.create(
        provider_run=run, reservation=reservation, read_at=T0 + timedelta(days=8)
    )
    # Closes the FK cycle the reverse must be able to unwind.
    get("catalog", "ApifySpendReservation").objects.filter(pk=reservation.pk).update(
        correction_monitor_closed_at=T0 + timedelta(days=8), correction_closing_read=read
    )
    get("catalog", "ApifyBudgetLatch").objects.create(
        tripped_at=T0, provider_run=run, reason="orphaned_start"
    )
    get("catalog", "ApifyBudgetCycle").objects.create(
        cycle_start=T0, cycle_end=T0 + timedelta(days=30), opened_at=T0
    )
    get("catalog", "ApifyCycleDiscovery").objects.create(opened_at=T0)
    get("catalog", "ApifyLedgerAuthority").objects.create(
        cycle_start=T0, kind="origin", ledger_id="proof", attested_by="owner", created_at=T0
    )

    apps = _migrate(BEFORE)

    assert not NEW_TABLES & _tables()
    assert _run_snapshot(apps, pk) == before

    _migrate(AFTER)
    assert _tables() >= NEW_TABLES
