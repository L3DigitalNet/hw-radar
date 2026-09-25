"""Offline test harness: a stub raw.githubusercontent.com and the contract-fixture builder.

No test touches the network: every fetch goes through serve_source(), an
httpx.MockTransport that answers pinned-commit URLs from fixtures/source/.

The frozen hw-radar fixtures (<repo>/tests/fixtures/apify_contract/v1/) are
generated here and only here, from each spec's faultMode, so the Actor's real
output and what hw-radar's classifier tests read cannot drift. A fixture is:
- Actor-produced content (datasetItems, output) regenerated from actorInput;
- plus the platform envelope the Actor does not control, remoteStatus and
  hw-radar's admitted scope, which must follow from the Actor's own behavior
  unless a declared platformEffect says otherwise (PLATFORM_EFFECTS).

Regenerate after an intentional contract or Actor change (from this directory):
    uv run python -m tests.support --write
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from synthetic_collector.contract import ACTOR_NAME
from synthetic_collector.core import (
    RAW_BASE_URL,
    SOURCE_ROOT,
    CollectionResult,
    collect,
    validate_input,
)

ACTOR_ROOT: Final = Path(__file__).resolve().parents[1]
SOURCE_DIR: Final = ACTOR_ROOT / "fixtures" / "source"
HW_RADAR_FIXTURE_DIR: Final = ACTOR_ROOT.parents[1] / "tests" / "fixtures" / "apify_contract" / "v1"

# A synthetic pin: tests never fetch, so any 40-hex value addresses the stub.
FIXTURE_COMMIT: Final = "1" * 40
STARTED_AT: Final = "2026-09-24T12:00:00Z"
CATALOG_PATHS: Final = ["catalog/page-1.json", "catalog/page-2.json"]

# The only envelope facts a fixture may state that the Actor's own run does not
# imply. Anything else in a fixture must regenerate exactly.
REMOTE_STATUS_OVERRIDE: Final = "remote_status_override"
OUTPUT_RECORD_ABSENT: Final = "output_record_absent"
ADMITTED_SCOPE_DIFFERS: Final = "admitted_scope_differs"
ROWS_FROM_UNKNOWN_SCHEMA_RUN: Final = "dataset_rows_from_fault_mode:unknown_schema"
PLATFORM_EFFECTS: Final = frozenset(
    {
        REMOTE_STATUS_OVERRIDE,
        OUTPUT_RECORD_ABSENT,
        ADMITTED_SCOPE_DIFFERS,
        ROWS_FROM_UNKNOWN_SCHEMA_RUN,
    }
)

FAULT_MODES: Final = (
    "none",
    "truncate_items",
    "truncate_pages",
    "truncate_time",
    "truncate_bytes",
    "partial_failure",
    "contradictory_report",
    "count_mismatch",
    "unknown_schema",
    "fail",
)


def base_input(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": "hw-radar-input/v1",
        "siteKey": "synthetic",
        "collectionScope": "synthetic:hdd:catalog",
        "categoryHint": "hdd",
        "maxItems": 50,
        "maxPages": 5,
        "maxRequests": 10,
        "maxBytes": 1_000_000,
        "timeBudgetSecs": 60,
        "fixtureCommit": FIXTURE_COMMIT,
        "fixturePaths": list(CATALOG_PATHS),
        "faultMode": "none",
    }
    payload.update(overrides)
    return payload


@dataclass
class StubSource:
    """Serves fixtures/source/ at any pinned commit and records every request."""

    requests: list[httpx.Request]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        prefix = f"{RAW_BASE_URL}/"
        url = str(request.url)
        if not url.startswith(prefix):
            return httpx.Response(404)
        commit, _, rest = url.removeprefix(prefix).partition("/")
        if len(commit) != 40 or not rest.startswith(f"{SOURCE_ROOT}/"):
            return httpx.Response(404)
        path = SOURCE_DIR / rest.removeprefix(f"{SOURCE_ROOT}/")
        if not path.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())


def serve_source() -> tuple[StubSource, httpx.MockTransport]:
    stub = StubSource(requests=[])
    return stub, httpx.MockTransport(stub.handler)


def still_clock() -> float:
    return 0.0


def run_collect(
    payload: dict[str, Any],
    *,
    transport: httpx.MockTransport | None = None,
    clock: Callable[[], float] = still_clock,
) -> CollectionResult:
    if transport is None:
        _, transport = serve_source()

    async def go(mock: httpx.MockTransport) -> CollectionResult:
        async with httpx.AsyncClient(transport=mock) as client:
            return await collect(
                validate_input(payload), client, clock=clock, started_at=STARTED_AT
            )

    return asyncio.run(go(transport))


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    description: str
    actor_input: dict[str, Any]
    effects: tuple[str, ...] = ()
    remote_status: str | None = None  # None: implied by the Actor's exit status


def _spec(
    name: str,
    description: str,
    fault_mode: str = "none",
    paths: list[str] | None = None,
    effects: tuple[str, ...] = (),
    remote_status: str | None = None,
) -> FixtureSpec:
    actor_input = base_input(faultMode=fault_mode, fixturePaths=paths or list(CATALOG_PATHS))
    return FixtureSpec(name, description, actor_input, effects, remote_status)


# The D1 fixture set (plan task D1), plus truncated-by-time so every truncation
# cause has a SUCCEEDED fixture of its own.
FIXTURE_SPECS: Final = (
    _spec("complete", "Every declared page and item fetched within all caps."),
    _spec(
        "complete-empty",
        "A source that declares zero items: proven complete-empty.",
        paths=["empty/page-1.json"],
    ),
    _spec(
        "ambiguous-empty",
        "SUCCEEDED, empty dataset, itemsDeclared missing: the source declared nothing.",
        paths=["undeclared-empty/page-1.json"],
    ),
    _spec(
        "nonempty-unusable",
        "A v1 OUTPUT counting rows that all carry an unknown listing major.",
        effects=(ROWS_FROM_UNKNOWN_SCHEMA_RUN,),
    ),
    _spec("truncated-by-pages", "The page cap stopped the run.", "truncate_pages"),
    _spec("truncated-by-items", "The item cap stopped the run.", "truncate_items"),
    _spec("truncated-by-bytes", "The byte cap stopped the run.", "truncate_bytes"),
    _spec("truncated-by-time", "The Actor's own time budget stopped the run.", "truncate_time"),
    _spec(
        "timed-out",
        "The platform timed the run out after the Actor's time budget tripped.",
        "truncate_time",
        effects=(REMOTE_STATUS_OVERRIDE,),
        remote_status="TIMED-OUT",
    ),
    _spec(
        "partial-with-errors", "A page fetch failed; the run still succeeded.", "partial_failure"
    ),
    _spec("failed-with-items", "The Actor failed after emitting page 1.", "fail"),
    _spec(
        "missing-output",
        "The Actor failed and its OUTPUT record is absent.",
        "fail",
        effects=(OUTPUT_RECORD_ABSENT,),
    ),
    _spec(
        "unknown-schema",
        "OUTPUT and rows carry a contract major hw-radar does not know.",
        "unknown_schema",
    ),
    _spec("count-mismatch", "itemsEmitted disagrees with the dataset count.", "count_mismatch"),
    _spec(
        "complete-with-limit-hit",
        "complete=true together with a hit limit.",
        "contradictory_report",
    ),
    _spec(
        "status-mismatch",
        "The platform says SUCCEEDED while OUTPUT.status says failed.",
        "fail",
        effects=(REMOTE_STATUS_OVERRIDE,),
        remote_status="SUCCEEDED",
    ),
    _spec(
        "scope-mismatch",
        "The OUTPUT echo differs from the scope hw-radar admitted.",
        effects=(ADMITTED_SCOPE_DIFFERS,),
    ),
)


def implied_remote_status(result: CollectionResult) -> str:
    return "SUCCEEDED" if result.succeeded else "FAILED"


def build_fixture(spec: FixtureSpec) -> dict[str, Any]:
    unknown = set(spec.effects) - PLATFORM_EFFECTS
    if unknown:
        raise ValueError(f"{spec.name}: undeclared platform effects {sorted(unknown)}")
    result = run_collect(spec.actor_input)
    rows = result.rows
    if ROWS_FROM_UNKNOWN_SCHEMA_RUN in spec.effects:
        rows = run_collect({**spec.actor_input, "faultMode": "unknown_schema"}).rows
    output = None if OUTPUT_RECORD_ABSENT in spec.effects else result.output
    remote_status = spec.remote_status or implied_remote_status(result)
    query_scope = validate_input(spec.actor_input).query_scope
    if ADMITTED_SCOPE_DIFFERS in spec.effects:
        query_scope = {**query_scope, "collectionScope": "synthetic:hdd:other-query"}
    return {
        "fixture": spec.name,
        "description": spec.description,
        "generatedBy": "actors/hw-radar-synthetic-collector/tests/support.py",
        "actorInput": spec.actor_input,
        "startedAt": STARTED_AT,
        "platformEffects": list(spec.effects),
        "remoteStatus": remote_status,
        "admitted": {"actorName": ACTOR_NAME, "queryScope": query_scope},
        "datasetItems": rows,
        "output": output,
    }


def fixture_text(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"


def write_fixtures() -> None:
    HW_RADAR_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for spec in FIXTURE_SPECS:
        (HW_RADAR_FIXTURE_DIR / f"{spec.name}.json").write_text(
            fixture_text(build_fixture(spec)), encoding="utf-8"
        )


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python -m tests.support --write")
    write_fixtures()
