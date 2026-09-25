"""Pilot acquisition evidence report (MS-2 plan F3, Task 6; risks R16, R18).

`build_report` reads recorded acquisition evidence for a time window and
returns a `PilotReport`; `render_text` and `PilotReport.to_json` are the two
output forms the `pilot_report` command prints. The report exists so the owner
can judge each source, provider, collection scope and category before enabling
it, so it never rounds evidence up: a figure with no recorded basis is shown as
"not recorded" (JSON `null`), and an `unknown` outcome is never counted as a
success or as complete.

Grouping: source -> provider -> collection scope -> category. Every row lands
in exactly one bucket; nothing is dropped:

- provider: `local` or `apify` (ProviderKind values) for runs. A ScraperRun
  linked to a ProviderRun is reported through the ProviderRun, never twice. For
  snapshots the provider is read from the stored raw payload's endpoint
  (`APIFY_RAW_ENDPOINT_PREFIX`); a snapshot whose raw payload is gone (expired
  or never stored) is bucketed under provider `not_recorded`.
- scope: the MS2-D-12 key. `null` is the "unscoped/legacy" bucket: the legacy
  NULL scope, plus every local run whose swept scopes are not recorded in its
  detail_json (all runs before F1, and every failed local run, whose failure
  path records no scopes). A remote run's scope is the scope it was admitted
  for (ProviderRun.scope_key), never the Actor's claim. A snapshot's scope is
  its listing's CURRENT collection_scope.
- category: the snapshot's collector-asserted hint (attrs_json category_hint),
  else the category the resolver's current edge recorded (evidence
  "category"), else `null` ("no category").

Outcomes (per run, or per scope sweep for a multi-scope local run):
`complete | truncated | partial_failure | failed | in_progress | not_recorded`.
Local: FAILED status -> failed; RUNNING -> in_progress; SUCCESS -> the per-scope
entry's completeness, else detail_json["provider"]["completeness"], else
not_recorded. Remote: import REJECTED -> failed; import not yet terminal ->
in_progress; FINALIZED -> ProviderRun.completeness. `completeness_rate` is
complete / runs. A run is *successful* when its data landed (local SUCCESS,
remote FINALIZED); freshness gaps and failure recovery are measured between
successful runs only.

Per-scope local outcomes (F1): the pipeline's per-scope record key was not
fixed when this module was written, so `_scope_entries` accepts any top-level
detail_json list whose items are all objects carrying a "scope_key". Each entry
may carry `completeness` (a RunCompleteness value) or a boolean `complete`, and
an integer `pages`. A run with such a list counts once in each scope it swept.

JSON schema (`schema` = "hw-radar/pilot-report/v1"; money is a decimal string,
durations are seconds, timestamps ISO-8601, rates 0..1 floats; `null` always
means "not recorded" unless stated otherwise; money figures of a non-metered
COST are null because no figure exists, not because one went unrecorded):

    {schema, generated_at, window: {since, until}, source_filter: [str] | null,
     sources: [{
       site_key, name,
       configured_provider: str | null,   # null: the source has no SourceConfig
       enabled: bool | null,
       heartbeat_runs: int,               # HEARTBEAT runs are not collection runs
       providers: [{
         provider,                        # "local" | "apify" | "not_recorded"
         parse: {runs, records_fetched, records_valid,
                 parse_dropped,                  # over runs with valid <= fetched
                 parse_drop_not_derivable_runs,  # valid > fetched (page items)
                 parse_skipped, resolver_errors, evaluator_errors},
         failures: {failed_runs, failure_classes: {str: int},  # distinct runs
                    reject_reasons: {str: int}, streaks, recovered,
                    unrecovered, recovery_p50_s, recovery_max_s},
         cost: COST,                      # rollup of the scopes' COST
         scopes: [{
           scope_key,                     # null: unscoped/legacy
           runs: {total, successful, full, probe,
                  outcomes: {complete, truncated, partial_failure, failed,
                             in_progress, not_recorded},
                  completeness_rate},     # null when total == 0
           freshness: {newest_observation_at, newest_observation_age_s,
                       last_successful_full_run_at, successful_full_runs,
                       full_run_gap_p50_s, full_run_gap_max_s},
           cost: COST,
           categories: [{
             category,                    # null: no category
             snapshots, listings,
             identifiers: {with_mpn_attr, with_model_id, with_identifier,
                           coverage},     # coverage = with_identifier / listings
             resolution: {grain: {none, family, model, variant, no_edge},
                          outcome: {accept, review, none, error, no_edge}},
             condition: {with_condition, coverage},
             shipping: {free, paid, unknown, known_coverage},
             watch_evaluations: {match, no_match, unknown, pending} | null}]}]}]}]}

    COST = {metered: bool | null, note: str,
            api_calls: int | null,              # local: pages recorded by sweeps
            settled_usd, monitoring_usd, outstanding_usd, ledger_debit_usd,
            provider_observed_usd: str | null,  # apify only
            runs_without_reservation, runs_without_usage, released_or_denied}

Local HTTP and official-API providers are not metered: their COST says
`metered: false` with NO_DIRECT_COST and carries no dollar figure at all. Apify
ledger figures follow acquisition.apify.report's attribution: a reconciled
reservation debits `actual_usd` plus `monitoring_bound_usd`, an open one its
`estimate_usd` plus `monitoring_bound_usd`, and a released or denied one
nothing.

SCOPE: read-only. Nothing here writes a row, takes a lock, or calls the
network; the watch-evaluation `pending` count reuses
eligibility.service.row_currency, the same read-time currency predicate the
shortlist uses (MS2-D-20), so the two cannot disagree. Aggregation happens in
Python over the window's rows, which is sized for pilot volumes, not for a
production-scale history.

Requirements: a Django context with the catalog app. No PostgreSQL-specific SQL.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Final, cast

from django.db.models import Max

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR
from hw_radar.catalog.models import (
    ListingResolution,
    OfferSnapshot,
    ProviderKind,
    ProviderRun,
    ResolutionGrain,
    RunCompleteness,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceConfig,
    SourceSite,
    WatchEvaluation,
)
from hw_radar.catalog.models.provider import ImportState, ReservationStatus
from hw_radar.eligibility.service import row_currency

SCHEMA: Final = "hw-radar/pilot-report/v1"
NOT_RECORDED: Final = "not recorded"
NO_DIRECT_COST: Final = "no direct provider cost (not metered)"
PROVIDER_NOT_RECORDED: Final = "not_recorded"
UNSCOPED_LABEL: Final = "unscoped/legacy"
NO_CATEGORY_LABEL: Final = "no category"
# Cross-file contract: acquisition.apify.provider._raw_item names every stored
# Actor dataset row "apify-dataset:<dataset id>/<index>". A local adapter's raw
# endpoint is the merchant URL it fetched, so this prefix is the only stored
# per-snapshot evidence of which provider collected an observation.
# tests/db/test_pilot_report.py pins the two together.
APIFY_RAW_ENDPOINT_PREFIX: Final = "apify-dataset:"

OUTCOMES: Final = (
    RunCompleteness.COMPLETE.value,
    RunCompleteness.TRUNCATED.value,
    RunCompleteness.PARTIAL_FAILURE.value,
    RunCompleteness.FAILED.value,
    "in_progress",
    "not_recorded",
)
_COMPLETENESS_VALUES: Final = frozenset(str(v) for v in RunCompleteness.values)
_OPEN_RESERVATION: Final = frozenset(
    {
        ReservationStatus.RESERVED.value,
        ReservationStatus.USAGE_PROVISIONAL.value,
        ReservationStatus.USAGE_FINALIZED.value,
    }
)
_GRAINS: Final = (*(str(v) for v in ResolutionGrain.values), "no_edge")
_EDGE_OUTCOMES: Final = ("accept", "review", "none", "error", "no_edge")
_VERDICTS: Final = ("match", "no_match", "unknown", "pending")


# ── Normalized run records ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Sweep:
    """One run's contribution to one (source, provider, scope) bucket."""

    run_id: str
    kind: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    successful: bool
    failure_class: str
    reject_reason: str
    pages: int | None
    reservation: _Reservation | None = None
    is_remote: bool = False
    usage_usd: Decimal | None = None


