"""MS-2 Slice E task E1 (migration 0022): the Apify spend ledger's single-row
invariants, proven at the database rather than by writer convention.

The ledger service (E3) and reconcile unit (E4) write these rows later. Pinning
the invariants here means a writer bug surfaces as an IntegrityError at the
write instead of as a reservation that aggregates count at $0, a build that
reconciles with nothing to monitor, or a second open cycle-discovery row that
doubles the read allowance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyCycleDiscovery,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ApifyUsageRead,
    CycleDiscoveryCloseReason,
    LedgerAuthorityKind,
    OperatorKind,
    ProviderKind,
    ProviderRun,
    ReservationStatus,
    RunKind,
    SettlementBasis,
    SourceSite,
    SourceType,
)

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
CYCLE_START = datetime(2026, 9, 5, 0, 0, 0, tzinfo=UTC)
CYCLE_END = datetime(2026, 10, 4, 23, 59, 59, 999000, tzinfo=UTC)
USD = Decimal("0.5000")


# An E1-local site key (conventions #9): the 0005 seed owns the real keys.
def _site(key: str = "e1schema") -> SourceSite:
    return SourceSite.objects.create(name=key, normalized_name=key, source_type=SourceType.OTHER)


def _run(site: SourceSite) -> ProviderRun:
    return ProviderRun.objects.create(
        source_site=site,
        provider_kind=ProviderKind.APIFY,
        scope_key="e1schema:gpu:q1",
        memory_mb=512,
        timeout_s=600,
        max_items=50,
        max_pages=5,
        admission_class=AdmissionClass.WATCH_REFRESH,
        run_kind=RunKind.FULL,
        admitted_at=T0,
        storage_cleanup_due_at=T0 + timedelta(hours=24),
    )


def _resv(**overrides: Any) -> ApifySpendReservation:
    """An admitted runtime reservation: the shape E3's reserve() writes."""
    fields: dict[str, Any] = {
        "admission_class": AdmissionClass.WATCH_REFRESH,
        "status": ReservationStatus.RESERVED,
        "estimate_usd": USD,
        "execution_bound_usd": Decimal("0.3000"),
        "post_run_liability_usd": Decimal("0.1978"),
        "monitoring_bound_usd": Decimal("0.0022"),
        "component_bounds": {"compute": "0.0278"},
        "estimator_version": "e1",
        "reserved_at": T0,
    }
    fields.update(overrides)
    if "source_site" not in fields and fields["admission_class"] != AdmissionClass.OPERATOR:
        fields["source_site"] = _site()
    return ApifySpendReservation.objects.create(**fields)


def _operator(kind: OperatorKind, **overrides: Any) -> ApifySpendReservation:
    envelope: dict[str, Any] = {}
    if kind is OperatorKind.INSPECT:
        envelope = {
            "envelope_max_items": 1000,
            "envelope_max_record_reads": 20,
            "envelope_max_bytes": 10_000_000,
            "monitoring_bound_usd": Decimal(0),
        }
    elif kind is OperatorKind.PROBE:
        envelope = {"envelope_max_calls": 10, "monitoring_bound_usd": Decimal(0)}
    return _resv(
        admission_class=AdmissionClass.OPERATOR, operator_kind=kind, **{**envelope, **overrides}
    )


def _reconciled(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": ReservationStatus.RECONCILED,
        "actual_usd": Decimal("0.4000"),
        "settled_run_usage_usd": Decimal("0.2000"),
        "settlement_basis": SettlementBasis.BOUND,
        "reconciled_at": T0 + timedelta(hours=2),
        "last_charge_at": T0 + timedelta(hours=2),
    }
    fields.update(overrides)
    return fields


def _rejects(constraint: str, factory: Any, *args: Any, **kwargs: Any) -> None:
    with transaction.atomic(), pytest.raises(IntegrityError, match=constraint):
        factory(*args, **kwargs)


# ── ApifySpendReservation: vocabularies ──


@pytest.mark.parametrize(
    ("field", "constraint"),
    [
        ("admission_class", "apify_resv_admission_class_valid"),
        ("status", "apify_resv_status_valid"),
        ("settlement_basis", "apify_resv_settlement_basis_valid"),
        ("post_run_cost_mode", "apify_resv_post_run_cost_mode_valid"),
    ],
)
def test_reservation_rejects_unknown_vocabulary(field: str, constraint: str) -> None:
    site = _site()
    _rejects(constraint, _resv, source_site=site, **{field: "bogus"})


