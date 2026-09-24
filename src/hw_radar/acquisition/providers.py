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
"""

from __future__ import annotations

from dataclasses import replace

from hw_radar.acquisition.contracts import DelistScope, ProviderRunEvidence
from hw_radar.catalog.models import RunCompleteness


def gate_delist_scope(
    scope: DelistScope | None, evidence: ProviderRunEvidence
) -> DelistScope | None:
    """Return the delist scope this run is entitled to act on, or None for none.

    Invariant (ADR 0021 / D5): only a `complete` run may keep `complete=True`;
    remote runs never reach stale absence. Concretely:

    - complete: the scope, unchanged;
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
        return scope
    if evidence.completeness is RunCompleteness.TRUNCATED and evidence.stale_absence_eligible:
        return replace(scope, complete=False)
    # Fail closed: partial_failure, failed, an ineligible truncation, and any
    # completeness value added later all prove nothing about absence.
    return None


def counts_toward_sweep_continuity(evidence: ProviderRunEvidence) -> bool:
    """Whether this FULL run extends the lane's CR-004 polling-continuity window.

    Same invariant as gate_delist_scope — only `complete` may keep
    `complete=True`; remote runs never reach stale absence — applied to the
    continuity clock: true only for a complete run or a stale-absence-eligible
    (local) truncated run. A failed or partial run did not sweep the lane, so
    counting it would let a later truncated sweep treat an outage as polling.
    """
    if evidence.completeness is RunCompleteness.COMPLETE:
        return True
    return evidence.completeness is RunCompleteness.TRUNCATED and evidence.stale_absence_eligible
