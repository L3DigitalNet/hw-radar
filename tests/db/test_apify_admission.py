"""MS-2 Slice E5: the start job admitted by the real spend ledger, and budget_paused.

jobs.LedgerAdmission is bound with the ledger_support settings (round test
prices, never Apify's live ones) and the configured live-anchor cycle around
the real now, because the start job stamps provider rows with real time.
Apify is served by test_apify_recovery_probe's LedgerFakeApify over
httpx.MockTransport, so no test touches the network. Also here: the E4
residuals folded into E5, namely the KV byte-cap trip (r1) and the stale
selector-4 marker left to poller start (r2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

import httpx
import ledger_support
import pytest
from django.test import override_settings
from django.utils import timezone
from test_apify_recovery_probe import (
    LIVE,
    SITE,
    AllowAllAdmission,
    LedgerFakeApify,
    _fixture,  # pyright: ignore[reportPrivateUsage]
    _live_cycle,  # pyright: ignore[reportPrivateUsage]
    _specs,  # pyright: ignore[reportPrivateUsage]
)

from hw_radar.acquisition import persist
from hw_radar.acquisition.apify import jobs
from hw_radar.acquisition.apify.budget import DenialReason
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.jobs import (
    LedgerAdmission,
    StartResult,
    StartStatus,
    TickReport,
    apify_poll_tick,
    start_provider_run,
)
from hw_radar.acquisition.apify.ledger import LatchReason, trip_latch
from hw_radar.acquisition.apify.reconcile import begin_monitoring_read
from hw_radar.acquisition.contracts import NormalizedListing, NullResolver
from hw_radar.catalog.models import (
    ApifyBudgetLatch,
    ApifySpendReservation,
    Category,
    Listing,
    ProviderKind,
    ProviderRun,
    RetentionClass,
    RunStatus,
    SchedulingLane,
    ScraperRun,
    SourceConfig,
    SourceSite,
    SourceTier,
    Watch,
)
from hw_radar.catalog.models.provider import AdmissionClass, ImportState, ReservationStatus
from hw_radar.eligibility.evaluate import evaluate_listing
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.eligibility.shortlist import OVERRUN_LATCH_REASON, Freshness, shortlist
from hw_radar.poller import service

# transaction=True: the jobs write from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded rows (categories, sites).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

WATCH_KEY: Final = "syn-watched"


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture
def actor_source() -> SourceConfig:
    site = SourceSite.objects.create(name="Synthetic", normalized_name=SITE)
    config = SourceConfig.objects.create(
        source_site=site,
        tier=SourceTier.T2_SPECIALIST,
        domain="synthetic.invalid",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        enabled=True,
        collection_provider=ProviderKind.APIFY,
    )
    config.lane_state(SchedulingLane.FULL)
    return SourceConfig.objects.select_related("source_site").get(pk=config.pk)


def _start(config: SourceConfig, fake: LedgerFakeApify) -> StartResult:
    return _run(
        start_provider_run(
            config,
            admission=LedgerAdmission(config=ledger_support.live_config()),
            run_specs=_specs(),
            client_factory=fake.client,
        )
    )


def _tick(fake: LedgerFakeApify) -> TickReport:
    return _run(
        apify_poll_tick(
            resolver=NullResolver(), client_factory=fake.client, ledger_config=ledger_support.CONFIG
        )
    )


def _exhaust_watch_refresh(at: Any) -> ApifySpendReservation:
    """An open row leaving $0.0001 of the watch_refresh class: no run fits."""
    room = ledger_support.ALLOCATION - Decimal("0.0001")
    return ledger_support.open_row(room, at)


def _watched_listing(site: SourceSite) -> tuple[Watch, Listing]:
    """A drive watch with no product constraint and one matching listing on `site`.

    The listing has no collection scope, so the complete sweep of the
    fixture's scope can never delist it.
    """
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=WATCH_KEY,
        canonical_url=f"https://synthetic.invalid/{WATCH_KEY}",
        url_hash=WATCH_KEY,
        title_raw="Drive watched",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=WATCH_KEY,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("100.00"),
            shipping_price=Decimal(0),
            stock_status="in_stock",
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
        ),
        observed_at=timezone.now(),
    )
    watch = Watch.objects.create(name="drive watch", category=Category.objects.get(slug="drive"))
    save_requirement(watch, DriveRequirementSpec())
    evaluate_listing(listing.pk)
    return watch, listing


# ── E5: a denied start ───────────────────────────────────────────────────────


@LIVE
def test_denied_start_records_denial_and_starts_nothing(actor_source: SourceConfig) -> None:
    cycle = _live_cycle()
    _exhaust_watch_refresh(cycle.cycle_start)
    fake = LedgerFakeApify(_fixture("complete"))

    result = _start(actor_source, fake)

    assert (result.status, result.reason) == (StartStatus.DENIED, "class_cap")
    assert fake.requests == []  # no start request, and no account read
    assert not ProviderRun.objects.exists()
    denial = ApifySpendReservation.objects.get(status=ReservationStatus.DENIED)
    assert (denial.admission_class, denial.denial_reason, denial.source_site) == (
        AdmissionClass.WATCH_REFRESH,
        "class_cap",
        actor_source.source_site,
    )
    assert denial.provider_run is None


@LIVE
def test_denied_start_shows_budget_paused_with_reason_in_shortlist(
    actor_source: SourceConfig,
) -> None:
    cycle = _live_cycle()
    filler = _exhaust_watch_refresh(cycle.cycle_start)
    fake = LedgerFakeApify(_fixture("complete"))
    watch, listing = _watched_listing(actor_source.source_site)
    [before] = shortlist(watch.pk)
    assert (before.freshness, before.budget_paused_reason) == (Freshness.FRESH, None)

    assert _start(actor_source, fake).status is StartStatus.DENIED

    [paused] = shortlist(watch.pk)
    assert paused.listing_id == listing.pk
    assert (paused.freshness, paused.budget_paused_reason) == (
        Freshness.BUDGET_PAUSED,
        "class_cap",
    )

    # A later successful import clears it. Free the class, then start,
    # import, and read the shortlist again.
    ApifySpendReservation.objects.filter(pk=filler.pk).update(status=ReservationStatus.RELEASED)
    started = _start(actor_source, fake)
    assert started.status is StartStatus.STARTED
    # The admitted reservation carries the run: selector 2 settles only such rows.
    run = ProviderRun.objects.get(pk=started.provider_run_id)
    resv = ApifySpendReservation.objects.get(provider_run=run)
    assert (resv.status, resv.source_site) == (
        ReservationStatus.RESERVED,
        actor_source.source_site,
    )
    _tick(fake)
    run.refresh_from_db()
    assert run.import_state == ImportState.FINALIZED

    [cleared] = shortlist(watch.pk)
    assert (cleared.freshness, cleared.budget_paused_reason) == (Freshness.FRESH, None)


def test_successful_import_after_denial_clears_budget_paused(
    actor_source: SourceConfig,
) -> None:
    # The import-clears half of the rule on its own: no admitted reservation
    # follows the denial, only a run (admitted earlier) whose import succeeds later.
    watch, _listing = _watched_listing(actor_source.source_site)
    now = timezone.now()
    ApifySpendReservation.objects.create(
        admission_class=AdmissionClass.WATCH_REFRESH,
        source_site=actor_source.source_site,
        status=ReservationStatus.DENIED,
        denial_reason="account_headroom",
        reserved_at=now - timedelta(minutes=5),
    )
    assert shortlist(watch.pk)[0].budget_paused_reason == "account_headroom"
    ScraperRun.objects.create(
        source_site=actor_source.source_site,
        started_at=now - timedelta(hours=1),
        finished_at=now,
        status=RunStatus.SUCCESS,
    )
    assert shortlist(watch.pk)[0].freshness is Freshness.FRESH


def test_open_latch_pauses_only_actor_sources(actor_source: SourceConfig) -> None:
    watch, _listing = _watched_listing(actor_source.source_site)
    local = SourceSite.objects.get(normalized_name="demo")
    local_listing = Listing.objects.create(
        source_site=local,
        source_listing_key="demo-watched",
        canonical_url="https://demo.invalid/w",
        url_hash="demo-watched",
        title_raw="Drive local",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    persist.append_snapshot(
        local_listing,
        NormalizedListing(
            source_listing_key="demo-watched",
            url=local_listing.canonical_url,
            title="Drive local",
            price=Decimal("90.00"),
            shipping_price=Decimal(0),
            stock_status="in_stock",
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
        ),
        observed_at=timezone.now(),
    )
    evaluate_listing(local_listing.pk)

    trip_latch(LatchReason.OVERRUN, config=ledger_support.CONFIG)

    by_source = {r.source: r for r in shortlist(watch.pk)}
    assert (by_source[SITE].freshness, by_source[SITE].budget_paused_reason) == (
        Freshness.BUDGET_PAUSED,
        "overrun_latch",
    )
    # The latch pauses Apify admission, not a local adapter's collection.
    assert by_source["demo"].freshness is Freshness.FRESH
    # Cross-package contract: the shortlist's literal is the ledger's reason.
    assert OVERRUN_LATCH_REASON == DenialReason.OVERRUN_LATCH


# ── E9.3: a start refused at the account's hard limit (MS2-D-48) ────────────

# Apify's documented example body for a 402 on POST /v2/acts/{id}/runs; the
# classification must rest on the status code, never on this type string.
PAYMENT_REQUIRED: Final = {
    "error": {"type": "x402-payment-required", "message": "usage limit exceeded"}
}


class RefusingApify(LedgerFakeApify):
    """LedgerFakeApify whose run start answers `status` (or loses the response)."""

    def __init__(self, status: int = 402, *, lose_response: bool = False) -> None:
        super().__init__(_fixture("complete"))
        self.status = status
        self.lose_response = lose_response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/runs"):
            self.requests.append(request)
            if self.lose_response:
                raise httpx.ReadTimeout("response lost", request=request)
            body = PAYMENT_REQUIRED if self.status == 402 else {"error": {"type": "internal"}}
            return httpx.Response(self.status, json=body)
        return super().__call__(request)


def _refusal_trips() -> list[tuple[str, int | None, bool]]:
    return [
        (reason, run_id, cleared is None)
        for reason, run_id, cleared in ApifyBudgetLatch.objects.filter(
            reason=LatchReason.ACCOUNT_LIMIT_REFUSED
        ).values_list("reason", "provider_run_id", "cleared_at")
    ]


@LIVE
def test_start_refused_with_402_trips_account_limit_latch_and_keeps_reservation(
    actor_source: SourceConfig,
) -> None:
    _live_cycle()
    fake = RefusingApify(402)

    result = _start(actor_source, fake)

    assert (result.status, result.reason) == (StartStatus.START_FAILED, "account_limit_refused")
    row = ProviderRun.objects.get(pk=result.provider_run_id)
    error = row.stage_detail["start_error"]
    assert error == {
        "type": "ApifyApiError",
        "status_code": 402,
        "error_type": "x402-payment-required",
    }
    assert _refusal_trips() == [("account_limit_refused", row.pk, True)]
    resv = ApifySpendReservation.objects.get(provider_run=row)
    estimate = resv.estimate_usd
    assert resv.status == ReservationStatus.RESERVED  # neither released nor reconciled

    # A tick before the deadline leaves the unstarted row alone ...
    _run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=fake.client,
            ledger_config=ledger_support.live_config(),
        )
    )
    row.refresh_from_db()
    resv.refresh_from_db()
    assert (row.external_run_id, row.orphaned_start_at) == (None, None)
    assert (resv.status, resv.estimate_usd) == (ReservationStatus.RESERVED, estimate)
    # ... and at its deadline selector 3 handles it as orphaned_start, unchanged.
    _run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=fake.client,
            ledger_config=ledger_support.live_config(),
            clock=ledger_support.Clock(row.storage_cleanup_due_at + timedelta(minutes=1)),
        )
    )
    row.refresh_from_db()
    assert row.orphaned_start_at is not None
    assert _refusal_trips() == [("account_limit_refused", row.pk, True)]


@LIVE
@pytest.mark.parametrize(
    ("fake_kwargs", "reason"),
    [({"status": 500}, "ApifyApiError"), ({"lose_response": True}, "ReadTimeout")],
    ids=["http-500", "transport-error"],
)
def test_start_error_other_than_402_does_not_trip_account_limit_latch(
    actor_source: SourceConfig, fake_kwargs: dict[str, Any], reason: str
) -> None:
    _live_cycle()

    result = _start(actor_source, RefusingApify(**fake_kwargs))

    assert (result.status, result.reason) == (StartStatus.START_FAILED, reason)
    assert _refusal_trips() == []
    assert ledger_support.open_latches() == []


# ── r1: the KV byte cap (MS2-D-32) ──────────────────────────────────────────


@LIVE
def test_output_record_over_kv_byte_cap_trips_latch(actor_source: SourceConfig) -> None:
    _live_cycle()
    fake = LedgerFakeApify(_fixture("complete"))
    started = _run(
        start_provider_run(
            actor_source,
            admission=AllowAllAdmission(),
            run_specs=_specs(),
            client_factory=fake.client,
        )
    )
    # The fixture's OUTPUT is a few hundred bytes: a 64-byte cap makes it over.
    with override_settings(HW_RADAR_APIFY_MAX_KV_BYTES=64):
        _tick(fake)

    trips = list(ApifyBudgetLatch.objects.values_list("reason", "provider_run_id"))
    assert trips == [(LatchReason.KV_STORE_OVER_CAP, started.provider_run_id)]


@LIVE
def test_output_record_within_kv_byte_cap_trips_nothing(actor_source: SourceConfig) -> None:
    _live_cycle()
    fake = LedgerFakeApify(_fixture("complete"))
    _run(
        start_provider_run(
            actor_source,
            admission=AllowAllAdmission(),
            run_specs=_specs(),
            client_factory=fake.client,
        )
    )
    with override_settings(HW_RADAR_APIFY_MAX_KV_BYTES=65536):
        _tick(fake)

    assert ledger_support.open_latches() == []


# ── r2: stale selector-4 markers resolve at poller start, not per tick ─────


def _no_client() -> ApifyClient:
    raise AssertionError("an idle tick must build no client")


def test_tick_leaves_stale_marker_for_poller_start(monkeypatch: pytest.MonkeyPatch) -> None:
    run, row = ledger_support.settled_run()
    ApifySpendReservation.objects.filter(pk=row.pk).update(
        status=ReservationStatus.RECONCILED,
        actual_usd=Decimal("0.05"),
        settled_run_usage_usd=Decimal("0.05"),
        reconciled_at=ledger_support.NOW,
        last_charge_at=ledger_support.NOW,
        correction_monitor_until=ledger_support.NOW + 7 * ledger_support.DAY,
        next_usage_read_at=ledger_support.NOW,
    )
    ledger_support.cycle()
    assert begin_monitoring_read(row.pk, ledger_support.NOW, ledger_support.CONFIG) is not None
    restart = ledger_support.NOW + ledger_support.HOUR
    monkeypatch.setattr(jobs, "PROCESS_STARTED_AT", restart)

    # A tick with nothing else to do touches no marker (and needs no client).
    _run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=_no_client,
            ledger_config=ledger_support.CONFIG,
            clock=ledger_support.Clock(ledger_support.NOW + 2 * ledger_support.HOUR),
        )
    )
    row.refresh_from_db()
    assert row.monitoring_call_pending_since == ledger_support.NOW

    assert service.resolve_stale_ledger_markers() == 1
    row.refresh_from_db()
    assert (row.monitoring_call_pending_since, row.monitoring_charge_last_at) == (None, restart)
    assert row.provider_run == run
