"""C.1 adapter contract: Pydantic I/O models + structural protocols.

Adapters own fetch/parse only; normalization (FX), resolution, and persistence
are pipeline-owned so every source shares one code path (spec C.1: "no
per-source code beyond the adapter").
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hw_radar.catalog.models import RunKind


class RawItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    http_status: int = 200
    content_type: str = "application/json"
    payload_json: dict[str, object] | None = None
    payload_text: str | None = None


class RawBatch(BaseModel):
    source: str
    fetched_at: datetime
    items: list[RawItem] = Field(default_factory=list)
    # Raw scrapy.statscollectors.StatsCollector.get_stats() snapshot from
    # run_spider() (None for non-Scrapy adapters). run_source() filters this
    # down to a stable subset before it lands in ScraperRun.detail_json.
    scrapy_stats: dict[str, object] | None = None


class ParsedListing(BaseModel):
    source_listing_key: str
    url: str
    title: str
    price: Decimal = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    shipping_price: Decimal | None = None
    stock_status: str = "unknown"
    quantity_available: int | None = None
    seller_name: str = ""
    condition_label: str = ""
    ships_from_country: str = "US"
    attrs: dict[str, object] = Field(default_factory=dict)
    raw_url: str = ""  # RawItem.url this listing was parsed from (per-item raw association)


class NormalizedListing(ParsedListing):
    """ParsedListing + the ADR-0008 FX stamp + the FR-004 international flag."""

    fx_rate: Decimal
    fx_pair: str
    fx_rate_date: date
    fx_source: str
    is_international: bool


class SourceAdapter(Protocol):
    name: str
    site_key: str
    run_kind: RunKind
    expects_json: bool  # drives the anti_bot "JSON endpoint answered text/html" check

    # How many raw source records the MOST RECENT parse() discarded as malformed.
    # Adapters reset it to 0 at the top of every parse() and increment at each
    # internal drop site; it is write-only from the adapter's perspective, and no
    # production path (run_source, the poller, heartbeat) reads or branches on it.
    # harvest_corpus adds it to the per-source `skipped_malformed` count, which is
    # otherwise blind to records parse() dropped before returning (PA-003) — a
    # source whose markup drifted would look perfectly healthy at 3 harvested rows.
    last_parse_skipped: int

    async def fetch(self) -> RawBatch: ...

    def parse(self, batch: RawBatch) -> list[ParsedListing]: ...


class ListingResolver(Protocol):
    def resolve_listing(self, listing_id: int) -> None: ...


class NullResolver:
    """C.3 isolation stub kept for pipeline tests; the poller wires
    matching.resolver.CatalogResolver (MS-1b)."""

    def resolve_listing(self, listing_id: int) -> None:
        return None