@dataclass(frozen=True)
class _Reservation:
    status: str
    actual_usd: Decimal | None
    estimate_usd: Decimal | None
    monitoring_bound_usd: Decimal | None


@dataclass(frozen=True)
class _RunCounters:
    """Run-level counters, attributed once per run to its (source, provider)."""

    records_fetched: int
    records_valid: int
    parse_skipped: int | None
    resolver_errors: int | None
    evaluator_errors: int | None


# ── Output model ─────────────────────────────────────────────────────────────


@dataclass
class CategoryReport:
    category: str | None
    snapshots: int = 0
    listings: int = 0
    with_mpn_attr: int = 0
    with_model_id: int = 0
    with_identifier: int = 0
    grain: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_GRAINS, 0))
    edge_outcome: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_EDGE_OUTCOMES, 0))
    with_condition: int = 0
    shipping_free: int = 0
    shipping_paid: int = 0
    shipping_unknown: int = 0
    # None: no watch evaluation exists for any listing of this bucket.
    verdicts: dict[str, int] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "category": self.category,
            "snapshots": self.snapshots,
            "listings": self.listings,
            "identifiers": {
                "with_mpn_attr": self.with_mpn_attr,
                "with_model_id": self.with_model_id,
                "with_identifier": self.with_identifier,
                "coverage": _rate(self.with_identifier, self.listings),
            },
            "resolution": {"grain": dict(self.grain), "outcome": dict(self.edge_outcome)},
            "condition": {
                "with_condition": self.with_condition,
                "coverage": _rate(self.with_condition, self.listings),
            },
            "shipping": {
                "free": self.shipping_free,
                "paid": self.shipping_paid,
                "unknown": self.shipping_unknown,
                "known_coverage": _rate(self.shipping_free + self.shipping_paid, self.snapshots),
            },
            "watch_evaluations": None if self.verdicts is None else dict(self.verdicts),
        }


