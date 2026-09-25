"""Stage runner: fetch → parse → normalize → persist → delist → resolve → evaluate,
in a ScraperRun.

Stages are independently re-runnable (§8.1); a resolver failure never blocks
persistence (C.3 — the listing lands unresolved), and neither does an evaluator
failure (MS2-D-20). ORM work runs through sync_to_async because the runner lives
on the poller's event loop.

The evaluate stage (MS-2 Slice C, MS2-D-20) runs the eligibility evaluator for
every listing whose resolution did not raise in this run. `evaluator=None` binds
the production WatchEvaluator rather than skipping the stage, so the poll,
heartbeat-fired FULL and recovery-probe paths — all of which reach this runner
through run_source with no evaluator argument — evaluate without per-caller
wiring. A listing the stage did not assess (resolver raised, evaluator raised)
keeps its old WatchEvaluation rows, which the read model then sees as
non-current (`pending`) because their snapshot binding no longer matches: the
failure path fails closed without writing anything.

The delist stage (CR-004) is opt-in per source: an adapter that can describe what
its sweep proves implements DelistDetector, and listings this site owns that the
sweep contradicts are soft-deleted (never row-deleted — see Listing.mark_delisted
and the PROTECT on ListingResolution.superseded_by). The adapter owns the absence
grace; the pipeline owns the proof that the lane was polling across it, because
only the pipeline can see SourceLaneState and the run history.

Every run enters through a CollectionProvider (ADR 0021, MS2-D-10):
run_collection is the stage runner, and run_source wraps a plain SourceAdapter in
a LocalCollectionProvider so poller, heartbeat and probe call sites are unchanged.
The provider's run evidence gates the delist stage (acquisition.providers), so a
truncated, partial or failed run is never read as evidence of absence.

Persist and delist are the extracted transactional stages of acquisition.stages
(MS2-D-22 *Code shape*), shared with the durable Apify importer: each runs in
its own transaction inside one sync_to_async call, so no transaction spans an
await, and both take the MS2-D-35 lock order with the in-memory retry.
Resolution and evaluation stay outside both, as before.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable, Sequence
from datetime import date, datetime

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import ValidationError

from hw_radar.acquisition import fx
from hw_radar.acquisition.classify import classify_exception, classify_response
from hw_radar.acquisition.contracts import (
    CollectionProvider,
    DelistScope,
    ListingResolver,
    NormalizedListing,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    ScopeSweepReport,
    SourceAdapter,
)
from hw_radar.acquisition.persist import ObservationRetention
from hw_radar.acquisition.providers import (
    LocalCollectionProvider,
    aggregate_local_evidence,
    counts_toward_sweep_continuity,
    gate_delist_scope,
)
from hw_radar.acquisition.scheduling.apply import RunOutcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.acquisition.stages import (
    PersistResult,
    apply_absence,
    apply_delist,
    atomic_with_retry,
    ensure_watermark_rows,
    persist_observations,
    target_scopes,
)
from hw_radar.catalog.models import (
    Listing,
    ProviderKind,
    ResolutionGrain,
    RetentionClass,
    RunFailureClass,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceSite,
)
from hw_radar.eligibility import ListingEvaluator, WatchEvaluator

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


def _apply_delist(  # pyright: ignore[reportUnusedFunction] - tests/db/test_scoped_delist.py drives it
    site: SourceSite, scope: DelistScope, continuous_since: datetime | None
) -> int:
    """Run the scoped delist step (acquisition.stages.apply_delist) in its own transaction.

    A direct entry point for callers outside a transaction; the pipeline itself
    reaches apply_delist through apply_absence inside the delist transaction.
    """
    with transaction.atomic():
        return apply_delist(site, scope, continuous_since)


def _median_body_bytes(site: SourceSite) -> int | None:
    # EC-007 invariant: detail_json["body_bytes"] is stored as a RUN-LEVEL SUM
    # of every item's body size (see run_collection below), but classify_response
    # consumes median_body_bytes as a PER-ITEM comparison basis (an item is a
    # soft-block if its own body is <20% of the median item size). Divide each
    # run's total by its records_fetched to recover a per-item average before
    # taking the median, and restrict the window to FULL runs — heartbeat/probe
    # runs fetch a single item, which would otherwise inject a distorted
    # "average of 1" and skew the basis.
    #
    # Local runs only (MS2-D-28): an imported run's "items" are small dataset
    # rows, not HTTP pages, so its body sizes are not a basis for judging a page.
    # A run that predates provider evidence has no "provider" key and is local.
    # Without this filter, a window of remote runs after a provider switch would
    # replace the page-size basis and soft-block the next ordinary local run.
    #
    # The basis still averages over every item of a run, including items that
    # opt out of the comparison (RawItem.body_size_comparable, e.g. eBay's
    # short final sweep pages). They can only pull the average DOWN, which
    # makes the rule less sensitive for the remaining full pages, never
    # trigger it; a few-KB challenge page stays far below either basis.
    rows = (
        ScraperRun.objects.filter(source_site=site, status=RunStatus.SUCCESS, run_kind=RunKind.FULL)
        .filter(
            Q(detail_json__provider__provider_kind=ProviderKind.LOCAL.value)
            | Q(detail_json__provider__provider_kind__isnull=True)
        )
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
            # RawItem.body_size_comparable: a legitimately short page (the last
            # page of a paginated sweep) skips only the body-size rule.
            median_body_bytes=median if item.body_size_comparable else None,
        )
        if verdict is not None:
            raise FetchFailure(verdict, f"{item.url} classified {verdict}")


def _grain_counts(listing_ids: list[int]) -> dict[str, int]:
    # POST-resolution tally (SA-003): read after resolver.resolve_listing has
    # run for every listing_id, so this reflects each listing's final grain
    # for the run, not its pre-resolve default. Every persisted listing has a
    # grain (default NONE), so counts always sum to records_valid.
    #
    # listing_ids can repeat a pk: two records in one batch sharing a
    # source_listing_key both resolve to the same Listing via upsert_listing's
    # (source_site, source_listing_key) key, so persist_observations appends that pk
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
    """Run one in-process SourceAdapter through the pipeline (see run_collection).

    Signature kept stable for the poller, heartbeat and probe call sites; retention
    must still be forwarded from adapter_retention(adapter) by the caller. It
    deliberately takes no evaluator: run_collection's default binds the
    production WatchEvaluator, so every one of those call sites evaluates
    (MS2-D-20) without being edited.
    """
    return await run_collection(
        LocalCollectionProvider(adapter),
        resolver,
        retention_class=retention_class,
        expires_policy=expires_policy,
        run_kind=run_kind,
        fetch_timeout_s=fetch_timeout_s,
    )


async def run_collection(
    provider: CollectionProvider,
    resolver: ListingResolver,
    *,
    retention_class: RetentionClass = RetentionClass.MERCHANT_FACT,
    expires_policy: Callable[[datetime], datetime | None] | None = None,
    run_kind: RunKind | None = None,
    fetch_timeout_s: float = FETCH_TIMEOUT_S,
    evaluator: ListingEvaluator | None = None,
) -> tuple[ScraperRun, RunOutcome]:
    """Fetch, classify, parse, persist, delist, resolve and evaluate one provider run.

    Always returns a recorded ScraperRun: every failure is classified and
    finalized rather than raised (NFR-001). A successful run stores the
    provider's ProviderRunEvidence in detail_json["provider"] and the count of
    listings whose eligibility evaluation raised in
    detail_json["evaluator_errors"]; an evaluator failure never fails the run.

    `evaluator=None` means the production WatchEvaluator, not "no evaluation"
    (MS2-D-20); tests inject a fake through this parameter.
    """
    # Rejected: a null-object default that each caller must override. That is
    # exactly the omission plan review F-03 found on the heartbeat path — any
    # caller that forgot the argument would silently leave stale verdicts
    # standing — so the production evaluator is the default instead.
    listing_evaluator: ListingEvaluator = evaluator if evaluator is not None else WatchEvaluator()
    effective_kind = run_kind or provider.run_kind
    site = await sync_to_async(SourceSite.objects.get)(normalized_name=provider.site_key)
    run = await sync_to_async(ScraperRun.objects.create)(
        source_site=site, run_kind=effective_kind, started_at=timezone.now()
    )
    try:
        async with asyncio.timeout(fetch_timeout_s):
            batch = await provider.fetch()
        # MS2-D-28: only a local provider's items are HTTP responses. A remote
        # provider's items are dataset rows with no body text, which the EC-007
        # body-size rule would call ANTI_BOT against any local median; remote
        # transport health is classify_run's job, inside the provider.
        if provider.provider_kind is ProviderKind.LOCAL:
            median = await sync_to_async(_median_body_bytes)(site)
            _classify_batch(batch, expects_json=provider.expects_json, median=median)
        try:
            parsed = provider.parse(batch)
        except ValidationError as exc:
            raise FetchFailure(RunFailureClass.PARSER_ROT, f"validation failed: {exc}") from exc
        if batch.items and not parsed:
            raise FetchFailure(RunFailureClass.PARSER_ROT, "authentic fetch yielded 0 records")
        normalized = await _normalize(parsed, batch.fetched_at.date())
        # expires_policy is a callable, not a fixed datetime, so bounded TTLs
        # (e.g. eBay's DR-008 <=6h) stay relative to this batch's own fetch
        # time rather than the moment run_collection happened to be called.
        expires_at = expires_policy(batch.fetched_at) if expires_policy else None
        persisted = await persist_local(
            site, batch, normalized, ObservationRetention(retention_class, expires_at)
        )
        listing_ids = persisted.listing_ids
        # Delist stage — runs AFTER persistence so this sweep's listings are
        # already revived/last_seen-bumped and cannot be delisted by their own
        # sweep. Restricted to FULL runs: a PROBE is a recovery poke and a
        # heartbeat is a cheap partial signal, so neither is entitled to conclude
        # that everything it failed to return has ended.
        #
        # The provider's scope is never applied as-is: gate_delist_scope reads it
        # through the run's completeness evidence, so a remote run that stopped at
        # a page/item/budget limit cannot delist however its scope is phrased, and
        # the same evidence AND scope decide whether the run advances the swept
        # scope's continuity or breaks it — a non-local run needs its own scope
        # to claim complete=True too (plan review F-04 residual; see
        # counts_toward_sweep_continuity). Continuity is recorded AFTER
        # delist_scope(), because it depends on the evidence; if delist_scope
        # raises, the run fails without advancing continuity, which can only
        # shorten the window — the conservative direction for stale absence.
        #
        # The swept scope is the DelistScope's scope_key; a scope-less run swept
        # the legacy NULL scope (MS2-D-31). A local adapter that sweeps several
        # scopes in one run (MultiScopeDelistDetector, the eBay category sweeps)
        # goes through _apply_scope_reports instead, which applies these same
        # rules once per swept scope.
        delisted = 0
        scope_outcomes: list[dict[str, object]] | None = None
        reports = (
            _scope_reports(provider, batch, parsed) if effective_kind is RunKind.FULL else None
        )
        if reports:
            evidence, delisted, scope_outcomes = await _apply_scope_reports(
                site, provider, batch, parsed, reports
            )
        elif effective_kind is RunKind.FULL:
            scope = provider.delist_scope(batch, parsed)
            evidence = provider.run_evidence(batch, parsed, scope, run_kind=effective_kind)
            delisted = await absence_local(
                site,
                swept_scope_key=None if scope is None else scope.scope_key,
                eligible=counts_toward_sweep_continuity(evidence, scope),
                gated=gate_delist_scope(scope, evidence),
                event_time=batch.fetched_at,
            )
        else:
            evidence = provider.run_evidence(batch, parsed, None, run_kind=effective_kind)
        resolver_errors = 0
        resolver_failed: set[int] = set()
        for listing_id in listing_ids:
            try:
                await sync_to_async(resolver.resolve_listing)(listing_id)
            except Exception:  # resolver failure never blocks ingestion (C.3)
                logger.exception("resolver failed for listing %s", listing_id)
                resolver_errors += 1
                resolver_failed.add(listing_id)
        evaluator_errors = await _evaluate_all(listing_evaluator, listing_ids, resolver_failed)
        grain_counts = await sync_to_async(_grain_counts)(listing_ids)
        run.records_fetched = len(batch.items)
        run.records_valid = len(normalized)
        run.listings_upserted = persisted.upserted
        run.snapshots_appended = persisted.appended
        run.detail_json = {
            "body_bytes": sum(len(item.payload_text or "") for item in batch.items),
            "resolver_errors": resolver_errors,
            "evaluator_errors": evaluator_errors,
            "grain_counts": grain_counts,
            "listings_delisted": delisted,
            "provider": evidence.model_dump(mode="json"),
        }
        if scope_outcomes is not None:
            run.detail_json["scopes"] = scope_outcomes
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


def _scope_reports(
    provider: CollectionProvider, batch: RawBatch, parsed: list[ParsedListing]
) -> Sequence[ScopeSweepReport] | None:
    """The run's per-scope sweep reports, or None for the single-scope path.

    Only a LocalCollectionProvider is asked (MultiScopeDelistDetector): a remote
    provider's run stays on the single-scope gate even if it grew a
    delist_scopes method, so no remote-provider invariant depends on this path.
    """
    if not isinstance(provider, LocalCollectionProvider):
        return None
    return provider.delist_scopes(batch, parsed)


async def _apply_scope_reports(
    site: SourceSite,
    provider: CollectionProvider,
    batch: RawBatch,
    parsed: list[ParsedListing],
    reports: Sequence[ScopeSweepReport],
) -> tuple[ProviderRunEvidence, int, list[dict[str, object]]]:
    """Apply absence and continuity to each swept scope of one multi-scope FULL run.

    Every scope goes through the same per-scope rules as a single-scope run —
    gate_delist_scope, counts_toward_sweep_continuity and absence_local with
    swept_scope_key=report.scope_key — in its own transaction, so a complete
    sweep of one scope can only delist listings recorded under that scope
    (apply_delist filters on collection_scope). Order is deterministic: the
    NULL scope first, then scope keys ascending.

    Two deviations from applying each report as if it were its own run:
    - a non-NULL report with no scope BREAKS that scope's continuity (see
      ScopeSweepReport.scope); the NULL scope keeps the single-scope rule;
    - when the run also swept the NULL scope, the non-NULL calls pass
      null_scope_swept=True so they leave the NULL lane to its own sweep's
      evidence (apply_absence, MS2-D-31).

    Returns the run-level evidence (record-only; aggregate_local_evidence), the
    total delisted, and per-scope outcomes for detail_json["scopes"].
    """
    ordered = sorted(reports, key=lambda r: (r.scope_key is not None, r.scope_key or ""))
    null_swept = any(r.scope_key is None for r in ordered)
    per_scope: list[ProviderRunEvidence] = []
    outcomes: list[dict[str, object]] = []
    total = 0
    for report in ordered:
        evidence = provider.run_evidence(batch, parsed, report.scope, run_kind=RunKind.FULL)
        eligible = counts_toward_sweep_continuity(evidence, report.scope) and (
            report.scope is not None or report.scope_key is None
        )
        delisted = await absence_local(
            site,
            swept_scope_key=report.scope_key,
            eligible=eligible,
            gated=gate_delist_scope(report.scope, evidence),
            event_time=batch.fetched_at,
            null_scope_swept=null_swept,
        )
        per_scope.append(evidence)
        total += delisted
        outcomes.append(
            {
                "scope_key": report.scope_key,
                "pages": report.pages,
                "complete": report.scope is not None and report.scope.complete,
                "reason": report.reason,
                "continuity": "recorded" if eligible else "broken",
                "delisted": delisted,
            }
        )
    return aggregate_local_evidence(per_scope), total, outcomes


async def persist_local(
    site: SourceSite,
    batch: RawBatch,
    normalized: list[NormalizedListing],
    retention: ObservationRetention,
) -> PersistResult:
    """Run the local persist step in one transaction (D10 *Local transactions*, ED-04).

    Everything the step writes (raw payloads, listing observation and
    creation, snapshots) commits together or not at all: a crash mid-persist
    rolls back the whole batch, where the MS-1 autocommit path kept the rows
    before the crash, and the next poll repairs it. A retry exhaustion
    (RetryExhausted) propagates and ends the run like any crash.
    """

    def persist() -> PersistResult:
        ensure_watermark_rows(site, target_scopes(site, normalized) | {None})
        return atomic_with_retry(
            lambda: persist_observations(site, batch, normalized, retention),
            label=f"persist {site.normalized_name}",
        )

    return await sync_to_async(persist)()


async def absence_local(
    site: SourceSite,
    *,
    swept_scope_key: str | None,
    eligible: bool,
    gated: DelistScope | None,
    event_time: datetime,
    null_scope_swept: bool = False,
) -> int:
    """Run the local delist step in its own transaction, after the persist step's commit.

    A crash between the two transactions leaves observations without absence,
    which fails toward keeping listings active; the next complete sweep delists.
    A multi-scope run calls this once per scope; a crash between those calls
    leaves the remaining scopes unapplied, which fails the same way.
    """

    def absence() -> int:
        ensure_watermark_rows(site, {swept_scope_key, None})
        return atomic_with_retry(
            lambda: apply_absence(
                site,
                swept_scope_key=swept_scope_key,
                eligible=eligible,
                gated=gated,
                event_time=event_time,
                null_scope_swept=null_scope_swept,
            ),
            label=f"delist {site.normalized_name}",
        )

    return await sync_to_async(absence)()


async def _evaluate_all(
    evaluator: ListingEvaluator, listing_ids: list[int], resolver_failed: set[int]
) -> int:
    """Evaluate each distinct listing of the run once; return how many raised.

    Runs AFTER the resolve stage, because product clauses read the listing's
    current resolution edge. A listing whose resolution raised in this run is
    skipped (MS2-D-20): its edge may not reflect this run's observation, so a
    verdict stamped now could bind a current-looking row to an input the
    resolver never assessed. Skipping it writes nothing, which leaves its old
    rows bound to the previous snapshot and therefore `pending`.

    Each listing is isolated: one failure is logged and counted, never raised,
    and the remaining listings are still evaluated (the evaluator writes
    nothing for a listing it raised on, so that listing fails closed too).
    """
    errors = 0
    # dict.fromkeys: listing_ids may repeat a pk (the case _grain_counts
    # guards); a repeat would be evaluated twice under the listing's row lock
    # for an identical result.
    for listing_id in dict.fromkeys(listing_ids):
        if listing_id in resolver_failed:
            continue
        try:
            await sync_to_async(evaluator.evaluate_listing)(listing_id)
        except Exception:  # evaluator failure never blocks ingestion (MS2-D-20)
            logger.exception("eligibility evaluation failed for listing %s", listing_id)
            errors += 1
    return errors


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