def test_operator_kind_rejects_unknown_value() -> None:
    _rejects(
        "apify_resv_operator_kind_valid",
        _resv,
        admission_class=AdmissionClass.OPERATOR,
        operator_kind="deploy",
    )


# ── ApifySpendReservation: admitted runtime and denial shapes ──


def test_admitted_runtime_reservation_attaches_one_to_one_to_its_run() -> None:
    site = _site()
    run = _run(site)
    row = _resv(source_site=site, provider_run=run)
    assert run.spend_reservation == row  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse accessor stubs
    _rejects("provider_run_id", _resv, source_site=site, provider_run=run)


def test_denial_records_reason_and_may_lack_an_estimate() -> None:
    # pricing_unverified is decided before any estimate can be computed.
    row = _resv(
        status=ReservationStatus.DENIED,
        denial_reason="pricing_unverified",
        estimate_usd=None,
        monitoring_bound_usd=None,
    )
    assert row.pk is not None


def test_denial_reason_set_exactly_on_denied_rows() -> None:
    site = _site()
    _rejects("apify_resv_denial_reason_iff_denied", _resv, source_site=site, denial_reason="x")
    _rejects(
        "apify_resv_denial_reason_iff_denied",
        _resv,
        source_site=site,
        status=ReservationStatus.DENIED,
    )


def test_denial_never_attaches_a_run() -> None:
    site = _site()
    _rejects(
        "apify_resv_run_only_on_admitted_runtime",
        _resv,
        source_site=site,
        provider_run=_run(site),
        status=ReservationStatus.DENIED,
        denial_reason="class_cap",
    )


@pytest.mark.parametrize("missing", ["estimate_usd", "monitoring_bound_usd"])
def test_admitted_row_must_carry_estimate_and_monitoring_bound(missing: str) -> None:
    # A NULL here would be summed as $0 by every aggregate: fail-open.
    _rejects("apify_resv_admitted_has_bounds", _resv, **{missing: None})


def test_runtime_row_must_name_its_source() -> None:
    _rejects("apify_resv_runtime_has_source", _resv, source_site=None)


@pytest.mark.parametrize(
    "status",
    [
        ReservationStatus.USAGE_PROVISIONAL,
        ReservationStatus.USAGE_FINALIZED,
        ReservationStatus.RECONCILED,
    ],
)
def test_runtime_usage_states_need_the_run(status: ReservationStatus) -> None:
    fields = _reconciled() if status is ReservationStatus.RECONCILED else {"status": status}
    _rejects("apify_resv_runtime_usage_needs_run", _resv, **fields)


def test_released_runtime_row_may_keep_its_run() -> None:
    site = _site()
    row = _resv(source_site=site, provider_run=_run(site), status=ReservationStatus.RELEASED)
    assert row.provider_run is not None


def test_reconciled_row_needs_actual_and_charge_interval() -> None:
    site = _site()
    for missing in ("actual_usd", "reconciled_at", "last_charge_at"):
        _rejects(
            "apify_resv_reconciled_has_settlement",
            _resv,
            source_site=site,
            provider_run=_run(site),
            **_reconciled(**{missing: None}),
        )
    row = _resv(source_site=site, provider_run=_run(site), **_reconciled())
    assert row.status == ReservationStatus.RECONCILED


def test_finalized_usage_and_its_time_are_written_together() -> None:
    site = _site()
    _rejects("apify_resv_finalized_usage_pair", _resv, source_site=site, usage_finalized_usd=USD)
    _rejects("apify_resv_finalized_usage_pair", _resv, source_site=site, usage_finalized_at=T0)


def test_money_columns_reject_negative_values() -> None:
    site = _site()
    for field in (
        "estimate_usd",
        "actual_usd",
        "execution_bound_usd",
        "post_run_liability_usd",
        "monitoring_bound_usd",
        "usage_provisional_usd",
        "usage_finalized_usd",
        "post_run_cost_usd",
        "settled_run_usage_usd",
    ):
        extra: dict[str, Any] = {field: Decimal("-0.0001")}
        if field == "usage_finalized_usd":
            extra["usage_finalized_at"] = T0
        _rejects(f"apify_resv_{field}_non_negative", _resv, source_site=site, **extra)


