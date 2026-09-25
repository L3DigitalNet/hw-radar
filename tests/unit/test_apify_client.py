"""Apify REST client (MS2-D-15, plan task D3): wire shape, parsing, token hygiene.

Every test runs against ``httpx.MockTransport``; nothing touches the network.
Response bodies are hand-built in Apify's documented ``{"data": ...}`` shape.
"""

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from hw_radar.acquisition.apify.client import (
    APIFY_TOKEN_ENV,
    DEFAULT_REQUEST_TIMEOUT,
    ApifyApiError,
    ApifyClient,
    ApifyNotFoundError,
    ApifyResponseError,
    ApifyTokenMissingError,
)

# Distinctive so a substring search over logs, reprs, and messages cannot
# produce a false negative by matching something else.
TOKEN = "apify_api_SECRETtoken0123456789"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, *, token: str = TOKEN) -> ApifyClient:
    return ApifyClient(token, transport=httpx.MockTransport(handler))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _run_body(**overrides: object) -> dict[str, object]:
    run: dict[str, object] = {
        "id": "run123",
        "actId": "act456",
        "status": "RUNNING",
        "startedAt": "2026-09-24T10:00:00.000Z",
        "finishedAt": None,
        "buildId": "build789",
        "buildNumber": "0.1.3",
        "defaultDatasetId": "ds1",
        "defaultKeyValueStoreId": "kv1",
        "options": {"memoryMbytes": 256, "timeoutSecs": 300, "build": "latest", "maxItems": None},
        "usageTotalUsd": None,
        "usageUsd": None,
    }
    run.update(overrides)
    return {"data": run}


def _json_response(body: object, status: int = 200, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, json=body, **kwargs)


def _error(status: int, error_type: str, message: str) -> httpx.Response:
    return _json_response({"error": {"type": error_type, "message": message}}, status)


def test_start_sends_memory_and_timeout_run_options() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_run_body(options={"memoryMbytes": 512, "timeoutSecs": 3600}), 201)

    async def go() -> None:
        async with _client(handler) as client:
            run = await client.start_run(
                "hw-radar/synthetic-collector",
                {"pages": ["a.html"]},
                memory_mbytes=512,
                timeout_secs=3600,
            )
        assert run.options.memory_mbytes == 512
        assert run.options.timeout_secs == 3600

    _run(go())
    (request,) = seen
    assert request.method == "POST"
    # `user/name` is rewritten to the REST path form `user~name`.
    assert request.url.path == "/v2/acts/hw-radar~synthetic-collector/runs"
    assert dict(request.url.params) == {"memory": "512", "timeout": "3600"}
    assert json.loads(request.content) == {"pages": ["a.html"]}
    # The httpx request timeout is the client's fixed tier, not the Actor's
    # 3600 s run timeout: conflating them is the MS2-D-15 naming trap.
    timeout = request.extensions["timeout"]
    assert timeout == DEFAULT_REQUEST_TIMEOUT.as_dict()
    assert 3600 not in timeout.values()


def test_start_passes_build_and_returns_options_for_verification() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # Apify applied different options than requested; the client must
        # surface them unchanged so the caller can detect the mismatch.
        return _json_response(_run_body(options={"memoryMbytes": 1024, "timeoutSecs": 60}), 201)

    async def go() -> None:
        async with _client(handler) as client:
            run = await client.start_run(
                "act456", {}, memory_mbytes=256, timeout_secs=300, build="0.1.3"
            )
        assert (run.options.memory_mbytes, run.options.timeout_secs) == (1024, 60)

    _run(go())
    assert seen[0].url.params["build"] == "0.1.3"


def test_client_never_sends_max_items_or_max_total_charge() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_run_body(), 201)

    async def go() -> None:
        async with _client(handler) as client:
            await client.start_run("act456", {"x": 1}, memory_mbytes=256, timeout_secs=300)
            await client.start_run("act456", {}, memory_mbytes=128, timeout_secs=60, build="b")

    _run(go())
    for request in seen:
        sent = (str(request.url) + request.content.decode()).lower()
        assert "maxitems" not in sent
        assert "maxtotalchargeusd" not in sent
        assert "waitforfinish" not in sent
        assert set(request.url.params.keys()) <= {"memory", "timeout", "build"}


