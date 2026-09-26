# pyright: reportUnknownMemberType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""OQ31 retirement at the DB-facing layers: the SourceConfig guard, the poller's
run paths over real rows, the Apify start refusal, and migration 0023.

Transactional because recovery_probe_job writes through sync_to_async and the
migration tests drive MigrationExecutor (schema state must commit to be seen
across executor calls); every migration test rolls forward to the leaf in a
`finally`. serialized_rollback=True restores the 0005 seed rows these tests
flip, for the reason test_watch_migration.py documents.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from importlib import import_module
from pathlib import Path

import pytest
from django.apps.registry import Apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from hw_radar.acquisition import sources
from hw_radar.acquisition.admission import RETIRED_SOURCES
from hw_radar.acquisition.apify.jobs import StartRefusal, StartStatus, start_provider_run
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import LifecycleState, SourceConfig
from hw_radar.poller.service import build_scheduler, load_schedules, recovery_probe_job

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

RETIRED = sorted(RETIRED_SOURCES)
BEFORE = ("catalog", "0022_apify_spend_ledger")
AFTER = ("catalog", "0023_retire_oq31_sources")


def _config(key: str) -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(source_site__normalized_name=key)


@pytest.mark.parametrize("key", RETIRED)
def test_model_refuses_enabling_a_retired_source(key: str) -> None:
    config = _config(key)
    config.enabled = True
    with pytest.raises(ValidationError, match="retired / permission-required"):
        config.full_clean()
    with pytest.raises(ValidationError, match="retired / permission-required"):
        config.save()
    assert _config(key).enabled is False


def test_model_still_saves_a_disabled_retired_row_and_enables_others() -> None:
    config = _config("serverpartdeals")
    config.notes = "retired (OQ31)"
    config.save()
    ok = _config("goharddrive")
    ok.enabled = True
    ok.full_clean()
    ok.save()
    assert _config("goharddrive").enabled is True


def _force_enabled(key: str, **fields: object) -> None:
    # queryset.update bypasses save() and clean(): the path a hand-written SQL
    # fix or a stale data migration would take, and the one the poller gate
    # must still hold against.
    SourceConfig.objects.filter(source_site__normalized_name=key).update(enabled=True, **fields)


@pytest.mark.parametrize("key", RETIRED)
def test_retired_row_forced_enabled_is_never_scheduled(
    key: str, admit: Callable[..., None]
) -> None:
    admit((key, "drive"))
    _force_enabled(key, lifecycle_state=LifecycleState.ACTIVE)
    configs = list(SourceConfig.objects.select_related("source_site").filter(enabled=True))
    assert key in {c.source_site.normalized_name for c in configs}

    scheduler = build_scheduler(BucketRegistry(), load_schedules(configs))

    assert scheduler.get_job(f"poll-{key}") is None
    assert scheduler.get_job(f"poll-heartbeat-{key}") is None


@pytest.mark.parametrize("key", RETIRED)
def test_recovery_probe_skips_a_forced_enabled_retired_source(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Tripwire:
        def __init__(self) -> None:
            calls.append(key)
            raise AssertionError("a retired adapter was built by the recovery probe")

    monkeypatch.setitem(sources.ADAPTERS, key, Tripwire)
    _force_enabled(key, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX)

    asyncio.run(recovery_probe_job(BucketRegistry()))

    assert calls == []
    assert _config(key).lifecycle_state == LifecycleState.PAUSED_PENDING_FIX


@pytest.mark.parametrize("key", ["wd-recertified", "goharddrive"])
def test_recovery_probe_skips_an_unadmitted_drive_source(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setitem(sources.ADAPTERS, key, lambda: calls.append(key))
    _force_enabled(key, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX)

    asyncio.run(recovery_probe_job(BucketRegistry()))

    assert calls == []


@pytest.mark.parametrize("key", RETIRED)
def test_apify_start_refuses_a_retired_source(key: str) -> None:
    result = asyncio.run(start_provider_run(_config(key)))
    assert (result.status, result.reason) == (StartStatus.REFUSED, StartRefusal.SOURCE_RETIRED)


def test_harvest_corpus_refuses_a_retired_source(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="serverpartdeals is retired"):
        call_command("harvest_corpus", "--source", "serverpartdeals", "--out", str(tmp_path))


# ── Migration 0023 ───────────────────────────────────────────────────────────


def _migrate(target: tuple[str, str]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


@pytest.fixture
def at_0022() -> Iterator[Apps]:
    leaf = MigrationExecutor(connection).loader.graph.leaf_nodes("catalog")[0]
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate(leaf)


def _states(apps: Apps) -> dict[str, tuple[bool, str]]:
    rows = apps.get_model("catalog", "SourceConfig").objects.values_list(
        "source_site__normalized_name", "enabled", "lifecycle_state"
    )
    return {key: (enabled, state) for key, enabled, state in rows}


def test_migration_retires_enabled_rows_and_leaves_others(at_0022: Apps) -> None:
    model = at_0022.get_model("catalog", "SourceConfig")
    model.objects.update(enabled=True, lifecycle_state="active")

    apps = _migrate(AFTER)

    states = _states(apps)
    for key in RETIRED:
        assert states[key] == (False, "skip")
    assert states["goharddrive"] == (True, "active")
    assert states["ebay"] == (True, "active")

    # Idempotent: replaying the step over already-retired rows changes nothing.
    import_module(f"hw_radar.catalog.migrations.{AFTER[1]}").retire(apps, None)
    assert _states(apps) == states


def test_migration_tolerates_absent_rows_and_reverses_as_noop(at_0022: Apps) -> None:
    model = at_0022.get_model("catalog", "SourceConfig")
    model.objects.filter(source_site__normalized_name__in=RETIRED).delete()

    apps = _migrate(AFTER)
    assert not set(RETIRED) & set(_states(apps))

    before = _states(apps)
    assert _states(_migrate(BEFORE)) == before