@dataclass
class CostReport:
    metered: bool | None
    note: str
    api_calls: int | None = None
    settled_usd: Decimal = Decimal(0)
    monitoring_usd: Decimal = Decimal(0)
    outstanding_usd: Decimal = Decimal(0)
    provider_observed_usd: Decimal | None = None
    runs_without_reservation: int = 0
    runs_without_usage: int = 0
    released_or_denied: int = 0

    @property
    def ledger_debit_usd(self) -> Decimal:
        return self.settled_usd + self.monitoring_usd + self.outstanding_usd

    def add(self, other: CostReport) -> None:
        if other.api_calls is not None:
            self.api_calls = (self.api_calls or 0) + other.api_calls
        self.settled_usd += other.settled_usd
        self.monitoring_usd += other.monitoring_usd
        self.outstanding_usd += other.outstanding_usd
        if other.provider_observed_usd is not None:
            self.provider_observed_usd = (
                self.provider_observed_usd or Decimal(0)
            ) + other.provider_observed_usd
        self.runs_without_reservation += other.runs_without_reservation
        self.runs_without_usage += other.runs_without_usage
        self.released_or_denied += other.released_or_denied

    def to_json(self) -> dict[str, object]:
        # A non-metered provider carries no dollar keys' values at all: printing
        # "0" would read as a measured zero cost, which nobody measured.
        metered = self.metered is True
        return {
            "metered": self.metered,
            "note": self.note,
            "api_calls": self.api_calls,
            "settled_usd": _money(self.settled_usd) if metered else None,
            "monitoring_usd": _money(self.monitoring_usd) if metered else None,
            "outstanding_usd": _money(self.outstanding_usd) if metered else None,
            "ledger_debit_usd": _money(self.ledger_debit_usd) if metered else None,
            "provider_observed_usd": (
                _money(self.provider_observed_usd)
                if metered and self.provider_observed_usd is not None
                else None
            ),
            "runs_without_reservation": self.runs_without_reservation,
            "runs_without_usage": self.runs_without_usage,
            "released_or_denied": self.released_or_denied,
        }


@dataclass
class ScopeReport:
    scope_key: str | None
    now: datetime
    sweeps: list[_Sweep] = field(default_factory=list[_Sweep])
    newest_observation_at: datetime | None = None
    categories: dict[str | None, CategoryReport] = field(
        default_factory=dict[str | None, CategoryReport]
    )
    cost: CostReport = field(default_factory=lambda: CostReport(None, NOT_RECORDED))

    def outcomes(self) -> dict[str, int]:
        counts = dict.fromkeys(OUTCOMES, 0)
        for s in self.sweeps:
            counts[s.outcome] += 1
        return counts

    def runs_json(self) -> dict[str, object]:
        outcomes = self.outcomes()
        total = len(self.sweeps)
        return {
            "total": total,
            "successful": sum(1 for s in self.sweeps if s.successful),
            "full": sum(1 for s in self.sweeps if s.kind == RunKind.FULL.value),
            "probe": sum(1 for s in self.sweeps if s.kind == RunKind.PROBE.value),
            "outcomes": outcomes,
            "completeness_rate": _rate(outcomes[RunCompleteness.COMPLETE.value], total),
        }

    def freshness_json(self) -> dict[str, object]:
        full_ok = sorted(
            s.started_at for s in self.sweeps if s.successful and s.kind == RunKind.FULL.value
        )
        gaps = [(b - a).total_seconds() for a, b in pairwise(full_ok)]
        newest = self.newest_observation_at
        return {
            "newest_observation_at": _iso(newest),
            "newest_observation_age_s": (
                None if newest is None else (self.now - newest).total_seconds()
            ),
            "last_successful_full_run_at": _iso(full_ok[-1]) if full_ok else None,
            "successful_full_runs": len(full_ok),
            "full_run_gap_p50_s": _p50(gaps),
            "full_run_gap_max_s": max(gaps) if gaps else None,
        }

    def to_json(self) -> dict[str, object]:
        return {
            "scope_key": self.scope_key,
            "runs": self.runs_json(),
            "freshness": self.freshness_json(),
            "cost": self.cost.to_json(),
            "categories": [
                self.categories[k].to_json() for k in sorted(self.categories, key=_none_last)
            ],
        }


@dataclass
class ProviderReport:
    provider: str
    now: datetime
    scopes: dict[str | None, ScopeReport] = field(default_factory=dict[str | None, ScopeReport])
    counters: list[_RunCounters] = field(default_factory=list[_RunCounters])

    def scope(self, key: str | None) -> ScopeReport:
        if key not in self.scopes:
            self.scopes[key] = ScopeReport(key, self.now, cost=_base_cost(self.provider))
        return self.scopes[key]

    def cost(self) -> CostReport:
        total = _base_cost(self.provider)
        for s in self.scopes.values():
            total.add(s.cost)
        return total

    def parse_json(self) -> dict[str, object]:
        fetched = sum(c.records_fetched for c in self.counters)
        valid = sum(c.records_valid for c in self.counters)
        # records_fetched counts raw ITEMS, and a page-granular adapter's item
        # is a whole page holding many listings, so valid > fetched is normal
        # there and fetched - valid says nothing about drops. Such runs are
        # excluded from parse_dropped and counted instead; netting them in
        # would let one page run's surplus hide another run's real drops.
        derivable = [c for c in self.counters if c.records_valid <= c.records_fetched]
        return {
            "runs": len(self.counters),
            "records_fetched": fetched,
            "records_valid": valid,
            "parse_dropped": sum(c.records_fetched - c.records_valid for c in derivable),
            "parse_drop_not_derivable_runs": len(self.counters) - len(derivable),
            "parse_skipped": _recorded_sum(c.parse_skipped for c in self.counters),
            "resolver_errors": _recorded_sum(c.resolver_errors for c in self.counters),
            "evaluator_errors": _recorded_sum(c.evaluator_errors for c in self.counters),
        }

    def failures_json(self) -> dict[str, object]:
        return _failures_json(s for scope in self.scopes.values() for s in scope.sweeps)

    def to_json(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "parse": self.parse_json(),
            "failures": self.failures_json(),
            "cost": self.cost().to_json(),
            "scopes": [self.scopes[k].to_json() for k in sorted(self.scopes, key=_none_last)],
        }


