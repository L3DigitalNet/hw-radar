"""What the Actor emits: contract-valid output, storage discipline, and frozen fixtures."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from synthetic_collector.contract import (
    ACTOR_NAME,
    ACTOR_VERSION,
    LISTING_SCHEMA_FILE,
    OUTPUT_KEY,
    RUN_SCHEMA_FILE,
    RUN_SCHEMA_VERSION,
    schema_errors,
)
from synthetic_collector.main import new_http_client, run_actor, utc_now_seconds
from tests.support import (
    ACTOR_ROOT,
    ADMITTED_SCOPE_DIFFERS,
    FAULT_MODES,
    FIXTURE_SPECS,
    HW_RADAR_FIXTURE_DIR,
    OUTPUT_RECORD_ABSENT,
    REMOTE_STATUS_OVERRIDE,
    ROWS_FROM_UNKNOWN_SCHEMA_RUN,
    STARTED_AT,
    base_input,
    build_fixture,
    fixture_text,
    implied_remote_status,
    mock_client,
    run_collect,
    serve_source,
)


@pytest.mark.parametrize("mode", FAULT_MODES)
def test_every_fault_mode_emits_schema_valid_output(mode: str) -> None:
    result = run_collect(base_input(faultMode=mode))

    assert result.output is not None
    output = dict(result.output)
    rows = [dict(row) for row in result.rows]
    if mode == "unknown_schema":
        # Deliberately a major hw-radar does not know; everything else about the
        # output must still be v1-shaped, so only the version may differ.
        assert output["schemaVersion"] != RUN_SCHEMA_VERSION
        output["schemaVersion"] = RUN_SCHEMA_VERSION
        rows = [{**row, "schemaVersion": "hw-radar-listing/v1"} for row in rows]
    assert schema_errors(RUN_SCHEMA_FILE, output) == []
    assert rows, "every mode emits at least one row from the catalog source"
    for row in rows:
        assert schema_errors(LISTING_SCHEMA_FILE, row) == []


@dataclass
class RecordingActor:
    """Records every storage write the entry point makes (an apify.Actor stand-in)."""

    actor_input: object
    dataset: list[dict[Any, Any]] = field(default_factory=list[dict[Any, Any]])
    kv_writes: list[tuple[str, Any]] = field(default_factory=list[tuple[str, Any]])
    failures: list[str | None] = field(default_factory=list[str | None])
    order: list[str] = field(default_factory=list[str])

    async def get_input(self) -> Any:
        return self.actor_input

    async def push_data(
        self, data: dict[Any, Any] | list[dict[Any, Any]], *, charged_event_name: str | None = None
    ) -> object:
        assert charged_event_name is None
        self.dataset.extend(data if isinstance(data, list) else [data])
        self.order.append("push_data")
        return None

    async def set_value(self, key: str, value: Any, *, content_type: str | None = None) -> None:
        self.kv_writes.append((key, value))
        self.order.append(f"set_value:{key}")

    async def fail(
        self,
        *,
        exit_code: int = 1,
        exception: BaseException | None = None,
        status_message: str | None = None,
    ) -> None:
        self.failures.append(status_message)
        self.order.append("fail")


def _run_entry_point(actor_input: object) -> RecordingActor:
    actor = RecordingActor(actor_input)
    _, transport = serve_source()
    asyncio.run(
        run_actor(
            actor,
            client_factory=lambda: mock_client(transport),
            clock=lambda: 0.0,
            wall_clock=lambda: STARTED_AT,
        )
    )
    return actor


@pytest.mark.parametrize("mode", FAULT_MODES)
def test_kv_store_holds_only_output_record(mode: str) -> None:
    actor = _run_entry_point(base_input(faultMode=mode))

    assert [key for key, _ in actor.kv_writes] == [OUTPUT_KEY]
    output = actor.kv_writes[0][1]
    # OUTPUT carries counts, scope, and errors only; merchant content (titles,
    # prices, URLs) never leaves through the KV store.
    assert set(output) == {
        "schemaVersion",
        "status",
        "completeness",
        "queryScope",
        "provider",
        "errors",
    }
    serialized = json.dumps(output)
    for row in actor.dataset:
        assert row["title"] not in serialized
        assert row["url"] not in serialized
    # Rows land before OUTPUT, and OUTPUT before any failure: OUTPUT is the commit marker.
    expected_order = ["push_data", f"set_value:{OUTPUT_KEY}"] + (["fail"] if mode == "fail" else [])
    assert actor.order == expected_order


def test_invalid_input_fails_without_output_or_rows() -> None:
    actor = _run_entry_point(base_input(maxItems=0, proxyConfiguration={"useApifyProxy": True}))

    assert actor.kv_writes == []
    assert actor.dataset == []
    assert len(actor.failures) == 1
    assert actor.failures[0] is not None
    assert actor.failures[0].startswith("input rejected")


def test_non_object_input_is_rejected() -> None:
    actor = _run_entry_point(["not", "an", "object"])

    assert actor.kv_writes == []
    assert actor.failures


@pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[spec.name for spec in FIXTURE_SPECS])
def test_contract_fixtures_regenerate_from_fault_modes(spec: Any) -> None:
    path = HW_RADAR_FIXTURE_DIR / f"{spec.name}.json"
    committed = path.read_text(encoding="utf-8")

    assert committed == fixture_text(build_fixture(spec)), (
        f"{path.name} drifted from the Actor; regenerate with `uv run python -m tests.support --write`"
    )


def test_fixture_envelopes_follow_the_actor_unless_declared() -> None:
    names = sorted(path.stem for path in HW_RADAR_FIXTURE_DIR.glob("*.json"))
    assert names == sorted(spec.name for spec in FIXTURE_SPECS)
    for spec in FIXTURE_SPECS:
        fixture = build_fixture(spec)
        actor_run = run_collect(spec.actor_input)
        effects = set(spec.effects)
        status_follows = fixture["remoteStatus"] == implied_remote_status(actor_run)
        assert status_follows is (REMOTE_STATUS_OVERRIDE not in effects), spec.name
        assert (fixture["output"] is None) is (OUTPUT_RECORD_ABSENT in effects), spec.name
        echo = actor_run.output["queryScope"] if actor_run.output else None
        assert (fixture["admitted"]["queryScope"] == echo) is (
            ADMITTED_SCOPE_DIFFERS not in effects
        ), spec.name
        assert (fixture["datasetItems"] == actor_run.rows) is (
            ROWS_FROM_UNKNOWN_SCHEMA_RUN not in effects
        ), spec.name


def test_actor_json_identity_matches_the_contract() -> None:
    actor_json = json.loads((ACTOR_ROOT / ".actor" / "actor.json").read_text(encoding="utf-8"))

    assert actor_json["actorSpecification"] == 1
    assert actor_json["name"] == ACTOR_NAME
    assert actor_json["version"] == ACTOR_VERSION
    # The Actor version's major equals the contract major (MS2-D-38).
    assert ACTOR_VERSION.split(".")[0] == RUN_SCHEMA_VERSION.rsplit("/v", 1)[1]
    assert actor_json["input"] == "./input_schema.json"
    assert isinstance(actor_json["defaultMemoryMbytes"], int)
    assert isinstance(actor_json["maxMemoryMbytes"], int)
    assert actor_json["defaultMemoryMbytes"] <= actor_json["maxMemoryMbytes"]


def test_production_http_client_is_proxy_free_and_pinned() -> None:
    async def inspect() -> tuple[bool, bool]:
        async with new_http_client() as client:
            return client.trust_env, client.follow_redirects

    assert asyncio.run(inspect()) == (False, False)


def test_wall_clock_is_utc_second_precision() -> None:
    stamp = utc_now_seconds()

    assert len(stamp) == 20
    assert stamp.endswith("Z")
