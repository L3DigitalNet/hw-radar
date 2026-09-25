"""Plan E3: one Hardware Radar ledger authority per billing cycle (MS2-D-45).

Two environments are two databases in production. A test has one, so the
second environment is simulated by exporting, then emptying the ledger tables
and switching the ledger id (`_become_other_environment`): what crosses
between them is exactly the exported record, as in production.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from ledger_support import (
    C1_START,
    C2_END,
    C2_START,
    CONFIG,
    DAY,
    HOUR,
    LEDGER_A,
    LEDGER_B,
    NOW,
    RUN_DEBIT,
    STANDING,
    WATCH,
    claim,
    close_monitoring,
    config,
    cycle,
    open_row,
    provider_run,
    reconcile,
    site,
    usage_read,
)

from hw_radar.acquisition.apify.budget import DenialReason, OperatorKind
from hw_radar.acquisition.apify.ledger import (
    HandoffExport,
    LedgerConfig,
    LedgerRefused,
    claim_origin,
    cycle_debits,
    export_handoff,
    import_handoff,
    record_digest,
    reserve,
    reserve_operator,
)
from hw_radar.catalog.models import (
    ApifyBudgetCycle,
    ApifyCycleDiscovery,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ReservationStatus,
)

D = Decimal
# The exporter must have its kill switch off (MS2-D-45).
EXPORTER = config(budget={"enabled": False})
ENV_B = config(ledger_id=LEDGER_B)
ENV_C = config(ledger_id="env-c")


def _section(record: Mapping[str, object], key: str) -> dict[str, object]:
    value = record[key]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _admit(cfg: LedgerConfig = CONFIG, at: datetime = NOW) -> bool:
    return reserve(WATCH, source_site_id=site().pk, config=cfg, now=at).admitted


def _denial(cfg: LedgerConfig = CONFIG, at: datetime = NOW) -> str:
    return reserve(WATCH, source_site_id=site().pk, config=cfg, now=at).reason


def _refusal(destination: str = LEDGER_B, at: datetime = NOW) -> str:
    with pytest.raises(LedgerRefused) as refused:
        export_handoff(destination, config=EXPORTER, now=at)
    return refused.value.code


def _settled(amount: str, at: datetime = NOW - 10 * DAY) -> ApifySpendReservation:
    """A drained runtime row: settled, monitored to its deadline, closed."""
    row = open_row(D("0.50"), at)
    return reconcile(row, actual=D(amount), last_charge_at=at + HOUR, closing_read_at=at + 8 * DAY)


def _become_other_environment() -> None:
    """Empty every ledger table: the destination's own database starts clean."""
    with connection.cursor() as cursor:
        # Fire Django's deferred FK checks first: TRUNCATE refuses a table
        # with pending trigger events inside the test transaction.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "TRUNCATE apify_usage_read, apify_spend_reservation, apify_budget_latch,"
            " apify_budget_cycle, apify_cycle_discovery, apify_ledger_authority CASCADE"
        )
    cycle()


def _export(at: datetime = NOW) -> HandoffExport:
    return export_handoff(LEDGER_B, config=EXPORTER, now=at)


# ── Authority required ──


@pytest.mark.django_db
def test_admission_denied_without_cycle_authority() -> None:
    cycle()
    assert _denial() == DenialReason.LEDGER_AUTHORITY_MISSING
    op = reserve_operator(OperatorKind.BUILD, config=CONFIG, now=NOW)
    assert op.reason == DenialReason.LEDGER_AUTHORITY_MISSING
    claim_origin("owner: no other environment admitted", config=CONFIG, now=NOW)
    assert _admit()


@pytest.mark.django_db
def test_authority_continues_at_cycle_rollover() -> None:
    cycle()
    cycle(C2_START, C2_END, observed_at=C2_START + HOUR)
    claim()
    assert _admit(at=C2_START + HOUR)
    continued = ApifyLedgerAuthority.objects.get(cycle_start=C2_START)
    assert (continued.kind, continued.ledger_id) == ("continued", LEDGER_A)


