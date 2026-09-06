"""MS-1 acceptance GATE (spec §19, minus MS-1e ratification).

This is an integration suite, not new product code: it drives the five landed
connectors end-to-end through the real `run_source` pipeline and asserts the
spec's cross-cutting acceptance invariants against persisted DB state.

The synthetic request/response bodies are copied verbatim from each connector's
own DB test (OQ8: never captured live — no real creds, no live network) so this
file is self-contained and the acceptance contract does not silently drift from
a sibling test module's private fixtures. Origin per source:
  - serverpartdeals: tests/db/test_source_serverpartdeals.py (Shopify products.json)
  - goharddrive:     tests/fixtures/ms1d/goharddrive_category.html (Scrapy file://)
  - wd-recertified:  tests/db/test_source_wd.py (two-step OCC search+product)
  - seagate:         tests/db/test_source_seagate.py (bootstrap-JSON HTML)
  - ebay:            tests/db/test_source_ebay.py (OAuth token + Browse)

The five cases:
  1. >=1 normalized listing per source on a synthetic run (all 5 adapters).
  2. FX stamping (FR-004): a non-USD eBay item carries fx_rate/fx_pair/
     fx_rate_date + non-null usd_item_price and is_international; a USD item
     stamps identity (rate 1.0, USD/USD).
  3. Append-not-duplicate (DR-005): a second run appends one OfferSnapshot and
     leaves Listing.count() unchanged.
  4. Per-grain counts (E1 contract): detail_json["grain_counts"] sums to
     records_valid for every run.
  5. Live CatalogResolver integration (CR-007): the catalog is seeded IN-TEST
     (import_refdata) and a ServerPartDeals listing whose title carries a seeded
     Exos MPN resolves through the REAL CatalogResolver to a non-`none` grain —
     proving the adapter->resolver->grain path resolves, not merely executes.
"""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest
from django.core.management import call_command

from hw_radar.acquisition.contracts import NullResolver
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.ebay import (
    _TOKEN_CACHE,  # pyright: ignore[reportPrivateUsage]
    EbayAdapter,
)
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.acquisition.sources.seagate import SeagateAdapter
from hw_radar.acquisition.sources.serverpartdeals import ServerPartDealsAdapter
from hw_radar.acquisition.sources.wd import WdAdapter
from hw_radar.catalog.models import (
    FxRateDaily,
    Listing,
    OfferSnapshot,
    ResolutionGrain,
    RunStatus,
    ScraperRun,
)
from hw_radar.matching.resolver import CatalogResolver

# transaction=True + serialized_rollback preserves the migration-0005 SourceSite
# seed rows (run_source looks each source up by normalized_name) and lets the
# module-scoped event loop that Scrapy needs span multiple tests.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

GOHD_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "ms1d" / "goharddrive_category.html"
)

# Grain string values (TextChoices members render as their value via str()); used
# for type-clean comparisons against the str keys of grain_counts / the
# resolution_grain CharField, without basedpyright's tuple-vs-str false positive.
_NONE_GRAIN = str(ResolutionGrain.NONE)
_MODEL_GRAIN = str(ResolutionGrain.MODEL)
_VARIANT_GRAIN = str(ResolutionGrain.VARIANT)

# --- serverpartdeals: synthetic Shopify products.json (two variants) ---------
SPD_PRODUCTS: dict[str, object] = {
    "products": [
        {
            "title": "Seagate Exos X20 20TB Recertified",
            "handle": "exos-x20-20tb-recert",
            "variants": [
                {"id": 111, "sku": "ST20000NM002D-RECERT", "price": "279.99", "available": True}
            ],
        },
        {
            "title": "WD Ultrastar DC HC560 20TB Recertified",
            "handle": "hc560-20tb-recert",
            "variants": [
                {"id": 222, "sku": "WUH722020BLE-RECERT", "price": "289.99", "available": False}
            ],
        },
    ]
}

