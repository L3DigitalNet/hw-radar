"""Eligibility: persisted watch requirements and `match | no_match | unknown`
verdicts per (watch, listing) (MS-2 Slice C; MS2-D-07/-08/-09/-20/-29).

A plain package, not a Django app: its models live in the catalog app
(catalog/models/watch.py), like matching/ and acquisition/.

- `requirements` — the single writer of watch requirements (`save_requirement`).

No ADR-0011 scoring artifact is read or written anywhere in this package.
"""

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
    "BasicRequirementSpec",
    "CpuRequirementSpec",
    "DriveRequirementSpec",
    "GpuRequirementSpec",
    "RamRequirementSpec",
    "RequirementError",
    "RequirementSaveResult",
    "RequirementSpec",
    "save_requirement",
]
