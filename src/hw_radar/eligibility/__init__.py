"""Eligibility: persisted watch requirements and `match | no_match | unknown`
verdicts per (watch, listing) (MS-2 Slice C; MS2-D-07/-08/-09/-20/-29).

A plain package, not a Django app: its models live in the catalog app
(catalog/models/watch.py), like matching/ and acquisition/.

- `requirements` — the single writer of watch requirements (`save_requirement`).
- `evaluate` — the evaluator and the frozen interface the ingestion pipeline
  calls (`evaluate_listing`, `ListingEvaluator`, `WatchEvaluator`,
  `catalog_inputs`); see that module's docstring for the contract.

No ADR-0011 scoring artifact is read or written anywhere in this package.
"""

from hw_radar.eligibility.evaluate import (
    CATALOG_INPUTS_VERSION,
    EVALUATOR_VERSION,
    CatalogInputs,
    InputBinding,
    ListingEvaluationResult,
    ListingEvaluator,
    WatchEvaluator,
    catalog_fingerprint,
    catalog_inputs,
    evaluate_listing,
    is_current,
    live_binding,
    stored_binding,
)
from hw_radar.eligibility.requirements import (
    BasicRequirementSpec,
    CpuRequirementSpec,
    DriveRequirementSpec,
    GpuRequirementSpec,
    RamRequirementSpec,
    RequirementError,
    RequirementSaveResult,
    RequirementSpec,
    save_requirement,
)

__all__ = [
    "CATALOG_INPUTS_VERSION",
    "EVALUATOR_VERSION",
    "BasicRequirementSpec",
    "CatalogInputs",
    "CpuRequirementSpec",
    "DriveRequirementSpec",
    "GpuRequirementSpec",
    "InputBinding",
    "ListingEvaluationResult",
    "ListingEvaluator",
    "RamRequirementSpec",
    "RequirementError",
    "RequirementSaveResult",
    "RequirementSpec",
    "WatchEvaluator",
    "catalog_fingerprint",
    "catalog_inputs",
    "evaluate_listing",
    "is_current",
    "live_binding",
    "save_requirement",
    "stored_binding",
]