@pytest.mark.django_db
def test_handed_off_authority_does_not_continue_at_rollover() -> None:
    cycle()
    cycle(C2_START, C2_END, observed_at=C2_START + HOUR)
    claim()
    _export()
    assert _denial(at=C2_START + HOUR) == DenialReason.LEDGER_AUTHORITY_MISSING


# ── Drain checks on export ──


@pytest.mark.django_db
def test_handoff_export_refused_while_proof_run_is_running() -> None:
    cycle()
    claim()
    run = provider_run(site(), NOW - HOUR, remote_status="RUNNING")
    open_row(D("0.40"), NOW - HOUR, run=run)
    assert _refusal() == "open_reservation"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [ReservationStatus.USAGE_PROVISIONAL, ReservationStatus.USAGE_FINALIZED]
)
def test_handoff_export_refused_while_usage_is_provisional_or_unfinalized(
    status: ReservationStatus,
) -> None:
    cycle()
    claim()
    open_row(D("0.40"), NOW - DAY, status=status, run=provider_run(site(), NOW - DAY))
    assert _refusal() == "open_reservation"


@pytest.mark.django_db
def test_handoff_export_refused_with_open_operator_reservation() -> None:
    cycle()
    claim()
    assert reserve_operator(OperatorKind.INSPECT, config=CONFIG, now=NOW).admitted
    assert _refusal() == "open_reservation"


@pytest.mark.django_db
def test_kill_switch_on_refuses_export() -> None:
    cycle()
    claim()
    with pytest.raises(LedgerRefused, match="apify_enabled"):
        export_handoff(LEDGER_B, config=CONFIG, now=NOW)


@pytest.mark.django_db
def test_handoff_export_refused_while_correction_monitoring_open() -> None:
    cycle()
    claim()
    row = open_row(D("0.40"), NOW - 3 * DAY)
    # Reconciled, deadline still ahead, no closing read.
    reconcile(row, actual=D("0.30"), last_charge_at=NOW - 2 * DAY)
    assert _refusal() == "correction_monitoring_open"


@pytest.mark.django_db
def test_handoff_export_refused_after_deadline_until_closing_read_commits() -> None:
    # The E4 outage scenario: settled at $0.50 on day 0, the provider raises it
    # to $1 on day 6, the poller is down days 5-8, the deadline is day 7.
    cycle()
    claim()
    day0 = C1_START + DAY
    row = reconcile(open_row(D("2.00"), day0 - HOUR), actual=D("0.50"), last_charge_at=day0)
    day8 = day0 + 8 * DAY
    assert _refusal(at=day8) == "correction_monitoring_open"  # past deadline, no closing read
    # The restarted poller's closing read commits the $1 correction (E4 stand-in).
    close_monitoring(row, read_at=day8, usage=D("1.00"))
    exported = export_handoff(LEDGER_B, config=EXPORTER, now=day8)
    assert _section(exported.record, "lines")["settled_runtime_usd"] == "1.0000"
    drain = _section(exported.record, "drain")
    assert drain["obligations"] == [
        {
            "reservation_id": row.pk,
            "correction_monitor_until": (day0 + 7 * DAY).isoformat(),
            "closing_read_at": day8.isoformat(),
            "closing_read_usage_usd": "1.00000000",
            "settled_usd": "1.0000",
        }
    ]
    _become_other_environment()
    assert import_handoff(exported.record, config=ENV_B, now=day8)
    carried = ApifyLedgerAuthority.objects.get().carried_consumption_usd
    assert carried == D(str(exported.record["carried_consumption_usd"]))
    assert carried >= D("1.00") + STANDING


@pytest.mark.django_db
def test_handoff_export_refused_with_read_above_settled_usage() -> None:
    cycle()
    claim()
    row = _settled("0.30")
    assert row.reconciled_at is not None
    usage_read(row, read_at=row.reconciled_at + DAY, usage=D("0.31"))
    assert _refusal() == "unapplied_correction"