@dataclass
class SourceReport:
    site_key: str
    name: str
    configured_provider: str | None
    enabled: bool | None
    now: datetime
    heartbeat_runs: int = 0
    providers: dict[str, ProviderReport] = field(default_factory=dict[str, ProviderReport])

    def provider(self, kind: str) -> ProviderReport:
        if kind not in self.providers:
            self.providers[kind] = ProviderReport(kind, self.now)
        return self.providers[kind]

    def to_json(self) -> dict[str, object]:
        return {
            "site_key": self.site_key,
            "name": self.name,
            "configured_provider": self.configured_provider,
            "enabled": self.enabled,
            "heartbeat_runs": self.heartbeat_runs,
            "providers": [self.providers[k].to_json() for k in sorted(self.providers)],
        }


@dataclass
class PilotReport:
    generated_at: datetime
    since: datetime
    source_filter: list[str] | None
    sources: list[SourceReport]

    def to_json(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "generated_at": _iso(self.generated_at),
            "window": {"since": _iso(self.since), "until": _iso(self.generated_at)},
            "source_filter": self.source_filter,
            "sources": [s.to_json() for s in self.sources],
        }


def _failures_json(sweeps: Iterable[_Sweep]) -> dict[str, object]:
    """Failure classes and recovery over DISTINCT runs, in start order.

    Per provider, not per scope: a failed local run records no swept scopes, so
    at scope grain its failure would sit in the unscoped bucket while the
    recovering run sits in its real scopes, and no recovery would ever be seen.
    A streak is a run of consecutive failures; its recovery time runs from the
    first failure's end to the next successful run's end.
    """
    distinct: dict[str, _Sweep] = {}
    for sweep in sweeps:
        distinct.setdefault(sweep.run_id, sweep)
    ordered = sorted(distinct.values(), key=lambda s: (s.started_at, s.run_id))
    classes: Counter[str] = Counter()
    rejects: Counter[str] = Counter()
    streak_start: datetime | None = None
    streaks = 0
    recoveries: list[float] = []
    for s in ordered:
        if s.outcome == RunCompleteness.FAILED.value:
            classes[s.failure_class or NOT_RECORDED] += 1
            if s.reject_reason:
                rejects[s.reject_reason] += 1
            if streak_start is None:
                streak_start = s.finished_at or s.started_at
                streaks += 1
        elif s.successful and streak_start is not None:
            recovered_at = s.finished_at or s.started_at
            recoveries.append((recovered_at - streak_start).total_seconds())
            streak_start = None
    return {
        "failed_runs": sum(classes.values()),
        "failure_classes": dict(sorted(classes.items())),
        "reject_reasons": dict(sorted(rejects.items())),
        "streaks": streaks,
        "recovered": len(recoveries),
        "unrecovered": streaks - len(recoveries),
        "recovery_p50_s": _p50(recoveries),
        "recovery_max_s": max(recoveries) if recoveries else None,
    }


