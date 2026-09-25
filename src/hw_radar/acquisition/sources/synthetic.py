"""Local collector for the synthetic proof site: the Actor's pages, parsed to the Actor's rows.

The synthetic site (MS2-D-42) is normally collected by the self-owned
hw-radar-synthetic-collector Actor. This adapter is its local twin, so an
operator can switch the site's `collection_provider` between `local` and
`apify` and show, live, that listing identity, history, and watch state
survive the switch (AC-4, MS2-D-10). Both providers must therefore produce
the SAME listings for the same fixture commit:

- fetch GETs the same pinned pages the Actor fetches: the Actor's own
  fixtures/source/ pages from this public repository on raw.githubusercontent.com,
  at the commit HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT names, for
  acquisition.apify.synthetic.FIXTURE_PATHS. There is no code-constant commit:
  unset or malformed, fetch raises FixtureCommitRequired before any request.
- parse builds, per source listing, the hw-radar-listing/v1 row the Actor's
  synthetic_collector.core._row emits (same key, url `<page url>#<id>`, fields,
  and optional-field renames) and admits it through the importer's own
  import_row, so every field reaches ParsedListing exactly as an imported Actor
  row would. The Actor is a separate uv project and is never imported here;
  tests/unit/test_source_synthetic.py pins the equivalence against the Actor's
  committed output for the same pages (tests/fixtures/apify_contract/v1/complete.json).

Bounds: at most MAX_REQUESTS page requests (no robots preflight, see
_get_page), REQUEST_TIMEOUT_S per network operation inside the pipeline's
whole-fetch timeout, and a MAX_RESPONSE_BYTES budget counted on the wire across
the run, checked after every network read.

SCOPE: the adapter never delists. It has no delist_scope, so a local run is
`completeness_not_asserted` evidence and the delist stage acts on nothing:
the switch proof must show survival, not exercise absence. The site stays
unscheduled (its SourceConfig is `enabled=False`, apify_synthetic_setup), and
the site does not exist in production, so registering this adapter makes no
production path able to fetch. While the config stays disabled,
synthetic_collect_local is the only caller; apify_synthetic_setup refuses to
switch a config whose `enabled` or lanes have drifted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, cast

import httpx
from django.conf import settings

from hw_radar.acquisition.apify import synthetic
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES
from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.http import HONEST_UA
from hw_radar.catalog.models import RunKind

# Cross-file contract: the Actor's synthetic_collector.core RAW_BASE_URL and
# SOURCE_ROOT. A different base would give every listing a different canonical
# URL from the Actor's, so a provider switch would rewrite them all.
RAW_BASE_URL: Final = "https://raw.githubusercontent.com/L3DigitalNet/hw-radar"
SOURCE_ROOT: Final = "actors/hw-radar-synthetic-collector/fixtures/source"

# One request per fixture page: the same two the Actor sends for this site.
MAX_REQUESTS: Final = 2
# The byte budget the Actor runs under (synthetic.DEFAULT_CAPS), whole-run.
MAX_RESPONSE_BYTES: Final = synthetic.DEFAULT_CAPS.max_bytes
REQUEST_TIMEOUT_S: Final = 15.0

# The Actor input contract's fixtureCommit pattern (SyntheticCollectorInput).
_COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")

# Source-listing key -> contract row key, as the Actor's _OPTIONAL_LISTING_FIELDS.
_OPTIONAL_LISTING_FIELDS: Final = (
    ("shippingPrice", "shippingPrice"),
    ("quantityAvailable", "quantityAvailable"),
    ("seller", "sellerName"),
    ("condition", "conditionLabel"),
    ("shipsFrom", "shipsFromCountry"),
    ("mpn", "mpn"),
)


class FixtureCommitRequired(RuntimeError):
    """HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT is unset or not a 40-hex commit."""


class ResponseTooLarge(RuntimeError):
    """The pages exceeded MAX_RESPONSE_BYTES on the wire; the batch is discarded."""


def fixture_commit() -> str:
    """Return the configured fixture commit, or raise FixtureCommitRequired."""
    commit: str = settings.HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT
    if not commit:
        raise FixtureCommitRequired(
            "HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT is unset; the synthetic site has no "
            "default commit, so set the one the Actor run pins"
        )
    if not _COMMIT_RE.fullmatch(commit):
        raise FixtureCommitRequired(
            "HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT must be a full 40-hex lowercase commit"
        )
    return commit


def page_url(commit: str, path: str) -> str:
    return f"{RAW_BASE_URL}/{commit}/{SOURCE_ROOT}/{path}"


class SyntheticAdapter:
    name = "synthetic"
    site_key = synthetic.SITE_KEY
    run_kind = RunKind.FULL
    # raw.githubusercontent.com serves every file as text/plain, so the pipeline's
    # "JSON endpoint answered text/html" check would call each page ANTI_BOT.
    # A page that is not the expected JSON yields no listings instead, which the
    # pipeline's zero-record guard reports as PARSER_ROT.
    expects_json = False
    last_parse_skipped = 0  # SourceAdapter parse diagnostic; see parse()

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Inject-or-own-and-close, as the other httpx connectors: tests inject a
        # MockTransport client (not closed here); production gets a fresh client
        # per fetch, closed on the way out. The constructor reads no settings,
        # so the registry can build the adapter where no commit is configured.
        self._client = client

    async def fetch(self) -> RawBatch:
        commit = fixture_commit()
        paths = synthetic.FIXTURE_PATHS
        if len(paths) > MAX_REQUESTS:
            raise RuntimeError(f"{len(paths)} fixture pages exceed MAX_REQUESTS={MAX_REQUESTS}")
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
        try:
            items: list[RawItem] = []
            received = 0
            for path in paths:
                item, received = await _get_page(client, page_url(commit, path), received)
                items.append(item)
            return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=items)
        finally:
            if owns:
                await client.aclose()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # Imported here, not at module level: provider imports retention_policy,
        # which imports sources.ebay and so runs this package's __init__, which
        # imports this module. A module-level import closes that cycle and fails
        # whenever provider or retention_policy is imported first.
        from hw_radar.acquisition.apify.provider import import_row

        # Every drop below increments last_parse_skipped (SourceAdapter
        # contract), in units of source listings that could have become rows; a
        # page that is not a listings object counts once.
        self.last_parse_skipped = 0
        observed_at = batch.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        parsed: list[ParsedListing] = []
        for item in batch.items:
            page = item.payload_json
            listings = None if page is None else page.get("listings")
            if not isinstance(listings, list):
                self.last_parse_skipped += 1
                continue
            for listing in cast("list[object]", listings):
                row = listing_row(listing, page=item.url, observed_at=observed_at)
                verdict = (
                    None
                    if row is None
                    else import_row(
                        row,
                        site_key=synthetic.SITE_KEY,
                        scope_key=synthetic.COLLECTION_SCOPE,
                        max_item_bytes=MAX_LISTING_ROW_BYTES,
                    )
                )
                if not isinstance(verdict, ParsedListing):
                    self.last_parse_skipped += 1
                    continue
                parsed.append(verdict.model_copy(update={"raw_url": item.url}))
        return parsed


def listing_row(listing: object, *, page: str, observed_at: str) -> dict[str, object] | None:
    """Return the hw-radar-listing/v1 row the Actor emits for one source listing.

    Mirrors synthetic_collector.core._row with the site's own category hint and
    scope (acquisition.apify.synthetic), which are what the site's Actor input
    asks for. None when the listing is not an object with a string id, where
    the Actor skips it too. The row is not validated here; import_row does that.
    """
    if not isinstance(listing, dict):
        return None
    source = cast("dict[str, object]", listing)
    listing_id = source.get("id")
    if not isinstance(listing_id, str):
        return None
    row: dict[str, object] = {
        "schemaVersion": "hw-radar-listing/v1",
        "siteKey": synthetic.SITE_KEY,
        "sourceListingKey": listing_id,
        "url": f"{page}#{listing_id}",
        "title": source.get("title"),
        "price": source.get("price"),
        "currency": source.get("currency"),
        "stockStatus": source.get("stockStatus", "unknown"),
        "categoryHint": synthetic.CATEGORY_HINT,
        "collectionScope": synthetic.COLLECTION_SCOPE,
        "observedAt": observed_at,
    }
    for source_key, row_key in _OPTIONAL_LISTING_FIELDS:
        if source.get(source_key) is not None:
            row[row_key] = source[source_key]
    return row


async def _get_page(client: httpx.AsyncClient, url: str, received: int) -> tuple[RawItem, int]:
    """GET one page within the run's byte budget; return its RawItem and the new byte total.

    Streams instead of calling http.get, which reads the whole body before the
    caller can bound it. robots.txt is not consulted, as for eBay's API: the
    target is this repository's own committed files at a pinned commit, not a
    crawl, and a preflight would be a third request. The honest User-Agent, the
    part of C-007 that still applies, is sent as http.get sends it.

    Identity encoding is requested and bytes are counted as received, so the
    budget bounds the transfer; the body is still decoded through httpx should
    the server compress anyway. Raises ResponseTooLarge past the budget and lets
    httpx errors propagate for the pipeline to classify (transport: TRANSIENT).
    """
    headers = {"Accept-Encoding": "identity", "User-Agent": HONEST_UA}
    body = bytearray()
    async with client.stream("GET", url, headers=headers) as response:
        async for chunk in response.aiter_raw():
            body.extend(chunk)
            if received + len(body) > MAX_RESPONSE_BYTES:
                raise ResponseTooLarge(f"{url}: over the {MAX_RESPONSE_BYTES}-byte run budget")
    decoded = httpx.Response(response.status_code, headers=response.headers, content=bytes(body))
    text = decoded.text
    payload: dict[str, object] | None = None
    if response.status_code == 200:
        payload = _json_object(decoded)
    item = RawItem(
        url=url,
        http_status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        payload_json=payload,
        payload_text=text,
    )
    return item, received + len(body)


def _json_object(response: httpx.Response) -> dict[str, object] | None:
    try:
        value: object = response.json()
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value)
