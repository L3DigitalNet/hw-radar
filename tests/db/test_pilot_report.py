"""Plan F3: `pilot_report` over a small, hand-built pilot history.

The fixture is shaped to put one row in every bucket the report must never
drop: a local source with complete, truncated and failed runs across two
scopes (GPU, RAM) plus the unscoped/legacy bucket; an Apify source with a
finalized run (reconciled reservation), a pending run (open reservation) and a
rejected run (no reservation); and listings with and without an MPN, a
condition and a known shipping price. Expected figures are derived from the
seeded values below, never from the report's own arithmetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any, cast

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hw_radar.acquisition.apify.provider import (
    _raw_item,  # pyright: ignore[reportPrivateUsage] - pins the endpoint-prefix contract
)
from hw_radar.acquisition.contracts import SCOPE_OUTCOMES_KEY
from hw_radar.acquisition.pilot_report import (
    APIFY_RAW_ENDPOINT_PREFIX,
    NO_DIRECT_COST,
    NOT_RECORDED,
    SCHEMA,
    build_report,
    render_text,
)
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifySpendReservation,
    Category,
    EligibilityVerdict,
    Listing,
    ListingResolution,
    Manufacturer,
    OfferSnapshot,
    ProductModel,
    ProviderKind,
    ProviderRun,
    RawPayload,
    ReservationStatus,
    RetentionClass,
    RunCompleteness,
    RunFailureClass,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceConfig,
    SourceLaneState,
    SourceSite,
    SourceTier,
    SourceType,
    Watch,
    WatchEvaluation,
)
from hw_radar.catalog.models.provider import ImportState

pytestmark = pytest.mark.django_db

D = Decimal
HOUR = timedelta(hours=1)
LOCAL = "pilotlocal"
APIFY = "pilotapify"
GPU = f"{LOCAL}:gpu:q1"
RAM = f"{LOCAL}:ram:q1"
AGPU = f"{APIFY}:gpu:q1"
# The pipeline's own key, so the report reads exactly what F1 writes.
SCOPES_KEY = SCOPE_OUTCOMES_KEY


def _site(key: str, provider: ProviderKind) -> SourceSite:
    site = SourceSite.objects.create(
        name=key.title(), normalized_name=key, source_type=SourceType.OTHER
    )
    SourceConfig.objects.create(
        source_site=site,
        tier=SourceTier.T2_SPECIALIST,
        domain=f"{key}.invalid",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        collection_provider=provider,
    )
    return site


def _run(
    site: SourceSite,
    started: datetime,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    kind: RunKind = RunKind.FULL,
    **fields: Any,
) -> ScraperRun:
    return ScraperRun.objects.create(
        source_site=site,
        run_kind=kind,
        started_at=started,
        finished_at=started + timedelta(minutes=1),
        status=status,
        **fields,
    )


def _evidence(completeness: RunCompleteness) -> dict[str, object]:
    return {
        "evidence_version": 1,
        "provider_kind": "local",
        "provider_key": "pilotlocal",
        "completeness": completeness.value,
        "completeness_reason": "adapter_sweep_complete",
        "stale_absence_eligible": True,
    }


def _listing(site: SourceSite, key: str, scope: str | None, *, condition: str = "") -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://{site.normalized_name}.invalid/{key}",
        url_hash=f"h-{key}",
        title_raw=f"item {key}",
        condition_label_raw=condition,
        collection_scope=scope,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _snap(
    listing: Listing,
    at: datetime,
    *,
    endpoint: str | None,
    shipping: Decimal | None,
    attrs: dict[str, object] | None = None,
) -> OfferSnapshot:
    raw = None
    if endpoint is not None:
        raw = RawPayload.objects.create(
            provider="acquisition",
            endpoint=endpoint,
            fetched_at=at,
            content_hash="0" * 64,
            http_status=200,
            retention_class=RetentionClass.MERCHANT_FACT,
        )
    return OfferSnapshot.objects.create(
        listing=listing,
        observed_at=at,
        item_price=D("100.00"),
        shipping_price=shipping,
        fx_rate=D("1"),
        attrs_json=attrs or {},
        raw_payload=raw,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _provider_run(site: SourceSite, admitted: datetime, **fields: Any) -> ProviderRun:
    return ProviderRun.objects.create(
        source_site=site,
        provider_kind=ProviderKind.APIFY,
        scope_key=AGPU,
        memory_mb=256,
        timeout_s=300,
        max_items=100,
        max_pages=5,
        admission_class=AdmissionClass.WATCH_REFRESH,
        run_kind=RunKind.FULL,
        admitted_at=admitted,
        started_at=admitted,
        finished_at=admitted + timedelta(minutes=2),
        storage_cleanup_due_at=admitted + timedelta(days=1),
        **fields,
    )


def _reservation(
    run: ProviderRun, status: ReservationStatus, estimate: Decimal, **fields: Any
) -> ApifySpendReservation:
    return ApifySpendReservation.objects.create(
        provider_run=run,
        source_site=run.source_site,
        admission_class=AdmissionClass.WATCH_REFRESH,
        status=status,
        estimate_usd=estimate,
        execution_bound_usd=estimate,
        post_run_liability_usd=D(0),
        monitoring_bound_usd=D("0.0022"),
        estimator_version="1",
        reserved_at=run.admitted_at,
        **fields,
    )


@dataclass(frozen=True)
class Pilot:
    now: datetime


@pytest.fixture
def pilot() -> Pilot:
    now = timezone.now()
    local = _site(LOCAL, ProviderKind.LOCAL)
    remote = _site(APIFY, ProviderKind.APIFY)

    # ── local runs ──
    _run(
        local,
        now - 5 * HOUR,
        records_fetched=10,
        records_valid=8,
        detail_json={
            "resolver_errors": 1,
            "evaluator_errors": 0,
            "provider": _evidence(RunCompleteness.TRUNCATED),
            SCOPES_KEY: [
                {"scope_key": GPU, "complete": True, "pages": 2},
                {"scope_key": RAM, "complete": False, "pages": 5},
            ],
        },
    )
    _run(
        local,
        now - 4 * HOUR,
        status=RunStatus.FAILED,
        failure_class=RunFailureClass.TRANSIENT,
        error="timeout",
    )
    _run(
        local,
        now - 3 * HOUR,
        records_fetched=4,
        records_valid=4,
        detail_json={
            "resolver_errors": 0,
            "evaluator_errors": 2,
            "provider": _evidence(RunCompleteness.COMPLETE),
            SCOPES_KEY: [
                {"scope_key": GPU, "completeness": "complete", "pages": 1},
                {"scope_key": RAM, "complete": True, "pages": 1},
            ],
        },
    )
    # A legacy scope-less run: no per-scope list, evidence only. One fetched
    # item (a page) held three listings, so its drop count is not derivable.
    _run(
        local,
        now - 2 * HOUR,
        records_fetched=1,
        records_valid=3,
        detail_json={"provider": _evidence(RunCompleteness.COMPLETE)},
    )
    _run(local, now - 90 * timedelta(minutes=1), kind=RunKind.HEARTBEAT)
    _run(local, now - 10 * timedelta(days=1), status=RunStatus.FAILED)  # outside the window

    # ── remote runs ──
    finalized_linked = _run(
        remote,
        now - 6 * HOUR,
        records_fetched=3,
        records_valid=3,
        detail_json={"resolver_errors": 0, "evaluator_errors": 0},
    )
    p1 = _provider_run(
        remote,
        now - 6 * HOUR,
        import_state=ImportState.FINALIZED,
        completeness=RunCompleteness.COMPLETE,
        usage_total_usd=D("0.0002"),
        scraper_run=finalized_linked,
        external_run_id="r1",
        import_idempotency_key="apify:r1",
    )
    _reservation(
        p1,
        ReservationStatus.RECONCILED,
        D("0.0348"),
        actual_usd=D("0.0278"),
        reconciled_at=now - 5 * HOUR,
        last_charge_at=now - 5 * HOUR,
    )
    p2 = _provider_run(remote, now - 1 * HOUR, import_state=ImportState.PENDING)
    _reservation(p2, ReservationStatus.RESERVED, D("0.0348"))
    rejected_linked = _run(
        remote,
        now - 3 * HOUR,
        status=RunStatus.FAILED,
        failure_class=RunFailureClass.PARSER_ROT,
    )
    _provider_run(
        remote,
        now - 3 * HOUR,
        import_state=ImportState.REJECTED,
        completeness=RunCompleteness.FAILED,
        stage_detail={"reject_reason": "failed_run"},
        scraper_run=rejected_linked,
    )

    # ── listings and snapshots ──
    l1 = _listing(local, "l1", GPU, condition="Used")
    _snap(
        l1,
        now - 5 * HOUR,
        endpoint="https://pilotlocal.invalid/p1",
        shipping=D("0"),
        attrs={"category_hint": "gpu", "mpn": "900-1G"},
    )
    _snap(
        l1,
        now - 3 * HOUR,
        endpoint="https://pilotlocal.invalid/p1",
        shipping=None,
        attrs={"category_hint": "gpu", "mpn": "900-1G"},
    )
    ListingResolution.objects.create(
        listing=l1, matcher_version="t", evidence={"outcome": "review", "category": "gpu"}
    )

    l2 = _listing(local, "l2", RAM)
    _snap(
        l2,
        now - 5 * HOUR,
        endpoint="https://pilotlocal.invalid/p2",
        shipping=D("5.00"),
        attrs={"category_hint": "ram"},
    )
    mfr = Manufacturer.objects.create(name="Samsung", normalized_name="samsung")
    model = ProductModel.objects.create(
        manufacturer=mfr,
        model_number="M393A4K40CB2",
        normalized_model_number="m393a4k40cb2",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ListingResolution.objects.create(
        listing=l2,
        grain="model",
        product_model=model,
        method="exact_alias",
        confidence=1.0,
        matcher_version="t",
        evidence={"outcome": "accept", "category": "ram"},
    )

    # Raw payload gone: provider cannot be attributed.
    l3 = _listing(local, "l3", None)
    _snap(l3, now - 2 * HOUR, endpoint=None, shipping=None)

    l4 = _listing(local, "l4", None)
    _snap(l4, now - 2 * HOUR, endpoint="https://pilotlocal.invalid/p4", shipping=None)
    ListingResolution.objects.create(
        listing=l4, matcher_version="t", evidence={"outcome": "none", "category": "drive"}
    )
    watch = Watch.objects.create(name="pilot drive", category=Category.objects.get(slug="drive"))
    WatchEvaluation.objects.create(
        watch=watch,
        listing=l4,
        verdict=EligibilityVerdict.UNKNOWN,
        reasons=[{"clause": "capacity", "outcome": "unknown"}],
        requirement_version=watch.requirement_version,
        evaluator_version="stale",
        catalog_fingerprint="0" * 64,
        retention_class=RetentionClass.MERCHANT_FACT,
    )

    a1 = _listing(remote, "a1", AGPU, condition="New")
    _snap(
        a1,
        now - 6 * HOUR,
        endpoint=f"{APIFY_RAW_ENDPOINT_PREFIX}ds1/0",
        shipping=D("0"),
        attrs={"category_hint": "gpu"},
    )
    return Pilot(now=now)


def _report(pilot: Pilot, sources: list[str] | None = None) -> dict[str, Any]:
    report = build_report(now=pilot.now, since=pilot.now - timedelta(days=7), sources=sources)
    return cast("dict[str, Any]", report.to_json())


def _source(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in data["sources"] if s["site_key"] == key)


def _provider(source: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(p for p in source["providers"] if p["provider"] == kind)


def _scope(provider: dict[str, Any], key: str | None) -> dict[str, Any]:
    return next(s for s in provider["scopes"] if s["scope_key"] == key)


def _category(scope: dict[str, Any], key: str | None) -> dict[str, Any]:
    return next(c for c in scope["categories"] if c["category"] == key)


def _outcomes(**counts: int) -> dict[str, int]:
    base = dict.fromkeys(
        ("complete", "truncated", "partial_failure", "failed", "in_progress", "not_recorded"), 0
    )
    return {**base, **counts}


def test_endpoint_prefix_matches_the_apify_provider_raw_items() -> None:
    assert _raw_item("ds9", 3, {"a": 1}).url.startswith(APIFY_RAW_ENDPOINT_PREFIX)


def test_local_source_runs_outcomes_and_freshness_per_scope(pilot: Pilot) -> None:
    source = _source(_report(pilot), LOCAL)
    assert source["configured_provider"] == "local"
    assert source["enabled"] is False
    assert source["heartbeat_runs"] == 1
    local = _provider(source, "local")

    gpu = _scope(local, GPU)
    assert gpu["runs"] == {
        "total": 2,
        "successful": 2,
        "full": 2,
        "probe": 0,
        "outcomes": _outcomes(complete=2),
        "completeness_rate": 1.0,
    }
    assert gpu["freshness"]["successful_full_runs"] == 2
    assert gpu["freshness"]["full_run_gap_p50_s"] == 2 * 3600
    assert gpu["freshness"]["full_run_gap_max_s"] == 2 * 3600
    # Newest GPU observation is l1's second snapshot, three hours old.
    assert gpu["freshness"]["newest_observation_age_s"] == 3 * 3600
    assert gpu["cost"]["metered"] is False
    assert gpu["cost"]["note"] == NO_DIRECT_COST
    assert gpu["cost"]["api_calls"] == 3
    assert gpu["cost"]["settled_usd"] is None
    assert gpu["cost"]["ledger_debit_usd"] is None

    ram = _scope(local, RAM)
    assert ram["runs"]["outcomes"] == _outcomes(complete=1, truncated=1)
    assert ram["runs"]["completeness_rate"] == 0.5
    assert ram["cost"]["api_calls"] == 6

    legacy = _scope(local, None)
    assert legacy["runs"]["total"] == 2
    assert legacy["runs"]["successful"] == 1
    assert legacy["runs"]["outcomes"] == _outcomes(complete=1, failed=1)
    assert legacy["runs"]["completeness_rate"] == 0.5
    assert legacy["freshness"]["successful_full_runs"] == 1
    assert legacy["freshness"]["full_run_gap_p50_s"] is None
    # No legacy sweep recorded pages: "not recorded", never an invented 0.
    assert legacy["cost"]["api_calls"] is None

    assert local["cost"]["metered"] is False
    assert local["cost"]["api_calls"] == 9


def test_local_parse_counters_and_failure_recovery(pilot: Pilot) -> None:
    local = _provider(_source(_report(pilot), LOCAL), "local")
    assert local["parse"] == {
        "runs": 4,
        "records_fetched": 15,
        "records_valid": 15,
        "parse_dropped": 2,
        "parse_drop_not_derivable_runs": 1,
        "parse_skipped": None,
        "resolver_errors": 1,
        "evaluator_errors": 2,
    }
    # Failure at T-4h (ends T-4h+1m); next success is the T-3h run (ends T-3h+1m).
    assert local["failures"] == {
        "failed_runs": 1,
        "failure_classes": {"transient": 1},
        "reject_reasons": {},
        "streaks": 1,
        "recovered": 1,
        "unrecovered": 0,
        "recovery_p50_s": 3600.0,
        "recovery_max_s": 3600.0,
    }


def test_apify_source_outcomes_cost_and_rejects(pilot: Pilot) -> None:
    source = _source(_report(pilot), APIFY)
    assert source["configured_provider"] == "apify"
    apify = _provider(source, "apify")
    scope = _scope(apify, AGPU)
    assert scope["runs"]["outcomes"] == _outcomes(complete=1, failed=1, in_progress=1)
    assert scope["runs"]["successful"] == 1
    assert scope["runs"]["completeness_rate"] == round(1 / 3, 4)
    assert scope["cost"] == {
        "metered": True,
        "note": "apify ledger debits + provider-observed usage",
        "api_calls": None,
        "settled_usd": "0.0278",
        "monitoring_usd": "0.0022",
        # The open reservation counts at its full estimate plus monitoring.
        "outstanding_usd": "0.0370",
        "ledger_debit_usd": "0.0670",
        "provider_observed_usd": "0.0002",
        "runs_without_reservation": 1,
        "runs_without_usage": 2,
        "released_or_denied": 0,
    }
    assert apify["failures"]["failure_classes"] == {"parser_rot": 1}
    assert apify["failures"]["reject_reasons"] == {"failed_run": 1}
    assert apify["failures"]["unrecovered"] == 1
    # Only the finalized and rejected runs' ScraperRuns are finished.
    assert apify["parse"]["runs"] == 2
    # The linked ScraperRuns are reported through their ProviderRuns only.
    assert [p["provider"] for p in source["providers"]] == ["apify"]
    assert scope["freshness"]["newest_observation_age_s"] == 6 * 3600


def test_listing_facts_per_category(pilot: Pilot) -> None:
    data = _report(pilot)
    local = _provider(_source(data, LOCAL), "local")

    gpu = _category(_scope(local, GPU), "gpu")
    assert gpu["listings"] == 1
    assert gpu["snapshots"] == 2
    assert gpu["identifiers"] == {
        "with_mpn_attr": 1,
        "with_model_id": 0,
        "with_identifier": 1,
        "coverage": 1.0,
    }
    assert gpu["resolution"]["grain"]["none"] == 1
    assert gpu["resolution"]["outcome"]["review"] == 1
    assert gpu["condition"] == {"with_condition": 1, "coverage": 1.0}
    # Free shipping (0) is known; a None shipping price is unknown.
    assert gpu["shipping"] == {"free": 1, "paid": 0, "unknown": 1, "known_coverage": 0.5}
    assert gpu["watch_evaluations"] is None

    ram = _category(_scope(local, RAM), "ram")
    assert ram["identifiers"]["with_mpn_attr"] == 0
    assert ram["identifiers"]["with_model_id"] == 1
    assert ram["resolution"]["grain"]["model"] == 1
    assert ram["resolution"]["outcome"]["accept"] == 1
    assert ram["condition"] == {"with_condition": 0, "coverage": 0.0}
    assert ram["shipping"]["known_coverage"] == 1.0

    drive = _category(_scope(local, None), "drive")
    assert drive["identifiers"]["coverage"] == 0.0
    assert drive["resolution"]["outcome"]["none"] == 1
    # Stored `unknown`, and non-current (stale evaluator version) -> pending.
    assert drive["watch_evaluations"] == {"match": 0, "no_match": 0, "unknown": 1, "pending": 1}

    unattributed = _provider(_source(data, LOCAL), "not_recorded")
    orphan_scope = _scope(unattributed, None)
    orphan = _category(orphan_scope, None)
    assert orphan["listings"] == 1
    assert orphan["resolution"]["grain"]["no_edge"] == 1
    assert orphan_scope["runs"]["total"] == 0
    assert orphan_scope["cost"]["metered"] is None
    assert orphan_scope["freshness"]["successful_full_runs"] == 0

    apify_gpu = _category(_scope(_provider(_source(data, APIFY), "apify"), AGPU), "gpu")
    assert apify_gpu["identifiers"]["coverage"] == 0.0
    assert apify_gpu["resolution"]["outcome"]["no_edge"] == 1
    assert apify_gpu["shipping"]["free"] == 1


def test_text_output_shows_missing_evidence_explicitly(pilot: Pilot) -> None:
    report = build_report(now=pilot.now, since=pilot.now - timedelta(days=7))
    text = render_text(report)
    assert NO_DIRECT_COST in text
    assert f"skipped {NOT_RECORDED}" in text
    assert "api calls (pages): not recorded" in text
    assert "no successful run" in text
    assert "scope unscoped/legacy" in text
    assert "category no category" in text
    assert "provider-observed $0.0002" in text
    assert "rejected: failed_run=1; recovered 0/1, no success since" in text
    assert "recovered 1/1, p50 60.0m, max 60.0m" in text


def test_json_command_schema_and_source_filter(pilot: Pilot) -> None:
    del pilot
    out = StringIO()
    call_command("pilot_report", "--json", "--source", APIFY, stdout=out)
    data = json.loads(out.getvalue())
    assert data["schema"] == SCHEMA
    assert set(data) == {"schema", "generated_at", "window", "source_filter", "sources"}
    assert data["source_filter"] == [APIFY]
    assert [s["site_key"] for s in data["sources"]] == [APIFY]
    source = data["sources"][0]
    assert set(source) == {
        "site_key",
        "name",
        "configured_provider",
        "enabled",
        "heartbeat_runs",
        "providers",
    }
    provider = source["providers"][0]
    assert set(provider) == {"provider", "parse", "failures", "cost", "scopes"}
    scope = provider["scopes"][0]
    assert set(scope) == {"scope_key", "runs", "freshness", "cost", "categories"}
    assert set(scope["categories"][0]) == {
        "category",
        "snapshots",
        "listings",
        "identifiers",
        "resolution",
        "condition",
        "shipping",
        "watch_evaluations",
    }


def test_text_command_window_excludes_older_runs(pilot: Pilot) -> None:
    del pilot
    out = StringIO()
    call_command("pilot_report", "--days", "1", "--source", LOCAL, stdout=out)
    text = out.getvalue()
    assert f"source {LOCAL}" in text
    assert APIFY not in text
    # The 10-day-old failed run is outside even the default window; within one
    # day the local provider saw exactly four collection runs.
    assert "parse       runs 4," in text


def test_empty_database_prints_an_explicit_empty_report() -> None:
    SourceLaneState.objects.all().delete()
    SourceConfig.objects.all().delete()
    out = StringIO()
    call_command("pilot_report", stdout=out)
    assert "no sources: no configured source and no run in the window" in out.getvalue()
    out = StringIO()
    call_command("pilot_report", "--json", stdout=out)
    assert json.loads(out.getvalue())["sources"] == []


def test_configured_source_without_evidence_is_listed() -> None:
    _site("quietsite", ProviderKind.LOCAL)
    out = StringIO()
    call_command("pilot_report", "--source", "quietsite", stdout=out)
    assert "source quietsite" in out.getvalue()
    assert "no evidence in window" in out.getvalue()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--days", "0"], "--days must be at least 1"),
        (["--since", "yesterday"], "not ISO-8601"),
        (["--since", "2999-01-01"], "in the future"),
    ],
)
def test_bad_window_arguments_are_rejected(args: list[str], message: str) -> None:
    with pytest.raises(CommandError, match=message):
        call_command("pilot_report", *args, stdout=StringIO())


def test_naive_since_is_read_as_utc() -> None:
    out = StringIO()
    call_command("pilot_report", "--since", "2026-01-01T00:00:00", "--json", stdout=out)
    assert json.loads(out.getvalue())["window"]["since"] == "2026-01-01T00:00:00+00:00"


def test_report_issues_no_writes(pilot: Pilot) -> None:
    with CaptureQueriesContext(connection) as ctx:
        render_text(build_report(now=pilot.now, since=pilot.now - timedelta(days=7)))
    writes = [
        q["sql"]
        for q in ctx.captured_queries
        if q["sql"].lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}
    ]
    assert writes == []
