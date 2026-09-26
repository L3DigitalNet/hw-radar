# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""Source x category admission matrix (OQ31 + the 2026-09-26 matrix decision).

Pure tests: the matrix module, the registries, the eBay factory's sweep
filtering (over an httpx MockTransport, never the network), and the scheduler's
job-building gate over in-memory configs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hw_radar.acquisition import admission
from hw_radar.acquisition.admission import (
    ADMISSION_MATRIX,
    MATRIX_CATEGORIES,
    RETIRED_SOURCES,
    CellStatus,
    admitted_categories,
    is_admitted,
    is_retired,
)
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.sources import (
    ADAPTERS,
    FIXTURE_SOURCE_KEYS,
    HARVEST_ADAPTERS,
    RETIRED_ADAPTERS,
    admitted_ebay_adapter,
    ebay,
)
from hw_radar.catalog.models import CheapSignal, SourceConfig, SourceSite
from hw_radar.matching.categories import registered_categories
from hw_radar.poller.service import SourceSchedule, build_scheduler

REGISTRY_KEYS = frozenset(ADAPTERS) | frozenset(RETIRED_ADAPTERS)


def test_matrix_has_an_explicit_cell_for_every_source_and_category() -> None:
    expected = {(s, c) for s in REGISTRY_KEYS for c in MATRIX_CATEGORIES}
    assert set(ADMISSION_MATRIX) == expected


def test_matrix_columns_are_the_owner_categories_and_are_registered() -> None:
    assert set(MATRIX_CATEGORIES) == {"drive", "cpu", "gpu", "ram"}
    assert set(MATRIX_CATEGORIES) <= registered_categories()


def test_retired_cells_are_exactly_the_retired_sources() -> None:
    assert frozenset({"serverpartdeals", "seagate-recertified"}) == RETIRED_SOURCES
    retired_cells = {
        key for key, status in ADMISSION_MATRIX.items() if status is CellStatus.RETIRED
    }
    assert retired_cells == {(s, c) for s in RETIRED_SOURCES for c in MATRIX_CATEGORIES}


def test_current_truth_nothing_is_admitted() -> None:
    # Owner decision 2026-09-26: no combination has passed its gates yet.
    assert CellStatus.ADMITTED not in set(ADMISSION_MATRIX.values())
    assert all(not admitted_categories(s) for s in REGISTRY_KEYS)


def test_category_coverage_per_source() -> None:
    def live(source: str) -> set[str]:
        return {
            c for c in MATRIX_CATEGORIES if ADMISSION_MATRIX[(source, c)] is CellStatus.NOT_ADMITTED
        }

    assert live("ebay") == {"drive", "cpu", "gpu", "ram"}
    assert live("wd-recertified") == {"drive"}
    assert live("goharddrive") == {"drive"}
    for fixture in FIXTURE_SOURCE_KEYS:
        assert {ADMISSION_MATRIX[(fixture, c)] for c in MATRIX_CATEGORIES} == {
            CellStatus.NOT_APPLICABLE
        }


def test_retired_registry_is_disjoint_from_the_schedulable_one() -> None:
    assert frozenset(RETIRED_ADAPTERS) == RETIRED_SOURCES
    assert not frozenset(ADAPTERS) & RETIRED_SOURCES
    assert not frozenset(HARVEST_ADAPTERS) & RETIRED_SOURCES


def test_matrix_fails_closed(admit: Callable[..., None]) -> None:
    # Retirement outranks an ADMITTED cell, and unknown keys, unknown categories
    # and the non-column basic-watch categories are never admitted.
    admit(("serverpartdeals", "drive"), ("ebay", "nic"))
    assert is_retired("serverpartdeals")
    assert not is_admitted("serverpartdeals", "drive")
    assert not is_admitted("ebay", "nic")
    assert not is_admitted("not-a-source", "drive")
    assert admission.scheduling_block("serverpartdeals") is not None
    assert "retired / permission-required" in (admission.scheduling_block("serverpartdeals") or "")


# ── eBay sweep filtering ─────────────────────────────────────────────────────


def _record_ebay_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        seen.append(request)
        return httpx.Response(200, json={"itemSummaries": [], "total": 0})

    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ebay.httpx, "AsyncClient", client)
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.test")
    ebay._TOKEN_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    return seen


def _categories_requested(seen: list[httpx.Request]) -> list[str | None]:
    return [r.url.params.get("category_ids") for r in seen]


def test_ebay_runs_only_the_admitted_category_sweep(
    monkeypatch: pytest.MonkeyPatch, admit: Callable[..., None]
) -> None:
    seen = _record_ebay_requests(monkeypatch)
    admit(("ebay", "gpu"))
    gpu_ids = [s.category_id for s in ebay.CATEGORY_SWEEPS if s.slug == "gpu"]

    asyncio.run(admitted_ebay_adapter().fetch())

    # No legacy drive GET (it has no category_ids) and no RAM/CPU sweep.
    assert _categories_requested(seen) == gpu_ids