# ── ApifySpendReservation: operator rows (MS2-D-46) ──


def test_operator_kind_set_exactly_on_operator_rows() -> None:
    _rejects(
        "apify_resv_operator_kind_iff_operator",
        _resv,
        admission_class=AdmissionClass.OPERATOR,
    )
    _rejects("apify_resv_operator_kind_iff_operator", _resv, operator_kind=OperatorKind.BUILD)


@pytest.mark.parametrize("kind", list(OperatorKind))
def test_operator_row_never_has_a_provider_run(kind: OperatorKind) -> None:
    site = _site()
    _rejects(
        "apify_resv_run_only_on_admitted_runtime",
        _operator,
        kind,
        source_site=site,
        provider_run=_run(site),
    )
    assert _operator(kind).provider_run is None


def test_operator_rows_carry_their_own_call_counters() -> None:
    row = _operator(OperatorKind.BUILD)
    assert (row.run_poll_count, row.correction_read_count) == (0, 0)


def test_envelope_limits_follow_operator_kind() -> None:
    # Inspection: its three limits and no call limit.
    _rejects(
        "apify_resv_envelope_limits_by_kind",
        _operator,
        OperatorKind.INSPECT,
        envelope_max_bytes=None,
    )
    _rejects(
        "apify_resv_envelope_limits_by_kind",
        _operator,
        OperatorKind.INSPECT,
        envelope_max_calls=10,
    )
    # Probe: its call limit only.
    _rejects(
        "apify_resv_envelope_limits_by_kind",
        _operator,
        OperatorKind.PROBE,
        envelope_max_calls=None,
    )
    _rejects(
        "apify_resv_envelope_limits_by_kind",
        _operator,
        OperatorKind.PROBE,
        envelope_max_items=1000,
    )
    # Build and runtime rows: none.
    _rejects(
        "apify_resv_envelope_limits_by_kind",
        _operator,
        OperatorKind.BUILD,
        envelope_max_calls=10,
    )
    _rejects("apify_resv_envelope_limits_by_kind", _resv, envelope_max_items=10)


def test_denied_envelope_row_may_lack_unset_limits() -> None:
    # E4 residual (c): a denial caused by an unset envelope limit is recorded,
    # so a denied row is exempt; an admitted one still needs every limit.
    denied = _operator(
        OperatorKind.INSPECT,
        envelope_max_bytes=None,
        status=ReservationStatus.DENIED,
        denial_reason="unbounded_component",
        estimate_usd=None,
        monitoring_bound_usd=None,
    )
    assert denied.envelope_max_bytes is None


def test_probe_dataset_id_only_on_probe_rows() -> None:
    _rejects(
        "apify_resv_probe_dataset_only_on_probe",
        _operator,
        OperatorKind.INSPECT,
        probe_dataset_id="ds-1",
    )
    assert _operator(OperatorKind.PROBE, probe_dataset_id="ds-1").probe_dataset_id == "ds-1"


@pytest.mark.parametrize("kind", [OperatorKind.INSPECT, OperatorKind.PROBE])
def test_envelope_rows_carry_no_monitoring_allowance(kind: OperatorKind) -> None:
    _rejects(
        "apify_resv_envelope_rows_no_monitoring",
        _operator,
        kind,
        monitoring_bound_usd=Decimal("0.0022"),
    )


def test_build_id_is_unique_and_only_on_build_rows() -> None:
    _operator(OperatorKind.BUILD, provider_build_id="b1")
    _rejects("provider_build_id", _operator, OperatorKind.BUILD, provider_build_id="b1")
    _rejects(
        "apify_resv_build_id_only_on_build",
        _operator,
        OperatorKind.INSPECT,
        provider_build_id="b2",
    )
    _rejects("apify_resv_build_id_only_on_build", _resv, provider_build_id="b3")
    # NULLs are distinct: any number of unbound build rows coexist.
    _operator(OperatorKind.BUILD)
    _operator(OperatorKind.BUILD)


def test_build_row_cannot_reconcile_without_its_build_id() -> None:
    _rejects(
        "apify_resv_reconciled_build_has_build_id",
        _operator,
        OperatorKind.BUILD,
        **_reconciled(),
    )
    row = _operator(OperatorKind.BUILD, provider_build_id="b9", **_reconciled())
    assert row.status == ReservationStatus.RECONCILED


