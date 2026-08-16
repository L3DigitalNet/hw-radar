"""eBay connector: Browse API item_summary/search over OAuth2 client-credentials.

httpx JSON connector (not Scrapy). Unlike the crawl-style sources, the Browse
API is an AUTHORIZED endpoint — we hold an application token, so the fetch calls
http.get(..., check_robots=False) (the B1 robots guard is for unauthorized
crawls, not a contracted API) while keeping the honest User-Agent.

Config seeded by migration 0011: heartbeat_enabled=True, fast_lane=True. eBay's
0005 seed is volatility_profile="drop_prone", cheap_signal="ebay_browse", which
satisfies the source_config_fast_lane_eligible CHECK. There is no separate cheap
tier here: the Browse poll IS the heartbeat (probe() reuses the same search
response), so this source is heartbeat-native.

Retention (DR-008, partial): observations are RetentionClass.EBAY_LISTING_OBSERVATION
with a <=6h TTL via expires_policy. This bounds observation STALENESS but is NOT
the delete-on-delist path — eBay `enabled=True` go-live stays BLOCKED (CR-004)
pending a separate Listing-grain soft-delete plan. C5 ships the connector only.

OAuth2 (CR-007):
  - Token is minted with a client-credentials POST to /identity/v1/oauth2/token
    (Basic b64(client_id:client_secret), form body) and cached per API base in
    _TOKEN_CACHE with a 300s safety skew (treated as expired early) so the
    rate-limited token endpoint is not hit per request.
  - The token is NEVER logged and never placed in exception text — a leaked
    bearer is a live credential.
  - On a 401 from the search GET (token revoked/expired server-side despite the
    skew), the cache is dropped and the token re-minted ONCE, then the GET is
    retried inside fetch() so the batch's final RawItem is a real 200 rather
    than a 401 that _classify_batch would flag.

Creds/config from env (OpenBao-injected at runtime; never in the repo):
EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_API_BASE (default https://api.ebay.com).
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from hw_radar.acquisition import http
from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.heartbeat import HeartbeatReading
from hw_radar.catalog.models import RetentionClass, RunKind

DEFAULT_API_BASE = "https://api.ebay.com"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE_ID = "EBAY_US"
# Browse item_summary/search caps limit at 200; q is the recert-drive sweep.
SEARCH_PARAMS = {"q": "recertified enterprise hard drive", "limit": "200"}
# Mint the token this many seconds before its stated expiry so a request never
# rides an about-to-expire token across the eBay boundary.
_TOKEN_SKEW_S = 300

# Process-global cache keyed by API base → (token, expires_at). Mirrors http.py's
# _ROBOTS_CACHE: a long-lived poller reuses the token until it nears expiry
# rather than minting per request against the rate-limited token endpoint.
_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}


def _expires_in_6h(observed: datetime) -> datetime:
    """DR-008 bounded-observation TTL: eBay observations expire 6h after fetch."""
    return observed + timedelta(hours=6)


def _api_base() -> str:
    return os.environ.get("EBAY_API_BASE", DEFAULT_API_BASE)


def _search_url(base: str) -> str:
    return f"{base}/buy/browse/v1/item_summary/search"


def _shipping_cost(summary: dict[str, object]) -> Decimal | None:
    # shippingOptions[0].shippingCost.value when present; isinstance-narrows each
    # level so a listing without a shipping quote degrades to None, not a raise.
    raw_options = summary.get("shippingOptions")
    if not isinstance(raw_options, list) or not raw_options:
        return None
    first = cast("list[object]", raw_options)[0]
    if not isinstance(first, dict):
        return None
    raw_cost = cast("dict[str, object]", first).get("shippingCost")
    if not isinstance(raw_cost, dict):
        return None
    value = cast("dict[str, object]", raw_cost).get("value")
    return Decimal(str(value)) if value is not None else None


def _ships_from(summary: dict[str, object]) -> str:
    raw_loc = summary.get("itemLocation")
    if not isinstance(raw_loc, dict):
        return "US"
    country = cast("dict[str, object]", raw_loc).get("country")
    return country if isinstance(country, str) and country else "US"


def _seller_username(summary: dict[str, object]) -> str:
    raw_seller = summary.get("seller")
    if not isinstance(raw_seller, dict):
        return ""
    username = cast("dict[str, object]", raw_seller).get("username")
    return username if isinstance(username, str) else ""


class EbayAdapter:
    name = "ebay"
    site_key = "ebay"  # == migration-0005 normalized_name
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0  # SourceAdapter parse diagnostic; see parse()
    # Consumed by the eBay run's run_source(...) call (D1 poller wiring): bounded
    # retention class + the per-batch TTL policy the pipeline applies as
    # expires_policy(batch.fetched_at).
    retention_class = RetentionClass.EBAY_LISTING_OBSERVATION
    expires_policy = staticmethod(_expires_in_6h)

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Inject-or-own-and-close: tests inject a MockTransport client (not
        # closed by us); production leaves this None and gets a fresh client
        # per fetch, closed on the way out.
        self._client = client

    async def _mint_token(self, client: httpx.AsyncClient, base: str) -> str:
        # Direct POST (not http.get): the token endpoint takes a form body + Basic
        # auth, and is an authorized call, so it bypasses the robots preflight. The
        # token is written to the cache and returned; it is never logged.
        client_id = os.environ["EBAY_CLIENT_ID"]
        client_secret = os.environ["EBAY_CLIENT_SECRET"]
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = await client.post(
            f"{base}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=f"grant_type=client_credentials&scope={OAUTH_SCOPE}",
        )
        resp.raise_for_status()  # raises with URL only — never echoes the token
        data = cast("dict[str, object]", resp.json())
        token = str(data["access_token"])
        expires_in = int(str(data["expires_in"]))
        expiry = datetime.now(UTC) + timedelta(seconds=expires_in - _TOKEN_SKEW_S)
        _TOKEN_CACHE[base] = (token, expiry)
        return token

    async def _token(self, client: httpx.AsyncClient, base: str) -> str:
        cached = _TOKEN_CACHE.get(base)
        if cached is not None and cached[1] > datetime.now(UTC):
            return cached[0]
        return await self._mint_token(client, base)

    async def _search_get(self, client: httpx.AsyncClient, base: str, token: str) -> httpx.Response:
        return await http.get(
            _search_url(base),
            client=client,
            params=SEARCH_PARAMS,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            },
            check_robots=False,  # authorized API, not a crawl
        )

    async def _search(self, client: httpx.AsyncClient, base: str) -> httpx.Response:
        token = await self._token(client, base)
        resp = await self._search_get(client, base, token)
        if resp.status_code == 401:
            # Token rejected server-side despite the skew; drop the cache and
            # re-mint ONCE so the batch's final RawItem is a real 200.
            _TOKEN_CACHE.pop(base, None)
            token = await self._mint_token(client, base)
            resp = await self._search_get(client, base, token)
        return resp

    async def fetch(self) -> RawBatch:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        base = _api_base()
        try:
            resp = await self._search(client, base)
            item = RawItem(
                url=str(resp.url),
                http_status=resp.status_code,
                content_type=resp.headers.get("content-type", "application/json"),
                payload_json=resp.json() if resp.status_code == 200 else None,
                payload_text=resp.text,
            )
            return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=[item])
        finally:
            if owns:
                await client.aclose()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # One ParsedListing per itemSummaries[] entry. isinstance/cast narrows the
        # untyped Browse JSON at each level (mirrors wd.py): a summary without a
        # price dict is skipped rather than raising, so a malformed body degrades
        # to "0 records" (run_source's PARSER_ROT guard) instead of a crash.
        #
        # Every `continue` below is a malformed-record drop and increments
        # last_parse_skipped (SourceAdapter contract), one count per summary that
        # could have become a listing.
        self.last_parse_skipped = 0
        out: list[ParsedListing] = []
        for item in batch.items:
            data = item.payload_json or {}
            raw_summaries = data.get("itemSummaries", [])
            if not isinstance(raw_summaries, list):
                self.last_parse_skipped += 1
                continue
            for raw_summary in cast("list[object]", raw_summaries):
                if not isinstance(raw_summary, dict):
                    self.last_parse_skipped += 1
                    continue
                summary = cast("dict[str, object]", raw_summary)
                item_id = summary.get("itemId")
                if item_id is None:
                    self.last_parse_skipped += 1
                    continue  # no itemId ⇒ can't key the listing; skip this entry
                raw_price = summary.get("price")
                if not isinstance(raw_price, dict):
                    self.last_parse_skipped += 1
                    continue
                price = cast("dict[str, object]", raw_price)
                value = price.get("value")
                if value is None:
                    self.last_parse_skipped += 1
                    continue
                try:
                    item_price = Decimal(str(value))
                except InvalidOperation:
                    self.last_parse_skipped += 1
                    continue
                out.append(
                    ParsedListing(
                        source_listing_key=str(item_id),
                        url=str(summary.get("itemWebUrl", "")),
                        title=str(summary.get("title", "")),
                        price=item_price,
                        currency=str(price.get("currency", "USD")),
                        shipping_price=_shipping_cost(summary),
                        # Search returns only active, buyable listings; there is no
                        # per-item stock field, so an active result is IN_STOCK.
                        stock_status="in_stock",
                        seller_name=_seller_username(summary),
                        ships_from_country=_ships_from(summary),
                        raw_url=item.url,  # per-item raw-payload association (Task B4)
                    )
                )
        return out

    async def probe(self) -> list[HeartbeatReading]:
        # The Browse poll doubles as the heartbeat (fast_lane): reuse fetch+parse
        # to yield one cheap reading per listing, no DB writes.
        batch = await self.fetch()
        endpoint = _search_url(_api_base())
        return [
            HeartbeatReading(
                source_sku=p.source_listing_key,
                price=p.price,
                currency=p.currency,
                stock_status=p.stock_status,
                shipping_price=p.shipping_price,
                http_status=200,
                latency_ms=None,
                endpoint=endpoint,
            )
            for p in self.parse(batch)
        ]
