"""The Actor enforces every input cap itself (MS2-D-26; owner acceptance item 3)."""

from __future__ import annotations

import asyncio
import gzip
import socket
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpcore
import httpx
import pytest

import synthetic_collector.main as entry
from synthetic_collector.core import HTTP_READ_CHUNK_BYTES, HTTP_RECEIVE_BUFFER_BYTES
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


# A body whose gzip encoding is larger than its decoded form: 15 decoded bytes,
# 35 on the wire. With maxBytes=20 a decoded-byte count would accept it; the
# wire count must not, because the wire bytes are what the run is billed for.
_TINY_PAGE = b'{"listings":[]}'
_TINY_PAGE_GZIP = gzip.compress(_TINY_PAGE, mtime=0)


@pytest.mark.parametrize(
    ("chunks", "headers", "max_bytes"),
    [
        # The R10-02 shape: one 100-byte read under maxBytes=10, then more body
        # the Actor must never pull.
        ([b"x" * 100, b"y" * 100, b"z" * 100], {}, 10),
        ([_TINY_PAGE_GZIP, b"never read"], {"Content-Encoding": "gzip"}, 20),
        # The 10-byte gzip header alone decodes to nothing, so a check that ran
        # only on decoded output would never see this read at all.
        ([_TINY_PAGE_GZIP[:10], _TINY_PAGE_GZIP[10:]], {"Content-Encoding": "gzip"}, 5),
    ],
    ids=[
        "oversized-first-read",
        "compressed-wire-over-decoded-under",
        "compressed-read-with-no-decoded-output",
    ],
)
def test_byte_cap_counts_wire_bytes_and_stops_on_first_crossing_chunk(
    chunks: list[bytes], headers: dict[str, str], max_bytes: int
) -> None:
    assert len(_TINY_PAGE) < 20 < len(_TINY_PAGE_GZIP)
    pulled: list[bytes] = []

    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            pulled.append(chunk)
            yield chunk

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers=headers, content=body())

    result = run_collect(base_input(maxBytes=max_bytes), transport=httpx.MockTransport(handler))

    assert pulled == chunks[:1]
    assert len(requests) == 1
    assert _limits(result.output) == _only_hit("bytes")
    assert result.rows == []


def test_byte_count_is_run_wide_across_responses_including_failed_ones() -> None:
    # Response 1 is a valid page, response 2 dies mid-body, and response 3
    # crosses the cap only because the first two responses' 30 bytes carry over
    # (a per-response count would see 16 of 40). Its third read is never pulled.
    pulled: list[tuple[int, bytes]] = []

    async def body(index: int) -> AsyncIterator[bytes]:
        if index == 0:
            pulled.append((index, _TINY_PAGE))
            yield _TINY_PAGE
        elif index == 1:
            pulled.append((index, _TINY_PAGE))
            yield _TINY_PAGE
            raise httpx.ReadError("connection reset")
        else:
            for chunk in (b"a" * 8, b"b" * 8, b"c" * 8):
                pulled.append((index, chunk))
                yield chunk

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body(len(requests) - 1))

    result = run_collect(
        base_input(
            maxBytes=40,
            fixturePaths=["catalog/page-1.json", "catalog/page-2.json", "empty/page-1.json"],
        ),
        transport=httpx.MockTransport(handler),
    )

    assert pulled == [(0, _TINY_PAGE), (1, _TINY_PAGE), (2, b"a" * 8), (2, b"b" * 8)]
    assert len(requests) == 3
    assert _limits(result.output) == _only_hit("bytes")
    assert result.output is not None
    assert [error["code"] for error in result.output["errors"]] == ["fetch_error"]


def test_compressed_body_within_wire_cap_is_decoded_and_parsed() -> None:
    page = (SOURCE_DIR / "catalog" / "page-1.json").read_bytes()
    wire = gzip.compress(page, mtime=0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=wire)

    result = run_collect(
        base_input(fixturePaths=["catalog/page-1.json"], maxBytes=len(wire)),
        transport=httpx.MockTransport(handler),
    )

    # maxBytes equals the wire size exactly: at the cap is within it.
    assert len(result.rows) == 2
    assert _limits(result.output)["bytes"] is False


def test_undecodable_body_is_an_invalid_page_not_a_crash() -> None:
    # Streamed, so the bad encoding reaches the Actor's decode step rather than
    # failing inside the mock's own eager read of a bytes body.
    async def body() -> AsyncIterator[bytes]:
        yield b"not gzip"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=body())

    result = run_collect(base_input(), transport=httpx.MockTransport(handler))

    assert result.output is not None
    assert [error["code"] for error in result.output["errors"]] == ["invalid_page"] * 2
    assert result.rows == []


def test_read_chunk_constant_is_httpcore_network_read_size() -> None:
    # The one-read overshoot bound hw-radar prices is httpcore's read size; an
    # httpcore upgrade that changes it must fail here, not silently widen it.
    assert httpcore.AsyncHTTP11Connection.READ_NUM_BYTES == HTTP_READ_CHUNK_BYTES == 65536


@pytest.mark.skipif(sys.platform != "linux", reason="SO_RCVBUF doubling is Linux semantics")
def test_http_client_pins_receive_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Read back from a real loopback connection. The pinned 65536 doubles to
    # 131072, which is also a common kernel default (tcp_rmem), so a second,
    # distinctive value proves the option really reaches the socket.
    async def serve_once(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        await writer.drain()
        writer.close()

    async def receive_buffer() -> int:
        server = await asyncio.start_server(serve_once, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with (
                entry.new_http_client() as client,
                client.stream("GET", f"http://127.0.0.1:{port}/") as response,
            ):
                sock: socket.socket = response.extensions["network_stream"].get_extra_info("socket")
                return sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        finally:
            server.close()
            await server.wait_closed()

    assert HTTP_RECEIVE_BUFFER_BYTES == 65536
    assert asyncio.run(receive_buffer()) == 2 * HTTP_RECEIVE_BUFFER_BYTES
    monkeypatch.setattr(entry, "HTTP_RECEIVE_BUFFER_BYTES", 12288)
    assert asyncio.run(receive_buffer()) == 2 * 12288


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