def test_reconciled_inspection_needs_no_build_id() -> None:
    row = _operator(OperatorKind.INSPECT, **_reconciled(settled_run_usage_usd=None))
    assert row.status == ReservationStatus.RECONCILED


# ── ApifySpendReservation: correction monitoring (MS2-D-41, -47) ──


def _read(row: ApifySpendReservation, **overrides: Any) -> ApifyUsageRead:
    fields: dict[str, Any] = {
        "reservation": row,
        "provider_run": row.provider_run,
        "read_at": T0 + timedelta(days=8),
        "usage_total_usd": Decimal("0.21000000"),
        "usage_usd": {"ACTOR_COMPUTE_UNITS": "0.21"},
        "price_settings_version": "2026-09-25",
    }
    fields.update(overrides)
    return ApifyUsageRead.objects.create(**fields)


def test_closure_commits_only_with_its_closing_read() -> None:
    site = _site()
    row = _resv(
        source_site=site,
        provider_run=_run(site),
        **_reconciled(correction_monitor_until=T0 + timedelta(days=7)),
    )
    read = _read(row)
    queryset = ApifySpendReservation.objects.filter(pk=row.pk)
    with transaction.atomic(), pytest.raises(IntegrityError, match="closure_with_closing_read"):
        queryset.update(correction_monitor_closed_at=read.read_at)
    with transaction.atomic(), pytest.raises(IntegrityError, match="closure_with_closing_read"):
        queryset.update(correction_closing_read=read)
    queryset.update(correction_monitor_closed_at=read.read_at, correction_closing_read=read)
    row.refresh_from_db()
    assert row.correction_closing_read == read


def test_closure_requires_a_monitoring_deadline() -> None:
    site = _site()
    row = _resv(source_site=site, provider_run=_run(site), **_reconciled())
    read = _read(row)
    with transaction.atomic(), pytest.raises(IntegrityError, match="closure_with_closing_read"):
        ApifySpendReservation.objects.filter(pk=row.pk).update(
            correction_monitor_closed_at=read.read_at, correction_closing_read=read
        )


def test_monitoring_interval_columns_start_null() -> None:
    row = _resv()
    assert (
        row.monitoring_charge_last_at,
        row.monitoring_call_pending_since,
        row.next_usage_read_at,
        row.correction_monitor_until,
    ) == (None, None, None, None)


# ── ApifyUsageRead ──


def test_build_read_has_no_provider_run() -> None:
    build = _operator(OperatorKind.BUILD, provider_build_id="b1")
    read = _read(build, usage_total_usd=None)
    assert read.provider_run is None
    assert list(ApifyUsageRead.objects.filter(reservation=build)) == [read]


def test_usage_read_rejects_negative_total() -> None:
    _rejects(
        "apify_usage_read_usage_total_usd_non_negative",
        _read,
        _resv(),
        usage_total_usd=Decimal("-1"),
    )


def test_reservation_with_evidence_cannot_be_deleted() -> None:
    row = _resv()
    _read(row)
    with transaction.atomic(), pytest.raises(ProtectedError):
        row.delete()


# ── ApifyBudgetLatch (MS2-D-26) ──


def _latch(**overrides: Any) -> ApifyBudgetLatch:
    fields: dict[str, Any] = {
        "tripped_at": T0,
        "reason": "orphaned_start",
        "estimator_version": "e1",
    }
    fields.update(overrides)
    return ApifyBudgetLatch.objects.create(**fields)


def test_latch_trip_needs_a_reason() -> None:
    _rejects("apify_latch_reason_nonblank", _latch, reason="")


def test_latch_clear_needs_a_reason_and_follows_the_trip() -> None:
    _rejects("apify_latch_clear_has_reason", _latch, cleared_at=T0)
    _rejects("apify_latch_clear_has_reason", _latch, cleared_reason="owner reset")
    _rejects(
        "apify_latch_cleared_after_trip",
        _latch,
        cleared_at=T0 - timedelta(seconds=1),
        cleared_reason="owner reset",
    )
    site = _site()
    trip = _latch(provider_run=_run(site))
    ApifyBudgetLatch.objects.filter(pk=trip.pk).update(
        cleared_at=T0 + timedelta(hours=1), cleared_reason="estimator_version e2"
    )
    assert not ApifyBudgetLatch.objects.filter(cleared_at__isnull=True).exists()