@pytest.mark.django_db
def test_pre_settlement_read_above_settled_usage_does_not_block_export() -> None:
    cycle()
    claim()
    row = _settled("0.30")
    assert row.reconciled_at is not None
    usage_read(row, read_at=row.reconciled_at - HOUR, usage=D("0.45"))
    assert _export().digest


# ── The drained handoff, end to end ──


@pytest.mark.django_db
def test_drained_handoff_carries_settled_consumption_into_second_environment() -> None:
    cycle()
    claim()
    _settled("0.30")
    _settled("0.20")
    exported = _export()
    assert exported.record["source_ledger_id"] == LEDGER_A
    assert exported.record["destination_ledger_id"] == LEDGER_B
    _become_other_environment()
    assert _denial(ENV_B) == DenialReason.LEDGER_AUTHORITY_MISSING
    assert import_handoff(exported.record, config=ENV_B, now=NOW)
    c1 = ApifyBudgetCycle.objects.get()
    debits, _ = cycle_debits(c1, ENV_B)
    assert debits.carried_handoff_usd == D(str(exported.record["carried_consumption_usd"]))
    assert debits.carried_handoff_usd >= D("0.50")
    assert _admit(ENV_B)


@pytest.mark.django_db
def test_second_environment_denied_until_handoff_imported() -> None:
    cycle()
    claim()
    exported = _export()
    _become_other_environment()
    assert _denial(ENV_B) == DenialReason.LEDGER_AUTHORITY_MISSING
    import_handoff(exported.record, config=ENV_B, now=NOW)
    assert _admit(ENV_B)


@pytest.mark.django_db
def test_exporting_environment_can_never_admit_again_in_that_cycle() -> None:
    cycle()
    claim()
    _export()
    assert _denial() == DenialReason.LEDGER_AUTHORITY_MISSING
    with pytest.raises(LedgerRefused, match="authority_exists"):
        claim_origin("owner", config=CONFIG, now=NOW)
    op = reserve_operator(OperatorKind.BUILD, config=CONFIG, now=NOW + HOUR)
    assert op.reason == DenialReason.LEDGER_AUTHORITY_MISSING


@pytest.mark.django_db
def test_duplicate_export_imported_into_two_ledgers_second_rejected() -> None:
    cycle()
    claim()
    exported = _export()
    _become_other_environment()
    with pytest.raises(LedgerRefused, match="wrong_destination"):
        import_handoff(exported.record, config=ENV_C, now=NOW)
    assert not ApifyLedgerAuthority.objects.exists()


@pytest.mark.django_db
def test_export_to_second_destination_refused_and_same_destination_retry_idempotent() -> None:
    cycle()
    claim()
    first = _export()
    again = export_handoff(LEDGER_B, config=EXPORTER, now=NOW + HOUR)
    assert (again.record, again.digest) == (first.record, first.digest)
    assert record_digest(again.record) == first.digest
    assert _refusal("env-c") == "already_handed_off"


@pytest.mark.django_db
def test_reimport_of_same_record_is_noop() -> None:
    cycle()
    claim()
    exported = _export()
    _become_other_environment()
    assert import_handoff(exported.record, config=ENV_B, now=NOW) is True
    assert import_handoff(exported.record, config=ENV_B, now=NOW + HOUR) is False
    assert ApifyLedgerAuthority.objects.count() == 1


@pytest.mark.django_db
def test_reimport_still_noop_after_the_destination_exports_onward() -> None:
    cycle()
    claim()
    exported = _export()
    _become_other_environment()
    import_handoff(exported.record, config=ENV_B, now=NOW)
    onward = export_handoff(
        "env-c", config=config(ledger_id=LEDGER_B, budget={"enabled": False}), now=NOW + HOUR
    )
    lines = _section(onward.record, "lines")
    assert D(str(lines["carried_in_usd"])) == D(str(exported.record["carried_consumption_usd"]))
    assert import_handoff(exported.record, config=ENV_B, now=NOW + 2 * HOUR) is False


