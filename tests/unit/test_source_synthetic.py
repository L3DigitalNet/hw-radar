"""The synthetic site's local adapter: the Actor's pages, parsed to the Actor's listings (AC-4).

The equivalence tests run the local adapter over the Actor's committed source
pages (actors/hw-radar-synthetic-collector/fixtures/source/) and compare it
with the Actor's own output for those same pages: the `complete` contract
fixture, which the Actor's harness generates and its tests keep from drifting.
That output goes through the importer's import_row, as a real import does. So
the Actor's parser runs on the committed fixtures without hw-radar importing
Actor code. No test touches the network: a MockTransport serves the pages.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from django.test import override_settings

from hw_radar.acquisition.apify import synthetic
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES
from hw_radar.acquisition.apify.provider import import_row
from hw_radar.acquisition.contracts import ParsedListing, RawBatch
from hw_radar.acquisition.http import HONEST_UA
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.acquisition.sources import synthetic as local
from hw_radar.acquisition.sources.synthetic import (
    FixtureCommitRequired,
    ResponseTooLarge,
    SyntheticAdapter,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SOURCE_DIR: Final = REPO_ROOT / "actors" / "hw-radar-synthetic-collector" / "fixtures" / "source"
ACTOR_OUTPUT: Final = REPO_ROOT / "tests" / "fixtures" / "apify_contract" / "v1" / "complete.json"
# The commit the Actor harness pins when it generates the contract fixtures, so
# the local URLs and the Actor's are comparable byte for byte.
COMMIT: Final = "1" * 40
PAGE_PREFIX: Final = f"/L3DigitalNet/hw-radar/{COMMIT}/{local.SOURCE_ROOT}/"


class FakeRaw:
    """MockTransport handler: raw.githubusercontent.com serving the committed pages.

    Every response goes back unread. httpx.Response(content=...) reads itself on
    construction and MockTransport hands it over as is, so the adapter's raw
    streaming (it counts wire bytes chunk by chunk) would raise StreamConsumed;
    a network transport never delivers a consumed response.
    """

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._serve(request)
        return httpx.Response(
            response.status_code, headers=response.headers, stream=response.stream
        )

    def _serve(self, request: httpx.Request) -> httpx.Response:
        if self._respond is not None:
            return self._respond(request)
        path = request.url.path
        if request.url.host != "raw.githubusercontent.com" or not path.startswith(PAGE_PREFIX):
            return httpx.Response(404, text="404: Not Found")
        page = SOURCE_DIR / path.removeprefix(PAGE_PREFIX)
        # text/plain is what raw.githubusercontent.com answers for every file.
        return httpx.Response(
            200,
            content=page.read_bytes(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )


def _adapter(fake: FakeRaw) -> SyntheticAdapter:
    return SyntheticAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(fake)))


def _fetch(adapter: SyntheticAdapter, commit: str = COMMIT) -> RawBatch:
    with override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT=commit):
        return asyncio.run(adapter.fetch())


def _local_listings() -> list[ParsedListing]:
    adapter = _adapter(FakeRaw())
    return adapter.parse(_fetch(adapter))


def _actor_listings() -> list[ParsedListing]:
    fixture: dict[str, Any] = json.loads(ACTOR_OUTPUT.read_text(encoding="utf-8"))
    assert fixture["actorInput"]["fixtureCommit"] == COMMIT
    assert fixture["actorInput"]["fixturePaths"] == list(synthetic.FIXTURE_PATHS)
    scope = fixture["actorInput"]["collectionScope"]
    admitted = [
        import_row(
            row, site_key=synthetic.SITE_KEY, scope_key=scope, max_item_bytes=MAX_LISTING_ROW_BYTES
        )
        for row in fixture["datasetItems"]
    ]
    assert all(isinstance(listing, ParsedListing) for listing in admitted)
    return [listing for listing in admitted if isinstance(listing, ParsedListing)]


# ── Equivalence with the Actor ───────────────────────────────────────────────


def test_local_parse_yields_the_actors_keys_prices_and_titles() -> None:
    local_rows = _local_listings()
    actor_rows = _actor_listings()

    assert [p.source_listing_key for p in local_rows] == [
        "syn-hdd-0001",
        "syn-hdd-0002",
        "syn-hdd-0003",
    ]
    assert [(p.source_listing_key, p.price, p.title) for p in local_rows] == [
        (p.source_listing_key, p.price, p.title) for p in actor_rows
    ]


def test_local_parse_matches_the_actor_listing_field_for_field() -> None:
    # Everything that lands on the Listing or its snapshot, url included. The
    # hint and scope are the run's input, not the parse: the contract fixture
    # was generated under `hdd`, the site's spec asks for `drive` (see below).
    # raw_url names each provider's own raw payload, which differs by design.
    varying = {"category_hint", "collection_scope", "raw_url"}
    local_rows = [p.model_dump(exclude=varying) for p in _local_listings()]
    actor_rows = [p.model_dump(exclude=varying) for p in _actor_listings()]

    assert local_rows == actor_rows


def test_local_listings_carry_the_sites_actor_hint_and_scope() -> None:
    # The same values synthetic.run_input sends the Actor, so a local run and a
    # site-spec Actor run observe the listings in one scope.
    run_input = synthetic.run_input(COMMIT)
    for listing in _local_listings():
        assert listing.category_hint == run_input["categoryHint"] == "drive"
        assert listing.collection_scope == run_input["collectionScope"]
        assert listing.raw_url.startswith(local.RAW_BASE_URL)


def test_adapter_is_registered_for_the_synthetic_site() -> None:
    assert ADAPTERS[synthetic.SITE_KEY] is SyntheticAdapter
    assert SyntheticAdapter.site_key == synthetic.SITE_KEY


# ── Fetch bounds and refusals ────────────────────────────────────────────────


def test_fetch_sends_exactly_the_fixture_page_requests_and_no_robots_preflight() -> None:
    fake = FakeRaw()
    batch = _fetch(_adapter(fake))

    assert len(fake.requests) == local.MAX_REQUESTS == len(synthetic.FIXTURE_PATHS)
    assert [str(r.url) for r in fake.requests] == [
        local.page_url(COMMIT, path) for path in synthetic.FIXTURE_PATHS
    ]
    for request in fake.requests:
        assert request.headers["user-agent"] == HONEST_UA
        assert request.headers["accept-encoding"] == "identity"
    assert [item.http_status for item in batch.items] == [200, 200]
    assert all(item.payload_json is not None for item in batch.items)


@pytest.mark.parametrize("commit", ["", "abc123", "1" * 39, "g" * 40, "A" * 40])
def test_fetch_refuses_without_a_valid_commit_before_any_request(commit: str) -> None:
    fake = FakeRaw()
    with pytest.raises(FixtureCommitRequired, match="HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT"):
        _fetch(_adapter(fake), commit)
    assert fake.requests == []


def test_constructing_the_adapter_needs_no_commit() -> None:
    # The registry builds adapters in contexts with no commit configured
    # (retention agreement tests, the poller); only fetch may refuse.
    with override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT=""):
        assert SyntheticAdapter().site_key == synthetic.SITE_KEY


def test_fetch_stops_at_the_byte_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = (SOURCE_DIR / synthetic.FIXTURE_PATHS[0]).stat().st_size
    # Page 1 fits; page 2 pushes the run past the budget.
    monkeypatch.setattr(local, "MAX_RESPONSE_BYTES", page_1 + 10)
    fake = FakeRaw()

    with pytest.raises(ResponseTooLarge):
        _fetch(_adapter(fake))
    assert len(fake.requests) == 2


def test_non_200_page_is_kept_for_the_pipeline_to_classify() -> None:
    fake = FakeRaw(lambda _r: httpx.Response(404, text="404: Not Found"))
    batch = _fetch(_adapter(fake))

    assert [(item.http_status, item.payload_json) for item in batch.items] == [
        (404, None),
        (404, None),
    ]


def test_transport_errors_propagate_for_the_pipeline_to_classify() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(httpx.ConnectError):
        _fetch(_adapter(FakeRaw(refuse)))


# ── Parse drops ──────────────────────────────────────────────────────────────


def test_parse_drops_malformed_pages_and_listings_and_counts_them() -> None:
    good = json.loads((SOURCE_DIR / synthetic.FIXTURE_PATHS[0]).read_text(encoding="utf-8"))
    good["listings"].extend(
        [
            "not an object",
            {"title": "no id", "price": "1.00", "currency": "USD"},
            {"id": "syn-unpriced", "title": "unpriced", "price": "0", "currency": "USD"},
        ]
    )
    pages = iter([json.dumps(good).encode(), b"not json"])

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=next(pages))

    adapter = _adapter(FakeRaw(respond))
    parsed = adapter.parse(_fetch(adapter))

    assert [p.source_listing_key for p in parsed] == ["syn-hdd-0001", "syn-hdd-0002"]
    # Three bad listings on page 1, and page 2 is not a listings object.
    assert adapter.last_parse_skipped == 4
