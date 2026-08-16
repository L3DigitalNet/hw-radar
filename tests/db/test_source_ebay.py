import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from django.utils import timezone

from hw_radar.acquisition.contracts import NullResolver, RawBatch, RawItem
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.ebay import (
    _TOKEN_CACHE,  # pyright: ignore[reportPrivateUsage]
    DELIST_ABSENCE_GRACE,
    EbayAdapter,
)
from hw_radar.catalog.models import (
    DelistReason,
    FxRateDaily,
    Listing,
    OfferSnapshot,
    RetentionClass,
    RunKind,
    RunStatus,
    StockStatus,
)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# Synthetic eBay Browse `item_summary/search` body (OQ8: not captured live — no
# real creds, no live network). Item 2 is a GBP listing shipping from GB: it
# must FX-stamp non-USD and flag is_international=True (ships-from, not currency,
# per EC-003).
SEARCH_RESULT: dict[str, object] = {
    "itemSummaries": [
        {
            "itemId": "v1|110500000001|0",
            "title": "Seagate Exos X18 18TB Recertified SAS",
            "itemWebUrl": "https://www.ebay.com/itm/110500000001",
            "price": {"value": "199.99", "currency": "USD"},
            "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
            "itemLocation": {"country": "US"},
            "seller": {"username": "diskdeals_us"},
        },
        {
            "itemId": "v1|110500000002|0",
            "title": "WD Ultrastar DC HC550 16TB Recertified",
            "itemWebUrl": "https://www.ebay.co.uk/itm/110500000002",
            "price": {"value": "149.50", "currency": "GBP"},
            "shippingOptions": [{"shippingCost": {"value": "12.00", "currency": "GBP"}}],
            "itemLocation": {"country": "GB"},
            "seller": {"username": "uk_server_parts"},
        },
    ]
}

# CR-004 delist fixtures. USD-only so no FX rate has to be seeded, and `total`
# is what makes a sweep provably complete: total <= summaries returned and no
# `next` page means the sweep enumerated the whole result set.
US_ITEM: dict[str, object] = cast("list[dict[str, object]]", SEARCH_RESULT["itemSummaries"])[0]
SECOND_ITEM = {
    "itemId": "v1|110500000003|0",
    "title": "HGST He10 10TB Recertified",
    "itemWebUrl": "https://www.ebay.com/itm/110500000003",
    "price": {"value": "99.00", "currency": "USD"},
    "itemLocation": {"country": "US"},
    "seller": {"username": "diskdeals_us"},
}
SWEEP_BOTH: dict[str, object] = {"itemSummaries": [US_ITEM, SECOND_ITEM], "total": 2}
SWEEP_COMPLETE_ONE: dict[str, object] = {"itemSummaries": [US_ITEM], "total": 1}
# Same page, but the source says 50 items match: absence here proves nothing on
# its own, so only the freshness-window grace can retire a listing.
SWEEP_TRUNCATED_ONE: dict[str, object] = {"itemSummaries": [US_ITEM], "total": 50}
DELISTED_KEY = "v1|110500000003|0"

TOKEN_BODY = {
    "access_token": "SYNTH-TOKEN",
    "expires_in": 7200,
    "token_type": "Application Access Token",
}


@pytest.fixture(autouse=True)
def ebay_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Creds come from os.environ (OpenBao-injected at runtime); tests supply
    # throwaway values. _TOKEN_CACHE is process-global (keyed by API base) — a
    # token minted by one test must not leak into another (mirrors
    # test_http_guard's _ROBOTS_CACHE reset).
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.com")
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


