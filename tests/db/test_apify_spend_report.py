"""Plan E6 (AC-7): the Apify spend attribution report over a seeded two-cycle ledger.

The ledger is seeded as E3's tests do (rows E4 will settle are written
directly), across the anniversary cycles C1 and C2 of ledger_support, with
`NOW_C2` ten days into C2. Expected figures are derived from the seeded
amounts and ledger_support's settings-derived constants, never from the
report's own arithmetic; the per-cycle totals are also pinned to
`ledger.cycle_debits`, the admission path's own sums, so the report's local
cycle placement cannot drift from the ledger's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from ledger_support import (
    ALLOCATION,
    C1_END,
    C1_START,
    C2_END,
    C2_START,
    CONFIG,
    DAY,
    HOUR,
    LEDGER_A,
    LEDGER_B,
    config,
    cycle,
    open_row,
    provider_run,
    reconcile,
    site,
)

from hw_radar.acquisition.apify.ledger import LedgerConfig, cycle_debits
from hw_radar.acquisition.apify.report import (
    CORRECTION_CLOSE_OVERDUE,
    OTHER_WORKLOADS_NOT_OBSERVED,
    SpendReport,
    build_report,
    render_report,
)
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyCycleDiscovery,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ReservationStatus,
)
from hw_radar.catalog.models.provider import LedgerAuthorityKind

D = Decimal
NOW_C2 = C2_START + 10 * DAY
CARRIED = D("0.25")
# Owner free text that must never reach the output (report SCOPE).
ATTESTATION = "test owner"
CLEAR_NOTE = "owner cleared after estimator review"


@dataclass(frozen=True)
class Seeded:
    c1: ApifyBudgetCycle
    c2: ApifyBudgetCycle
    settled_c1: ApifySpendReservation
    open_straddling: ApifySpendReservation
    overrun_c2: ApifySpendReservation
    build_unbound: ApifySpendReservation
    inspect_c2: ApifySpendReservation
    denial: ApifySpendReservation


def _usd(amount: Decimal) -> str:
    return f"${amount.quantize(D('0.0001'))}"


def _seed() -> Seeded:
    c1 = cycle(C1_START, C1_END)
    c2 = cycle(C2_START, C2_END)
    ApifyLedgerAuthority.objects.create(
        cycle_start=C1_START,
        kind=LedgerAuthorityKind.ORIGIN,
        ledger_id=LEDGER_A,
        attested_by=ATTESTATION,
        created_at=C1_START,
        handed_off_at=C1_END - HOUR,
        handed_off_to=LEDGER_B,
        # The export's stored record; its content is irrelevant to the report.
        handoff_record={"version": 1},
        handoff_record_digest="0" * 64,
    )
    ApifyLedgerAuthority.objects.create(
        cycle_start=C2_START,
        kind=LedgerAuthorityKind.HANDOFF,
        ledger_id=LEDGER_B,
        carried_consumption_usd=CARRIED,
        attested_by=f"handoff from {LEDGER_A}",
        created_at=C2_START,
        imported_record_digest="1" * 64,
    )
    alpha = site("alpha-shop")
    beta = site("beta-shop")

    # C1 only: settled, monitored, and closed well inside C1.
    t = C1_START + 2 * DAY
    settled_c1 = open_row(D("0.50"), t)
    settled_c1.source_site = alpha
    settled_c1.save()
    run_a = provider_run(alpha, t, usage_total_usd=D("0.40"))
    reconcile(
        settled_c1,
        actual=D("0.40"),
        last_charge_at=t + HOUR,
        closing_read_at=t + 8 * DAY,
        run=run_a,
    )

    # Unreconciled since late C1: counts in C1 AND C2 at its estimate.
    open_straddling = open_row(D("0.30"), C1_START + 20 * DAY)
    open_straddling.source_site = beta
    open_straddling.save()

    # C2: settled above its estimate, obligation never closed, deadline passed.
    t2 = C2_START + DAY
    overrun_c2 = open_row(D("0.20"), t2)
    overrun_c2.source_site = alpha
    overrun_c2.save()
    run_b = provider_run(alpha, t2, usage_total_usd=D("0.35"))
    reconcile(overrun_c2, actual=D("0.35"), last_charge_at=t2 + HOUR, run=run_b)
    ApifyBudgetLatch.objects.create(
        tripped_at=t2 + HOUR, provider_run=run_b, reason="actual_above_estimate"
    )
    ApifyBudgetLatch.objects.create(
        tripped_at=C1_START + DAY,
        reason="external_liability_exceeded",
        cleared_at=C1_START + 2 * DAY,
        cleared_reason=CLEAR_NOTE,
    )

    build_unbound = open_row(
        D("0.41"),
        C2_START + 3 * DAY,
        admission_class=AdmissionClass.OPERATOR,
        operator_kind="build",
    )
    inspect_c2 = ApifySpendReservation.objects.create(
        admission_class=AdmissionClass.OPERATOR,
        operator_kind="inspect",
        status=ReservationStatus.RESERVED,
        estimate_usd=D("0.05"),
        execution_bound_usd=D("0.05"),
        post_run_liability_usd=D(0),
        monitoring_bound_usd=D(0),
        envelope_max_items=1000,
        envelope_max_record_reads=20,
        envelope_max_bytes=10_000_000,
        reserved_at=C2_START + 4 * DAY,
    )
    reconcile(inspect_c2, actual=D("0.05"), last_charge_at=C2_START + 4 * DAY)
    denial = ApifySpendReservation.objects.create(
        admission_class=AdmissionClass.DISCOVERY,
        source_site=beta,
        status=ReservationStatus.DENIED,
        denial_reason="class_cap",
        reserved_at=C2_START + 5 * DAY,
    )
    return Seeded(
        c1, c2, settled_c1, open_straddling, overrun_c2, build_unbound, inspect_c2, denial
    )


def _report(now: datetime = NOW_C2, cfg: LedgerConfig = CONFIG) -> tuple[SpendReport, str]:
    report = build_report(config=cfg, now=now)
    return report, render_report(report)


def _section(text: str, header: str, *, until: str = "\n\n") -> str:
    start = text.index(header)
    end = text.find(until, start)
    return text[start : end if end != -1 else len(text)]


@pytest.mark.django_db
def test_report_for_seeded_ledger_spanning_two_cycles() -> None:
    seeded = _seed()
    report, text = _report()

    c1_text = _section(text, "Billing cycle 2026-09-05T00:00:00Z")
    c2_text = _section(text, "Billing cycle 2026-10-05T00:00:00Z")
    assert "[current]" in c2_text and "[current]" not in c1_text

    # C1: the settled alpha row and the open beta row.
    assert f"consumed (settled usage):        {_usd(D('0.40'))}" in c1_text
    assert f"outstanding reservations:        {_usd(D('0.30'))}" in c1_text
    remaining_c1 = ALLOCATION - D("0.70")
    assert f"remaining project budget:        {_usd(remaining_c1)}" in c1_text
    assert f"project allocation (A):          {_usd(ALLOCATION)}" in c1_text
    assert f"ledger authority:                origin ledger={LEDGER_A}" in c1_text
    assert f"handed off to {LEDGER_B}" in c1_text

    # C2: the open row carried over, the overrun and inspect settlements, the
    # unbound build at its bound, the handoff's carried consumption.
    assert f"consumed (settled usage):        {_usd(D('0.40'))}" in c2_text
    assert f"outstanding reservations:        {_usd(D('0.71'))}" in c2_text
    assert f"carried handoff consumption:     {_usd(CARRIED)}" in c2_text
    remaining_c2 = ALLOCATION - (D("0.30") + D("0.35") + CARRIED)
    assert f"remaining project budget:        {_usd(remaining_c2)}" in c2_text
    assert f"ledger authority:                handoff ledger={LEDGER_B}" in c2_text
    assert f"declared external liability (E): {_usd(D('5.00'))}" in c2_text
    # External-liability headroom = P - HR_cycle - E, with P = 19.00 - 1.90.
    hr_c2 = D("0.30") + D("0.35") + D("0.41") + D("0.05") + CARRIED
    assert f"external-liability headroom:     {_usd(D('17.10') - hr_c2 - D('5.00'))}" in c2_text
    assert f"other workloads' spend:          {OTHER_WORKLOADS_NOT_OBSERVED}" in c2_text
    operator_c2 = D("0.41") + D("0.05")
    assert f"operator allowance remaining:    {_usd(D('1.00') - operator_c2)}" in c2_text

    by_kind = _section(c2_text, "operator consumption by kind:", until="per source:")
    assert f"build: rows=1 settled={_usd(D(0))} outstanding={_usd(D('0.41'))}" in by_kind
    assert f"inspect: rows=1 settled={_usd(D('0.05'))}" in by_kind
    by_source = _section(c2_text, "per source:", until="per provider")
    assert f"alpha-shop: rows=1 settled={_usd(D('0.35'))}" in by_source
    assert f"beta-shop: rows=1 settled={_usd(D(0))} outstanding={_usd(D('0.30'))}" in by_source
    assert "denials=1" in by_source
    by_provider = _section(c2_text, "per provider", until="denials by reason:")
    assert (
        f"apify: rows=1 settled={_usd(D('0.35'))} outstanding={_usd(D(0))}"
        f" monitoring={_usd(D(0))} total={_usd(D('0.35'))}"
        f" provider_runs=1 provider_observed_usage={_usd(D('0.35'))}"
    ) in by_provider
    assert "none (operator): rows=2" in by_provider
    assert "unattached: rows=1" in by_provider
    assert "class_cap: 1" in _section(c2_text, "denials by reason:")

    unsettled = _section(text, "Unsettled rows:")
    assert f"#{seeded.open_straddling.pk} watch_refresh beta-shop" in unsettled
    # Admitted in cycle 1 and still open after it ended: reconcile.is_unreconciled_stale.
    assert "reserved, unreconciled_stale, no_provider_run" in unsettled
    assert f"#{seeded.build_unbound.pk} operator/build" in unsettled
    assert "build_id_unbound" in unsettled
    assert f"#{seeded.overrun_c2.pk} watch_refresh alpha-shop" in unsettled
    assert CORRECTION_CLOSE_OVERDUE in unsettled
    # Closed obligations and envelope rows (no obligation) are not unsettled.
    assert f"#{seeded.settled_c1.pk} " not in unsettled
    assert f"#{seeded.inspect_c2.pk} " not in unsettled

    overruns = _section(text, "Overrun rows:")
    assert f"#{seeded.overrun_c2.pk} " in overruns
    assert f"actual_above_estimate (estimate {_usd(D('0.20'))})" in overruns
    latches = _section(text, "Overrun latch trips:")
    open_trip = "actual_above_estimate tripped 2026-10-06T01:00:00Z [OPEN]"
    assert f"{open_trip} reservation={seeded.overrun_c2.pk}" in latches
    assert "external_liability_exceeded" in latches and "cleared 2026-09-07" in latches

    trend = _section(text, "Trailing 31 days (secondary")
    # Window [NOW_C2 - 31 d, NOW_C2]: everything but the C1 settlement, which
    # ended before it.
    trend_total = D("0.30") + D("0.35") + D("0.41") + D("0.05")
    assert f": {_usd(trend_total)} (within the project allocation)" in trend
    assert report.trend_total == trend_total

    # SCOPE: no owner free text, no credential.
    for secret in (ATTESTATION, CLEAR_NOTE, "test-token", f"handoff from {LEDGER_A}"):
        assert secret not in text


@pytest.mark.django_db
def test_cycle_totals_match_ledger_cycle_debits() -> None:
    """The report's local cycle placement agrees with the admission path's sums."""
    seeded = _seed()
    report, _ = _report()
    for observed, reported in zip((seeded.c1, seeded.c2), report.cycles, strict=True):
        debits, _ = cycle_debits(observed, CONFIG)
        hr_total = reported.consumed_settled + reported.outstanding + reported.monitoring
        assert hr_total == debits.runtime_committed_usd + debits.operator_committed_usd
        assert reported.carried_handoff == debits.carried_handoff_usd
        operator = sum((t.total for t in reported.operator_by_kind.values()), D(0))
        assert operator == debits.operator_committed_usd


@pytest.mark.django_db
def test_monitoring_close_overdue_by_read_cap_before_deadline() -> None:
    """MS2-D-32: at the correction-read cap the obligation is overdue even pre-deadline."""
    cycle(C2_START, C2_END)
    src = site("gamma-shop")
    row = open_row(D("0.20"), NOW_C2 - DAY)
    row.source_site = src
    row.save()
    run = provider_run(src, row.reserved_at, correction_read_count=12)
    reconcile(row, actual=D("0.10"), last_charge_at=NOW_C2 - HOUR, run=run)
    _, text = _report()
    assert f"#{row.pk} watch_refresh gamma-shop" in _section(text, "Unsettled rows:")
    assert CORRECTION_CLOSE_OVERDUE in text
    run.correction_read_count = 3
    run.save()
    _, text = _report()
    assert CORRECTION_CLOSE_OVERDUE not in text


@pytest.mark.django_db
def test_unpriceable_settings_degrade_to_unavailable_not_errors() -> None:
    _seed()
    broken = config(budget={"cycle_target_usd": None, "account_limit_usd": None})
    _, text = _report(cfg=broken)
    assert "project allocation (A):          unavailable (budget_setting_invalid)" in text
    assert "usable account limit (P):        unavailable (account_state_unobservable)" in text
    assert "external-liability headroom:     unavailable (account_state_unobservable)" in text
    assert "remaining project budget:        unavailable (" in text
    assert "(unavailable (allocation settings invalid))" in text


@pytest.mark.django_db
def test_empty_ledger_reports_no_cycle() -> None:
    _, text = _report(cfg=config(budget={"billing_cycle_anchor": None}))
    assert (
        "No billing cycle recorded or derivable from the anchor (admission: cycle_unknown)." in text
    )
    assert "Unsettled rows:\n  (none)" in text


@pytest.mark.django_db
def test_report_prints_configured_account_state_without_observed_usage() -> None:
    # Empty ledger: the anchor still derives C2 for NOW_C2, and the report
    # prints it, labelled, without creating its row (read-only).
    _, text = _report()
    assert not ApifyBudgetCycle.objects.exists()
    c2_text = _section(text, "Billing cycle 2026-10-05T00:00:00Z")
    assert "[current; derived from the anchor, not yet recorded]" in c2_text
    assert "billing cycle anchor:            2026-09-05T00:00:00Z" in c2_text
    assert f"configured account limit:        {_usd(D('19.00'))}" in c2_text
    assert f"usable account limit (P):        {_usd(D('17.10'))}" in c2_text
    assert f"configured plan base price:      {_usd(D('19.00'))}" in c2_text
    assert "configured data retention:       31 days" in c2_text
    assert "account state verified on:       2026-09-05" in c2_text
    assert f"external-liability headroom:     {_usd(D('12.10'))}" in c2_text
    assert f"other workloads' spend:          {OTHER_WORKLOADS_NOT_OBSERVED}" in c2_text
    # No observed-usage figure of any kind survives MS2-D-48.
    for retired in ("observed account usage", "prepaid", "snapshot", "watermark", "discovery"):
        assert retired not in text


@pytest.mark.django_db
def test_command_prints_report_read_only_and_limits_cycles() -> None:
    seeded = _seed()
    counts = (
        ApifySpendReservation.objects.count(),
        ApifyBudgetLatch.objects.count(),
        ApifyCycleDiscovery.objects.count(),
        ApifyLedgerAuthority.objects.count(),
    )
    out = StringIO()
    call_command("apify_spend_report", stdout=out)
    text = out.getvalue()
    assert text.startswith("Apify spend report as of ")
    assert "Billing cycle 2026-09-05T00:00:00Z" in text
    assert "Billing cycle 2026-10-05T00:00:00Z" in text
    assert ATTESTATION not in text

    out = StringIO()
    call_command("apify_spend_report", "--cycles", "1", stdout=out)
    assert "Billing cycle 2026-09-05T00:00:00Z" not in out.getvalue()
    assert "Billing cycle 2026-10-05T00:00:00Z" in out.getvalue()
    with pytest.raises(CommandError):
        call_command("apify_spend_report", "--cycles", "0")

    assert counts == (
        ApifySpendReservation.objects.count(),
        ApifyBudgetLatch.objects.count(),
        ApifyCycleDiscovery.objects.count(),
        ApifyLedgerAuthority.objects.count(),
    )
    seeded.c2.refresh_from_db()
    assert seeded.c2.account_read_count == 0


# ── E9.6: verification-age warning (owner R39 point 1; MS2-D-48) ────────────


def _verification_warnings(text: str) -> list[str]:
    return [line for line in text.splitlines() if "WARNING" in line and "VERIFIED_ON" in line]


@pytest.mark.django_db
def test_report_warns_when_account_verification_predates_current_cycle() -> None:
    _seed()
    # Verified on the anchor day (C1); NOW_C2 is in C2, which starts 2026-10-05.
    _, text = _report()
    warnings = _verification_warnings(text)
    assert len(warnings) == 1
    assert "HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON" in warnings[0]
    assert "2026-09-05" in warnings[0] and "2026-10-05" in warnings[0]
    # In the current cycle's header, not in the older cycle's section.
    assert warnings[0] in _section(text, "Billing cycle 2026-10-05T00:00:00Z")


@pytest.mark.django_db
def test_report_has_no_verification_warning_when_verified_this_cycle() -> None:
    _seed()
    fresh = config(budget={"account_verified_on": (C2_START + DAY).date()})
    _, text = _report(cfg=fresh)
    assert _verification_warnings(text) == []
