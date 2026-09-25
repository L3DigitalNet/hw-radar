"""The Actor never claims completeness the source does not support.

Each case is a source shape that must surface as an OUTPUT error (so hw-radar
classifies partial_failure, never complete) rather than as silent success.
"""

from __future__ import annotations

import asyncio
import json
from types import TracebackType
from typing import Any

import httpx
import pytest

import synthetic_collector.main as entry
from synthetic_collector.contract import load_schema
from synthetic_collector.core import MAX_ERRORS
from tests.support import base_input, mock_client, run_collect, serve_source


def _serving(page: object) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps(page).encode())
    )


def _codes(output: dict[str, Any] | None) -> list[str]:
    assert output is not None
    return [error["code"] for error in output["errors"]]


def test_declared_pages_missing_from_input_are_an_error() -> None:
    result = run_collect(base_input(fixturePaths=["catalog/page-1.json"]))

    assert _codes(result.output) == ["source_pages_unlisted"]
    assert result.output is not None
    assert result.output["completeness"]["complete"] is False


def test_declared_item_total_mismatch_is_an_error() -> None:
    page: dict[str, object] = {"pagesDeclared": 1, "itemsDeclared": 5, "listings": []}

    result = run_collect(base_input(fixturePaths=["x.json"]), transport=_serving(page))

    assert _codes(result.output) == ["declared_item_count_mismatch"]


@pytest.mark.parametrize("page", [["a", "list"], {"listings": "nope"}])
def test_page_without_a_listings_array_is_an_error(page: object) -> None:
    result = run_collect(base_input(fixturePaths=["x.json"]), transport=_serving(page))

    assert _codes(result.output) == ["invalid_page"]


def test_invalid_listings_are_skipped_and_the_error_list_is_capped() -> None:
    listings: list[object] = [{"title": "no id"}] * (MAX_ERRORS + 10)
    listings += ["not an object", {"id": "x", "title": "", "price": "1", "currency": "USD"}]
    page = {"listings": listings}

    result = run_collect(base_input(fixturePaths=["x.json"]), transport=_serving(page))

    codes = _codes(result.output)
    assert len(codes) == MAX_ERRORS
    assert codes[-1] == "errors_truncated"
    assert set(codes[:-1]) == {"invalid_source_listing"}
    assert result.rows == []


def test_missing_contract_file_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("hw-radar-nonexistent-v1.schema.json")


class _SdkActorStandIn:
    """Mimics `async with Actor:` plus the four calls run_actor makes."""

    def __init__(self) -> None:
        self.entered = False
        self.kv: dict[str, Any] = {}

    async def __aenter__(self) -> _SdkActorStandIn:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def get_input(self) -> Any:
        return base_input()

    async def push_data(self, data: Any, *, charged_event_name: str | None = None) -> object:
        return None

    async def set_value(self, key: str, value: Any, *, content_type: str | None = None) -> None:
        self.kv[key] = value

    async def fail(
        self,
        *,
        exit_code: int = 1,
        exception: BaseException | None = None,
        status_message: str | None = None,
    ) -> None:
        raise AssertionError(status_message)


def test_main_runs_inside_the_sdk_actor_context(monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = _SdkActorStandIn()
    _, transport = serve_source()
    monkeypatch.setattr(entry, "Actor", stand_in)
    monkeypatch.setattr(entry, "new_http_client", lambda: mock_client(transport))

    asyncio.run(entry.main())

    assert stand_in.entered
    assert list(stand_in.kv) == ["OUTPUT"]
