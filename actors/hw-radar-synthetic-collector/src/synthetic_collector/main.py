"""Thin Apify SDK entry point around the pure core (MS2-D-38 structure).

Storage contract (MS2-D-25): merchant content goes only to the default dataset,
and the only default key-value record this Actor writes is OUTPUT, which holds
counts, scope, and errors. (The platform itself stores the run's INPUT record in
the same store; the Actor never writes it.)

Ordering invariant: rows are pushed BEFORE OUTPUT is written, and OUTPUT is
written before the run is failed. OUTPUT is therefore a commit marker: when it
exists, every row it counts is already in the dataset, and a crash between the
two leaves a missing OUTPUT, which hw-radar classifies as failed, never complete.

SCOPE: no proxy. The HTTP client is built with trust_env=False so no *_PROXY
environment variable can route traffic, and no Apify ProxyConfiguration is ever
constructed (MS2-D-26; tests/test_boundaries.py scans for it).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from apify import Actor

from synthetic_collector.contract import ACTOR_NAME, ACTOR_VERSION, OUTPUT_KEY
from synthetic_collector.core import InputRejected, collect, validate_input


class ActorRuntime(Protocol):
    """The subset of apify.Actor this Actor uses; tests substitute a recorder."""

    async def get_input(self) -> Any: ...

    async def push_data(
        self, data: dict[Any, Any] | list[dict[Any, Any]], *, charged_event_name: str | None = None
    ) -> object: ...

    async def set_value(self, key: str, value: Any, *, content_type: str | None = None) -> None: ...

    async def fail(
        self,
        *,
        exit_code: int = 1,
        exception: BaseException | None = None,
        status_message: str | None = None,
    ) -> None: ...


def new_http_client() -> httpx.AsyncClient:
    # follow_redirects=False: raw.githubusercontent.com serves pinned-commit
    # paths directly; a redirect would mean the fetch left the pinned content.
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        headers={"User-Agent": f"{ACTOR_NAME}/{ACTOR_VERSION}"},
    )


def utc_now_seconds() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_actor(
    actor: ActorRuntime,
    client_factory: Callable[[], httpx.AsyncClient] = new_http_client,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], str] = utc_now_seconds,
) -> None:
    """Validate input, collect, then store rows, OUTPUT, and the exit status in that order."""
    try:
        actor_input = validate_input(await actor.get_input())
    except InputRejected as exc:
        # No OUTPUT: there is no admitted scope to echo, and a missing OUTPUT is
        # classified failed by hw-radar.
        await actor.fail(status_message=f"input rejected: {exc}"[:1000])
        return
    async with client_factory() as client:
        result = await collect(actor_input, client, clock=clock, started_at=wall_clock())
    if result.rows:
        await actor.push_data(result.rows)
    if result.output is not None:
        await actor.set_value(OUTPUT_KEY, result.output)
    if not result.succeeded:
        await actor.fail(status_message="collection failed; see OUTPUT.errors")


async def main() -> None:
    async with Actor:
        await run_actor(Actor, new_http_client)
