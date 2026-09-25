"""MS-2 Slice D task D2 (migration 0021): provider-run, per-scope continuity, and
per-source provider selection, proven at the database rather than by writer
convention.

The rows these constraints guard are written by later D tasks (the start job,
the staged importer, the continuity recorder). Pinning the invariants here means
a writer bug surfaces as an IntegrityError at the write instead of as a
duplicated import, a NULL-scope remote run, or a cross-scope continuity read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction

from hw_radar.catalog.models import (
    AdmissionClass,
    CheapSignal,
    ImportState,
    Listing,
    ProviderKind,
    ProviderRun,
    RetentionClass,
    RunCompleteness,
    RunKind,
    SchedulingLane,
    ScopeSweepContinuity,
    SourceConfig,
    SourceLaneState,
    SourceSite,
    SourceTier,
    SourceType,
    StorageState,
    TruncationReason,
    VolatilityProfile,
)

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
SCOPE = "d2schema:gpu:q1"


# Names are D2-local on purpose (conventions #9): the 0005 seed owns the real
# site keys, and reusing one would collide with the seeded unique row.
def _site(key: str = "d2schema") -> SourceSite:
    return SourceSite.objects.create(name=key, normalized_name=key, source_type=SourceType.OTHER)


def _config(site: SourceSite, **overrides: object) -> SourceConfig:
    fields: dict[str, object] = {
        "source_site": site,
        "tier": SourceTier.T2_SPECIALIST,
        "domain": "example.test",
        "cadence_baseline_s": 3600,
        "cadence_ceiling_s": 900,
    }
    fields.update(overrides)
    return SourceConfig.objects.create(**fields)


def _run(site: SourceSite, **overrides: Any) -> ProviderRun:
    """An admitted, not-yet-started run: the shape the D5 start job writes first."""
    fields: dict[str, Any] = {
        "source_site": site,
        "provider_kind": ProviderKind.APIFY,
        "actor_ref": "owner~hw-radar-synthetic-collector",
        "contract_schema_version": "hw-radar-input/v1",
        "query_scope": {"collectionScope": SCOPE, "maxItems": 50},
        "scope_key": SCOPE,
        "memory_mb": 512,
        "timeout_s": 600,
        "max_items": 50,
        "max_pages": 5,
        "admission_class": AdmissionClass.WATCH_REFRESH,
        "run_kind": RunKind.FULL,
        "admitted_at": T0,
        "storage_cleanup_due_at": T0 + timedelta(hours=24),
    }
    fields.update(overrides)
    return ProviderRun.objects.create(**fields)


def _started(site: SourceSite, run_id: str, **overrides: Any) -> ProviderRun:
    return _run(site, external_run_id=run_id, import_idempotency_key=f"apify:{run_id}", **overrides)


# ── SourceConfig.collection_provider (MS2-D-18) ──


def test_collection_provider_defaults_to_local() -> None:
    assert _config(_site()).collection_provider == ProviderKind.LOCAL


def test_apify_source_may_not_keep_local_only_lanes_on() -> None:
    site = _site()
    with transaction.atomic(), pytest.raises(IntegrityError, match="local_only_lanes"):
        _config(site, collection_provider=ProviderKind.APIFY, heartbeat_enabled=True)
    with transaction.atomic(), pytest.raises(IntegrityError, match="local_only_lanes"):
        _config(
            site,
            collection_provider=ProviderKind.APIFY,
            fast_lane=True,
            volatility_profile=VolatilityProfile.DROP_PRONE,
            cheap_signal=CheapSignal.SHOPIFY_PRODUCTS_JSON,
        )
    # The same lanes stay legal for a local source, and an apify source with both
    # lanes off is legal: the CHECK constrains only the combination.
    _config(site, heartbeat_enabled=True).delete()
    assert _config(site, collection_provider=ProviderKind.APIFY).pk is not None


def test_collection_provider_rejects_unknown_value() -> None:
    with pytest.raises(IntegrityError, match="collection_provider_valid"):
        _config(_site(), collection_provider="zyte")


def test_switching_a_heartbeat_source_to_apify_is_refused_until_lanes_are_off() -> None:
    config = _config(_site(), heartbeat_enabled=True)
    config.collection_provider = ProviderKind.APIFY
    with transaction.atomic(), pytest.raises(IntegrityError, match="local_only_lanes"):
        config.save()
    config.heartbeat_enabled = False
    config.save()
    config.refresh_from_db()
    assert config.collection_provider == ProviderKind.APIFY


# ── ProviderRun identity and idempotency (MS2-D-13) ──


def test_external_run_id_is_unique_per_provider_kind() -> None:
    site = _site()
    _started(site, "run-1")
    with pytest.raises(IntegrityError, match="provider_run_unique_external_run"):
        _run(site, external_run_id="run-1", import_idempotency_key="apify:run-1-other")


def test_import_idempotency_key_is_unique() -> None:
    site = _site()
    _started(site, "run-1")
    with pytest.raises(IntegrityError, match="import_idempotency_key"):
        _run(site, external_run_id="run-2", import_idempotency_key="apify:run-1")


def test_starting_rows_may_share_null_run_id_and_key() -> None:
    # Rows are created at admission, before the start response names the run
    # (MS2-D-33), so any number of starting rows coexist with both still NULL.
    site = _site()
    first = _run(site)
    second = _run(site)
    assert first.external_run_id is None
    assert second.external_run_id is None
    assert first.import_idempotency_key is None
    assert second.import_idempotency_key is None


@pytest.mark.parametrize(
    "fields",
    [
        {"external_run_id": "run-1"},
        {"import_idempotency_key": "apify:run-1"},
    ],
    ids=["run_id_without_key", "key_without_run_id"],
)
def test_idempotency_key_is_set_together_with_run_id(fields: dict[str, str]) -> None:
    with pytest.raises(IntegrityError, match="idempotency_key_with_run_id"):
        _run(_site(), **fields)


def test_provider_run_scope_key_is_required() -> None:
    # Revision 10 (ED-03): every provider_run is an Actor run, and the contract
    # requires collectionScope, so a remote run can never sweep the NULL scope.
    # The empty string is refused too: it would be a NULL scope by another name.
    site = _site()
    with transaction.atomic(), pytest.raises(IntegrityError, match="scope_key"):
        _run(site, scope_key=None)
    with transaction.atomic(), pytest.raises(IntegrityError, match="scope_key_nonblank"):
        _run(site, scope_key="")


# ── ProviderRun state columns (MS2-D-22, -23, -25, -32, -33) ──


def test_admitted_row_starts_with_zero_counters_and_initial_states() -> None:
    run = _run(_site())
    run.refresh_from_db()
    assert run.import_state == ImportState.PENDING
    assert run.storage_state == StorageState.RETAINED
    assert run.remote_status is None
    assert run.completeness == ""
    assert run.truncation_reason == ""
    assert run.import_listing_ids == []
    assert run.stage_detail == {}
    assert run.scraper_run is None
    counters = (
        run.import_attempts,
        run.dataset_read_count,
        run.kv_read_count,
        run.storage_cleanup_attempts,
        run.run_poll_count,
        run.correction_read_count,
    )
    assert counters == (0, 0, 0, 0, 0, 0)


def test_import_listing_ids_hold_bigint_listing_pks() -> None:
    run = _run(_site(), import_listing_ids=[1, 2**40])
    run.refresh_from_db()
    assert run.import_listing_ids == [1, 2**40]


def test_run_kind_is_full_or_probe_only() -> None:
    site = _site()
    _run(site, run_kind=RunKind.PROBE)
    for kind in (RunKind.HEARTBEAT, RunKind.REFERENCE):
        with transaction.atomic(), pytest.raises(IntegrityError, match="run_kind_full_or_probe"):
            _run(site, run_kind=kind)


def test_operator_admission_class_has_no_provider_run() -> None:
    # Operator reservations carry no provider_run (MS2-D-46, E1), so a run row
    # is always admitted as watch_refresh or discovery.
    site = _site()
    _run(site, admission_class=AdmissionClass.DISCOVERY)
    with pytest.raises(IntegrityError, match="admission_class_runtime"):
        _run(site, admission_class=AdmissionClass.OPERATOR)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("provider_kind", "scrapy_cloud", "provider_kind_valid"),
        ("import_state", "imported", "import_state_valid"),
        ("storage_state", "gone", "storage_state_valid"),
        ("completeness", "mostly", "completeness_valid"),
    ],
)
def test_enum_columns_reject_unknown_values(field: str, value: str, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _run(_site(), **{field: value})


def test_truncation_reason_accompanies_truncated_completeness_only() -> None:
    # Mirrors the evidence rule of MS2-D-11: a remote TRUNCATED result must say
    # which cap cut it, and no other completeness may carry a cause.
    site = _site()
    _run(
        site,
        completeness=RunCompleteness.TRUNCATED,
        truncation_reason=TruncationReason.ITEM_LIMIT,
    )
    with transaction.atomic(), pytest.raises(IntegrityError, match="truncation_reason_coherent"):
        _run(site, completeness=RunCompleteness.TRUNCATED)
    with transaction.atomic(), pytest.raises(IntegrityError, match="truncation_reason_coherent"):
        _run(
            site,
            completeness=RunCompleteness.COMPLETE,
            truncation_reason=TruncationReason.PAGE_LIMIT,
        )
    with transaction.atomic(), pytest.raises(IntegrityError, match="truncation_reason_valid"):
        _run(site, completeness=RunCompleteness.TRUNCATED, truncation_reason="cosmic_ray")


# ── Per-scope continuity and NULL-scope watermarks (MS2-D-31, -35, -36) ──


def test_scope_sweep_continuity_is_unique_per_site_and_scope() -> None:
    site = _site()
    other = _site("d2schema-other")
    row = ScopeSweepContinuity.objects.create(source_site=site, collection_scope=SCOPE)
    # All four watermarks start NULL, which every guard reads as "no bound".
    assert (
        row.continuous_since,
        row.last_eligible_sweep_at,
        row.continuity_broken_at,
        row.last_complete_sweep_at,
    ) == (None, None, None, None)
    ScopeSweepContinuity.objects.create(source_site=other, collection_scope=SCOPE)
    ScopeSweepContinuity.objects.create(source_site=site, collection_scope="d2schema:ram:q1")
    with pytest.raises(IntegrityError, match="scope_sweep_continuity_unique_scope"):
        ScopeSweepContinuity.objects.create(source_site=site, collection_scope=SCOPE)


def test_scope_sweep_continuity_never_holds_the_null_scope() -> None:
    # The NULL scope's watermarks live on the FULL lane row (MS2-D-31 rejected
    # alternative (b)); a blank key here would be a second, divergent copy.
    site = _site()
    with transaction.atomic(), pytest.raises(IntegrityError, match="collection_scope"):
        ScopeSweepContinuity.objects.create(source_site=site, collection_scope=None)
    with transaction.atomic(), pytest.raises(IntegrityError, match="scope_nonblank"):
        ScopeSweepContinuity.objects.create(source_site=site, collection_scope="")


@pytest.mark.parametrize(
    "field", ["last_eligible_sweep_at", "continuity_broken_at", "last_complete_sweep_at"]
)
def test_null_scope_watermarks_are_written_only_on_the_full_lane(field: str) -> None:
    config = _config(_site())
    full = config.lane_state(SchedulingLane.FULL)
    heartbeat = config.lane_state(SchedulingLane.HEARTBEAT)
    assert getattr(full, field) is None
    setattr(full, field, T0)
    full.save()
    setattr(heartbeat, field, T0)
    with pytest.raises(IntegrityError, match="scope_watermarks_full_only"):
        heartbeat.save()


def test_new_listing_columns_default_to_null() -> None:
    listing = Listing.objects.create(
        source_site=_site(),
        source_listing_key="k1",
        canonical_url="https://example.test/k1",
        url_hash="h" * 64,
        title_raw="Tesla P40 24GB",
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    listing.refresh_from_db()
    assert listing.collection_scope is None
    assert listing.last_observed_at is None
    assert listing.last_absence_at is None


def test_lane_state_rows_exist_without_scope_watermarks() -> None:
    config = _config(_site())
    full = config.lane_state(SchedulingLane.FULL)
    assert isinstance(full, SourceLaneState)
    assert (
        full.last_eligible_sweep_at,
        full.continuity_broken_at,
        full.last_complete_sweep_at,
    ) == (
        None,
        None,
        None,
    )


def test_rows_render_for_admin_and_logs() -> None:
    site = _site()
    assert str(_run(site)) == f"apify:(not started) [{SCOPE}]"
    assert str(_started(site, "run-9")) == f"apify:run-9 [{SCOPE}]"
    row = ScopeSweepContinuity.objects.create(source_site=site, collection_scope=SCOPE)
    assert str(row) == f"{site.pk}:{SCOPE}"
