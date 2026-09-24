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
  - [How to maintain this document](#how-to-maintain-this-document)

## Open questions

Two decisions remain open, both raised by the 2026-09-24 MS-2 multi-category watch-core plan.

### OQ23 — Apify paid-plan base fee vs the $20/month Hardware Radar ceiling

**From:** the MS-2 plan (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`,
MS2-D-26). **Decision needed:** does an Apify Starter-plan base fee ($19/month) count against
the hard **$20/month** Hardware Radar Apify ceiling set by [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md),
or is it a separate allocation? Live Apify admission stays disabled until this is decided.

#### Agent notes

- Options: (1) the base fee counts against the $20/month ceiling, leaving very little headroom
  for actual Actor-run spend; (2) the base fee is excluded from the ceiling, which is a per-run
  compute/proxy budget only; (3) the owner authorizes a separate allocation for the base fee.
- Plan recommendation (MS2-D-26): the fee counts against the ceiling unless the owner authorizes
  a separate allocation.

#### My Comments

_(none yet)_

---

### OQ24 — Actor-proof source selection

**From:** the MS-2 plan. **Decision needed:** which marketplace(s) serve as the self-owned
private Actor integration proof, given per-source Terms of Use / robots constraints.

#### Agent notes

- Newegg is excluded: its Terms of Use prohibit automated access/scraping "for any purpose"
  (retrieved 2026-09-24).
- Other candidates (B&H, refurbished server-parts sellers) still need a ToS/robots review before
  selection.

#### My Comments

_(none yet)_

---

All five questions raised by the **2026-07-04 spec gap analysis**
(OQ16–OQ20) were owner-resolved 2026-07-04 and relocated to
[`resolved-questions.md`](resolved-questions.md) (OQ17 and OQ20 research-backed). OQ21
(`httpx` dependency, raised by the 2026-07-05 MS-1 brainstorm) was owner-resolved the same
day and recorded directly in `resolved-questions.md`. OQ22 (retention class for
resolver-learned `ProductAlias` rows, raised by the migration-0016 follow-up) was
owner-resolved 2026-09-06 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq22--retention-class-and-expires_at-policy-for-resolver-learned-listing_derived-productalias-rows).

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
