"""MS-1e validation-corpus evaluation harness (design
`docs/superpowers/specs/2026-07-06-ms1e-validation-corpus-ratification-design.md`).

Three layers, one rule: this package orchestrates the production resolver and
contains NO matching logic of its own (design §2).

- `corpus`   — the versioned on-disk schema, loaders, and audit-sample selection.
- `evaluate` — Approach-A replay of each entry through the real ingest + resolver.
- `report`   — precision, coverage, floors, audit gate, and the composite verdict.

Import the submodules directly; nothing is re-exported here, so the DB-touching
evaluator never gets pulled in by a schema-only (unit-test) import.
"""
