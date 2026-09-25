"""Thin async client for the Apify REST API v2 (MS2-D-15; plan task D3).

This module is the only place hw-radar speaks HTTP to Apify. It exposes exactly
the calls MS2-D-15 inventories, each returning a small frozen dataclass rather
than the raw JSON, so callers never depend on Apify's wire names:

* runs: ``start_run``, ``get_run``, ``abort_run``;
* run storages: ``list_dataset_items`` / ``iter_dataset_items`` (paginated),
  ``get_record``, ``delete_dataset``, ``delete_key_value_store``;
* account reads for the billing-cycle budget (MS2-D-40): ``get_account_limits``,
  ``get_monthly_usage``, and ``get_account_plan`` (the plan block, because the
  limits response does not carry the base price or prepaid credit);
* ``get_build`` for operator builds settled through the ledger (MS2-D-46).

There is deliberately no retry, backoff, or polling loop here. The poll job
(MS2-D-16) owns cadence and retries, and a retry hidden inside the transport
would make a billable ``start_run`` non-idempotent. Transport failures
(``httpx.TransportError``) propagate unchanged so ``classify_exception`` maps
them to TRANSIENT; any non-2xx answer raises
``ApifyApiError`` carrying the status and Apify's error ``type``.

Cost-safety contract (MS2-D-15, MS2-D-26):

* ``start_run`` sends only the ``memory`` (MB) and ``timeout`` (s) run options,
  an explicit ``restartOnError=false``, and an optional ``build``. A platform
  restart could run past ``memory x timeout``, so it is refused rather than left
  to the Actor's default (ED-20). It never sends ``maxItems`` or
  ``maxTotalChargeUsd``: both apply only to pay-per-result / pay-per-event
  Actors and would give a false sense of a cost bound for our own Actors.
* No proxy option is ever sent, and a run input with a top-level proxy key is
  rejected before any request: proxy traffic is billed per GB or per request,
  so the memory-times-timeout bound MS2-D-26 admits against would not cover it.
* The Actor's ``timeout`` run option (seconds of Actor runtime) and the httpx
  request timeout are unrelated. The request timeout is fixed per client
  (``request_timeout``) and never derived from the run options.
* Money is parsed as ``Decimal`` straight from the JSON text, never through
  ``float``, and ``usage_total_usd`` stays ``None`` when Apify omits or nulls
  it: a missing figure must never read as a zero-cost run (MS2-D-41).
* A ``usage`` / ``usageUsd`` entry whose value is not a number is never
  silently dropped: its name lands in ``unparseable_usage``, because the
  MS2-D-26 allowlist latch must trip on it (ED-10).

Wire ceiling (MS2-D-32 *Per-call bound*). Slice E prices every call from a
fixed byte bound, so this client enforces the bound's parts rather than
trusting Apify to stay small:

* Every body is streamed and read only up to its cap: a dataset page up to
  ``settings.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES``, every other body (control
  calls, error bodies, KV records) up to
  ``settings.HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES``. A larger body raises
  ``ApifyResponseTooLargeError`` and the connection is dropped unread. Valid
  content never reaches a cap, so the caller treats the error as a latch trip
  (``api_response_over_cap``), not a retry. Both the wire (possibly compressed)
  and the decoded byte counts are held to the cap: the wire count bounds
  transfer, the decoded count bounds memory against a compression bomb.
* Every socket sets ``SO_RCVBUF`` to ``HTTP_RECEIVE_BUFFER_BYTES``. Stopping
  reading does not stop the sender, and whatever the kernel has already
  accepted is billed transfer; Linux doubles the value, so at most
  ``2 x HTTP_RECEIVE_BUFFER_BYTES`` arrive after an abandoned response, where
  the autotuned default can grow to megabytes.
* A request body above ``MAX_API_REQUEST_BODY_BYTES`` is refused before
  anything is sent (only ``start_run`` has a body).
* The rest of the per-call overhead (response headers, TLS handshake) is
  bounded by httpcore itself: ``MAX_INCOMPLETE_EVENT_SIZE`` (100 KiB) for the
  header block. ``tests/unit/test_apify_client.py`` pins that constant and
  ``READ_NUM_BYTES`` so an httpcore upgrade that changes either fails the gate.

Token handling: the token comes from the constructor or, when omitted, from the
``HW_RADAR_APIFY_TOKEN`` environment variable at construction time (never at
import). It travels only in the ``Authorization`` header, never in a query
string, so it cannot appear in a URL that httpx logs or an exception message
quotes. The client's repr masks it, error messages are scrubbed of it, and the
parsed records and their ``detail()`` dicts contain only Apify's response
fields. ``trust_env=False`` stops httpx from picking up ambient proxy or
``.netrc`` settings for a client that carries a credential.

Wire-name verification (plan D3 bullet). Checked against the official API
reference on 2026-09-24; several pages render client-side, so parts of the field
lists come from a fetched-page summary rather than a verbatim schema block, and
F5a's live reads remain the final check:

* Envelope ``{"data": ...}``, error ``{"error": {"type", "message"}}``, and
  ``Authorization: Bearer``: https://docs.apify.com/api/v2
* Start run (201; ``memory``, ``timeout``, ``build``, boolean
  ``restartOnError``; documents ``invalid-input`` and ``invalid-input-schema``
  as synchronous 400s). The path is ``POST /v2/actors/{actorId}/runs``; the old
  ``/v2/acts/`` prefix is "deprecated but still fully functional" (API
  introduction). Re-verified 2026-09-25 against the published OpenAPI document
  (``https://docs.apify.com/api/openapi.json``, version
  ``v2-2026-09-24T114302Z``), which lists no ``/v2/acts/`` path at all:
  https://docs.apify.com/api/v2/actors-runs-post
* Run object (``usageTotalUsd``, ``usageUsd``, ``usage``, ``buildNumber``,
  ``finishedAt``, ``options.memoryMbytes``/``timeoutSecs``/``maxItems``,
  ``stats.restartCount``; the first read after completion may be preliminary):
  https://docs.apify.com/api/v2/actor-run-get. The ``usage`` / ``usageUsd``
  component keys (``ACTOR_COMPUTE_UNITS``, ``DATASET_READS``,
  ``DATASET_WRITES``, ``KEY_VALUE_STORE_READS``, ``KEY_VALUE_STORE_WRITES``,
  ``KEY_VALUE_STORE_LISTS``, ``REQUEST_QUEUE_READS``, ``REQUEST_QUEUE_WRITES``,
  ``DATA_TRANSFER_INTERNAL_GBYTES``, ``DATA_TRANSFER_EXTERNAL_GBYTES``,
  ``PROXY_RESIDENTIAL_TRANSFER_GBYTES``, ``PROXY_SERPS``; each a nullable
  number) are confirmed 2026-09-25 from the OpenAPI ``RunUsage`` /
  ``RunUsageUsd`` schemas.
* Abort run: https://docs.apify.com/api/v2/actor-run-abort-post (the optional
  ``gracefully`` flag is not sent).
* Dataset items (``offset``, ``limit``, ``format``; ``X-Apify-Pagination-Total``):
  https://docs.apify.com/api/v2/dataset-items-get
* KV record: https://docs.apify.com/api/v2/key-value-store-record-get
* Delete dataset / KV store (204; a repeat delete answers 404
  ``record-not-found``): https://docs.apify.com/api/v2/dataset-delete and
  https://docs.apify.com/api/v2/key-value-store-delete
* Build record (``status``, ``finishedAt``, ``buildNumber``, ``usage``,
  ``usageUsd``, ``usageTotalUsd``; usage hidden from unauthenticated reads):
  https://docs.apify.com/api/v2/actor-build-get
* Limits (``monthlyUsageCycle.startAt/endAt``, ``limits.maxMonthlyUsageUsd``,
  ``limits.dataRetentionDays`` (a required integer in the OpenAPI ``Limits``
  schema, 2026-09-25), ``current.monthlyUsageUsd``):
  https://docs.apify.com/api/v2/users-me-limits-get
* Monthly usage (``date`` as ``YYYY-MM-DD``, ``usageCycle``,
  ``monthlyServiceUsage.*.amountAfterVolumeDiscountUsd``,
  ``dailyServiceUsages[].{date, serviceUsage, totalUsageCreditsUsd}``,
  ``totalUsageCreditsUsdAfterVolumeDiscount``):
  https://docs.apify.com/api/v2/users-me-usage-monthly-get

UNCONFIRMED at this revision (parsing is tolerant, so a wrong guess yields
``None``, a dropped monthly-usage item, or an ``unparseable_usage`` entry, never
a crash or a fabricated number; F5a settles these live):

* Exact casing of the build and monthly-usage sub-fields: taken from page
  summaries, not verbatim schemas.
* Whether a run's ``options`` echoes ``restartOnError``. The OpenAPI
  ``RunOptions`` schema does not list it (2026-09-25), so
  ``RunOptions.restart_on_error`` is usually ``None``; an observed restart is
  visible as ``ApifyRun.restart_count`` (``stats.restartCount``), which is
  documented.
* The ``GET /v2/users/me`` plan block (``plan.id``,
  ``plan.monthlyBasePriceUsd``, ``plan.monthlyUsageCreditsUsd``): names match the
  owner's live account read, not a reference page. That the limits response
  lacks them is inferred from its documented field list.
* The response to aborting a run that has already finished.
* Whether an input failing the Actor's input schema creates no billable run:
  the synchronous 400 is documented, the absence of any charge is inferred.
* Whether a scoped (limited-permission) token can read the account endpoints.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import AsyncIterator, Generator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final, Literal, Self, cast, override
from urllib.parse import quote

import httpx
from django.conf import settings

__all__ = [
    "APIFY_TOKEN_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_DATASET_PAGE_SIZE",
    "HTTP_RECEIVE_BUFFER_BYTES",
    "MAX_API_REQUEST_BODY_BYTES",
    "AccountLimits",
    "AccountPlan",
    "ApifyApiError",
    "ApifyBuild",
    "ApifyClient",
    "ApifyError",
    "ApifyNotFoundError",
    "ApifyRequestTooLargeError",
    "ApifyResponseError",
    "ApifyResponseTooLargeError",
    "ApifyRun",
    "ApifyTokenMissingError",
    "DailyUsage",
    "DatasetPage",
    "DeleteOutcome",
    "KeyValueRecord",
    "MonthlyUsage",
    "RunOptions",
]

APIFY_TOKEN_ENV = "HW_RADAR_APIFY_TOKEN"
DEFAULT_BASE_URL = "https://api.apify.com"
# Default ``limit`` for a direct ``list_dataset_items`` call outside the
# importer. The importer never uses it: it passes the derived page_limit to
# ``iter_dataset_items``, which has no default (MS2-D-32, revision 11).
DEFAULT_DATASET_PAGE_SIZE = 1000
# Per-HTTP-request budget. Deliberately independent of any Actor run timeout:
# the start call returns as soon as the run is queued (no waitForFinish).
DEFAULT_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Code constants of the MS2-D-32 wire ceiling, not settings: Slice E's
# request_wire_overhead is priced from them, so changing either here without
# the estimator would under-reserve every call. The synthetic Actor pins its
# own HTTP_RECEIVE_BUFFER_BYTES to the same value for the same reason
# (actors/hw-radar-synthetic-collector/src/synthetic_collector/core.py).
HTTP_RECEIVE_BUFFER_BYTES: Final = 65536
MAX_API_REQUEST_BODY_BYTES: Final = 16384

# Top-level input keys that would configure Apify Proxy. Matched
# case-insensitively as substrings so `proxyConfiguration`, `proxy`, and
# `useApifyProxy` are all caught.
_PROXY_KEY_MARKER = "proxy"
_MAX_ERROR_MESSAGE_CHARS = 500

type DeleteOutcome = Literal["deleted", "absent"]


class ApifyError(Exception):
    """Base class for every error this client raises itself."""


class ApifyTokenMissingError(ApifyError):
    """No token was given and ``HW_RADAR_APIFY_TOKEN`` is unset or empty."""


class ApifyResponseError(ApifyError):
    """A 2xx response whose body does not have the shape the client relies on."""


class ApifyResponseTooLargeError(ApifyResponseError):
    """A response body (of any status) exceeded its MS2-D-32 byte cap.

    Raised as soon as the declared ``Content-Length`` or the bytes read so far
    pass ``limit_bytes``; the rest of the body is never read. A contract-valid
    Apify answer cannot trigger it, so callers trip the overrun latch
    (``api_response_over_cap``) instead of retrying.
    """

    def __init__(self, limit_bytes: int, path: str) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(f"Apify response body for {path} exceeds {limit_bytes} bytes")


class ApifyRequestTooLargeError(ApifyError, ValueError):
    """A request body above ``MAX_API_REQUEST_BODY_BYTES``; nothing was sent.

    Also a ``ValueError``, like ``start_run``'s other pre-request refusals.
    """


class ApifyApiError(ApifyError):
    """Apify answered with a non-2xx status.

    ``error_type`` is Apify's machine-readable ``error.type`` when the body has
    the standard ``{"error": {"type", "message"}}`` envelope, else ``None``.
    The message is truncated and scrubbed of the client's token.
    """

    def __init__(self, status_code: int, error_type: str | None, message: str) -> None:
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        super().__init__(f"Apify API error {status_code} ({error_type or 'unknown'}): {message}")


class ApifyNotFoundError(ApifyApiError):
    """HTTP 404: the run, build, storage, or record does not exist."""


@dataclass(frozen=True, slots=True)
class RunOptions:
    """The run options Apify reports back, for the caller to verify (MS2-D-26)."""

    memory_mbytes: int | None
    timeout_secs: int | None
    build: str | None
    # Reported so a caller can assert it is unset; the client never sends it.
    max_items: int | None
    # ``options.restartOnError`` when Apify echoes it; the documented run
    # options omit it, so ``None`` means "not reported", never "false".
    restart_on_error: bool | None


@dataclass(frozen=True, slots=True)
class ApifyRun:
    """One Actor run as returned by start, get, or abort.

    ``usage_usd`` maps usage items (``ACTOR_COMPUTE_UNITS``,
    ``PROXY_RESIDENTIAL_TRANSFER_GBYTES``, ...) to dollars and ``usage`` maps the
    same items to quantities in their own units. ``usage_total_usd``,
    ``usage_usd``, and ``usage`` are ``None`` whenever Apify omits or nulls
    them; right after a terminal status they may be preliminary (MS2-D-41), so
    callers re-read rather than settle on the first value.

    ``unparseable_usage`` names every usage entry the maps could not hold, as
    ``"usage.<KEY>"`` / ``"usageUsd.<KEY>"`` for a non-numeric value, or the
    bare field name when the field itself is present but not an object. A
    ``null`` component is the documented "no figure" and is omitted from the
    map without being listed. ``restart_count`` is ``stats.restartCount``:
    ``None`` when unreported, and non-zero means the platform restarted the
    run, which MS2-D-26 treats as a start-option mismatch.
    """

    id: str
    act_id: str | None
    status: str
    status_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    build_id: str | None
    build_number: str | None
    default_dataset_id: str | None
    default_key_value_store_id: str | None
    options: RunOptions
    usage_total_usd: Decimal | None
    usage_usd: Mapping[str, Decimal] | None
    usage: Mapping[str, Decimal] | None
    unparseable_usage: tuple[str, ...]
    restart_count: int | None

    def detail(self) -> dict[str, object]:
        """Return a JSON-safe dict of the parsed fields for ``detail_json``."""
        return {
            "id": self.id,
            "act_id": self.act_id,
            "status": self.status,
            "status_message": self.status_message,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "build_id": self.build_id,
            "build_number": self.build_number,
            "default_dataset_id": self.default_dataset_id,
            "default_key_value_store_id": self.default_key_value_store_id,
            "options": {
                "memory_mbytes": self.options.memory_mbytes,
                "timeout_secs": self.options.timeout_secs,
                "build": self.options.build,
                "max_items": self.options.max_items,
                "restart_on_error": self.options.restart_on_error,
            },
            "restart_count": self.restart_count,
            "usage_total_usd": _money_str(self.usage_total_usd),
            "usage_usd": _money_map_str(self.usage_usd),
            "usage": _money_map_str(self.usage),
            "unparseable_usage": list(self.unparseable_usage),
        }


@dataclass(frozen=True, slots=True)
class ApifyBuild:
    """One Actor build (MS2-D-46); usage is parsed exactly as for a run."""

    id: str
    act_id: str | None
    status: str
    build_number: str | None
    started_at: datetime | None
    finished_at: datetime | None
    usage_total_usd: Decimal | None
    usage_usd: Mapping[str, Decimal] | None
    usage: Mapping[str, Decimal] | None
    unparseable_usage: tuple[str, ...]

    def detail(self) -> dict[str, object]:
        """Return a JSON-safe dict of the parsed fields for ``detail_json``."""
        return {
            "id": self.id,
            "act_id": self.act_id,
            "status": self.status,
            "build_number": self.build_number,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "usage_total_usd": _money_str(self.usage_total_usd),
            "usage_usd": _money_map_str(self.usage_usd),
            "usage": _money_map_str(self.usage),
            "unparseable_usage": list(self.unparseable_usage),
        }


@dataclass(frozen=True, slots=True)
class DatasetPage:
    """One page of dataset items plus Apify's pagination headers.

    ``total`` is ``None`` when the ``X-Apify-Pagination-Total`` header is
    absent; ``iter_dataset_items`` then stops on the first short page.
    """

    items: Sequence[object]
    offset: int
    limit: int
    total: int | None


@dataclass(frozen=True, slots=True)
class KeyValueRecord:
    """A raw key-value store record; the caller parses the body by content type."""

    content_type: str | None
    body: bytes


@dataclass(frozen=True, slots=True)
class AccountLimits:
    """The billing cycle and account limit from ``GET /v2/users/me/limits``.

    The cycle bounds are required: without them MS2-D-40 cannot place spend in
    a period, so their absence raises ``ApifyResponseError`` instead of
    defaulting. The dollar figures are nullable. ``data_retention_days`` is
    ``None`` when ``limits.dataRetentionDays`` is missing or not a positive
    integer; the client does not raise, because the consequence (admission
    denies with ``unbounded_component``, MS2-D-40) is the caller's.
    """

    cycle_start: datetime
    cycle_end: datetime
    max_monthly_usage_usd: Decimal | None
    monthly_usage_usd: Decimal | None
    data_retention_days: int | None


@dataclass(frozen=True, slots=True)
class AccountPlan:
    """The plan block of ``GET /v2/users/me`` (MS2-D-40 cash-ceiling guard)."""

    plan_id: str | None
    monthly_base_price_usd: Decimal | None
    monthly_usage_credits_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class DailyUsage:
    """One day of per-service usage; ``service_usd`` maps item → dollars."""

    date: datetime | None
    service_usd: Mapping[str, Decimal]
    total_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class MonthlyUsage:
    """Per-cycle and per-day service usage from ``GET /v2/users/me/usage/monthly``.

    Service maps carry only items whose dollar amount parsed; an item with an
    unparseable amount is dropped rather than read as zero, and ``total_usd``
    is Apify's own total, never a sum computed here.
    """

    cycle_start: datetime | None
    cycle_end: datetime | None
    service_usd: Mapping[str, Decimal]
    daily: Sequence[DailyUsage]
    total_usd: Decimal | None


class _BearerAuth(httpx.Auth):
    """Attach the bearer token to each outgoing request.

    Rejected alternative: client-wide default headers, which any dump of
    ``AsyncClient.headers`` would show. The Auth object keeps the credential out
    of the client's public attributes, and its repr is masked.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    @override
    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request

    @override
    def __repr__(self) -> str:
        return "_BearerAuth(token=***)"