@pytest.mark.django_db
def test_import_refuses_record_without_closing_read_evidence() -> None:
    cycle()
    claim()
    _settled("0.30")
    exported = _export()
    forged = dict(exported.record)
    drain = _section(forged, "drain")
    obligations = [dict(o) for o in cast(list[dict[str, object]], drain["obligations"])]
    obligations[0]["closing_read_at"] = None
    forged["drain"] = {**drain, "obligations": obligations}
    _become_other_environment()
    with pytest.raises(LedgerRefused, match="closing_read_missing"):
        import_handoff(forged, config=ENV_B, now=NOW)
    with pytest.raises(LedgerRefused, match="open_liability"):
        import_handoff(
            {**exported.record, "drain": {**drain, "open_reservations": 1}}, config=ENV_B, now=NOW
        )
    assert not ApifyLedgerAuthority.objects.exists()


@pytest.mark.django_db
def test_import_refuses_record_for_another_cycle() -> None:
    cycle()
    claim()
    exported = _export()
    ApifySpendReservation.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE apify_budget_cycle, apify_ledger_authority CASCADE")
    cycle(C2_START, C2_END, observed_at=C2_START + HOUR)
    with pytest.raises(LedgerRefused, match="wrong_cycle"):
        import_handoff(exported.record, config=ENV_B, now=C2_START + HOUR)


@pytest.mark.django_db
def test_same_cycle_handoff_carries_account_read_and_monitoring_debits() -> None:
    cycle()
    claim()
    ApifyCycleDiscovery.objects.create(
        opened_at=C1_START - HOUR,
        read_count=1,
        last_read_at=C1_START - HOUR,
        closed_at=C1_START,
        cycle_start=C1_START,
        close_reason="discovered",
    )
    row = _settled("0.30")
    ApifySpendReservation.objects.filter(pk=row.pk).update(monitoring_bound_usd=D("0.0022"))
    exported = _export()
    lines = _section(exported.record, "lines")
    assert D(str(lines["standing_account_read_usd"])) == STANDING
    assert D(str(lines["discovery_allowance_usd"])) > 0
    assert D(str(lines["monitoring_allowance_usd"])) == D("0.0022")
    _become_other_environment()
    import_handoff(exported.record, config=ENV_B, now=NOW)
    debits, _ = cycle_debits(ApifyBudgetCycle.objects.get(), ENV_B)
    total = D(str(exported.record["carried_consumption_usd"]))
    assert debits.carried_handoff_usd == total
    assert total == sum((D(str(v)) for v in lines.values()), D(0))
    # The destination's own standing debit is added by admission on top of
    # the carried one: B's room is A - carried - its own standing.
    room = D("11.00") - total - STANDING
    open_row(room - RUN_DEBIT + D("0.0001"), NOW)
    assert _denial(ENV_B) == DenialReason.CLASS_CAP


# ── Serialization with admission (two threads) ──


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_admission_racing_handoff_export_serializes() -> None:
    cycle()
    claim()
    site_id = site().pk
    barrier = threading.Barrier(2)

    def admit() -> bool:
        try:
            barrier.wait(timeout=10)
            return reserve(WATCH, source_site_id=site_id, config=CONFIG, now=NOW).admitted
        finally:
            connection.close()

    def export() -> bool:
        try:
            barrier.wait(timeout=10)
            export_handoff(LEDGER_B, config=EXPORTER, now=NOW)
        except LedgerRefused as refused:
            assert refused.code == "open_reservation"
            return False
        finally:
            connection.close()
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted_f, exported_f = pool.submit(admit), pool.submit(export)
        admitted, exported = admitted_f.result(), exported_f.result()
    # Exactly one of: reservation committed and export refused, or export
    # committed and admission denied.
    assert admitted != exported
    authority = ApifyLedgerAuthority.objects.get(cycle_start=C1_START)
    assert (authority.handed_off_at is not None) == exported
    assert ApifySpendReservation.objects.filter(status="reserved").exists() == admitted
    denied = ApifySpendReservation.objects.filter(status="denied").first()
    if exported:
        assert denied is not None and denied.denial_reason == "ledger_authority_missing"


