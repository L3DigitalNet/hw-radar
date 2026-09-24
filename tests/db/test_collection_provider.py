"""MS2-D-10/11 DB proof: provider evidence is recorded, and only complete runs delist.

The negative cases use a remote-shaped fake provider whose DelistScope *claims*
complete=True, which is exactly the lie the completeness gate exists to refuse:
without the gate, each of those runs would mark `k-old` ABSENT_FROM_SWEEP. The
complete-run positive control proves the same fixture does delist when the gate
allows it, so a green negative case cannot come from a dead delist stage.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from django.utils import timezone

from hw_radar.acquisition.contracts import (
    DelistScope,
    NullResolver,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    RawItem,
)
from hw_radar.acquisition.pipeline import run_collection, run_source
from hw_radar.catalog.models import (
    DelistReason,
    Listing,
    ProviderKind,
    RunCompleteness,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScraperRun,
    SourceConfig,
    SourceLaneState,
)

# transaction=True: run_source writes from sync_to_async threads.
# serialized_rollback=True: truncation would otherwise delete the migration-0005
# seed rows (the `demo` SourceSite/SourceConfig) for every later test.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

ABSENCE_GRACE = timedelta(hours=1)
# Far beyond ABSENCE_GRACE, so `k-old` is a delist candidate on every path the
# gate lets through; a failing negative case therefore means the gate leaked.
LONG_AGO = timedelta(days=30)
# An established continuity run, well over ABSENCE_GRACE old: on its own it
# would entitle a truncated local sweep to stale-delist `k-old`.
OLD_CONTINUITY = timedelta(days=2)

NON_COMPLETE = [
    RunCompleteness.TRUNCATED,
    RunCompleteness.PARTIAL_FAILURE,
    RunCompleteness.FAILED,
]


def _parsed(key: str) -> ParsedListing:
    return ParsedListing(
        source_listing_key=key,
        url=f"https://demo.invalid/{key}",
        title="Demo 8TB",
        price=Decimal("99.99"),
        raw_url="https://demo.invalid/sweep",
    )


def _batch(source: str) -> RawBatch:
    # Fresh timestamp per call: observed_at is half of OfferSnapshot's composite PK.
    return RawBatch(
        source=source,
        fetched_at=datetime.now(UTC),
        items=[RawItem(url="https://demo.invalid/sweep", payload_json={"sku": "x"})],
    )


class _LocalAdapter:
    """Local SourceAdapter parsing a fixed key set, with no DelistDetector capability."""

    name = "fake-local"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self.last_fetched_at: datetime | None = None

    async def fetch(self) -> RawBatch:
        batch = _batch(self.name)
        self.last_fetched_at = batch.fetched_at
        return batch

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [_parsed(key) for key in self._keys]


def test_run_source_records_local_provider_evidence() -> None:
    run, _ = asyncio.run(run_source(_LocalAdapter(["k-1"]), NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["provider"] == {
        "evidence_version": 1,
        "provider_kind": "local",
        "provider_key": "local",
        "completeness": "truncated",
        "completeness_reason": "completeness_not_asserted",
        "stale_absence_eligible": True,
    }
    assert run.detail_json["listings_delisted"] == 0
    assert run.detail_json["resolver_errors"] == 0
    assert sum(cast(dict[str, int], run.detail_json["grain_counts"]).values()) == 1
    assert run.detail_json["body_bytes"] == 0


class _IncompleteSweepAdapter(_LocalAdapter):
    """Mirrors the eBay truncated-page shape: an honest complete=False scope."""

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        return DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=False,
            absence_grace=ABSENCE_GRACE,
        )


class FakeRemoteProvider:
    """Remote-shaped CollectionProvider whose scope always claims a complete sweep."""

    provider_key = "fake-actor"
    run_kind = RunKind.FULL
    expects_json = True

    def __init__(
        self,
        *,
        provider_kind: ProviderKind,
        site_key: str,
        completeness: RunCompleteness,
        scope_complete: bool = True,
        absence_grace: timedelta = ABSENCE_GRACE,
    ) -> None:
        self.provider_kind = provider_kind
        self.site_key = site_key
        self._completeness = completeness
        self._scope_complete = scope_complete
        self._absence_grace = absence_grace

    async def fetch(self) -> RawBatch:
        return _batch(self.provider_key)

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [_parsed("k-new")]

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        return DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=self._scope_complete,
            absence_grace=self._absence_grace,
        )

    def run_evidence(
        self,
        batch: RawBatch,
        parsed: list[ParsedListing],
        scope: DelistScope | None,
        *,
        run_kind: RunKind,
    ) -> ProviderRunEvidence:
        return ProviderRunEvidence(
            provider_kind=self.provider_kind,
            provider_key=self.provider_key,
            completeness=self._completeness,
            completeness_reason=f"fake_{self._completeness}",
            stale_absence_eligible=False,
        )


def _full_lane() -> SourceLaneState:
    return SourceConfig.objects.get(source_site__normalized_name="demo").lane_state(
        SchedulingLane.FULL
    )


def _set_continuous_since(value: datetime | None) -> None:
    lane = _full_lane()
    lane.continuous_since = value
    lane.save(update_fields=["continuous_since"])


def _seed_stale_k_old(*, continuous_since: datetime | None) -> None:
    """Persist an active `k-old` on demo, unseen for LONG_AGO, then set lane continuity."""
    run, _ = asyncio.run(run_source(_LocalAdapter(["k-old"]), NullResolver()))
    assert run.status == RunStatus.SUCCESS
    Listing.objects.filter(source_listing_key="k-old").update(last_seen=timezone.now() - LONG_AGO)
    _set_continuous_since(continuous_since)


def _k_old() -> Listing:
    return Listing.objects.get(source_site__normalized_name="demo", source_listing_key="k-old")


def _run_remote(completeness: RunCompleteness) -> ScraperRun:
    provider = FakeRemoteProvider(
        provider_kind=ProviderKind.APIFY, site_key="demo", completeness=completeness
    )
    run, _ = asyncio.run(run_collection(provider, NullResolver()))
    # The run itself succeeds: completeness is about what it proves, not whether
    # ingestion worked, so `k-new` is persisted either way.
    assert run.status == RunStatus.SUCCESS
    return run


def _assert_k_old_survived(run: ScraperRun) -> None:
    assert run.detail_json["listings_delisted"] == 0
    k_old = _k_old()
    assert k_old.delisted_at is None
    assert k_old.delist_reason == ""


def test_truncated_remote_run_claiming_complete_scope_cannot_delist() -> None:
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    run = _run_remote(RunCompleteness.TRUNCATED)
    _assert_k_old_survived(run)
    provider_evidence = cast(dict[str, object], run.detail_json["provider"])
    assert provider_evidence["provider_kind"] == "apify"
    assert provider_evidence["completeness"] == "truncated"


@pytest.mark.parametrize("completeness", [RunCompleteness.PARTIAL_FAILURE, RunCompleteness.FAILED])
def test_partial_failure_and_failed_remote_runs_cannot_delist(
    completeness: RunCompleteness,
) -> None:
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    _assert_k_old_survived(_run_remote(completeness))


@pytest.mark.parametrize("completeness", NON_COMPLETE)
def test_truncated_remote_run_does_not_advance_continuity(
    completeness: RunCompleteness,
) -> None:
    # Truncated per the plan; partial_failure and failed are held to the same rule.
    _seed_stale_k_old(continuous_since=None)
    _run_remote(completeness)
    assert _full_lane().continuous_since is None


@pytest.mark.parametrize("completeness", NON_COMPLETE)
def test_ineligible_run_breaks_existing_continuity(completeness: RunCompleteness) -> None:
    # Plan-review F-04: leaving an old continuous_since in place would let the
    # ineligible run's SUCCESS row read as continuous polling to the next sweep.
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    _run_remote(completeness)
    assert _full_lane().continuous_since is None


def test_local_after_prolonged_truncated_remote_cannot_mass_delist() -> None:
    # F-04's end-to-end sequence: continuity established long ago, then a spell
    # of truncated remote runs, then a truncated local sweep. Without the break,
    # the local sweep would inherit OLD_CONTINUITY (> ABSENCE_GRACE) and stale-
    # delist `k-old`, although the remote runs never proved the lane was swept.
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    for _ in range(3):
        _assert_k_old_survived(_run_remote(RunCompleteness.TRUNCATED))

    adapter = _IncompleteSweepAdapter(["k-new"])
    run, _ = asyncio.run(run_source(adapter, NullResolver()))

    assert run.status == RunStatus.SUCCESS
    _assert_k_old_survived(run)
    assert _full_lane().continuous_since == adapter.last_fetched_at


def test_complete_remote_run_delists_absent_listing() -> None:
    # Positive control: the same fixture and the same complete=True scope DO
    # delist once the evidence says complete, so the negative cases above are
    # green because of the gate, not because the delist stage never ran.
    _seed_stale_k_old(continuous_since=None)
    run = _run_remote(RunCompleteness.COMPLETE)
    assert run.detail_json["listings_delisted"] == 1
    k_old = _k_old()
    assert k_old.delisted_at is not None
    assert k_old.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    assert _full_lane().continuous_since is not None


def test_complete_remote_evidence_with_incomplete_scope_cannot_stale_delist() -> None:
    # ADR 0021 gap (ledger 7): a remote provider can report COMPLETE evidence
    # while its own DelistScope still says complete=False — e.g. a partial
    # page mislabeled by the provider. A short grace makes `k-old` a stale-
    # absence candidate immediately, so if the gate let COMPLETE evidence
    # override an incomplete scope, this run would delist `k-old` on the spot.
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    provider = FakeRemoteProvider(
        provider_kind=ProviderKind.APIFY,
        site_key="demo",
        completeness=RunCompleteness.COMPLETE,
        scope_complete=False,
        absence_grace=timedelta(seconds=1),
    )
    run, _ = asyncio.run(run_collection(provider, NullResolver()))
    assert run.status == RunStatus.SUCCESS
    _assert_k_old_survived(run)


def test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist() -> (
    None
):
    # Ledger 7: counts_toward_sweep_continuity, not just gate_delist_scope, must
    # refuse a remote run whose COMPLETE evidence contradicts its own incomplete
    # scope. Otherwise a chain of such runs (each individually blocked from
    # delisting `k-old` by the gate above) would still keep continuous_since
    # alive, and a later local truncated sweep could inherit that unearned
    # continuity and stale-delist `k-old` on its own short grace.
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    for _ in range(3):
        provider = FakeRemoteProvider(
            provider_kind=ProviderKind.APIFY,
            site_key="demo",
            completeness=RunCompleteness.COMPLETE,
            scope_complete=False,
        )
        run, _ = asyncio.run(run_collection(provider, NullResolver()))
        assert run.status == RunStatus.SUCCESS
        _assert_k_old_survived(run)
    # The first ineligible run already breaks the old (unearned) continuity.
    assert _full_lane().continuous_since is None

    adapter = _IncompleteSweepAdapter(["k-new"])
    run, _ = asyncio.run(run_source(adapter, NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["listings_delisted"] == 0
    _assert_k_old_survived(run)
    # The local sweep restarts continuity at its own fetch time rather than
    # inheriting anything from the remote runs.
    assert _full_lane().continuous_since == adapter.last_fetched_at


def test_local_incomplete_scope_keeps_stale_absence_path() -> None:
    # Mirrors test_source_ebay.py's truncated-sweep case: a local complete=False
    # scope still stale-delists once the listing has been unseen for the grace
    # and the lane has been polling continuously across it.
    _seed_stale_k_old(continuous_since=timezone.now() - OLD_CONTINUITY)
    run, _ = asyncio.run(run_source(_IncompleteSweepAdapter(["k-new"]), NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["listings_delisted"] == 1
    assert _k_old().delist_reason == DelistReason.ABSENT_STALE
