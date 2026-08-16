"""Stage runner: fetch → parse → normalize → persist → delist → resolve, in a ScraperRun.

Stages are independently re-runnable (§8.1); a resolver failure never blocks
persistence (C.3 — the listing lands unresolved). ORM work runs through
sync_to_async because the runner lives on the poller's event loop.

The delist stage (CR-004) is opt-in per source: an adapter that can describe what
its sweep proves implements DelistDetector, and listings this site owns that the
sweep contradicts are soft-deleted (never row-deleted — see Listing.mark_delisted
and the PROTECT on ListingResolution.superseded_by).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

from asgiref.sync import sync_to_async
from django.utils import timezone
from pydantic import ValidationError

from hw_radar.acquisition import fx
from hw_radar.acquisition.classify import classify_exception, classify_response
from hw_radar.acquisition.contracts import (
    ListingResolver,
    NormalizedListing,
    ParsedListing,
    RawBatch,
    SourceAdapter,
)
from hw_radar.acquisition.persist import append_snapshot, store_raw, upsert_listing
from hw_radar.acquisition.scheduling.apply import RunOutcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import (
    DelistReason,
    Listing,
    RawPayload,
    ResolutionGrain,
    RetentionClass,
    RunFailureClass,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceSite,
)

logger = logging.getLogger(__name__)

MEDIAN_BODY_WINDOW = 10  # recent successful runs consulted for EC-007 body-size outliers
FETCH_TIMEOUT_S = (
    120.0  # ADR-0012 hard fetch-stage timeout; a hung adapter must not wedge the poller
)

_EVENT_BY_CLASS: dict[RunFailureClass, LifecycleEvent] = {
    RunFailureClass.TRANSIENT: LifecycleEvent.TRANSIENT_FAILURE,
    RunFailureClass.ANTI_BOT: LifecycleEvent.ANTI_BOT,
    RunFailureClass.PARSER_ROT: LifecycleEvent.PARSER_ROT,
    RunFailureClass.DEGRADATION: LifecycleEvent.DEGRADATION,
    RunFailureClass.UNKNOWN: LifecycleEvent.UNKNOWN_FAILURE,
}


class FetchFailure(Exception):
    def __init__(self, failure_class: RunFailureClass, message: str) -> None:
        super().__init__(message)
        self.failure_class = failure_class


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
        is "how stale may this offer be before we must stop showing it".

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


def _apply_delist(site: SourceSite, scope: DelistScope) -> int:
    """Soft-delete this site's active listings that the sweep contradicts.

    Deliberately per-row rather than a bulk .update(): mark_delisted also pulls the
    DR-008 evidence TTLs forward, and a queryset update would mark the listings
    terminal while leaving their snapshots on the original freshness clock.
    """
    candidates = (
        Listing.objects.not_delisted()
        .filter(source_site=site)
        .exclude(source_listing_key__in=scope.seen_keys)
    )
    if scope.complete:
        reason = DelistReason.ABSENT_FROM_SWEEP
    else:
        reason = DelistReason.ABSENT_STALE
        candidates = candidates.filter(last_seen__lt=scope.observed_at - scope.absence_grace)
    delisted = 0
    for listing in candidates.iterator():
        if listing.mark_delisted(reason, when=scope.observed_at):
            delisted += 1
    return delisted


def _median_body_bytes(site: SourceSite) -> int | None:
    # EC-007 invariant: detail_json["body_bytes"] is stored as a RUN-LEVEL SUM
    # of every item's body size (see run_source below), but classify_response
    # consumes median_body_bytes as a PER-ITEM comparison basis (an item is a
    # soft-block if its own body is <20% of the median item size). Divide each
    # run's total by its records_fetched to recover a per-item average before
    # taking the median, and restrict the window to FULL runs — heartbeat/probe
    # runs fetch a single item, which would otherwise inject a distorted
    # "average of 1" and skew the basis.
    rows = (
        ScraperRun.objects.filter(source_site=site, status=RunStatus.SUCCESS, run_kind=RunKind.FULL)
        .order_by("-started_at")
        .values_list("detail_json__body_bytes", "records_fetched")[:MEDIAN_BODY_WINDOW]
    )
    averages = [
        size / records for size, records in rows if isinstance(size, int) and size > 0 and records
    ]
    return int(statistics.median(averages)) if averages else None


def _classify_batch(batch: RawBatch, *, expects_json: bool, median: int | None) -> None:
    for item in batch.items:
        verdict = classify_response(
            http_status=item.http_status,
            content_type=item.content_type,
            expected_json=expects_json,
            body_text=item.payload_text or "",
            median_body_bytes=median,
        )
        if verdict is not None:
            raise FetchFailure(verdict, f"{item.url} classified {verdict}")


def _persist_all(
    site: SourceSite,
    batch: RawBatch,
    normalized: list[NormalizedListing],
    retention_class: RetentionClass,
    expires_at: datetime | None,
) -> tuple[list[int], int, int]:
    # CR-002: every RawItem is stored, not just items[0] — multi-request
    # connectors (e.g. WD's search sweep + per-product fetches) otherwise lose
    # provenance for every item but the first. raws is a LIST (not a dict keyed
    # by url) so duplicate source URLs each still get their own stored row.
    raws = [
        store_raw(
            item,
            fetched_at=batch.fetched_at,
            retention_class=retention_class,
            expires_at=expires_at,
        )
        for item in batch.items
    ]
    # url -> RawPayload for snapshot association; first raw per url wins when
    # a batch has duplicate source URLs (rare, but must not drop a stored row).
    by_url: dict[str, RawPayload] = {}
    for item, raw in zip(batch.items, raws, strict=True):
        by_url.setdefault(item.url, raw)
    # Single-item batches have no meaningful raw_url to key on (adapters may
    # leave it unset); fall back to the one raw payload that must be it.
    sole = raws[0] if len(raws) == 1 else None
    listing_ids: list[int] = []
    upserted = 0
    appended = 0
    # observed_at is the instant the offer was observed at fetch time, not
    # persistence time: it must match RawPayload.fetched_at and the FX stamping
    # basis (batch.fetched_at.date()) so stored raw payloads can be replayed
    # faithfully.
    observed_at = batch.fetched_at
    for record in normalized:
        listing, _created = upsert_listing(site, record, retention_class, expires_at=expires_at)
        # Seeing a listing is proof it is not delisted, so any terminal mark is
        # cleared here rather than in the delist stage. This is what makes the
        # absence heuristics (see DelistScope) self-healing: a listing wrongly
        # delisted for missing a truncated sweep returns to the live set on its
        # next appearance, keeping its pk, its history and its resolution edges.
        listing.mark_relisted()
        # append_snapshot reads listing.expires_at (not the expires_at param
        # directly) so a snapshot's TTL always matches its listing's current
        # value, even if a future caller mutates the listing between calls.
        raw = by_url.get(record.raw_url) or sole  # per-item; fallback to the sole raw
        append_snapshot(listing, record, observed_at=observed_at, raw=raw)
        listing_ids.append(listing.pk)
        upserted += 1
        appended += 1
    return listing_ids, upserted, appended


def _grain_counts(listing_ids: list[int]) -> dict[str, int]:
    # POST-resolution tally (SA-003): read after resolver.resolve_listing has
    # run for every listing_id, so this reflects each listing's final grain
    # for the run, not its pre-resolve default. Every persisted listing has a
    # grain (default NONE), so counts always sum to records_valid.
    #
    # listing_ids can repeat a pk: two records in one batch sharing a
    # source_listing_key both resolve to the same Listing via upsert_listing's
    # (source_site, source_listing_key) key, so _persist_all appends that pk
    # once per record, not once per distinct listing. A filter(...).count()-style
    # tally over DISTINCT query rows would collapse the repeat and undercount
    # (E1 review) — build a pk->grain map instead and iterate listing_ids
    # (with duplicates) so sum(counts) == len(listing_ids) == records_valid
    # unconditionally.
    grain_by_pk: dict[int, str] = dict(
        Listing.objects.filter(pk__in=listing_ids).values_list("pk", "resolution_grain")
    )
    counts: dict[str, int] = {str(choice): 0 for choice in ResolutionGrain.values}
    for listing_id in listing_ids:
        key = str(grain_by_pk[listing_id])
        counts[key] = counts.get(key, 0) + 1
    return counts


# Scalar keys pulled verbatim from a Scrapy StatsCollector snapshot into
# detail_json["scrapy_stats"]. Deliberately NOT the raw stats dict: Scrapy
# accumulates internal/volatile keys (memusage/*, scheduler/*, responses_per_minute)
# that are noise here and would grow detail_json unboundedly across stat additions
# in future Scrapy versions. Keep this list and _STATS_PREFIXES in sync with the
# research note in the MS-1 Scrapy-diagnostics follow-up.
_STATS_SCALAR_KEYS = (
    "finish_reason",
    "start_time",
    "finish_time",
    "elapsed_time_seconds",
    "item_scraped_count",
    "item_dropped_count",
    "response_received_count",
)
# Prefix families worth keeping in full: response-status and exception counts
# are the actual blind spot this feature closes (non-2xx responses never
# reach parse(), so they are otherwise invisible in ScraperRun). log_count/*
# is deliberately excluded: empirically, with BASE_SETTINGS["LOG_ENABLED"] =
# False, Scrapy's LogStats extension never fires and no log_count/* key is
# ever populated (verified against a demo-adapter run on Scrapy 2.16.0).
_STATS_PREFIXES = (
    "downloader/response_status_count/",
    "downloader/exception_count",
    "downloader/exception_type_count/",
    "retry/",
    "item_dropped_reasons_count/",
)


def _filter_scrapy_stats(stats: dict[str, object]) -> dict[str, object]:
    """Select the stable diagnostic subset of a raw Scrapy stats snapshot.

    datetimes (start_time/finish_time) are not JSON-serializable, so they are
    converted to ISO strings here rather than at the detail_json call site.
    """
    selected: dict[str, object] = {}
    for key, value in stats.items():
        if key in _STATS_SCALAR_KEYS or key.startswith(_STATS_PREFIXES):
            selected[key] = value.isoformat() if isinstance(value, datetime) else value
    return selected


async def _normalize(
    parsed_records: list[ParsedListing], observed_date: date
) -> list[NormalizedListing]:
    normalized: list[NormalizedListing] = []
    for parsed in parsed_records:
        try:
            normalized.append(await sync_to_async(fx.stamp)(parsed, observed_date))
        except fx.MissingRateError:
            await fx.refresh_daily((parsed.currency,))
            normalized.append(await sync_to_async(fx.stamp)(parsed, observed_date))
    return normalized


async def run_source(
    adapter: SourceAdapter,
    resolver: ListingResolver,
    *,
    retention_class: RetentionClass = RetentionClass.MERCHANT_FACT,
    expires_policy: Callable[[datetime], datetime | None] | None = None,
    run_kind: RunKind | None = None,
    fetch_timeout_s: float = FETCH_TIMEOUT_S,
) -> tuple[ScraperRun, RunOutcome]:
    effective_kind = run_kind or adapter.run_kind
    site = await sync_to_async(SourceSite.objects.get)(normalized_name=adapter.site_key)
    run = await sync_to_async(ScraperRun.objects.create)(
        source_site=site, run_kind=effective_kind, started_at=timezone.now()
    )
    try:
        async with asyncio.timeout(fetch_timeout_s):
            batch = await adapter.fetch()
        median = await sync_to_async(_median_body_bytes)(site)
        _classify_batch(batch, expects_json=adapter.expects_json, median=median)
        try:
            parsed = adapter.parse(batch)
        except ValidationError as exc:
            raise FetchFailure(RunFailureClass.PARSER_ROT, f"validation failed: {exc}") from exc
        if batch.items and not parsed:
            raise FetchFailure(RunFailureClass.PARSER_ROT, "authentic fetch yielded 0 records")
        normalized = await _normalize(parsed, batch.fetched_at.date())
        # expires_policy is a callable, not a fixed datetime, so bounded TTLs
        # (e.g. eBay's DR-008 <=6h) stay relative to this batch's own fetch
        # time rather than the moment run_source happened to be called.
        expires_at = expires_policy(batch.fetched_at) if expires_policy else None
        listing_ids, upserted, appended = await sync_to_async(_persist_all)(
            site, batch, normalized, retention_class, expires_at
        )
        # Delist stage — runs AFTER persistence so this sweep's listings are
        # already revived/last_seen-bumped and cannot be delisted by their own
        # sweep. Restricted to FULL runs: a PROBE is a recovery poke and a
        # heartbeat is a cheap partial signal, so neither is entitled to conclude
        # that everything it failed to return has ended.
        delisted = 0
        if effective_kind is RunKind.FULL and isinstance(adapter, DelistDetector):
            scope = adapter.delist_scope(batch, parsed)
            if scope is not None:
                delisted = await sync_to_async(_apply_delist)(site, scope)
        resolver_errors = 0
        for listing_id in listing_ids:
            try:
                await sync_to_async(resolver.resolve_listing)(listing_id)
            except Exception:  # resolver failure never blocks ingestion (C.3)
                logger.exception("resolver failed for listing %s", listing_id)
                resolver_errors += 1
        grain_counts = await sync_to_async(_grain_counts)(listing_ids)
        run.records_fetched = len(batch.items)
        run.records_valid = len(normalized)
        run.listings_upserted = upserted
        run.snapshots_appended = appended
        run.detail_json = {
            "body_bytes": sum(len(item.payload_text or "") for item in batch.items),
            "resolver_errors": resolver_errors,
            "grain_counts": grain_counts,
            "listings_delisted": delisted,
        }
        if batch.scrapy_stats:
            run.detail_json["scrapy_stats"] = _filter_scrapy_stats(batch.scrapy_stats)
        run.status = RunStatus.SUCCESS
        run.finished_at = timezone.now()
        await sync_to_async(run.save)()
        event = (
            LifecycleEvent.PROBE_SUCCESS
            if effective_kind is RunKind.PROBE
            else LifecycleEvent.SUCCESS
        )
        return run, RunOutcome(event)
    except FetchFailure as exc:
        return await _finalize_failure(run, exc.failure_class, str(exc), effective_kind)
    except Exception as exc:  # every crash must classify + record (NFR-001)
        return await _finalize_failure(run, classify_exception(exc), repr(exc), effective_kind)


async def _finalize_failure(
    run: ScraperRun, failure_class: RunFailureClass, message: str, run_kind: RunKind
) -> tuple[ScraperRun, RunOutcome]:
    logger.warning("run %s failed: %s (%s)", run.pk, failure_class, message)
    run.status = RunStatus.FAILED
    run.failure_class = failure_class
    run.error = message[:2000]
    run.finished_at = timezone.now()
    await sync_to_async(run.save)()
    if run_kind is RunKind.PROBE:
        # A failed recovery probe keeps the source paused (ADR-0017); PROBE_FAILURE
        # is state-neutral in apply_run_outcome (only last_run_at moves), so it
        # cannot stack a back-off window or remap to ANTI_BOT/etc.
        return run, RunOutcome(LifecycleEvent.PROBE_FAILURE)
    return run, RunOutcome(_EVENT_BY_CLASS[failure_class])
