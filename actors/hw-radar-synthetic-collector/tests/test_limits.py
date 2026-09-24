"""The Actor enforces every input cap itself (MS2-D-26; owner acceptance item 3)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest

from tests.support import SOURCE_DIR, base_input, run_collect, serve_source

PAGE_1_BYTES = len((SOURCE_DIR / "catalog" / "page-1.json").read_bytes())


def _limits(output: dict[str, Any] | None) -> dict[str, bool]:
    assert output is not None
    return output["completeness"]["limitsHit"]


def _only_hit(name: str) -> dict[str, bool]:
    return {key: key == name for key in ("pages", "requests", "items", "time", "bytes")}


@pytest.mark.parametrize(
    ("cap", "overrides", "expected_rows", "expected_requests"),
    [
        # 3 listings over 2 pages; each cap is set just below what the source needs.
        ("items", {"maxItems": 2}, 2, 1),
        ("pages", {"maxPages": 1}, 2, 1),
        ("requests", {"maxRequests": 1}, 2, 1),
        # Page 1 fits, page 2 overruns the remaining budget and is discarded whole.
        ("bytes", {"maxBytes": PAGE_1_BYTES + 10}, 2, 2),
    ],
)
def test_item_page_request_byte_caps_enforced(
    cap: str, overrides: dict[str, Any], expected_rows: int, expected_requests: int
) -> None:
    stub, transport = serve_source()

    result = run_collect(base_input(**overrides), transport=transport)

    assert len(result.rows) == expected_rows
    assert len(stub.requests) == expected_requests
    assert _limits(result.output) == _only_hit(cap)
    assert result.output is not None
    report = result.output["completeness"]
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["itemsEmitted"] == expected_rows


def test_time_budget_enforced_between_pages() -> None:
    ticks: Iterator[float] = iter([0.0, 0.0, 5.0, 61.0, 61.0])
    stub, transport = serve_source()

    result = run_collect(
        base_input(timeBudgetSecs=60), transport=transport, clock=lambda: next(ticks)
    )

    assert len(stub.requests) == 1
    assert _limits(result.output) == _only_hit("time")


def test_request_timeout_is_the_remaining_time_budget() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("slow", request=request)

    ticks: Iterator[float] = iter([100.0, 100.0, 130.0])
    result = run_collect(
        base_input(timeBudgetSecs=60),
        transport=httpx.MockTransport(handler),
        clock=lambda: next(ticks),
    )

    assert seen[0]["read"] == pytest.approx(30.0)
    assert _limits(result.output) == _only_hit("time")
    assert result.rows == []


class _FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_slow_drip_response_cannot_outlast_the_time_budget() -> None:
    # Each chunk arrives 10 s after the last, always inside any per-read
    # timeout, so only a total deadline can stop it. Served whole, the page
    # would finish at t=16 x 10 s = 160 s, far past the 60 s budget.
    clock = _FakeClock()
    page = (SOURCE_DIR / "catalog" / "page-1.json").read_bytes()
    chunks = [page[i : i + 16] for i in range(0, len(page), 16)][:16]
    assert len(chunks) == 16
    served: list[float] = []

    async def drip() -> AsyncIterator[bytes]:
        for chunk in chunks:
            clock.now += 10.0
            served.append(clock.now)
            yield chunk

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=drip())

    result = run_collect(
        base_input(timeBudgetSecs=60), transport=httpx.MockTransport(handler), clock=clock
    )

    # The first chunk at or past the deadline ends the request: t=60 is the 6th.
    assert served[-1] == 60.0
    assert len(requests) == 1
    assert result.rows == []
    assert _limits(result.output) == _only_hit("time")
    assert result.output is not None
    assert result.output["errors"] == []
    assert result.output["completeness"]["truncated"] is True


def test_stalled_response_is_cut_off_at_the_remaining_budget_in_real_time() -> None:
    # The injected clock leaves 0.05 s of budget; the body then stalls, so no
    # chunk arrives for the per-chunk check to see. MockTransport ignores
    # httpx timeouts, which isolates the real-time ceiling on the request.
    ticks: Iterator[float] = iter([0.0, 59.95, 59.95, 59.95])

    async def stall() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(5)
        yield b"}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stall())

    started = time.monotonic()
    result = run_collect(
        base_input(timeBudgetSecs=60),
        transport=httpx.MockTransport(handler),
        clock=lambda: next(ticks),
    )

    assert time.monotonic() - started < 2.0
    assert result.rows == []
    assert _limits(result.output) == _only_hit("time")


def test_byte_and_time_limits_binding_on_one_chunk_are_both_reported() -> None:
    clock = _FakeClock()

    async def late_oversized() -> AsyncIterator[bytes]:
        clock.now = 61.0
        yield b"x" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=late_oversized())

    result = run_collect(
        base_input(timeBudgetSecs=60, maxBytes=10),
        transport=httpx.MockTransport(handler),
        clock=clock,
    )

    limits = _limits(result.output)
    assert limits["time"] is True
    assert limits["bytes"] is True


def test_source_that_exactly_fits_every_cap_is_complete() -> None:
    fits = base_input(maxItems=3, maxPages=2, maxRequests=2)

    result = run_collect(fits)

    assert result.output is not None
    assert result.output["completeness"]["complete"] is True
    assert result.output["completeness"]["truncated"] is False
    assert len(result.rows) == 3


def test_every_binding_limit_is_reported() -> None:
    result = run_collect(base_input(maxItems=2, maxPages=1, maxRequests=1))

    assert _limits(result.output) == {
        "pages": True,
        "requests": True,
        "items": True,
        "time": False,
        "bytes": False,
    }


def test_fault_truncations_use_the_real_cap_path() -> None:
    clock: Callable[[], float] = lambda: 0.0  # noqa: E731
    for mode, limit in (
        ("truncate_items", "items"),
        ("truncate_pages", "pages"),
        ("truncate_bytes", "bytes"),
        ("truncate_time", "time"),
    ):
        result = run_collect(base_input(faultMode=mode), clock=clock)
        assert _limits(result.output) == _only_hit(limit), mode


def test_http_error_and_bad_pages_become_errors_not_crashes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("page-1.json"):
            return httpx.Response(503)
        return httpx.Response(200, content=b"not json")

    result = run_collect(base_input(), transport=httpx.MockTransport(handler))

    assert result.output is not None
    assert [error["code"] for error in result.output["errors"]] == ["http_status", "invalid_page"]
    assert result.output["completeness"]["complete"] is False
    assert result.rows == []


def test_transport_error_is_recorded_and_the_next_page_still_fetched() -> None:
    stub, _ = serve_source()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("page-1.json"):
            raise httpx.ConnectError("refused", request=request)
        return stub.handler(request)

    result = run_collect(base_input(), transport=httpx.MockTransport(handler))

    assert result.output is not None
    assert result.output["errors"][0]["code"] == "fetch_error"
    assert len(result.rows) == 1