class ApifyClient:
    """Async client for the Apify REST calls listed in MS2-D-15.

    Use as ``async with ApifyClient() as client:`` or call ``aclose()``.
    ``transport`` is the test seam (``httpx.MockTransport``); production uses
    an ``httpx.AsyncHTTPTransport`` whose sockets pin ``SO_RCVBUF``. The two
    body caps are read from Django settings at construction and must be
    positive (``ValueError`` otherwise).
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        request_timeout: httpx.Timeout = DEFAULT_REQUEST_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved = token if token is not None else os.environ.get(APIFY_TOKEN_ENV, "")
        if not resolved:
            raise ApifyTokenMissingError(f"Apify token missing: set {APIFY_TOKEN_ENV}")
        self._token = resolved
        self._max_response_bytes = _positive_cap(
            "HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES", settings.HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES
        )
        self._max_dataset_page_bytes = _positive_cap(
            "HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES", settings.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES
        )
        if transport is None:
            # Read at construction, not bound at import, so the Linux loopback
            # test can prove the option reaches the socket with a second value.
            transport = httpx.AsyncHTTPTransport(
                socket_options=[(socket.SOL_SOCKET, socket.SO_RCVBUF, HTTP_RECEIVE_BUFFER_BYTES)]
            )
        self._http = httpx.AsyncClient(
            base_url=base_url,
            auth=_BearerAuth(resolved),
            timeout=request_timeout,
            transport=transport,
            trust_env=False,
        )

    @override
    def __repr__(self) -> str:
        return f"ApifyClient(base_url={str(self._http.base_url)!r}, token=***)"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def start_run(
        self,
        actor_id: str,
        run_input: Mapping[str, object],
        *,
        memory_mbytes: int,
        timeout_secs: int,
        build: str | None = None,
    ) -> ApifyRun:
        """Start one run and return it, including the options Apify applied.

        ``memory_mbytes`` and ``timeout_secs`` are the Actor's run options, sent
        as the ``memory`` and ``timeout`` query parameters (the MS2-D-15 naming
        trap); they have nothing to do with the HTTP request timeout. The caller
        must compare ``run.options`` with what it asked for and abort on a
        mismatch (MS2-D-26); ``restartOnError=false`` is always sent. Raises
        ``ValueError`` before any request when the input carries a top-level
        proxy key, the options are not positive, or the serialized input exceeds
        ``MAX_API_REQUEST_BODY_BYTES`` (``ApifyRequestTooLargeError``).
        """
        if memory_mbytes <= 0 or timeout_secs <= 0:
            raise ValueError("memory_mbytes and timeout_secs must be positive")
        proxy_keys = sorted(k for k in run_input if _PROXY_KEY_MARKER in k.lower())
        if proxy_keys:
            raise ValueError(f"run input must not configure Apify Proxy: {proxy_keys}")
        params: dict[str, str | int] = {
            "memory": memory_mbytes,
            "timeout": timeout_secs,
            "restartOnError": "false",
        }
        if build is not None:
            params["build"] = build
        payload = await self._request_json(
            "POST",
            f"/v2/actors/{_actor_path_id(actor_id)}/runs",
            params=params,
            json=dict(run_input),
        )
        return _parse_run(_data(payload))

    async def get_run(self, run_id: str) -> ApifyRun:
        """Return the run's current state; raises ``ApifyNotFoundError`` on 404."""
        payload = await self._request_json("GET", f"/v2/actor-runs/{_seg(run_id)}")
        return _parse_run(_data(payload))

    async def abort_run(self, run_id: str) -> ApifyRun:
        """Abort the run and return its state as Apify reports it.

        Apify's answer for a run that already finished is unconfirmed (see the
        module docstring); a non-2xx answer raises ``ApifyApiError`` so the
        caller can re-read the run instead of assuming it stopped.
        """
        payload = await self._request_json("POST", f"/v2/actor-runs/{_seg(run_id)}/abort")
        return _parse_run(_data(payload))

    async def get_build(self, build_id: str) -> ApifyBuild:
        """Return one Actor build; raises ``ApifyNotFoundError`` on 404."""
        payload = await self._request_json("GET", f"/v2/actor-builds/{_seg(build_id)}")
        return _parse_build(_data(payload))

    async def list_dataset_items(
        self, dataset_id: str, *, offset: int = 0, limit: int = DEFAULT_DATASET_PAGE_SIZE
    ) -> DatasetPage:
        """Return one page of raw dataset items (unvalidated JSON values)."""
        if offset < 0 or limit <= 0:
            raise ValueError("offset must be >= 0 and limit > 0")
        response = await self._request(
            "GET",
            f"/v2/datasets/{_seg(dataset_id)}/items",
            params={"offset": offset, "limit": limit, "format": "json"},
            max_body_bytes=self._max_dataset_page_bytes,
        )
        body = _json(response)
        if not isinstance(body, list):
            raise ApifyResponseError("dataset items response is not a JSON array")
        total_header = response.headers.get("X-Apify-Pagination-Total")
        return DatasetPage(
            items=cast(list[object], body),
            offset=offset,
            limit=limit,
            total=_header_int(total_header),
        )

    async def iter_dataset_items(self, dataset_id: str, *, page_size: int) -> AsyncIterator[object]:
        """Yield every item of the dataset, one page request at a time.

        ``page_size`` is sent as every page's ``limit`` and has no default: the
        importer must pass the ``page_limit`` derived from the dataset page byte
        cap and the serialized-row bound (MS2-D-32), and a default would let a
        caller silently read pages that exceed that cap.

        Stops on an empty page, on a page shorter than ``page_size``, or once
        ``offset`` reaches the reported total. Each page is billed as dataset
        reads, so a caller enforcing a cap should stop iterating, not filter.
        """
        offset = 0
        while True:
            page = await self.list_dataset_items(dataset_id, offset=offset, limit=page_size)
            for item in page.items:
                yield item
            offset += len(page.items)
            if (
                not page.items
                or len(page.items) < page_size
                or (page.total is not None and offset >= page.total)
            ):
                return

    async def get_record(self, store_id: str, key: str) -> KeyValueRecord | None:
        """Return the raw record, or ``None`` when the store or key does not exist."""
        try:
            response = await self._request(
                "GET", f"/v2/key-value-stores/{_seg(store_id)}/records/{_seg(key)}"
            )
        except ApifyNotFoundError:
            return None
        return KeyValueRecord(content_type=response.headers.get("Content-Type"), body=response.body)

    async def delete_dataset(self, dataset_id: str) -> DeleteOutcome:
        """Delete the dataset; a 404 is ``"absent"``, not an error (see ``_delete``)."""
        return await self._delete(f"/v2/datasets/{_seg(dataset_id)}")

    async def delete_key_value_store(self, store_id: str) -> DeleteOutcome:
        """Delete the key-value store; a 404 is ``"absent"`` (see ``_delete``)."""
        return await self._delete(f"/v2/key-value-stores/{_seg(store_id)}")

    async def get_account_limits(self) -> AccountLimits:
        """Return the account's current billing cycle, limit, and cycle usage."""
        data = _data(await self._request_json("GET", "/v2/users/me/limits"))
        cycle = _mapping(data.get("monthlyUsageCycle"))
        start = _opt_dt(cycle.get("startAt"))
        end = _opt_dt(cycle.get("endAt"))
        if start is None or end is None or end <= start:
            raise ApifyResponseError("limits response lacks a valid monthlyUsageCycle")
        return AccountLimits(
            cycle_start=start,
            cycle_end=end,
            max_monthly_usage_usd=_opt_money(
                _mapping(data.get("limits")).get("maxMonthlyUsageUsd")
            ),
            monthly_usage_usd=_opt_money(_mapping(data.get("current")).get("monthlyUsageUsd")),
            data_retention_days=_opt_positive_int(
                _mapping(data.get("limits")).get("dataRetentionDays")
            ),
        )

    async def get_monthly_usage(self, on: date | None = None) -> MonthlyUsage:
        """Return usage for the billing cycle containing ``on`` (default: current)."""
        params = {"date": on.isoformat()} if on is not None else None
        data = _data(await self._request_json("GET", "/v2/users/me/usage/monthly", params=params))
        cycle = _mapping(data.get("usageCycle"))
        daily_raw = data.get("dailyServiceUsages")
        days = cast(list[object], daily_raw) if isinstance(daily_raw, list) else []
        daily = [
            DailyUsage(
                date=_opt_dt(day.get("date")),
                service_usd=_service_usd(day.get("serviceUsage")),
                total_usd=_opt_money(day.get("totalUsageCreditsUsd")),
            )
            for day in map(_mapping, days)
        ]
        return MonthlyUsage(
            cycle_start=_opt_dt(cycle.get("startAt")),
            cycle_end=_opt_dt(cycle.get("endAt")),
            service_usd=_service_usd(data.get("monthlyServiceUsage")),
            daily=daily,
            total_usd=_opt_money(data.get("totalUsageCreditsUsdAfterVolumeDiscount")),
        )

    async def get_account_plan(self) -> AccountPlan:
        """Return the plan's base price and prepaid usage credit."""
        # SCOPE: only the plan block is extracted. The private-user response
        # also carries account data unrelated to billing (it can include the
        # Apify Proxy password), so the raw body is never returned or stored.
        data = _data(await self._request_json("GET", "/v2/users/me"))
        plan = _mapping(data.get("plan"))
        return AccountPlan(
            plan_id=_opt_str(plan.get("id")),
            monthly_base_price_usd=_opt_money(plan.get("monthlyBasePriceUsd")),
            monthly_usage_credits_usd=_opt_money(plan.get("monthlyUsageCreditsUsd")),
        )

    async def _delete(self, path: str) -> DeleteOutcome:
        try:
            await self._request("DELETE", path)
        except ApifyNotFoundError:
            # Reported, not judged: a 404 may mean already gone (an earlier
            # attempt whose response was lost), or a scoped token masking
            # storage it cannot access. Whether "absent" counts as deleted is
            # the caller's MS2-D-25 *404 rule* (storage_cleanup, ED-19).
            return "absent"
        return "deleted"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: object = None,
    ) -> object:
        return _json(await self._request(method, path, params=params, json=json))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: object = None,
        max_body_bytes: int | None = None,
    ) -> _Reply:
        """Send one request and return its body read under a byte cap.

        ``max_body_bytes`` defaults to the control-response cap; only the
        dataset-page read passes the larger page cap. The cap applies to
        error bodies too: an oversized error answer raises
        ``ApifyResponseTooLargeError``, not ``ApifyApiError``.
        """
        request = self._http.build_request(method, path, params=params, json=json)
        # httpx serializes a json= body eagerly, so .content is the exact
        # bytes that would go on the wire -- checked before anything is sent.
        if len(request.content) > MAX_API_REQUEST_BODY_BYTES:
            raise ApifyRequestTooLargeError(
                f"Apify request body for {path} exceeds {MAX_API_REQUEST_BODY_BYTES} bytes"
            )
        cap = self._max_response_bytes if max_body_bytes is None else max_body_bytes
        response = await self._http.send(request, stream=True)
        try:
            body = await _read_capped(response, cap, path)
        finally:
            # On the over-cap path this closes the connection with the body
            # unread (httpcore does not drain), so no further bytes are pulled
            # beyond what SO_RCVBUF already admitted.
            await response.aclose()
        reply = _Reply(
            status_code=response.status_code,
            reason_phrase=response.reason_phrase,
            headers=response.headers,
            body=body,
        )
        if response.is_success:
            return reply
        raise self._api_error(reply)

    def _api_error(self, response: _Reply) -> ApifyApiError:
        message = response.reason_phrase
        envelope: Mapping[str, object]
        try:
            envelope = _mapping(_mapping(json.loads(response.body)).get("error"))
        except ValueError:
            envelope = {}
        error_type = _opt_str(envelope.get("type"))
        message = _opt_str(envelope.get("message")) or message
        # Apify's message text is untrusted: it could echo request details, so
        # it is scrubbed of the token and truncated before it can reach a log
        # line or a persisted error string.
        message = message.replace(self._token, "***")[:_MAX_ERROR_MESSAGE_CHARS]
        cls = ApifyNotFoundError if response.status_code == 404 else ApifyApiError
        return cls(response.status_code, error_type, message)


