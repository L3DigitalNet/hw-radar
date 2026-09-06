# Open Questions — `hw-radar.md`

## Important Notes

- **Document Handling Rules and Guidelines:** [How to maintain this document](#how-to-maintain-this-document)
- **Terminology:**
  - _open question_ (`OQ#`) is a decision still to be made — the primary unit of this document.
  - _resolved question_ (`RQ#`, already settled) and the original _gaps_ (`gap #`, the twelve spec-audit findings that seeded these questions) are settled provenance and live in the companion file [`resolved-questions.md`](resolved-questions.md).

## Table of Contents

- [Open Questions — `hw-radar.md`](#open-questions--hw-radarmd)
  - [Important Notes](#important-notes)
  - [Table of Contents](#table-of-contents)
  - [Open questions](#open-questions)
    - [OQ22 — Retention class and `expires_at` policy for resolver-learned (`listing_derived`) `ProductAlias` rows](#oq22--retention-class-and-expires_at-policy-for-resolver-learned-listing_derived-productalias-rows)
  - [How to maintain this document](#how-to-maintain-this-document)

## Open questions

One question is open (**OQ22**). All five questions raised by the **2026-07-04 spec gap
analysis** (OQ16–OQ20) were owner-resolved 2026-07-04 and relocated to
[`resolved-questions.md`](resolved-questions.md) (OQ17 and OQ20 research-backed). OQ21
(`httpx` dependency, raised by the 2026-07-05 MS-1 brainstorm) was owner-resolved the same
day and recorded directly in `resolved-questions.md`. The next question opened here takes
the number **OQ23**.

### OQ22 — Retention class and `expires_at` policy for resolver-learned (`listing_derived`) `ProductAlias` rows

**From:** the migration-0016 retention-index follow-up (2026-09-06). **Decision needed:**
`matching/resolver.py`'s `_emit_learned_aliases` creates `ProductAlias` rows with
`retention_class=""`. DR-001 requires every row to carry a class, and the two candidates
conflict: an indefinite class (`merchant_fact` / `manufacturer_reference`) persists a token
extracted from an eBay title past DR-008's six-hour eBay deletion duty and stamps false
provenance, while a bounded class (`ebay_listing_observation`) makes the hourly sweep delete
learned aliases and destroys the self-shrinking review queue that ADR-0019 rule 7 relies on.
Options: (a) a new indefinite class `listing_derived_alias` with explicit provenance
semantics; (b) a bounded class, re-learned on the next observation; (c) treat learned aliases
as `manufacturer_reference` once corroborated by two or more sources. Needed before the
DR-001 CHECK pair can be added to `ProductModel`/`DriveSpec`/`ProductAlias`.

#### Agent notes

Until decided, the identity models carry only the partial `expires_at` index (migration
0016) and not the DR-001 CHECK pair; 45 tests/db fixture sites also omit the class and need
updating when the CHECK lands (see the `ProductModel.Meta` comment and `purge_expired.py`'s
docstring).

#### My Comments

_(owner — pending)_

## How to maintain this document

These rules govern **both** files: this one (open) and its companion [`resolved-questions.md`](resolved-questions.md) (settled).

- Read **[Open questions](#open-questions)** for anything that still needs a call. Everything settled lives in [`resolved-questions.md`](resolved-questions.md) — you should not have to read it to know what's outstanding.
- When a question is settled, move it to [`resolved-questions.md`](resolved-questions.md). If a question is partially settled, move the decided half there and leave a focused open question here covering _only_ the remaining fork.
- Once an ADR is written for a settled question, the resolved decision can be safely removed from `resolved-questions.md` to control its size. The ADR is the canonical record of the decision. (This is why the ADR-backed OQs in [`resolved-questions.md`](resolved-questions.md) are condensed to a one-line pointer + ADR link, while the OQs with no ADR retain their full decided substance there.)

**Rules:**

1. **Open questions first, distilled.** Each open question states _only_ the unresolved decision — not the history behind it. The history lives in `resolved-questions.md` and in the research reports.
2. **When a question is settled, move it to `resolved-questions.md`.** Relocate its substance there (record the decision + any ADR) and remove it from this file. Never leave a settled item in Open questions.
3. **Split partially-settled items.** If a gap is half-decided, move the decided half to `resolved-questions.md` and leave a focused open question here covering _only_ the remaining fork. (This is how the OQs in `resolved-questions.md` were produced from the twelve gaps.)
4. **Two comment layers per open question, kept separate:**
   - `#### Agent notes` — research/reconciliation context, maintained by the assistant.
   - `#### My Comments` — the owner's notes and decisions; **the assistant does not edit this block.** (When an OQ is relocated to `resolved-questions.md`, its owner comments are preserved verbatim.)
5. **Cross-reference by stable ID.** `OQ#` = open question, `RQ#` = resolved question, `gap #` = original gap. ADRs, the spec, and TODO link here by those IDs — keep them stable. The `#oq#` / `#gap#` **anchors** derive from heading _text_, so they survive a move between files — **but a link that names the file (`open-questions.md#oq…`) breaks when the item moves to `resolved-questions.md`; update every referring ADR/TODO/spec/research link to the new file in the same change.** If you must renumber, update the referencing docs in the same change.
6. **Not a log:** Do not append a log of routine maintenance or administrative changes. This is a _decision record_, not a change log. Use the Git history for that and `docs/handoff.md` and/or `TODO.md` where appropriate.
