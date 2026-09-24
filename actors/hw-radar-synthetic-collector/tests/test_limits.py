"""The Actor enforces every input cap itself (MS2-D-26; owner acceptance item 3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
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