def test_category_harvest_adapter_requests_only_that_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # harvest_corpus --category: a focused harvest must not spend Browse calls
    # on the drive GET or on other categories' sweeps.
    seen = _record_ebay_requests(monkeypatch)

    asyncio.run(ebay.category_sweep_adapter("cpu").fetch())

    cpu = [s for s in ebay.CATEGORY_SWEEPS if s.slug == "cpu"]
    assert [(r.url.params["category_ids"], r.url.params["q"]) for r in seen] == [
        (s.category_id, s.q) for s in cpu
    ]


def test_ebay_drive_cell_alone_runs_only_the_legacy_drive_get(
    monkeypatch: pytest.MonkeyPatch, admit: Callable[..., None]
) -> None:
    seen = _record_ebay_requests(monkeypatch)
    admit(("ebay", "drive"))

    asyncio.run(admitted_ebay_adapter().fetch())

    assert _categories_requested(seen) == [None]
    assert seen[0].url.params.get("q") == ebay.SEARCH_PARAMS["q"]


def test_ebay_probe_sends_nothing_without_the_drive_cell(
    monkeypatch: pytest.MonkeyPatch, admit: Callable[..., None]
) -> None:
    # The heartbeat probe IS the legacy drive GET.
    seen = _record_ebay_requests(monkeypatch)
    admit(("ebay", "gpu"))

    assert asyncio.run(admitted_ebay_adapter().probe()) == []
    assert seen == []


def test_ebay_with_nothing_admitted_sweeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_ebay_requests(monkeypatch)

    asyncio.run(admitted_ebay_adapter().fetch())

    assert seen == []


# ── Scheduler gate ───────────────────────────────────────────────────────────


def _schedule(key: str, *, heartbeat: bool = False) -> SourceSchedule:
    # In-memory config, as tests/unit/test_poller.py's _mem_schedule: the
    # scheduler gate must hold whatever the row says, so this row is "enabled".
    config = SourceConfig()
    site = SourceSite()
    site.normalized_name = key
    site.name = key
    config.source_site = site
    config.enabled = True
    config.heartbeat_enabled = heartbeat
    config.cheap_signal = CheapSignal.EBAY_BROWSE if key == "ebay" else CheapSignal.NONE
    config.cadence_baseline_s = 600
    config.misfire_grace_s = 60
    config.bucket_rate_per_min = 6.0
    config.bucket_burst = 3
    return SourceSchedule(config=config, full_interval_s=600, heartbeat_interval_s=120)


def _source_jobs(key: str, schedules: list[SourceSchedule]) -> set[str]:
    scheduler = build_scheduler(BucketRegistry(), schedules)
    return {j.id for j in scheduler.get_jobs() if j.id in {f"poll-{key}", f"poll-heartbeat-{key}"}}


@pytest.mark.parametrize("key", sorted(RETIRED_SOURCES))
def test_enabled_retired_source_is_never_scheduled(
    key: str, admit: Callable[..., None], caplog: pytest.LogCaptureFixture
) -> None:
    admit(*[(key, c) for c in MATRIX_CATEGORIES])
    with caplog.at_level(logging.WARNING, logger="hw_radar.poller.service"):
        jobs = _source_jobs(key, [_schedule(key, heartbeat=True)])
    assert jobs == set()
    assert any("retired" in r.getMessage() and key in r.getMessage() for r in caplog.records)


def test_ebay_with_no_admitted_cell_gets_no_job(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="hw_radar.poller.service"):
        jobs = _source_jobs("ebay", [_schedule("ebay", heartbeat=True)])
    assert jobs == set()
    assert any("no admitted category" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("key", ["wd-recertified", "goharddrive"])
def test_drive_only_source_needs_its_drive_cell(key: str, admit: Callable[..., None]) -> None:
    assert _source_jobs(key, [_schedule(key)]) == set()
    admit((key, "drive"))
    assert _source_jobs(key, [_schedule(key)]) == {f"poll-{key}"}


def test_unrelated_admission_is_never_a_prerequisite(admit: Callable[..., None]) -> None:
    # Matrix decision: each combination stands alone. Admitting goharddrive's
    # drive cell alone schedules goharddrive, and nothing else.
    admit(("goharddrive", "drive"))
    keys = ["goharddrive", "wd-recertified", "ebay"]
    scheduler = build_scheduler(BucketRegistry(), [_schedule(k) for k in keys])
    ids = {j.id for j in scheduler.get_jobs()}
    assert "poll-goharddrive" in ids
    assert not {"poll-wd-recertified", "poll-ebay", "poll-heartbeat-ebay"} & ids
