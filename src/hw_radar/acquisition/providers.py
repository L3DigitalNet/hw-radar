"""Collection providers and the completeness gate between a run and the delist stage.

ADR 0021 separates source identity (the SourceSite whose listings a run touches)
from the collection provider (who fetched the data: a local adapter today, a
self-owned Apify Actor from Slice D). This module holds the provider-side policy
the pipeline applies to every run, whoever collected it:

- gate_delist_scope decides what a provider's DelistScope may still prove once
  the run's completeness is known;
- counts_toward_sweep_continuity decides whether the run extends the FULL lane's
  CR-004 polling-continuity window.

Both are pure so their full truth tables are unit-tested
(tests/unit/test_provider_evidence.py) independently of the pipeline.

LocalCollectionProvider wraps an unchanged SourceAdapter so today's collectors
enter the pipeline through the same CollectionProvider seam a remote provider will.
Its evidence mapping is chosen so the gate is the identity on every local path:
the eBay complete/truncated delist behavior and scope-less sources' continuity
are exactly what they were before the seam existed.
"""

from __future__ import annotations

from dataclasses import replace

from hw_radar.acquisition.contracts import (
    DelistDetector,
    DelistScope,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    SourceAdapter,
)
from hw_radar.catalog.models import ProviderKind, RunCompleteness, RunKind

# completeness_reason values a LocalCollectionProvider records. They land in
# ScraperRun.detail_json["provider"], so changing one changes stored run history.
REASON_ADAPTER_SWEEP_COMPLETE = "adapter_sweep_complete"
REASON_ADAPTER_SWEEP_INCOMPLETE = "adapter_sweep_incomplete"
# The adapter has no DelistDetector, or its delist_scope returned None.
REASON_COMPLETENESS_NOT_ASSERTED = "completeness_not_asserted"
# HEARTBEAT/PROBE runs: the delist stage is FULL-only, so no scope is requested.
REASON_ABSENCE_NOT_EVALUATED = "absence_not_evaluated"


def gate_delist_scope(
    scope: DelistScope | None, evidence: ProviderRunEvidence
) -> DelistScope | None:
    """Return the delist scope this run is entitled to act on, or None for none.

    Invariant (ADR 0021 / D5): only a `complete` run may keep `complete=True`;
    remote runs never reach stale absence. Concretely:

    - complete: the scope, unchanged — but only when the scope itself claims
      completeness (`scope.complete`) or the evidence is stale-absence
      eligible (local-only). COMPLETE evidence paired with `scope.complete=False`
      from an ineligible (remote) provider is not a legitimate stale-absence
      claim — a remote provider's own scope already says it did not enumerate
      everything, so trusting the run's COMPLETE label anyway would let a
      remote provider reach the stale-absence path by mislabeling itself;
    - truncated: the scope downgraded to `complete=False` (stale-absence path,
      which still needs the adapter's grace and lane continuity) when the
      evidence is stale-absence eligible — which only a local provider can be —
      otherwise None;
    - partial_failure / failed: None.

    None means the delist stage does nothing for this run. The downgrade matters
    because a provider's DelistScope is its own claim: a remote provider that
    stopped at its item or budget limit may still describe what it saw as a
    complete sweep, and trusting that would mass-delist every listing past the
    limit (reversible via mark_relisted, but IR-002 redaction runs first).
    """
    if scope is None:
        return None
    if evidence.completeness is RunCompleteness.COMPLETE:
        if scope.complete or evidence.stale_absence_eligible:
            return scope
        return None
    if evidence.completeness is RunCompleteness.TRUNCATED and evidence.stale_absence_eligible:
        return replace(scope, complete=False)
    # Fail closed: partial_failure, failed, an ineligible truncation, and any
    # completeness value added later all prove nothing about absence.
    return None


def counts_toward_sweep_continuity(
    evidence: ProviderRunEvidence, scope: DelistScope | None
) -> bool:
    """Whether this FULL run extends the lane's CR-004 polling-continuity window.

    Same invariant as gate_delist_scope — only `complete` may keep
    `complete=True`; remote runs never reach stale absence — applied to the
    continuity clock: true only for a complete run or a stale-absence-eligible
    (local) truncated run. A failed or partial run did not sweep the lane, so
    counting it would let a later truncated sweep treat an outage as polling.

    For a non-local provider, COMPLETE evidence alone is not enough (plan
    review F-04 residual): the run's own DelistScope must also claim
    `complete=True`. gate_delist_scope already refuses to act on a remote
    run whose COMPLETE label contradicts its own incomplete scope; without the
    same check here, a chain of such runs would keep extending
    continuous_since anyway, and a later local truncated sweep could then
    stale-delist on continuity those remote runs never actually proved (ADR
    0021: incomplete remote collection must not indirectly authorize
    absence). Local behavior is unchanged — a local run's own scope already
    determines its stale_absence_eligible / completeness mapping, so `scope`
    is not consulted on that path.
    """
    if evidence.provider_kind is ProviderKind.LOCAL:
        if evidence.completeness is RunCompleteness.COMPLETE:
            return True
        return (
            evidence.completeness is RunCompleteness.TRUNCATED and evidence.stale_absence_eligible
        )
    return (
        evidence.completeness is RunCompleteness.COMPLETE and scope is not None and scope.complete
    )


class LocalCollectionProvider:
    """CollectionProvider over an in-process SourceAdapter, delegating unchanged.

    Evidence mapping (MS2-D-11 "local mapping"): an adapter scope with
    complete=True is COMPLETE; anything else — complete=False, no scope, no
    DelistDetector, or a non-FULL run — is stale-absence-eligible TRUNCATED. That
    keeps the local absence heuristics exactly as they were: truncated local
    sweeps still reach the grace-plus-continuity stale path and still advance
    lane continuity, which scope-less sources have always done.
    """

    provider_key = "local"

    def __init__(self, adapter: SourceAdapter) -> None:
        self.adapter = adapter
        self.provider_kind = ProviderKind.LOCAL
        self.site_key = adapter.site_key
        self.run_kind = adapter.run_kind
        self.expects_json = adapter.expects_json

    async def fetch(self) -> RawBatch:
        return await self.adapter.fetch()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return self.adapter.parse(batch)

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        # DelistDetector stays an optional structural capability, as it was when
        # run_source probed the adapter directly: no adapter changes for the seam.
        if isinstance(self.adapter, DelistDetector):
            return self.adapter.delist_scope(batch, parsed)
        return None

    def run_evidence(
        self,
        batch: RawBatch,
        parsed: list[ParsedListing],
        scope: DelistScope | None,
        *,
        run_kind: RunKind,
    ) -> ProviderRunEvidence:
        completeness = RunCompleteness.TRUNCATED
        if run_kind is not RunKind.FULL:
            reason = REASON_ABSENCE_NOT_EVALUATED
        elif scope is None:
            reason = REASON_COMPLETENESS_NOT_ASSERTED
        elif scope.complete:
            completeness = RunCompleteness.COMPLETE
            reason = REASON_ADAPTER_SWEEP_COMPLETE
        else:
            reason = REASON_ADAPTER_SWEEP_INCOMPLETE
        return ProviderRunEvidence(
            provider_kind=self.provider_kind,
            provider_key=self.provider_key,
            completeness=completeness,
            completeness_reason=reason,
            stale_absence_eligible=True,
        )