def test_no_proxy_parameters_ever_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_run_body(), 201)

    async def go() -> None:
        async with _client(handler) as client:
            await client.start_run("act456", {"pages": []}, memory_mbytes=256, timeout_secs=300)
            for key in ("proxyConfiguration", "proxy", "useApifyProxy"):
                with pytest.raises(ValueError, match="Proxy"):
                    await client.start_run(
                        "act456",
                        {key: {"useApifyProxy": True}},
                        memory_mbytes=256,
                        timeout_secs=300,
                    )

    _run(go())
    # Only the clean input reached the transport; the proxy inputs were
    # rejected before any request was built.
    assert len(seen) == 1
    assert "proxy" not in (str(seen[0].url) + seen[0].content.decode()).lower()


def test_start_rejects_non_positive_options_before_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async def go() -> None:
        async with _client(handler) as client:
            with pytest.raises(ValueError):
                await client.start_run("act456", {}, memory_mbytes=0, timeout_secs=300)
            with pytest.raises(ValueError):
                await client.start_run("act456", {}, memory_mbytes=256, timeout_secs=-1)

    _run(go())


def test_run_parser_keeps_usage_nullable_and_returns_finished_at() -> None:
    bodies = [
        _run_body(status="SUCCEEDED", finishedAt="2026-09-24T10:05:00.000Z"),
        _run_body(
            status="SUCCEEDED",
            finishedAt="2026-09-24T10:05:00.000Z",
            usageTotalUsd=0.00079,
            usageUsd={"ACTOR_COMPUTE_UNITS": 0.0007, "DATASET_WRITES": 0.00009},
            usage={"ACTOR_COMPUTE_UNITS": 0.0035, "PROXY_RESIDENTIAL_TRANSFER_GBYTES": 0},
        ),
    ]
    # A run object with the usage keys absent altogether, not merely null.
    bare = _run_body(status="RUNNING")
    run_obj = bare["data"]
    assert isinstance(run_obj, dict)
    del run_obj["usageTotalUsd"], run_obj["usageUsd"]
    bodies.append(bare)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/actor-runs/run123"
        return _json_response(bodies.pop(0))

    async def go() -> None:
        async with _client(handler) as client:
            first = await client.get_run("run123")
            second = await client.get_run("run123")
            third = await client.get_run("run123")
        assert first.usage_total_usd is None and first.usage_usd is None
        assert first.finished_at == datetime(2026, 9, 24, 10, 5, tzinfo=UTC)
        # Exact decimal from the JSON text: no float round-trip.
        assert second.usage_total_usd == Decimal("0.00079")
        assert second.usage_usd == {
            "ACTOR_COMPUTE_UNITS": Decimal("0.0007"),
            "DATASET_WRITES": Decimal("0.00009"),
        }
        assert (third.usage_total_usd, third.usage_usd, third.finished_at) == (None, None, None)
        # Quantities are kept per item so MS2-D-26 can see any proxy usage.
        assert second.usage == {
            "ACTOR_COMPUTE_UNITS": Decimal("0.0035"),
            "PROXY_RESIDENTIAL_TRANSFER_GBYTES": Decimal(0),
        }
        assert first.usage is None and third.usage is None
        assert second.build_number == "0.1.3"
        assert second.default_dataset_id == "ds1"

    _run(go())