def _mock(
    *, counters: dict[str, int] | None = None, fail_first_search: bool = False
) -> httpx.MockTransport:
    calls = counters if counters is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/identity/v1/oauth2/token":
            calls["token"] = calls.get("token", 0) + 1
            return httpx.Response(200, json=TOKEN_BODY)
        if path == "/buy/browse/v1/item_summary/search":
            calls["search"] = calls.get("search", 0) + 1
            if fail_first_search and calls["search"] == 1:
                # A stale/revoked token: the search must invalidate the cache and
                # re-mint ONCE so the final RawItem is a real 200 (else
                # _classify_batch would flag the 401 as a failure).
                return httpx.Response(401, json={"errors": [{"message": "Invalid access token"}]})
            return httpx.Response(200, json=SEARCH_RESULT)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _mock_body(body: dict[str, object]) -> httpx.MockTransport:
    """MockTransport serving one specific search body (delist tests drive the
    sweep contents; _mock's fixed SEARCH_RESULT can't express a shrinking sweep)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        if request.url.path == "/buy/browse/v1/item_summary/search":
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _run_ebay(
    loop: asyncio.AbstractEventLoop,
    transport: httpx.MockTransport,
    *,
    run_kind: RunKind | None = None,
):
    adapter = EbayAdapter(client=httpx.AsyncClient(transport=transport))
    return loop.run_until_complete(
        run_source(
            adapter,
            NullResolver(),
            retention_class=EbayAdapter.retention_class,
            expires_policy=EbayAdapter.expires_policy,
            run_kind=run_kind,
        )
    )


def test_ebay_persists_search_listings(loop: asyncio.AbstractEventLoop) -> None:
    run, _ = _run_ebay(loop, _mock())
    assert run.status == RunStatus.SUCCESS
    assert run.records_valid == 2
    listings = Listing.objects.filter(source_site__normalized_name="ebay")
    assert listings.count() == 2

    us = OfferSnapshot.objects.get(listing__source_listing_key="v1|110500000001|0")
    assert us.item_price == Decimal("199.99")
    assert us.shipping_price == Decimal("0.00")
    assert us.stock_status == StockStatus.IN_STOCK  # search returns active, buyable listings


def test_ebay_parse_maps_summary_fields() -> None:
    # seller_name / ships_from_country are ParsedListing fields (not persisted by
    # upsert_listing today) — assert the parse mapping directly.
    batch = RawBatch(
        source="ebay",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://api.ebay.com/buy/browse/v1/item_summary/search",
                payload_json=SEARCH_RESULT,
            )
        ],
    )
    parsed = {p.source_listing_key: p for p in EbayAdapter().parse(batch)}
    us = parsed["v1|110500000001|0"]
    assert us.seller_name == "diskdeals_us"
    assert us.ships_from_country == "US"
    assert us.shipping_price == Decimal("0.00")
    gb = parsed["v1|110500000002|0"]
    assert gb.currency == "GBP"
    assert gb.ships_from_country == "GB"
    assert gb.seller_name == "uk_server_parts"


def test_ebay_remints_token_on_401(loop: asyncio.AbstractEventLoop) -> None:
    counters: dict[str, int] = {}
    run, _ = _run_ebay(loop, _mock(counters=counters, fail_first_search=True))
    assert run.status == RunStatus.SUCCESS  # 401 recovered inside fetch(), final item is 200
    assert run.records_valid == 2
    assert counters["token"] == 2  # minted once, re-minted after the 401
    assert counters["search"] == 2  # first 401, retry 200


def test_ebay_non_usd_is_international_and_fx_stamped(loop: asyncio.AbstractEventLoop) -> None:
    # Pre-seed the GBP→USD daily rate so fx.stamp hits cache — no live Frankfurter
    # call. observed_date is the batch fetch date (UTC), which the pipeline uses
    # as the FX stamping basis.
    FxRateDaily.objects.create(
        rate_date=datetime.now(UTC).date(),
        base="GBP",
        quote="USD",
        rate=Decimal("1.270000"),
    )
    run, _ = _run_ebay(loop, _mock())
    assert run.status == RunStatus.SUCCESS

    gbp_listing = Listing.objects.get(source_listing_key="v1|110500000002|0")
    assert gbp_listing.is_international is True  # ships from GB (EC-003: origin, not currency)
    gbp_snap = OfferSnapshot.objects.get(listing=gbp_listing)
    assert gbp_snap.currency == "GBP"
    assert gbp_snap.usd_item_price is not None  # generated column: 149.50 * 1.27


def test_ebay_bounded_retention_and_ttl(loop: asyncio.AbstractEventLoop) -> None:
    run, _ = _run_ebay(loop, _mock())
    assert run.status == RunStatus.SUCCESS
    listing = Listing.objects.get(source_listing_key="v1|110500000001|0")
    assert listing.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert listing.expires_at is not None  # DR-008 bounded TTL (<=6h)
    snap = OfferSnapshot.objects.get(listing=listing)
    assert snap.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert snap.expires_at is not None


def test_ebay_probe_reuses_search(loop: asyncio.AbstractEventLoop) -> None:
    # HeartbeatProbe contract (migration 0011 flips heartbeat_enabled=True): the
    # Browse poll IS the heartbeat, so probe() reuses fetch()+parse() to yield one
    # reading per listing with no DB writes.
    adapter = EbayAdapter(client=httpx.AsyncClient(transport=_mock()))
    readings = loop.run_until_complete(adapter.probe())
    assert {r.source_sku for r in readings} == {"v1|110500000001|0", "v1|110500000002|0"}
    assert all(r.stock_status == StockStatus.IN_STOCK for r in readings)
    assert all(r.endpoint.endswith("/buy/browse/v1/item_summary/search") for r in readings)


def test_ebay_token_cached_across_fetches(loop: asyncio.AbstractEventLoop) -> None:
    # The token is minted once and reused from _TOKEN_CACHE (300s skew) — a second
    # fetch on the same client must NOT hit the rate-limited token endpoint again.
    counters: dict[str, int] = {}
    adapter = EbayAdapter(client=httpx.AsyncClient(transport=_mock(counters=counters)))
    loop.run_until_complete(adapter.fetch())
    loop.run_until_complete(adapter.fetch())
    assert counters["token"] == 1  # cache hit on the second fetch
    assert counters["search"] == 2


def test_ebay_parse_skips_malformed_summaries() -> None:
    # Defensive isinstance-narrowing: a non-list itemSummaries, a non-dict entry,
    # a summary without a price dict, and missing/mistyped shippingOptions /
    # itemLocation / seller must degrade to "skip" or a safe default, not raise.
    # Also covers a summary with a price but missing the required `itemId`
    # (PR #12 review: KeyError-on-malformed-body must degrade to "skip this
    # entry", not crash the whole run).
    batch = RawBatch(
        source="ebay",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(url="https://api.ebay.com/a", payload_json={"itemSummaries": "oops"}),
            RawItem(
                url="https://api.ebay.com/b",
                payload_json={
                    "itemSummaries": [
                        "not-a-dict",
                        {"itemId": "no-price"},  # missing price dict
                        {"itemId": "bad-price-type", "price": "oops"},
                        {"title": "No itemId", "price": {"value": "9.99", "currency": "USD"}},
                        {
                            "itemId": "v1|minimal|0",
                            "title": "Minimal",
                            "price": {"value": "50.00", "currency": "USD"},
                            "shippingOptions": "oops",  # non-list ⇒ shipping None
                            "itemLocation": "oops",  # non-dict ⇒ US default
                            "seller": "oops",  # non-dict ⇒ "" default
                        },
                    ]
                },
            ),
        ],
    )
    parsed = EbayAdapter().parse(batch)
    assert [p.source_listing_key for p in parsed] == ["v1|minimal|0"]
    only = parsed[0]
    assert only.shipping_price is None
    assert only.ships_from_country == "US"
    assert only.seller_name == ""


def test_ebay_complete_sweep_delists_missing_listing(loop: asyncio.AbstractEventLoop) -> None:
    # CR-004 / IR-002: the Browse search returns only active items, so once a sweep
    # proves it enumerated the whole result set, a tracked key it omits has ended.
    _run_ebay(loop, _mock_body(SWEEP_BOTH))
    assert Listing.objects.active().filter(source_site__normalized_name="ebay").count() == 2

    run, _ = _run_ebay(loop, _mock_body(SWEEP_COMPLETE_ONE))

    assert run.detail_json["listings_delisted"] == 1
    gone = Listing.objects.get(source_listing_key=DELISTED_KEY)
    assert gone.delisted_at is not None
    assert gone.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    assert gone.expires_at == gone.delisted_at  # DR-008: evidence due immediately
    assert [
        listing.source_listing_key
        for listing in Listing.objects.active().filter(source_site__normalized_name="ebay")
    ] == ["v1|110500000001|0"]


def test_ebay_truncated_sweep_needs_the_absence_grace(loop: asyncio.AbstractEventLoop) -> None:
    # One page of a 50-item result set: absence is pagination/ranking churn until
    # the listing has missed every sweep for the whole 6h freshness window.
    _run_ebay(loop, _mock_body(SWEEP_BOTH))

    run, _ = _run_ebay(loop, _mock_body(SWEEP_TRUNCATED_ONE))
    assert run.detail_json["listings_delisted"] == 0
    assert Listing.objects.get(source_listing_key=DELISTED_KEY).delisted_at is None

    Listing.objects.filter(source_listing_key=DELISTED_KEY).update(
        last_seen=timezone.now() - DELIST_ABSENCE_GRACE - timedelta(minutes=1)
    )
    run, _ = _run_ebay(loop, _mock_body(SWEEP_TRUNCATED_ONE))

    assert run.detail_json["listings_delisted"] == 1
    gone = Listing.objects.get(source_listing_key=DELISTED_KEY)
    assert gone.delist_reason == DelistReason.ABSENT_STALE


def test_ebay_relisting_revives_the_same_row(loop: asyncio.AbstractEventLoop) -> None:
    # The self-healing half of the absence heuristics: a listing that reappears
    # comes back as the SAME row, so its history and resolution edges are intact.
    _run_ebay(loop, _mock_body(SWEEP_BOTH))
    _run_ebay(loop, _mock_body(SWEEP_COMPLETE_ONE))
    pk = Listing.objects.get(source_listing_key=DELISTED_KEY).pk
    snapshots_before = OfferSnapshot.objects.filter(listing_id=pk).count()

    _run_ebay(loop, _mock_body(SWEEP_BOTH))

    revived = Listing.objects.get(source_listing_key=DELISTED_KEY)
    assert revived.pk == pk
    assert revived.delisted_at is None
    assert revived.delist_reason == ""
    assert revived.expires_at is not None and revived.expires_at > timezone.now()
    assert OfferSnapshot.objects.filter(listing_id=pk).count() == snapshots_before + 1


def test_ebay_probe_run_never_delists(loop: asyncio.AbstractEventLoop) -> None:
    # A PROBE is a recovery poke, not a census: it may not conclude that the
    # listings it did not return have ended, even from a "complete" page.
    _run_ebay(loop, _mock_body(SWEEP_BOTH))

    run, _ = _run_ebay(loop, _mock_body(SWEEP_COMPLETE_ONE), run_kind=RunKind.PROBE)

    assert run.detail_json["listings_delisted"] == 0
    assert Listing.objects.get(source_listing_key=DELISTED_KEY).delisted_at is None


def test_ebay_parse_drop_forfeits_the_completeness_claim() -> None:
    # A summary we could not parse is not a listing that ended: last_parse_skipped
    # downgrades the sweep to the grace path even when the page looks complete.
    adapter = EbayAdapter()
    body: dict[str, object] = {"itemSummaries": [US_ITEM, {"itemId": "no-price"}], "total": 2}
    batch = RawBatch(
        source="ebay",
        fetched_at=datetime.now(UTC),
        items=[RawItem(url="https://api.ebay.com/s", payload_json=body)],
    )
    parsed = adapter.parse(batch)
    scope = adapter.delist_scope(batch, parsed)
    assert scope is not None
    assert scope.complete is False

    clean_batch = RawBatch(
        source="ebay",
        fetched_at=datetime.now(UTC),
        items=[RawItem(url="https://api.ebay.com/s", payload_json=SWEEP_COMPLETE_ONE)],
    )
    clean_scope = adapter.delist_scope(clean_batch, adapter.parse(clean_batch))
    assert clean_scope is not None
    assert clean_scope.complete is True


def test_ebay_empty_sweep_concludes_nothing() -> None:
    # Guard against the catastrophic case: an empty result set must not "prove"
    # that every tracked eBay listing has ended.
    adapter = EbayAdapter()
    batch = RawBatch(
        source="ebay",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(url="https://api.ebay.com/s", payload_json={"itemSummaries": [], "total": 0})
        ],
    )
    assert adapter.delist_scope(batch, adapter.parse(batch)) is None


def test_ebay_token_never_logged(
    loop: asyncio.AbstractEventLoop, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        run, _ = _run_ebay(loop, _mock())
    assert run.status == RunStatus.SUCCESS
    assert "SYNTH-TOKEN" not in caplog.text  # bearer token must never reach logs