# ── Small helpers ────────────────────────────────────────────────────────────


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def _p50(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _money(value: Decimal) -> str:
    return f"{value:.4f}"


def _none_last(key: str | None) -> tuple[bool, str]:
    return (key is None, key or "")


def _recorded_sum(values: Iterable[int | None]) -> int | None:
    """Sum the recorded values; None when no run recorded the counter at all."""
    recorded = [v for v in values if v is not None]
    return sum(recorded) if recorded else None


def _opt_int(value: object) -> int | None:
    # bool is an int subclass; a stray True must not read as one error.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _base_cost(provider: str) -> CostReport:
    if provider == ProviderKind.APIFY.value:
        return CostReport(True, "apify ledger debits + provider-observed usage")
    if provider == ProviderKind.LOCAL.value:
        return CostReport(False, NO_DIRECT_COST)
    return CostReport(None, NOT_RECORDED)


def _scope_entries(detail: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    """Return the run's per-scope outcome list, or None when none is recorded.

    Deliberately structural rather than keyed: F1 adds the list in parallel
    with this module and its key name was not fixed. Any top-level list whose
    items are all objects carrying "scope_key" qualifies; an empty list does
    not (a run that swept nothing records no scopes).
    """
    for value in detail.values():
        if not isinstance(value, list) or not value:
            continue
        items = cast("list[object]", value)
        if all(isinstance(i, dict) and "scope_key" in i for i in items):
            return [cast("Mapping[str, object]", i) for i in items]
    return None


def _entry_outcome(entry: Mapping[str, object]) -> str | None:
    completeness = entry.get("completeness")
    if isinstance(completeness, str) and completeness in _COMPLETENESS_VALUES:
        return completeness
    complete = entry.get("complete")
    if isinstance(complete, bool):
        return RunCompleteness.COMPLETE.value if complete else RunCompleteness.TRUNCATED.value
    return None


def _evidence_outcome(detail: Mapping[str, object]) -> str | None:
    evidence = detail.get("provider")
    if not isinstance(evidence, dict):
        return None
    completeness = cast("dict[str, object]", evidence).get("completeness")
    if isinstance(completeness, str) and completeness in _COMPLETENESS_VALUES:
        return completeness
    return None


def _evidence_provider(detail: Mapping[str, object]) -> str:
    evidence = detail.get("provider")
    if isinstance(evidence, dict):
        kind = cast("dict[str, object]", evidence).get("provider_kind")
        if isinstance(kind, str) and kind:
            return kind
    # No evidence (a failed run, or a run from before Slice A recorded it): the
    # run went through run_collection without a ProviderRun, which is the
    # local path by construction — remote runs always have a ProviderRun.
    return ProviderKind.LOCAL.value


# ── Collection ───────────────────────────────────────────────────────────────


def _local_runs(report: dict[str, SourceReport], since: datetime) -> None:
    runs = (
        ScraperRun.objects.filter(
            started_at__gte=since,
            source_site__normalized_name__in=list(report),
            provider_run__isnull=True,
        )
        .select_related("source_site")
        .order_by("started_at", "pk")
    )
    for run in runs:
        source = report[run.source_site.normalized_name]
        if run.run_kind == RunKind.HEARTBEAT.value:
            source.heartbeat_runs += 1
            continue
        if run.run_kind == RunKind.REFERENCE.value:
            continue
        detail: Mapping[str, object] = run.detail_json
        provider = source.provider(_evidence_provider(detail))
        provider.counters.append(
            _RunCounters(
                records_fetched=run.records_fetched,
                records_valid=run.records_valid,
                parse_skipped=_opt_int(detail.get("parse_skipped")),
                resolver_errors=_opt_int(detail.get("resolver_errors")),
                evaluator_errors=_opt_int(detail.get("evaluator_errors")),
            )
        )
        successful = run.status == RunStatus.SUCCESS.value
        entries = _scope_entries(detail) if successful else None
        targets: list[tuple[str | None, str | None, int | None]]
        if entries is None:
            targets = [(None, None, None)]
        else:
            targets = []
            for entry in entries:
                key = entry.get("scope_key")
                targets.append(
                    (
                        key if isinstance(key, str) else None,
                        _entry_outcome(entry),
                        _opt_int(entry.get("pages")),
                    )
                )
        for scope_key, entry_outcome, pages in targets:
            if run.status == RunStatus.FAILED.value:
                outcome = RunCompleteness.FAILED.value
            elif run.status == RunStatus.RUNNING.value:
                outcome = "in_progress"
            else:
                outcome = entry_outcome or _evidence_outcome(detail) or "not_recorded"
            scope = provider.scope(scope_key)
            scope.sweeps.append(
                _Sweep(
                    run_id=f"scraper_run:{run.pk}",
                    kind=run.run_kind,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    outcome=outcome,
                    successful=successful,
                    failure_class=run.failure_class,
                    reject_reason="",
                    pages=pages,
                )
            )
            if pages is not None and scope.cost.metered is False:
                scope.cost.api_calls = (scope.cost.api_calls or 0) + pages


def _remote_runs(report: dict[str, SourceReport], since: datetime) -> None:
    runs = (
        ProviderRun.objects.filter(
            admitted_at__gte=since, source_site__normalized_name__in=list(report)
        )
        .select_related("source_site", "scraper_run", "spend_reservation")
        .order_by("admitted_at", "pk")
    )
    for run in runs:
        source = report[run.source_site.normalized_name]
        provider = source.provider(run.provider_kind)
        linked = run.scraper_run
        if run.import_state == ImportState.REJECTED.value:
            outcome = RunCompleteness.FAILED.value
        elif run.import_state != ImportState.FINALIZED.value:
            outcome = "in_progress"
        elif run.completeness in _COMPLETENESS_VALUES:
            outcome = run.completeness
        else:
            outcome = "not_recorded"
        if linked is not None and linked.status != RunStatus.RUNNING.value:
            detail: Mapping[str, object] = linked.detail_json
            provider.counters.append(
                _RunCounters(
                    records_fetched=linked.records_fetched,
                    records_valid=linked.records_valid,
                    parse_skipped=_opt_int(detail.get("parse_skipped")),
                    resolver_errors=_opt_int(detail.get("resolver_errors")),
                    evaluator_errors=_opt_int(detail.get("evaluator_errors")),
                )
            )
        reservation = _reservation_of(run)
        reject = run.stage_detail.get("reject_reason")
        sweep = _Sweep(
            run_id=f"provider_run:{run.pk}",
            kind=run.run_kind,
            started_at=run.started_at or run.admitted_at,
            finished_at=run.finished_at,
            outcome=outcome,
            successful=run.import_state == ImportState.FINALIZED.value,
            failure_class=linked.failure_class if linked is not None else "",
            reject_reason=reject if isinstance(reject, str) else "",
            pages=None,
            reservation=reservation,
            is_remote=True,
            usage_usd=run.usage_total_usd,
        )
        scope = provider.scope(run.scope_key)
        scope.sweeps.append(sweep)
        _charge(scope.cost, sweep)


def _reservation_of(run: ProviderRun) -> _Reservation | None:
    # Reverse one-to-one: a run admitted before the ledger existed (or in a
    # test fixture) has no reservation, and the accessor raises for that.
    try:
        row = run.spend_reservation  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType] - django-types has no reverse one-to-one stubs
    except ProviderRun.spend_reservation.RelatedObjectDoesNotExist:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        return None
    return _Reservation(
        status=cast("str", row.status),  # pyright: ignore[reportUnknownMemberType]
        actual_usd=cast("Decimal | None", row.actual_usd),  # pyright: ignore[reportUnknownMemberType]
        estimate_usd=cast("Decimal | None", row.estimate_usd),  # pyright: ignore[reportUnknownMemberType]
        monitoring_bound_usd=cast("Decimal | None", row.monitoring_bound_usd),  # pyright: ignore[reportUnknownMemberType]
    )


def _charge(cost: CostReport, sweep: _Sweep) -> None:
    if sweep.usage_usd is None:
        cost.runs_without_usage += 1
    else:
        cost.provider_observed_usd = (cost.provider_observed_usd or Decimal(0)) + sweep.usage_usd
    row = sweep.reservation
    if row is None:
        cost.runs_without_reservation += 1
        return
    monitoring = row.monitoring_bound_usd or Decimal(0)
    if row.status == ReservationStatus.RECONCILED.value:
        cost.settled_usd += row.actual_usd or Decimal(0)
        cost.monitoring_usd += monitoring
    elif row.status in _OPEN_RESERVATION:
        # MS2-D-26: an unreconciled row counts at its full estimate.
        cost.outstanding_usd += (row.estimate_usd or Decimal(0)) + monitoring
    else:
        cost.released_or_denied += 1


def _snapshot_provider(endpoint: str | None) -> str:
    if endpoint is None:
        return PROVIDER_NOT_RECORDED
    if endpoint.startswith(APIFY_RAW_ENDPOINT_PREFIX):
        return ProviderKind.APIFY.value
    return ProviderKind.LOCAL.value


def _freshness(report: dict[str, SourceReport]) -> None:
    """Newest observation per (source, provider, scope), over ALL time.

    Not window-bounded on purpose: a scope that stopped producing observations
    before the window opened is exactly the stale one the owner must see, with
    its real age rather than "no observation".
    """
    # One maximum per (site, scope, raw endpoint): the provider is derived from
    # the endpoint, so the per-provider maximum is taken over these in Python.
    per_endpoint = (
        OfferSnapshot.objects.filter(listing__source_site__normalized_name__in=list(report))
        .values(
            "listing__source_site__normalized_name",
            "listing__collection_scope",
            "raw_payload__endpoint",
        )
        .annotate(newest=Max("observed_at"))
    )
    for row in per_endpoint:
        site_key = cast("str", row["listing__source_site__normalized_name"])
        scope_key = cast("str | None", row["listing__collection_scope"])
        endpoint = cast("str | None", row["raw_payload__endpoint"])
        newest = cast("datetime", row["newest"])
        provider = report[site_key].provider(_snapshot_provider(endpoint))
        scope = provider.scope(scope_key)
        if scope.newest_observation_at is None or newest > scope.newest_observation_at:
            scope.newest_observation_at = newest


def _listing_facts(report: dict[str, SourceReport], since: datetime) -> None:
    snapshots = list(
        OfferSnapshot.objects.filter(
            observed_at__gte=since, listing__source_site__normalized_name__in=list(report)
        )
        .select_related("listing__source_site", "raw_payload")
        .order_by("observed_at")
    )
    if not snapshots:
        return
    listing_ids = {cast("int", s.listing.pk) for s in snapshots}
    edges: dict[int, ListingResolution] = {
        cast("int", e.listing.pk): e
        for e in ListingResolution.objects.filter(
            listing_id__in=listing_ids, is_current=True
        ).select_related("listing")
    }
    verdicts = _verdicts(listing_ids)

    # Key: (site, provider, scope, listing). Snapshots are ordered by
    # observed_at, so the last write wins and holds the newest in-window
    # snapshot of that listing under that provider.
    newest: dict[tuple[str, str, str | None, int], OfferSnapshot] = {}
    counts: defaultdict[tuple[str, str, str | None, int], int] = defaultdict(int)
    shipping: defaultdict[tuple[str, str, str | None, int], Counter[str]] = defaultdict(Counter)
    for snap in snapshots:
        listing = snap.listing
        raw = snap.raw_payload
        key = (
            listing.source_site.normalized_name,
            _snapshot_provider(None if raw is None else raw.endpoint),
            listing.collection_scope,
            cast("int", listing.pk),
        )
        newest[key] = snap
        counts[key] += 1
        if snap.shipping_price is None:
            shipping[key]["unknown"] += 1
        elif snap.shipping_price == 0:
            shipping[key]["free"] += 1
        else:
            shipping[key]["paid"] += 1

    for key, snap in newest.items():
        site_key, provider_kind, scope_key, listing_id = key
        attrs: Mapping[str, object] = snap.attrs_json
        edge = edges.get(listing_id)
        category = _category(attrs, edge)
        scope = report[site_key].provider(provider_kind).scope(scope_key)
        cat = scope.categories.get(category)
        if cat is None:
            cat = scope.categories[category] = CategoryReport(category)
        cat.listings += 1
        cat.snapshots += counts[key]
        mpn = attrs.get("mpn")
        has_mpn = isinstance(mpn, str) and bool(mpn.strip())
        has_model = edge is not None and edge.grain in (
            ResolutionGrain.MODEL.value,
            ResolutionGrain.VARIANT.value,
        )
        cat.with_mpn_attr += has_mpn
        cat.with_model_id += has_model
        cat.with_identifier += has_mpn or has_model
        if edge is None:
            cat.grain["no_edge"] += 1
            cat.edge_outcome["no_edge"] += 1
        else:
            cat.grain[edge.grain] = cat.grain.get(edge.grain, 0) + 1
            outcome = _edge_outcome(edge)
            cat.edge_outcome[outcome] = cat.edge_outcome.get(outcome, 0) + 1
        cat.with_condition += bool(snap.listing.condition_label_raw.strip())
        ship = shipping[key]
        cat.shipping_free += ship["free"]
        cat.shipping_paid += ship["paid"]
        cat.shipping_unknown += ship["unknown"]
        listing_verdicts = verdicts.get(listing_id)
        if listing_verdicts is not None:
            if cat.verdicts is None:
                cat.verdicts = dict.fromkeys(_VERDICTS, 0)
            for verdict, n in listing_verdicts.items():
                cat.verdicts[verdict] += n


def _category(attrs: Mapping[str, object], edge: ListingResolution | None) -> str | None:
    hint = attrs.get(CATEGORY_HINT_ATTR)
    if isinstance(hint, str) and hint:
        return hint
    if edge is not None:
        recorded = edge.evidence.get("category")
        if isinstance(recorded, str) and recorded:
            return recorded
    return None


def _edge_outcome(edge: ListingResolution) -> str:
    # The resolver writes an "error" key on a matcher-crash edge and an
    # "outcome" of accept | review | none otherwise (matching.resolver).
    if "error" in edge.evidence:
        return "error"
    outcome = edge.evidence.get("outcome")
    if isinstance(outcome, str) and outcome in _EDGE_OUTCOMES:
        return outcome
    return "accept" if edge.grain != ResolutionGrain.NONE.value else "none"


def _verdicts(listing_ids: set[int]) -> dict[int, Counter[str]]:
    """Stored verdicts per listing, plus how many rows are no longer current.

    `pending` counts rows whose MS2-D-20 binding went stale (R16); such a row
    ALSO keeps its stored verdict in the match/no_match/unknown counts, which
    are the verdicts as stored, not as currently trusted.
    """
    evaluations = list(
        WatchEvaluation.objects.filter(listing_id__in=listing_ids)
        .select_related("watch__category", "listing__source_site")
        .order_by("pk")
    )
    out: dict[int, Counter[str]] = {}
    for state in row_currency(evaluations):
        e = state.evaluation
        counter = out.setdefault(cast("int", e.listing.pk), Counter())
        counter[e.verdict] += 1
        if not state.current:
            counter["pending"] += 1
    return out


# ── Entry point ──────────────────────────────────────────────────────────────


def build_report(
    *, now: datetime, since: datetime, sources: Sequence[str] | None = None
) -> PilotReport:
    """Collect the pilot evidence for [since, now] into a `PilotReport`.

    Sources: every SourceSite with a SourceConfig or any run in the window,
    narrowed to `sources` (site keys) when given. An unknown site key is not
    an error: it simply matches nothing, and the report says so by listing no
    source for it.
    """
    configs = {
        c.source_site.normalized_name: c for c in SourceConfig.objects.select_related("source_site")
    }
    active = set(
        ScraperRun.objects.filter(started_at__gte=since).values_list(
            "source_site__normalized_name", flat=True
        )
    ) | set(
        ProviderRun.objects.filter(admitted_at__gte=since).values_list(
            "source_site__normalized_name", flat=True
        )
    )
    keys = set(configs) | active
    if sources is not None:
        keys &= set(sources)
    sites = SourceSite.objects.filter(normalized_name__in=keys).order_by("normalized_name")
    report: dict[str, SourceReport] = {}
    for site in sites:
        config = configs.get(site.normalized_name)
        report[site.normalized_name] = SourceReport(
            site_key=site.normalized_name,
            name=site.name,
            configured_provider=None if config is None else config.collection_provider,
            enabled=None if config is None else config.enabled,
            now=now,
        )
    if report:
        _local_runs(report, since)
        _remote_runs(report, since)
        _listing_facts(report, since)
        _freshness(report)
    return PilotReport(
        generated_at=now,
        since=since,
        source_filter=None if sources is None else sorted(sources),
        sources=list(report.values()),
    )


# ── Text rendering ───────────────────────────────────────────────────────────


def _txt(value: object, unit: str = "") -> str:
    if value is None:
        return NOT_RECORDED
    if isinstance(value, float) and unit == "%":
        return f"{value * 100:.1f}%"
    if isinstance(value, float) and unit == "s":
        return _duration(value)
    return f"{value}{unit}"


def _duration(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.1f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _kv(counts: Mapping[str, int]) -> str:
    return " ".join(f"{k}={v}" for k, v in counts.items()) or "none"


def _render_cost(cost: CostReport) -> str:
    if cost.metered is False:
        calls = "not recorded" if cost.api_calls is None else str(cost.api_calls)
        return f"{NO_DIRECT_COST}; api calls (pages): {calls}"
    if cost.metered is None:
        return f"cost {NOT_RECORDED}"
    observed = (
        NOT_RECORDED
        if cost.provider_observed_usd is None
        else f"${_money(cost.provider_observed_usd)}"
    )
    return (
        f"ledger ${_money(cost.ledger_debit_usd)} (settled ${_money(cost.settled_usd)}, "
        f"monitoring ${_money(cost.monitoring_usd)}, outstanding "
        f"${_money(cost.outstanding_usd)}); provider-observed {observed}; "
        f"runs w/o reservation {cost.runs_without_reservation}, "
        f"w/o usage {cost.runs_without_usage}, released/denied {cost.released_or_denied}"
    )


def _render_failures(fail: Mapping[str, object]) -> list[str]:
    classes = cast("dict[str, int]", fail["failure_classes"])
    if not classes:
        return ["    failures    none"]
    rejects = cast("dict[str, int]", fail["reject_reasons"])
    # With no recovered streak the recovery figures are absent because no
    # success followed, not because anything went unrecorded.
    timing = (
        "no success since"
        if fail["recovered"] == 0
        else f"p50 {_txt(fail['recovery_p50_s'], 's')}, max {_txt(fail['recovery_max_s'], 's')}"
    )
    return [
        f"    failures    {_kv(classes)}"
        + (f"; rejected: {_kv(rejects)}" if rejects else "")
        + f"; recovered {fail['recovered']}/{fail['streaks']}, {timing}"
    ]


def _render_scope(scope: ScopeReport) -> list[str]:
    label = scope.scope_key or UNSCOPED_LABEL
    runs = scope.runs_json()
    fresh = scope.freshness_json()
    lines = [f"    scope {label}"]
    if runs["total"] == 0:
        lines.append("      runs        none in window")
    else:
        lines.append(
            f"      runs        {runs['total']} (full {runs['full']}, probe {runs['probe']}, "
            f"successful {runs['successful']}); complete "
            f"{_txt(runs['completeness_rate'], '%')}"
        )
        lines.append(f"      outcomes    {_kv(cast('dict[str, int]', runs['outcomes']))}")
    age = fresh["newest_observation_age_s"]
    lines.append(
        "      freshness   newest obs "
        + ("none" if age is None else f"{_duration(cast('float', age))} ago")
        + "; full-run gap "
        + (
            "no successful run"
            if fresh["successful_full_runs"] == 0
            else (
                "one successful run"
                if fresh["successful_full_runs"] == 1
                else f"p50 {_txt(fresh['full_run_gap_p50_s'], 's')}, "
                f"max {_txt(fresh['full_run_gap_max_s'], 's')}"
            )
        )
    )
    lines.append(f"      cost        {_render_cost(scope.cost)}")
    for key in sorted(scope.categories, key=_none_last):
        lines.extend(_render_category(scope.categories[key]))
    return lines


def _render_category(cat: CategoryReport) -> list[str]:
    data = cat.to_json()
    ids = cast("dict[str, object]", data["identifiers"])
    cond = cast("dict[str, object]", data["condition"])
    ship = cast("dict[str, object]", data["shipping"])
    verdicts = "none" if cat.verdicts is None else _kv(cat.verdicts)
    return [
        f"      category {cat.category or NO_CATEGORY_LABEL}: "
        f"{cat.listings} listings, {cat.snapshots} snapshots",
        f"        identifier  {_txt(ids['coverage'], '%')} (mpn attr {cat.with_mpn_attr}, "
        f"model id {cat.with_model_id}); grain {_kv(cat.grain)}",
        f"        resolution  {_kv(cat.edge_outcome)}",
        f"        condition   {_txt(cond['coverage'], '%')}; shipping known "
        f"{_txt(ship['known_coverage'], '%')} (free {cat.shipping_free}, "
        f"paid {cat.shipping_paid}, unknown {cat.shipping_unknown})",
        f"        watch evals {verdicts}",
    ]


def render_text(report: PilotReport) -> str:
    lines = [
        f"pilot report {_iso(report.generated_at)}",
        f"window {_iso(report.since)} .. {_iso(report.generated_at)}",
    ]
    if report.source_filter is not None:
        lines.append(f"sources filter: {', '.join(report.source_filter)}")
    if not report.sources:
        lines.append("no sources: no configured source and no run in the window")
        return "\n".join(lines) + "\n"
    for source in report.sources:
        configured = (
            "no source config"
            if source.configured_provider is None
            else f"configured provider {source.configured_provider}, "
            f"{'enabled' if source.enabled else 'disabled'}"
        )
        lines.append("")
        lines.append(f"source {source.site_key} ({source.name}) — {configured}")
        lines.append(f"  heartbeat runs {source.heartbeat_runs}")
        if not source.providers:
            lines.append("  no evidence in window")
            continue
        for kind in sorted(source.providers):
            provider = source.providers[kind]
            parse = provider.parse_json()
            lines.append(f"  provider {kind}")
            lines.append(
                f"    parse       runs {parse['runs']}, fetched {parse['records_fetched']}, "
                f"valid {parse['records_valid']}, dropped {parse['parse_dropped']} "
                f"(not derivable for {parse['parse_drop_not_derivable_runs']} runs), "
                f"skipped {_txt(parse['parse_skipped'])}; resolver errors "
                f"{_txt(parse['resolver_errors'])}, evaluator errors "
                f"{_txt(parse['evaluator_errors'])}"
            )
            lines.extend(_render_failures(provider.failures_json()))
            lines.append(f"    cost        {_render_cost(provider.cost())}")
            for key in sorted(provider.scopes, key=_none_last):
                lines.extend(_render_scope(provider.scopes[key]))
    return "\n".join(lines) + "\n"