# ── ApifyBudgetCycle (MS2-D-40) ──


def _cycle(**overrides: Any) -> ApifyBudgetCycle:
    fields: dict[str, Any] = {
        "cycle_start": CYCLE_START,
        "cycle_end": CYCLE_END,
        "allocation_usd": Decimal("11.0000"),
        "account_prepaid_credit_usd": Decimal(19),
        "account_base_price_usd": Decimal(19),
        "account_limit_usd": Decimal(19),
        "account_usage_usd": Decimal("0.09"),
        "account_observed_at": T0,
        "account_data_retention_days": 31,
        "opened_at": T0,
    }
    fields.update(overrides)
    return ApifyBudgetCycle.objects.create(**fields)


def test_cycle_start_is_unique() -> None:
    assert _cycle().account_read_count == 0
    _rejects("cycle_start", _cycle)


def test_cycle_end_after_start() -> None:
    _rejects("apify_cycle_end_after_start", _cycle, cycle_end=CYCLE_START)


def test_cycle_money_rejects_negative_values() -> None:
    for field in (
        "allocation_usd",
        "account_prepaid_credit_usd",
        "account_base_price_usd",
        "account_limit_usd",
        "account_usage_usd",
    ):
        _rejects(f"apify_cycle_{field}_non_negative", _cycle, **{field: Decimal(-1)})


def test_cycle_account_figures_may_be_unobserved() -> None:
    row = _cycle(
        account_prepaid_credit_usd=None,
        account_base_price_usd=None,
        account_limit_usd=None,
        account_usage_usd=None,
        account_observed_at=None,
        account_data_retention_days=None,
    )
    assert row.account_observed_at is None


# ── ApifyCycleDiscovery (MS2-D-32 *Cycle discovery*) ──


def _discovery(**overrides: Any) -> ApifyCycleDiscovery:
    return ApifyCycleDiscovery.objects.create(opened_at=T0, **overrides)


def test_only_one_discovery_row_may_be_open() -> None:
    first = _discovery()
    assert first.read_count == 0
    _rejects("apify_discovery_one_open", _discovery)
    ApifyCycleDiscovery.objects.filter(pk=first.pk).update(
        closed_at=T0 + timedelta(minutes=5),
        cycle_start=CYCLE_START,
        close_reason=CycleDiscoveryCloseReason.DISCOVERED,
    )
    # Closed rows never collide, whatever their reason.
    _discovery(
        closed_at=T0,
        close_reason=CycleDiscoveryCloseReason.OWNER_RESET,
        cycle_start=None,
        reason="owner reset",
    )
    _discovery(closed_at=T0, close_reason=CycleDiscoveryCloseReason.DISCOVERED, cycle_start=T0)
    assert _discovery().closed_at is None


def test_discovery_close_needs_a_valid_reason() -> None:
    _rejects("apify_discovery_closed_with_reason", _discovery, closed_at=T0)
    _rejects(
        "apify_discovery_closed_with_reason",
        _discovery,
        close_reason=CycleDiscoveryCloseReason.OWNER_RESET,
    )
    _rejects("apify_discovery_close_reason_valid", _discovery, closed_at=T0, close_reason="gave_up")


def test_owner_reset_close_records_the_owners_reason() -> None:
    # E4 residual (d): the reset is a spend decision, so its reason is kept.
    _rejects(
        "apify_discovery_owner_reset_has_reason",
        _discovery,
        closed_at=T0,
        close_reason=CycleDiscoveryCloseReason.OWNER_RESET,
    )
    row = _discovery(
        closed_at=T0, close_reason=CycleDiscoveryCloseReason.OWNER_RESET, reason="outage over"
    )
    assert row.reason == "outage over"


def test_discovered_close_records_the_cycle_found() -> None:
    _rejects(
        "apify_discovery_discovered_has_cycle",
        _discovery,
        closed_at=T0,
        close_reason=CycleDiscoveryCloseReason.DISCOVERED,
    )


# ── ApifyLedgerAuthority (MS2-D-45) ──