# Settings-level prices for the commands, which read Django settings.
PRICED = {
    "HW_RADAR_APIFY_USD_PER_CU": D("0.20"),
    "HW_RADAR_APIFY_DATASET_READS_USD_PER_1000": D("0.0004"),
    "HW_RADAR_APIFY_DATASET_WRITES_USD_PER_1000": D("0.005"),
    "HW_RADAR_APIFY_DATASET_STORAGE_USD_PER_GB_HOUR": D("0.001"),
    "HW_RADAR_APIFY_KV_READS_USD_PER_1000": D("0.005"),
    "HW_RADAR_APIFY_KV_WRITES_USD_PER_1000": D("0.05"),
    "HW_RADAR_APIFY_KV_STORAGE_USD_PER_GB_HOUR": D("0.001"),
    "HW_RADAR_APIFY_TRANSFER_USD_PER_GB": D("0.20"),
    "HW_RADAR_APIFY_MARGIN": D("0.10"),
    "HW_RADAR_APIFY_STORAGE_MAX_LIFETIME": 31 * 86400,
}


# ── Owner commands (thin wrappers; the service above carries the rules) ──


@pytest.mark.django_db
def test_claim_and_handoff_commands_round_trip(tmp_path: Path) -> None:
    cycle(observed_at=timezone.now())
    ApifyBudgetCycle.objects.update(
        cycle_start=timezone.now() - DAY, cycle_end=timezone.now() + DAY
    )
    out = StringIO()
    with override_settings(**PRICED, HW_RADAR_APIFY_LEDGER_ID=LEDGER_A):
        call_command("apify_ledger_claim", "--origin", "--reason", "fresh cycle", stdout=out)
        with pytest.raises(CommandError, match="authority_exists"):
            call_command("apify_ledger_claim", "--origin", "--reason", "again")
        record = tmp_path / "handoff.json"
        call_command("apify_ledger_handoff", "--export", "--to", LEDGER_B, "--output", str(record))
        with pytest.raises(CommandError, match="--to"):
            call_command("apify_ledger_handoff", "--export")
    assert "claimed origin" in out.getvalue()
    assert ApifyLedgerAuthority.objects.get().attested_by.endswith(": fresh cycle")
    _become_other_environment()
    ApifyBudgetCycle.objects.update(
        cycle_start=timezone.now() - DAY, cycle_end=timezone.now() + DAY
    )
    out = StringIO()
    with override_settings(HW_RADAR_APIFY_LEDGER_ID=LEDGER_B):
        # The exporter's cycle row moved with it; import needs the same start.
        exported = json.loads(record.read_text(encoding="utf-8"))
        ApifyBudgetCycle.objects.update(cycle_start=datetime.fromisoformat(exported["cycle_start"]))
        call_command("apify_ledger_handoff", "--import", str(record), stdout=out)
        call_command("apify_ledger_handoff", "--import", str(record), stdout=out)
        bad = tmp_path / "bad.json"
        bad.write_text("[]", encoding="utf-8")
        with pytest.raises(CommandError, match="JSON object"):
            call_command("apify_ledger_handoff", "--import", str(bad))
    assert out.getvalue().splitlines() == ["handoff imported", "handoff already imported (no-op)"]


@pytest.mark.django_db
def test_operator_reserve_command_reserves_or_refuses() -> None:
    cycle(observed_at=timezone.now())
    ApifyBudgetCycle.objects.update(
        cycle_start=timezone.now() - DAY, cycle_end=timezone.now() + DAY
    )
    with override_settings(**PRICED, HW_RADAR_APIFY_ENABLED=True):
        with pytest.raises(CommandError, match="ledger_authority_missing"):
            call_command("apify_operator_reserve", "--kind", "build", "--reason", "rebuild")
        claim(ApifyBudgetCycle.objects.get().cycle_start)
        out = StringIO()
        call_command("apify_operator_reserve", "--kind", "build", "--reason", "rebuild", stdout=out)
    assert "reserved build" in out.getvalue()
    row = ApifySpendReservation.objects.get(status="reserved")
    assert (row.admission_class, row.operator_kind, row.provider_run) == ("operator", "build", None)
