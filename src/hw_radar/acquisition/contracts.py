"""C.1 adapter contract: Pydantic I/O models + structural protocols.

Adapters own fetch/parse only; normalization (FX), resolution, and persistence
are pipeline-owned so every source shares one code path (spec C.1: "no
per-source code beyond the adapter").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hw_radar.catalog.models import ProviderKind, RetentionClass, RunCompleteness, RunKind
from hw_radar.matching.categories import CATEGORY_SLUG_MAX_LENGTH, CATEGORY_SLUG_RE

# Reserved OfferSnapshot.attrs_json key under which persist.append_snapshot stores
# a non-null ParsedListing.category_hint, and from which the resolver reads it
# back. ParsedListing rejects it in `attrs` so the two can never disagree.
CATEGORY_HINT_ATTR: Final = "category_hint"


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
    # run_spider() (None for non-Scrapy adapters). run_collection() filters this
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
    # MS2-D-03: the category the collector's QUERY SCOPE asserts (eBay category
    # sweep, Actor input scope) — never inferred from title text. None = no
    # assertion, which the resolver treats as the legacy drive default.
    category_hint: str | None = Field(
        default=None,
        max_length=CATEGORY_SLUG_MAX_LENGTH,
        pattern=CATEGORY_SLUG_RE.pattern,
    )

    @model_validator(mode="after")
    def _attrs_do_not_carry_category_hint(self) -> Self:
        # attrs lands verbatim in attrs_json, where the hint is persisted under
        # the same key: an attrs-borne value would masquerade as a collector
        # assertion (or clobber the real one) at resolution time.
        if CATEGORY_HINT_ATTR in self.attrs:
            raise ValueError(
                f"attrs may not carry the reserved {CATEGORY_HINT_ATTR!r} key; "
                "set ParsedListing.category_hint instead"
            )
        return self


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


@dataclass(frozen=True)
class DelistScope:
    """What one sweep proves about the listings it did NOT contain (CR-004).

    A source that can say "these keys are what exists right now" returns this from
    delist_scope(); the pipeline turns it into soft-delete marks. The two fields
    that matter are evidence-strength knobs, because absence is the weakest kind
    of evidence there is:

    complete — the sweep enumerated the ENTIRE result set for its query (no unseen
        pages). Absence from a complete sweep is direct evidence and delists on the
        spot. A source that cannot prove completeness must pass False; claiming it
        falsely converts one truncated page into a mass delist.
    absence_grace — for a truncated sweep, how long a listing must go unseen
        across EVERY sweep before absence is believed. Set it from the source's
        freshness obligation, not from the poll interval: the question it answers
        is "how stale may this offer be before we must stop showing it". It is
        measured in POLLING time, not wall-clock time — the pipeline's delist
        stage (see _record_sweep_continuity) refuses stale-absence marks unless
        the lane actually swept continuously across the whole window, so a source
        that was paused for a day does not delist its catalogue on resume.

    Both paths are reversible — Listing.mark_relisted() clears the mark when the
    source shows the listing again — so the failure mode of a wrong delist is a
    temporarily hidden offer, not lost data.
    """

    seen_keys: frozenset[str]
    observed_at: datetime
    complete: bool
    absence_grace: timedelta


@runtime_checkable
class DelistDetector(Protocol):
    """Optional adapter capability, discovered structurally so that wiring a
    source for delete-on-delist needs no change in the poller's run_source call."""

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None: ...


@dataclass(frozen=True)
class AdapterRetention:
    """The DR-001 retention an adapter claims for every row one of its runs persists."""

    retention_class: RetentionClass
    expires_policy: Callable[[datetime], datetime | None] | None


def adapter_retention(adapter: SourceAdapter) -> AdapterRetention:
    """Read an adapter's declared retention, defaulting to indefinite merchant fact.

    Both attributes are optional on the SourceAdapter protocol — only bounded
    sources declare them (eBay: ebay_listing_observation + a <=6h expires_policy
    per DR-008) — so they are read reflectively rather than being protocol
    members every adapter must spell out.

    EVERY run_source call site that fires a real adapter must forward this;
    run_source's own defaults are merchant_fact with no TTL, so a call site that
    forgets silently persists bounded evidence indefinitely, where the DR-001
    sweeper can never reach it. Call sites: run_heartbeat (acquisition.heartbeat),
    and poll_source / recovery_probe_job in hw_radar.poller.service.
    """
    return AdapterRetention(
        retention_class=getattr(adapter, "retention_class", RetentionClass.MERCHANT_FACT),
        expires_policy=getattr(adapter, "expires_policy", None),
    )


class ListingResolver(Protocol):
    def resolve_listing(self, listing_id: int) -> None: ...


class NullResolver:
    """C.3 isolation stub kept for pipeline tests; the poller wires
    matching.resolver.CatalogResolver (MS-1b)."""

    def resolve_listing(self, listing_id: int) -> None:
        return None


class ProviderRunEvidence(BaseModel):
    """What one collection run proves about its own completeness (MS2-D-11).

    Recorded for every successful run in ScraperRun.detail_json["provider"] (the
    Slice D provider-run table will hold it queryably). The delist stage consults
    it through acquisition.providers.gate_delist_scope, never the provider's
    DelistScope alone: a provider's scope says what it saw, this says whether that
    sighting may be read as absence.

    stale_absence_eligible — whether a TRUNCATED run may still feed the CR-004
        stale-absence path (absence believed only after the adapter's grace, across
        continuous polling). Only a local provider may claim it: the local
        adapters' truncated sweeps are the long-standing eBay/lane-continuity
        contract, whereas a remote run's truncation is set by budget and item
        limits it reports after the fact, so it is never absence evidence of any
        strength (ADR 0021).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_version: Literal[1] = 1
    provider_kind: ProviderKind
    provider_key: str = Field(min_length=1)
    completeness: RunCompleteness
    completeness_reason: str = Field(min_length=1)
    stale_absence_eligible: bool

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        # A blank reason would make a non-complete run unexplainable in the run
        # record, which is exactly when an operator needs the reason.
        if not self.completeness_reason.strip():
            raise ValueError("completeness_reason must be non-blank")
        if self.stale_absence_eligible and self.provider_kind is not ProviderKind.LOCAL:
            raise ValueError("only a local provider may be stale-absence eligible")
        return self


class CollectionProvider(Protocol):
    """Who collects one source's data for the pipeline (MS2-D-10, ADR 0021).

    run_collection (acquisition.pipeline) is the only consumer. site_key names the
    SourceSite whose listings the run touches — source identity — which is
    independent of provider_kind/provider_key, so switching a source between a
    local adapter and a remote Actor never forks listing identity or history.

    delist_scope reports what the provider saw; run_evidence reports whether that
    may be read as absence. The pipeline never acts on the scope directly, only on
    acquisition.providers.gate_delist_scope(scope, evidence). run_evidence takes
    the pipeline's effective run kind (which can override the provider's own
    run_kind, e.g. a PROBE replay) and the scope the pipeline obtained — None for
    non-FULL runs, where absence is never evaluated.
    """

    provider_kind: ProviderKind
    provider_key: str
    site_key: str
    run_kind: RunKind
    expects_json: bool  # drives the anti_bot "JSON endpoint answered text/html" check

    async def fetch(self) -> RawBatch: ...

    def parse(self, batch: RawBatch) -> list[ParsedListing]: ...

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None: ...

    def run_evidence(
        self,
        batch: RawBatch,
        parsed: list[ParsedListing],
        scope: DelistScope | None,
        *,
        run_kind: RunKind,
    ) -> ProviderRunEvidence: ...