def _authority(**overrides: Any) -> ApifyLedgerAuthority:
    fields: dict[str, Any] = {
        "cycle_start": CYCLE_START,
        "kind": LedgerAuthorityKind.ORIGIN,
        "ledger_id": "proof",
        "attested_by": "owner",
        "created_at": T0,
    }
    fields.update(overrides)
    return ApifyLedgerAuthority.objects.create(**fields)


def test_authority_cycle_start_is_unique() -> None:
    assert _authority().carried_consumption_usd == 0
    _rejects("cycle_start", _authority, kind=LedgerAuthorityKind.CONTINUED)


def test_authority_vocabulary_and_required_fields() -> None:
    _rejects("apify_authority_kind_valid", _authority, kind="borrowed")
    _rejects("apify_authority_ledger_id_nonblank", _authority, ledger_id="")
    _rejects("apify_authority_origin_attested", _authority, attested_by="")
    _rejects(
        "apify_authority_carried_consumption_usd_non_negative",
        _authority,
        carried_consumption_usd=Decimal(-1),
    )
    # continued is created by rollover, not by an attestation.
    assert _authority(kind=LedgerAuthorityKind.CONTINUED, attested_by="").pk is not None


def test_handoff_row_is_keyed_by_a_unique_record_digest() -> None:
    _rejects("apify_authority_handoff_has_digest", _authority, kind=LedgerAuthorityKind.HANDOFF)
    # Only an imported row has an import key.
    _rejects("apify_authority_handoff_has_digest", _authority, imported_record_digest="c" * 64)
    _authority(kind=LedgerAuthorityKind.HANDOFF, imported_record_digest="d" * 64)
    _rejects(
        "imported_record_digest",
        _authority,
        cycle_start=CYCLE_END,
        kind=LedgerAuthorityKind.HANDOFF,
        imported_record_digest="d" * 64,
    )


def test_imported_handoff_row_can_be_exported_onward_keeping_its_import_key() -> None:
    row = _authority(kind=LedgerAuthorityKind.HANDOFF, imported_record_digest="d" * 64)
    ApifyLedgerAuthority.objects.filter(pk=row.pk).update(
        handed_off_at=T0 + timedelta(days=1),
        handed_off_to="production",
        handoff_record_digest="e" * 64,
        handoff_record={"destination_ledger_id": "production"},
    )
    row.refresh_from_db()
    assert (row.imported_record_digest, row.handoff_record_digest) == ("d" * 64, "e" * 64)


def test_hand_off_binds_destination_and_record_together() -> None:
    row = _authority()
    queryset = ApifyLedgerAuthority.objects.filter(pk=row.pk)
    with transaction.atomic(), pytest.raises(IntegrityError, match="binds_destination"):
        queryset.update(handed_off_at=T0 + timedelta(days=1))
    with transaction.atomic(), pytest.raises(IntegrityError, match="binds_destination"):
        queryset.update(handed_off_at=T0 + timedelta(days=1), handed_off_to="production")
    with transaction.atomic(), pytest.raises(IntegrityError, match="binds_destination"):
        queryset.update(handed_off_to="production", handoff_record_digest="e" * 64)
    with transaction.atomic(), pytest.raises(IntegrityError, match="binds_destination"):
        queryset.update(
            handed_off_at=T0 + timedelta(days=1),
            handed_off_to="production",
            handoff_record_digest="e" * 64,
        )
    # An export record without the hand-off would be evidence of nothing.
    with transaction.atomic(), pytest.raises(IntegrityError, match="binds_destination"):
        queryset.update(handoff_record={"destination_ledger_id": "production"})
    queryset.update(
        handed_off_at=T0 + timedelta(days=1),
        handed_off_to="production",
        handoff_record_digest="e" * 64,
        handoff_record={"destination_ledger_id": "production"},
    )
    row.refresh_from_db()
    assert row.handed_off_to == "production"


# ── Indexes the plan names (E1) ──


def _indexes(table: str) -> dict[str, dict[str, Any]]:
    with connection.cursor() as cursor:
        return connection.introspection.get_constraints(cursor, table)


def test_reservation_indexes_exist() -> None:
    columns = {
        tuple(info["columns"])
        for info in _indexes("apify_spend_reservation").values()
        if info["index"]
    }
    assert {("reserved_at",), ("status",), ("status", "last_charge_at")} <= columns