@dataclass(frozen=True, slots=True)
class _Reply:
    """A fully read, size-capped response; the stream is already closed."""

    status_code: int
    reason_phrase: str
    headers: httpx.Headers
    body: bytes


async def _read_capped(response: httpx.Response, cap: int, path: str) -> bytes:
    declared = _header_int(response.headers.get("Content-Length"))
    if declared is not None and declared > cap:
        raise ApifyResponseTooLargeError(cap, path)
    chunks: list[bytes] = []
    decoded = 0
    async for chunk in response.aiter_bytes():
        decoded += len(chunk)
        # num_bytes_downloaded counts raw (pre-decompression) bytes, so a
        # gzip body is held to the cap on the wire as well as after decoding.
        # Content-Length alone is not enough: it may be absent (chunked) or
        # describe the compressed form.
        if decoded > cap or response.num_bytes_downloaded > cap:
            raise ApifyResponseTooLargeError(cap, path)
        chunks.append(chunk)
    return b"".join(chunks)


def _positive_cap(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer byte count")
    return value


def _actor_path_id(actor_id: str) -> str:
    # The REST path uses `username~actor-name`; the familiar `username/actor-name`
    # form would otherwise become two path segments and hit a different route.
    return _seg(actor_id.replace("/", "~"))


def _seg(value: str) -> str:
    """Return ``value`` as one path segment; reject anything that could escape it."""
    if not value or "/" in value or value in {".", ".."}:
        raise ValueError(f"invalid Apify identifier: {value!r}")
    return quote(value, safe="~-._")


def _json(response: _Reply) -> object:
    try:
        # parse_float=Decimal keeps dollar amounts exact from the JSON text;
        # routing them through float first would bake in binary rounding.
        return json.loads(response.body, parse_float=Decimal)
    except ValueError as exc:
        raise ApifyResponseError("Apify response is not valid JSON") from exc


def _data(payload: object) -> Mapping[str, object]:
    data = _mapping(payload).get("data")
    if not isinstance(data, Mapping):
        raise ApifyResponseError("Apify response lacks a 'data' object")
    return cast(Mapping[str, object], data)


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _parse_run(data: Mapping[str, object]) -> ApifyRun:
    options = _mapping(data.get("options"))
    restart_on_error = options.get("restartOnError")
    usage_usd, bad_usd = _usage_map(data, "usageUsd")
    usage, bad_usage = _usage_map(data, "usage")
    return ApifyRun(
        id=_req_str(data, "id"),
        act_id=_opt_str(data.get("actId")),
        status=_req_str(data, "status"),
        status_message=_opt_str(data.get("statusMessage")),
        started_at=_opt_dt(data.get("startedAt")),
        finished_at=_opt_dt(data.get("finishedAt")),
        build_id=_opt_str(data.get("buildId")),
        build_number=_opt_str(data.get("buildNumber")),
        default_dataset_id=_opt_str(data.get("defaultDatasetId")),
        default_key_value_store_id=_opt_str(data.get("defaultKeyValueStoreId")),
        options=RunOptions(
            memory_mbytes=_opt_int(options.get("memoryMbytes")),
            timeout_secs=_opt_int(options.get("timeoutSecs")),
            build=_opt_str(options.get("build")),
            max_items=_opt_int(options.get("maxItems")),
            restart_on_error=restart_on_error if isinstance(restart_on_error, bool) else None,
        ),
        usage_total_usd=_opt_money(data.get("usageTotalUsd")),
        usage_usd=usage_usd,
        usage=usage,
        unparseable_usage=bad_usd + bad_usage,
        restart_count=_opt_int(_mapping(data.get("stats")).get("restartCount")),
    )


def _parse_build(data: Mapping[str, object]) -> ApifyBuild:
    usage_usd, bad_usd = _usage_map(data, "usageUsd")
    usage, bad_usage = _usage_map(data, "usage")
    return ApifyBuild(
        id=_req_str(data, "id"),
        act_id=_opt_str(data.get("actId")),
        status=_req_str(data, "status"),
        build_number=_opt_str(data.get("buildNumber")),
        started_at=_opt_dt(data.get("startedAt")),
        finished_at=_opt_dt(data.get("finishedAt")),
        usage_total_usd=_opt_money(data.get("usageTotalUsd")),
        usage_usd=usage_usd,
        usage=usage,
        unparseable_usage=bad_usd + bad_usage,
    )


def _req_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ApifyResponseError(f"Apify response lacks a string {key!r}")
    return value


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_int(value: object) -> int | None:
    # bool is an int subclass; a JSON true must not read as 1 MB of memory.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return None


def _opt_money(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    return None


def _opt_positive_int(value: object) -> int | None:
    parsed = _opt_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _usage_map(
    data: Mapping[str, object], field: str
) -> tuple[Mapping[str, Decimal] | None, tuple[str, ...]]:
    """Parse one usage field into (amounts, names of unparseable entries).

    Absent or ``null`` means "no figure" (``None``, nothing flagged). Every
    other value that cannot be held as a Decimal is reported, never dropped:
    the MS2-D-26 allowlist latch keys on component names, and a dropped
    entry -- say a ``PROXY_SERPS`` amount sent as a string -- would pass it
    unseen (ED-10).
    """
    value = data.get(field)
    if value is None:
        return None, ()
    if not isinstance(value, Mapping):
        return None, (field,)
    parsed: dict[str, Decimal] = {}
    bad: list[str] = []
    for key, amount in cast(Mapping[str, object], value).items():
        if amount is None:
            # Documented as a nullable number; no figure is not a bad figure.
            continue
        money = _opt_money(amount)
        if money is None:
            bad.append(f"{field}.{key}")
        else:
            parsed[key] = money
    return parsed, tuple(bad)


def _service_usd(value: object) -> Mapping[str, Decimal]:
    parsed: dict[str, Decimal] = {}
    for item, detail in _mapping(value).items():
        amount = _opt_money(_mapping(detail).get("amountAfterVolumeDiscountUsd"))
        if amount is not None:
            parsed[item] = amount
    return parsed


def _opt_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Apify timestamps are UTC ("...Z"); a naive value is pinned to UTC rather
    # than compared against aware datetimes and raising later in the ledger.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _header_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _money_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _money_map_str(value: Mapping[str, Decimal] | None) -> dict[str, str] | None:
    return {k: str(v) for k, v in value.items()} if value is not None else None
