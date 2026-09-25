# Specs And Plans

Last updated: 2026-09-24 (session 2: Slice B complete, Slice C code complete, D-prep D1+D3; MS-2 plan rev 8, review converged)

## MS-2 Plan Review Lineage

- r1 `42deeff0`: REVISION NEEDED, 12 findings, all dispositioned in plan rev 2.
- r2 `537cd698`: REVISION NEEDED, 7 resolved / 5 partial / 3 new, addressed in plan rev 3.
- r3 `f1b156e6`: REVISION NEEDED, 5 resolved / 3 partial / 1 new, addressed in plan rev 4.
- r4 `a4b2e45b`: READY WITH ADVISORIES, no new findings. Converged at rev 4 (session 1).
- r5 `0469e098` (codex): REVISION NEEDED, 5 findings, all accepted, addressed in rev 6.
- r6 `6af5388b` (codex): REVISION NEEDED, 3 partial + 2 new, all accepted, addressed in rev 7.
- r7 `01639c2a` (codex): REVISION NEEDED, 2 partial + 1 new, all accepted, addressed in rev 8.
- r8 `b73b6633` (codex): READY, no new findings. Converged at rev 8 (session 2, Apify budget design).

## Active Design Artifacts

| Artifact | Role | Status |
| --- | --- | --- |
| `docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md` | MS-2 multi-category watch-core implementation plan | Active, revision 8, review converged (see "Review lineage" above). Slice B complete on `dev`. Slice C code complete (C5 close-out this session). D-prep (D1+D3) code complete; no Apify push/build/run yet. Next: Slice D entry-gate review, then D2. |
| `docs/research/2026-09-24-ms2-code-dependency-map.md` | Code dependency map supporting the MS-2 plan | Active; keep alongside the plan |
| `docs/superpowers/specs/2026-09-06-ms2-scoring-design.md` | Advanced HDD/SSD scoring design | Revision 14 owner-accepted; **deferred from immediate milestone sequencing by ADR 0022**; retain for later category-local drive scoring |
| `docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md` | Deferred HDD/SSD scoring-substrate plan | Revision 4 reviewed; **do not execute now**. Rebase schema/migration assumptions before any future owner-authorized activation. |
| `docs/superpowers/specs/2026-07-06-ms1e-validation-corpus-ratification-design.md` | MS-1e validation-corpus + ADR-0019 ratification design | Implemented on dev; §6 owner-gated |
| `docs/superpowers/plans/2026-08-16-ms1e-validation-corpus.md` | MS-1e harvest-tooling implementation plan | Implemented on dev; converged |
| `docs/research/2026-07-05-ms1c-catalog-seed-inputs.md` | MS-1c catalog seed input ledger | Implemented; keep for provenance |
| `docs/superpowers/plans/2026-07-05-ms1c-catalog-seed.md` | MS-1c catalog seed implementation plan | Implemented; keep for provenance |
| `docs/superpowers/specs/2026-07-05-ms1-ingestion-design.md` | MS-1 ingestion design and milestone split | Implemented foundation; retain for provenance/current drive ingestion behavior |
| `docs/superpowers/plans/2026-07-06-ms1d-connectors.md` | MS-1d connectors + heartbeat implementation plan | Implemented (PR #12); keep for provenance |
| `docs/superpowers/plans/2026-07-05-ms1b-matching.md` | MS-1b matching implementation plan | Implemented; keep for provenance |
| `docs/superpowers/plans/2026-07-05-ms1a-substrate.md` | MS-1a ingestion substrate implementation plan | Implemented; keep for provenance |
| `docs/superpowers/plans/2026-07-04-ms0-foundation.md` | MS-0 foundation implementation plan | Implemented; keep for provenance |

## Canonical Repo Docs

- Current strategy ADRs: `docs/adr/adr-0021-hybrid-acquisition-apify.md` and `docs/adr/adr-0022-multi-category-watch-first-v1.md`
- Master spec: `docs/specs/hw-radar-master-spec.md`
- ADR index: `docs/adr/README.md`
- Open questions: `docs/open-questions.md`
- Resolved questions: `docs/resolved-questions.md`
