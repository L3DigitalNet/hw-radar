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
    - [OQ33 — Legacy and rebranded drive family names in corpus labels](#oq33--legacy-and-rebranded-drive-family-names-in-corpus-labels)
    - [OQ34 — CPU auto-accept scope: category-wide or the ratified EPYC family](#oq34--cpu-auto-accept-scope-category-wide-or-the-ratified-epyc-family)
  - [How to maintain this document](#how-to-maintain-this-document)

## Open questions

### OQ33 — Legacy and rebranded drive family names in corpus labels

Raised 2026-09-26 by the MS-1e stratified audit
([packet §7](evidence/2026-09-26-ms1e-expanded-audit-packet.md#7-owner-rulings-applied-and-final-measurement-2026-09-26-matcher-2026093)).
Q2 counts an explicitly named family at family grain, but does not say how to name a line that was
discontinued or renamed. Should a listing naming one be labeled:

1. with the **historical family name** as written (Constellation ES, Enterprise Capacity, WD RE,
   pre-2020 "WD Red" on an EFRX drive now sold as Red Plus);
2. with the **current successor line** (Enterprise Capacity → Exos, WD RE → Gold, EFRX → Red Plus); or
3. **`none`** (legacy names are not family evidence)?

Rows: ebay-0446, ghd-0085, ghd-0062, ghd-0039, ghd-0033, ghd-0038, ghd-0011 (all in the audit sample),
and ebay-0017. It blocks only the MS-1e audit gate. The matcher resolves all eight rows to `none`, so
no answer changes measured precision (366/366).

#### Agent notes

- Option 1 matches Q3's reasoning (a product's historical brand identity is not erased by a later
  owner) and needs no successor mapping. Option 2 needs a first-party rebrand table per line.
  Option 3 is the most conservative, but contradicts Q2 for lines that are unambiguous (Pipeline HD).
- Whatever the answer, the matcher change (if any) is recall-only and can follow separately.

#### My Comments

### OQ34 — CPU auto-accept scope: category-wide or the ratified EPYC family

Raised 2026-09-26 by the CPU owner audit
([packet §9](evidence/2026-09-26-cpu-epyc-audit-packet.md#9-owner-audit-applied-and-final-measurement-2026-09-26-matcher-2026093)).
The owner-audited EPYC corpus passes the identity gate (105/105 would-accepts, audit PASS).
`CategoryRules.auto_accept` is one flag for the whole CPU category, but production refdata also
holds five Intel Xeon models the corpus never measured. Should the flip:

1. enable auto-accept **category-wide** (Xeon exact-alias hits auto-accept on EPYC evidence);
2. be **scoped to the ratified family** (a small code change: auto-accept only aliases whose
   family is on a per-category ratified list, starting with AMD EPYC); or
3. **wait** for a Xeon corpus?

#### Agent notes

- Recommendation: option 2. It ratifies exactly what was measured, keeps Xeon in review, and the
  Intel refdata already carries a TODO about bare-number alias collision risk.
- None of these admits collection: eBay × CPU stays `NOT_ADMITTED` until the per-cell live
  checklist in `docs/handoff/deployed.md` passes, and the F6 watch must set
  `require_vendor_unlocked`.

#### My Comments

OQ24, OQ31, and OQ32 — the production Actor-backed merchant
source fork, the ServerPartDeals/Seagate Terms conflict, and the MS-1e ratification-gate floor —
were owner-resolved 2026-09-26 and relocated to [`resolved-questions.md`](resolved-questions.md).
OQ25–OQ29, five other questions from the same 2026-09-24 MS-2 session-2 review that raised OQ24,
were owner-resolved 2026-09-25 and relocated there earlier. When a new question is raised, add it
here per [How to maintain this document](#how-to-maintain-this-document).

All five questions raised by the **2026-07-04 spec gap analysis**
(OQ16–OQ20) were owner-resolved 2026-07-04 and relocated to
[`resolved-questions.md`](resolved-questions.md) (OQ17 and OQ20 research-backed). OQ21
(`httpx` dependency, raised by the 2026-07-05 MS-1 brainstorm) was owner-resolved the same
day and recorded directly in `resolved-questions.md`. OQ22 (retention class for
resolver-learned `ProductAlias` rows, raised by the migration-0016 follow-up) was
owner-resolved 2026-09-06 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq22--retention-class-and-expires_at-policy-for-resolver-learned-listing_derived-productalias-rows). OQ23 (Apify base fee vs the
$20/month ceiling) was owner-resolved 2026-09-24 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq23--apify-paid-plan-base-fee-vs-the-20month-hardware-radar-ceiling);
OQ24 was split the same day, with its first-proof half relocated there; the production-source
fork was owner-resolved 2026-09-26 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq24--production-actor-backed-merchant-source-admission)
(see above). OQ25 (Apify credential and MCP tool scope), OQ26
(external-liability bound), OQ27 (retention class for non-first-party reference data), OQ28
(MS-2 exit on the synthetic proof alone), and OQ29 (operator allowance size) were
owner-resolved 2026-09-25 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope).

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