# --- wd-recertified: synthetic OCC search sweeps + per-product bodies ---------
# The WD adapter now runs three independent search sweeps (consumer query +
# cat_data_center_drives + cat_nas_hdd category selectors; see
# hw_radar/acquisition/sources/wd.py module docstring) since the OCC catalog
# has no recert facet for enterprise SKUs. Codes surviving all three sweeps
# are merged, deduped, and filtered to the `-recertified` suffix before the
# per-product fetch, so every code below must carry that suffix or the
# adapter drops it and the acceptance run degrades to parser_rot (0 records).
WD_SEARCH_CONSUMER: dict[str, object] = {"products": [{"code": "WDBBGB0040HBK-recertified"}]}
WD_SEARCH_DATA_CENTER: dict[str, object] = {"products": [{"code": "wd-gold-sata-hdd-recertified"}]}
WD_SEARCH_NAS: dict[str, object] = {"products": [{"code": "wd-red-sata-hdd-recertified"}]}
WD_PRODUCTS: dict[str, dict[str, object]] = {
    "WDBBGB0040HBK-recertified": {
        "code": "WDBBGB0040HBK-recertified",
        "name": "WD My Book 4TB Recertified",
        "variantOptions": [
            {
                "code": "RWDBBGB0040HBK-NESN",
                "priceData": {"value": 79.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": True,
            }
        ],
    },
    "wd-gold-sata-hdd-recertified": {
        "code": "wd-gold-sata-hdd-recertified",
        "name": "WD Gold 8TB Recertified",
        "variantOptions": [
            {
                "code": "WD-GOLD-8TB-RECERT",
                "priceData": {"value": 129.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": False,
            }
        ],
    },
    "wd-red-sata-hdd-recertified": {
        "code": "wd-red-sata-hdd-recertified",
        "name": "WD Red 4TB Recertified",
        "variantOptions": [
            {
                "code": "WD-RED-4TB-RECERT",
                "priceData": {"value": 99.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": True,
            }
        ],
    },
}

# --- seagate: synthetic category page with a <script id="sku-bootstrap-data">
SEAGATE_HTML = """
<html><body>
<div id="product-grid">Exos Recertified</div>
<script id="sku-bootstrap-data" type="application/json">
{"ST16000NM002C": {"final_price": 349.99, "stock_status": "IN_STOCK"},
 "ST18000NM004C": {"final_price": 419.99, "stock_status": "BACKORDER"}}
</script>
</body></html>
"""

# --- ebay: synthetic Browse item_summary/search (US + GBP shipping-from-GB) ---
EBAY_SEARCH: dict[str, object] = {
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
EBAY_TOKEN: dict[str, object] = {
    "access_token": "SYNTH-TOKEN",
    "expires_in": 7200,
    "token_type": "Application Access Token",
}
GBP_USD_RATE = Decimal("1.270000")

# Case 5: a ServerPartDeals product whose TITLE carries a seeded Exos MPN
# (ST16000NM002C is a first-party alias in seagate-exos-recertified.json). The
# resolver decodes the MPN token from title_raw and hits the seeded alias, so
# this drives a real adapter->CatalogResolver->grain resolution, not a no-op.
CATALOG_HIT_PRODUCTS: dict[str, object] = {
    "products": [
        {
            "title": "Seagate Exos ST16000NM002C 16TB SATA 512e Recertified Enterprise HDD",
            "handle": "exos-st16000nm002c-16tb",
            "variants": [
                {"id": 900, "sku": "ST16000NM002C-RECERT", "price": "199.99", "available": True}
            ],
        }
    ]
}


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    # SINGLE module loop for every test: Scrapy (goharddrive) binds its asyncio
    # reactor to the loop present at install, so all Scrapy-touching runs must
    # share one loop; the httpx adapters are indifferent to which loop they use.
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


@pytest.fixture(autouse=True)
def ebay_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # eBay creds come from os.environ (OpenBao-injected in prod); supply
    # throwaways. _TOKEN_CACHE is process-global — reset it around every test so
    # a token minted here never leaks. Autouse: harmless for the other sources.
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.com")
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


def _spd_transport(body: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _wd_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path.endswith("/products/search"):
            # Route by category selector (see hw_radar.acquisition.sources.wd
            # SEARCH_PARAMS_LIST): mirrors the three-sweep contract that
            # tests/db/test_source_wd.py's _mock() also implements.
            query = request.url.params.get("query", "")
            if "cat_data_center_drives" in query:
                return httpx.Response(200, json=WD_SEARCH_DATA_CENTER)
            if "cat_nas_hdd" in query:
                return httpx.Response(200, json=WD_SEARCH_NAS)
            return httpx.Response(200, json=WD_SEARCH_CONSUMER)
        return httpx.Response(200, json=WD_PRODUCTS[path.rsplit("/", 1)[-1]])

    return httpx.MockTransport(handler)


def _seagate_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 20\n")
        return httpx.Response(200, text=SEAGATE_HTML, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def _ebay_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json=EBAY_TOKEN)
        if path == "/buy/browse/v1/item_summary/search":
            return httpx.Response(200, json=EBAY_SEARCH)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _run_serverpartdeals(loop: asyncio.AbstractEventLoop) -> ScraperRun:
    adapter = ServerPartDealsAdapter(
        client=httpx.AsyncClient(transport=_spd_transport(SPD_PRODUCTS))
    )
    run, _ = loop.run_until_complete(run_source(adapter, NullResolver()))
    return run


def _run_goharddrive(loop: asyncio.AbstractEventLoop) -> ScraperRun:
    # file:// fixture cannot serve /robots.txt, so obey_robots=False FOR THE TEST
    # ONLY (production keeps ROBOTSTXT_OBEY=True).
    adapter = GoHardDriveAdapter(start_url=GOHD_FIXTURE.as_uri(), obey_robots=False)
    run, _ = loop.run_until_complete(run_source(adapter, NullResolver()))
    return run


def _run_wd(loop: asyncio.AbstractEventLoop) -> ScraperRun:
    adapter = WdAdapter(client=httpx.AsyncClient(transport=_wd_transport()))
    run, _ = loop.run_until_complete(run_source(adapter, NullResolver()))
    return run


def _run_seagate(loop: asyncio.AbstractEventLoop) -> ScraperRun:
    adapter = SeagateAdapter(client=httpx.AsyncClient(transport=_seagate_transport()))
    run, _ = loop.run_until_complete(run_source(adapter, NullResolver()))
    return run


def _run_ebay(loop: asyncio.AbstractEventLoop) -> ScraperRun:
    # Pre-seed the GBP->USD daily rate so fx.stamp hits cache — the GBP item
    # would otherwise trip _normalize into a live Frankfurter fetch.
    FxRateDaily.objects.get_or_create(
        rate_date=datetime.now(UTC).date(),
        base="GBP",
        quote="USD",
        defaults={"rate": GBP_USD_RATE},
    )
    adapter = EbayAdapter(client=httpx.AsyncClient(transport=_ebay_transport()))
    run, _ = loop.run_until_complete(
        run_source(
            adapter,
            NullResolver(),
            retention_class=EbayAdapter.retention_class,
            expires_policy=EbayAdapter.expires_policy,
        )
    )
    return run


# (source normalized_name, runner). The normalized_name is the migration-0005
# SourceSite key each adapter resolves against.
SourceRunner = Callable[[asyncio.AbstractEventLoop], ScraperRun]
SOURCE_RUNNERS: list[tuple[str, SourceRunner]] = [
    ("serverpartdeals", _run_serverpartdeals),
    ("goharddrive", _run_goharddrive),
    ("wd-recertified", _run_wd),
    ("seagate-recertified", _run_seagate),
    ("ebay", _run_ebay),
]


def _grain_counts_of(run: ScraperRun) -> dict[str, int]:
    raw = run.detail_json["grain_counts"]
    assert isinstance(raw, dict)
    return cast("dict[str, int]", raw)


@pytest.mark.parametrize(
    ("source_key", "runner"), SOURCE_RUNNERS, ids=[k for k, _ in SOURCE_RUNNERS]
)
def test_case1_at_least_one_listing_per_source(
    source_key: str,
    runner: SourceRunner,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Case 1: each of the five connectors, driven through run_source with a
    synthetic client/fixture, produces at least one persisted Listing."""
    run = runner(loop)
    assert run.status == RunStatus.SUCCESS
    assert Listing.objects.filter(source_site__normalized_name=source_key).count() >= 1


@pytest.mark.parametrize(
    "runner", [r for _, r in SOURCE_RUNNERS], ids=[k for k, _ in SOURCE_RUNNERS]
)
def test_case4_grain_counts_sum_to_records_valid(
    runner: SourceRunner,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Case 4: every run's grain_counts partition every valid record — the E1
    invariant sum(grain_counts.values()) == records_valid holds per source."""
    run = runner(loop)
    assert run.status == RunStatus.SUCCESS
    counts = _grain_counts_of(run)
    assert sum(counts.values()) == run.records_valid
    assert run.records_valid >= 1


def test_case2_fx_stamping_non_usd_and_usd_identity(loop: asyncio.AbstractEventLoop) -> None:
    """Case 2 (FR-004): the GBP eBay item FX-stamps (rate/pair/date + non-null
    usd_item_price) and flags is_international by ship origin (EC-003); the US
    item stamps the USD/USD identity rate 1.0."""
    run = _run_ebay(loop)
    assert run.status == RunStatus.SUCCESS

    gbp_listing = Listing.objects.get(source_listing_key="v1|110500000002|0")
    assert gbp_listing.is_international is True  # ships from GB, not GBP currency
    gbp_snap = OfferSnapshot.objects.get(listing=gbp_listing)
    assert gbp_snap.currency == "GBP"
    assert gbp_snap.fx_rate == GBP_USD_RATE
    assert gbp_snap.fx_pair == "GBP/USD"
    assert gbp_snap.fx_rate_date is not None
    assert gbp_snap.usd_item_price == Decimal("149.50") * GBP_USD_RATE  # generated column

    usd_listing = Listing.objects.get(source_listing_key="v1|110500000001|0")
    assert usd_listing.is_international is False  # ships from US
    usd_snap = OfferSnapshot.objects.get(listing=usd_listing)
    assert usd_snap.currency == "USD"
    assert usd_snap.fx_rate == Decimal("1")  # identity stamp
    assert usd_snap.fx_pair == "USD/USD"
    assert usd_snap.fx_source == "identity"
    assert usd_snap.usd_item_price == Decimal("199.99")


def test_case3_append_not_duplicate(loop: asyncio.AbstractEventLoop) -> None:
    """Case 3 (DR-005): re-running the same source appends a second OfferSnapshot
    per listing and leaves the Listing rows untouched (no duplicate listings)."""
    run1 = _run_serverpartdeals(loop)
    assert run1.status == RunStatus.SUCCESS
    listings_after_first = Listing.objects.filter(
        source_site__normalized_name="serverpartdeals"
    ).count()
    assert listings_after_first >= 1
    sample = Listing.objects.get(source_listing_key="exos-x20-20tb-recert:111")
    assert OfferSnapshot.objects.filter(listing=sample).count() == 1

    run2 = _run_serverpartdeals(loop)
    assert run2.status == RunStatus.SUCCESS
    assert (
        Listing.objects.filter(source_site__normalized_name="serverpartdeals").count()
        == listings_after_first
    )  # unchanged: upsert on (source_site, source_listing_key)
    assert OfferSnapshot.objects.filter(listing=sample).count() == 2  # +1 appended


def test_case5_live_catalog_resolver_resolves_non_none_grain(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Case 5 (CR-007): the catalog is NOT migration-seeded — seed it in-test via
    import_refdata, then run a ServerPartDeals listing whose title carries the
    seeded Exos MPN ST16000NM002C through the REAL CatalogResolver. The run must
    succeed AND grain_counts must show a non-`none` grain, proving the
    adapter->resolver->grain path resolves rather than merely executing."""
    call_command("import_refdata")  # seeds the MS-1c corpus (Seagate Exos, WD Ultrastar)

    adapter = ServerPartDealsAdapter(
        client=httpx.AsyncClient(transport=_spd_transport(CATALOG_HIT_PRODUCTS))
    )
    run, _ = loop.run_until_complete(run_source(adapter, CatalogResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["resolver_errors"] == 0

    counts = _grain_counts_of(run)
    resolved = {grain: n for grain, n in counts.items() if grain != _NONE_GRAIN and n > 0}
    assert resolved, f"expected a non-`none` grain from the live resolver, got {counts}"

    # Prove the seeded ALIAS was hit, not just family vocabulary: the MPN token
    # resolves to model grain, upgraded to variant on demand once the
    # "Recertified" condition is normalized (C.3.3). Either grain reaches the
    # seeded Exos model — via the model FK (MODEL) or the variant FK (VARIANT).
    listing = Listing.objects.get(source_listing_key="exos-st16000nm002c-16tb:900")
    assert listing.resolution_grain in (_MODEL_GRAIN, _VARIANT_GRAIN)
    resolved_model = listing.product_model or (
        listing.product_variant.product_model if listing.product_variant is not None else None
    )
    assert resolved_model is not None
    assert resolved_model.model_number == "ST16000NM002C"  # the seeded first-party alias target