def test_build_record_parsed_with_nullable_usage() -> None:
    bodies = [
        {
            "data": {
                "id": "build789",
                "actId": "act456",
                "status": "SUCCEEDED",
                "buildNumber": "0.1.3",
                "startedAt": "2026-09-24T09:00:00.000Z",
                "finishedAt": "2026-09-24T09:00:15.000Z",
                "usageTotalUsd": 0.0041,
                "usageUsd": {"ACTOR_COMPUTE_UNITS": 0.0041},
                "usage": {"ACTOR_COMPUTE_UNITS": 0.0205},
            }
        },
        {"data": {"id": "build790", "status": "RUNNING", "finishedAt": None}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.startswith("/v2/actor-builds/")
        return _json_response(bodies.pop(0))

    async def go() -> None:
        async with _client(handler) as client:
            done = await client.get_build("build789")
            running = await client.get_build("build790")
        assert done.status == "SUCCEEDED"
        assert done.finished_at == datetime(2026, 9, 24, 9, 0, 15, tzinfo=UTC)
        assert done.usage_total_usd == Decimal("0.0041")
        assert done.usage_usd == {"ACTOR_COMPUTE_UNITS": Decimal("0.0041")}
        assert done.usage == {"ACTOR_COMPUTE_UNITS": Decimal("0.0205")}
        assert running.finished_at is None
        assert running.usage_total_usd is None and running.usage_usd is None
        assert running.usage is None

    _run(go())


def test_abort_posts_to_abort_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/v2/actor-runs/run123/abort")
        return _json_response(_run_body(status="ABORTING"))

    async def go() -> None:
        async with _client(handler) as client:
            assert (await client.abort_run("run123")).status == "ABORTING"

    _run(go())


def test_dataset_pagination_reads_every_page() -> None:
    items = [{"n": i} for i in range(5)]
    seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/datasets/ds1/items"
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        seen.append((offset, limit))
        return _json_response(
            items[offset : offset + limit],
            headers={"X-Apify-Pagination-Total": str(len(items))},
        )

    async def go() -> list[object]:
        async with _client(handler) as client:
            return [item async for item in client.iter_dataset_items("ds1", page_size=2)]

    assert _run(go()) == items
    # Stops at the reported total: no extra (billable) request for an empty page.
    assert seen == [(0, 2), (2, 2), (4, 2)]


def test_dataset_pagination_without_total_header_stops_on_short_page() -> None:
    items = [{"n": i} for i in range(4)]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        calls.append(offset)
        return _json_response(items[offset : offset + 2])

    async def go() -> list[object]:
        async with _client(handler) as client:
            return [item async for item in client.iter_dataset_items("ds1", page_size=2)]

    # Exactly-full last page without a total needs one empty-page probe.
    assert _run(go()) == items
    assert calls == [0, 2, 4]


def test_dataset_items_must_be_an_array() -> None:
    async def go() -> None:
        async with _client(lambda _r: _json_response({"data": []})) as client:
            with pytest.raises(ApifyResponseError):
                await client.list_dataset_items("ds1")

    _run(go())


def test_get_record_returns_raw_body_or_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/key-value-stores/kv1/records/OUTPUT":
            return httpx.Response(
                200, content=b'{"ok":true}', headers={"Content-Type": "application/json"}
            )
        return _error(404, "record-not-found", "Record was not found")

    async def go() -> None:
        async with _client(handler) as client:
            record = await client.get_record("kv1", "OUTPUT")
            assert record is not None
            assert record.body == b'{"ok":true}'
            assert record.content_type == "application/json"
            assert await client.get_record("kv1", "MISSING") is None

    _run(go())


def test_delete_404_is_success() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/gone"):
            return _error(404, "record-not-found", "Dataset was not found")
        return httpx.Response(204)

    async def go() -> None:
        async with _client(handler) as client:
            assert await client.delete_dataset("ds1") == "deleted"
            assert await client.delete_dataset("gone") == "absent"
            assert await client.delete_key_value_store("kv1") == "deleted"
            assert await client.delete_key_value_store("gone") == "absent"

    _run(go())
    assert seen == [
        ("DELETE", "/v2/datasets/ds1"),
        ("DELETE", "/v2/datasets/gone"),
        ("DELETE", "/v2/key-value-stores/kv1"),
        ("DELETE", "/v2/key-value-stores/gone"),
    ]


def test_delete_other_errors_raise() -> None:
    async def go() -> None:
        async with _client(lambda _r: _error(500, "internal-error", "boom")) as client:
            with pytest.raises(ApifyApiError) as info:
                await client.delete_dataset("ds1")
        assert info.value.status_code == 500
        assert not isinstance(info.value, ApifyNotFoundError)

    _run(go())


def test_account_limits_parsed_into_cycle_bounds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/v2/users/me/limits")
        return _json_response(
            {
                "data": {
                    "monthlyUsageCycle": {
                        "startAt": "2026-09-05T00:00:00.000Z",
                        "endAt": "2026-10-04T23:59:59.999Z",
                    },
                    "limits": {"maxMonthlyUsageUsd": 19, "dataRetentionDays": 31},
                    "current": {"monthlyUsageUsd": 0.0931},
                }
            }
        )

    async def go() -> None:
        async with _client(handler) as client:
            limits = await client.get_account_limits()
        assert limits.cycle_start == datetime(2026, 9, 5, tzinfo=UTC)
        assert limits.cycle_end == datetime(2026, 10, 4, 23, 59, 59, 999000, tzinfo=UTC)
        assert limits.max_monthly_usage_usd == Decimal(19)
        assert limits.monthly_usage_usd == Decimal("0.0931")

    _run(go())


@pytest.mark.parametrize(
    "cycle",
    [
        None,
        {"startAt": "2026-09-05T00:00:00.000Z"},
        {"startAt": "2026-10-05T00:00:00.000Z", "endAt": "2026-09-05T00:00:00.000Z"},
        {"startAt": "not-a-date", "endAt": "2026-10-04T23:59:59.999Z"},
    ],
)
def test_account_limits_without_valid_cycle_fail_closed(cycle: object) -> None:
    # MS2-D-40 denies with cycle_unknown when the cycle is unobservable; the
    # client must raise rather than invent bounds.
    body: dict[str, object] = {"data": {"monthlyUsageCycle": cycle, "limits": {}, "current": {}}}

    async def go() -> None:
        async with _client(lambda _r: _json_response(body)) as client:
            with pytest.raises(ApifyResponseError):
                await client.get_account_limits()

    _run(go())


def test_monthly_usage_parsed_with_date_parameter() -> None:
    seen: list[httpx.Request] = []
    body = {
        "data": {
            "usageCycle": {
                "startAt": "2026-09-05T00:00:00.000Z",
                "endAt": "2026-10-04T23:59:59.999Z",
            },
            "monthlyServiceUsage": {
                "ACTOR_COMPUTE_UNITS": {"quantity": 0.4, "amountAfterVolumeDiscountUsd": 0.08},
                "DATASET_READS": {"quantity": 1000, "amountAfterVolumeDiscountUsd": 0.0004},
                "BROKEN": {"quantity": 1},
            },
            "dailyServiceUsages": [
                {
                    "date": "2026-09-05T00:00:00.000Z",
                    "serviceUsage": {"ACTOR_COMPUTE_UNITS": {"amountAfterVolumeDiscountUsd": 0.08}},
                    "totalUsageCreditsUsd": 0.08,
                }
            ],
            "totalUsageCreditsUsdAfterVolumeDiscount": 0.0804,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v2/users/me/usage/monthly"
        return _json_response(body)

    async def go() -> None:
        async with _client(handler) as client:
            usage = await client.get_monthly_usage(date(2026, 9, 5))
            await client.get_monthly_usage()
        assert usage.cycle_start == datetime(2026, 9, 5, tzinfo=UTC)
        assert usage.cycle_end is not None and usage.cycle_end.month == 10
        # An item with no parseable amount is dropped, not read as zero.
        assert usage.service_usd == {
            "ACTOR_COMPUTE_UNITS": Decimal("0.08"),
            "DATASET_READS": Decimal("0.0004"),
        }
        (day,) = usage.daily
        assert day.date == datetime(2026, 9, 5, tzinfo=UTC)
        assert day.service_usd == {"ACTOR_COMPUTE_UNITS": Decimal("0.08")}
        assert day.total_usd == Decimal("0.08")
        assert usage.total_usd == Decimal("0.0804")

    _run(go())
    assert dict(seen[0].url.params) == {"date": "2026-09-05"}
    assert dict(seen[1].url.params) == {}


def test_account_plan_parsed_from_users_me() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/users/me"
        return _json_response(
            {
                "data": {
                    "username": "someone",
                    "plan": {
                        "id": "STARTER",
                        "monthlyBasePriceUsd": 19,
                        "monthlyUsageCreditsUsd": 19,
                    },
                }
            }
        )

    async def go() -> None:
        async with _client(handler) as client:
            plan = await client.get_account_plan()
        assert plan.plan_id == "STARTER"
        assert plan.monthly_base_price_usd == Decimal(19)
        assert plan.monthly_usage_credits_usd == Decimal(19)

    _run(go())


def test_token_sent_only_as_bearer_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_run_body())

    async def go() -> None:
        async with _client(handler) as client:
            await client.get_run("run123")

    _run(go())
    (request,) = seen
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)


def test_token_never_in_logs_errors_reprs_or_detail(caplog: pytest.LogCaptureFixture) -> None:
    # Apify's error text is untrusted; this one echoes the token back to prove
    # the client scrubs it before it can reach a log line or persisted error.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/abort"):
            return _error(401, "token-not-valid", f"Token {TOKEN} is not valid")
        return _json_response(
            _run_body(
                status="SUCCEEDED", usageTotalUsd=0.001, usageUsd={"ACTOR_COMPUTE_UNITS": 0.001}
            )
        )

    async def go() -> list[str]:
        texts: list[str] = []
        async with _client(handler) as client:
            texts.append(repr(client))
            run = await client.get_run("run123")
            texts += [repr(run), json.dumps(run.detail())]
            with pytest.raises(ApifyApiError) as info:
                await client.abort_run("run123")
            texts += [str(info.value), repr(info.value), info.value.message]
            assert info.value.status_code == 401
            assert info.value.error_type == "token-not-valid"
        return texts

    with caplog.at_level(logging.DEBUG):
        texts = _run(go())
    texts += [record.getMessage() for record in caplog.records]
    assert caplog.records, "httpx request logging should have been captured"
    for text in texts:
        assert TOKEN not in text


def test_token_read_lazily_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_run_body())

    monkeypatch.setenv(APIFY_TOKEN_ENV, "env-token-value")
    client = ApifyClient(transport=httpx.MockTransport(handler))

    async def go() -> None:
        async with client:
            await client.get_run("run123")

    _run(go())
    assert seen[0].headers["Authorization"] == "Bearer env-token-value"


def test_missing_token_fails_without_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(APIFY_TOKEN_ENV, raising=False)
    with pytest.raises(ApifyTokenMissingError, match=APIFY_TOKEN_ENV):
        ApifyClient()
    monkeypatch.setenv(APIFY_TOKEN_ENV, "")
    with pytest.raises(ApifyTokenMissingError):
        ApifyClient()


def test_get_run_404_raises_not_found() -> None:
    async def go() -> None:
        async with _client(
            lambda _r: _error(404, "record-not-found", "Actor run was not found")
        ) as client:
            with pytest.raises(ApifyNotFoundError) as info:
                await client.get_run("nope")
        assert info.value.error_type == "record-not-found"

    _run(go())


def test_non_json_error_body_still_raises_api_error() -> None:
    async def go() -> None:
        async with _client(
            lambda _r: httpx.Response(502, content=b"<html>bad gateway</html>")
        ) as client:
            with pytest.raises(ApifyApiError) as info:
                await client.get_run("run123")
        assert (info.value.status_code, info.value.error_type) == (502, None)

    _run(go())


@pytest.mark.parametrize(
    "body",
    [
        {"nodata": {}},
        {"data": {"status": "RUNNING"}},
        {"data": {"id": "run123"}},
    ],
)
def test_malformed_run_response_raises(body: object) -> None:
    async def go() -> None:
        async with _client(lambda _r: _json_response(body)) as client:
            with pytest.raises(ApifyResponseError):
                await client.get_run("run123")

    _run(go())


def test_invalid_json_success_body_raises() -> None:
    async def go() -> None:
        async with _client(lambda _r: httpx.Response(200, content=b"not json")) as client:
            with pytest.raises(ApifyResponseError):
                await client.get_run("run123")

    _run(go())


@pytest.mark.parametrize("bad", ["", "..", "a/b"])
def test_identifiers_cannot_escape_their_path_segment(bad: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async def go() -> None:
        async with _client(handler) as client:
            with pytest.raises(ValueError):
                await client.get_run(bad)

    _run(go())


def test_iter_dataset_items_requires_an_explicit_page_size() -> None:
    # MS2-D-32 (revision 11, R10-08): the importer must send the derived
    # page_limit, so iteration has no default a caller could fall back to and
    # silently read 1000-row pages past the dataset page byte cap.
    parameter = inspect.signature(ApifyClient.iter_dataset_items).parameters["page_size"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
