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

Six decisions remain open, raised by the 2026-09-24 MS-2 multi-category watch-core plan
and its session-2 owner review.

### OQ24 — Production Actor-backed merchant source admission

**From:** the MS-2 plan (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`,
MS2-D-44, task F5b). **Decision needed:** which merchant source, if any, Hardware Radar may
collect through its own private Apify Actor in production, decided per candidate from a
source-admission record (`docs/research/source-admission/`) with a recommendation of
`eligible | permission-required | exclude`.

#### Agent notes

- Narrowed 2026-09-24 (owner split). The first Actor proof no longer needs this answer: it uses
  a controlled synthetic source, recorded in
  [`resolved-questions.md`](resolved-questions.md#oq24-part-a--first-actor-proof-uses-a-controlled-synthetic-source).
- Newegg is excluded: its Terms of Use prohibit automated access/scraping "for any purpose"
  (retrieved 2026-09-24).
- Other candidates (B&H, ServerPartDeals, refurbished server-parts sellers) each need an
  admission record first. Robots permission is not contractual permission, and the absence of an
  obvious prohibition is not permission. A source needing residential proxies, proxy rotation,
  CAPTCHA solving, paid unblockers, or paid third-party Actors fails admission. Bounded-retention
  sources also need a verified per-run storage expiry (plan risk R20).
- Existing local connectors are not grandfathered into an Actor path; a conflict found for an
  existing disabled connector is recorded separately.

#### My Comments

_(none yet)_

---

### OQ25 — Hardware Radar Apify credential and MCP tool scope

**From:** MS-2 session-2 review (R24, R25). **Decision needed:** provision a Hardware
Radar-scoped Apify API token (proposed OpenBao path `secret/apps/hw-radar/apify`, env
`HW_RADAR_APIFY_TOKEN`) and decide whether/how to widen the project's `.mcp.json` beyond
the current 4 anonymous read-only Apify tools to a spend-capable tool such as `call-actor`.
Hardware Radar has no Apify token of its own today; the only existing token belongs to the
apify-actors agent namespace.

#### Agent notes

- Recommendation (from the plan): provision the token at the proposed path/env now; widen
  `.mcp.json` only after OQ26–OQ28 admit at least one live paid Actor run, since a
  spend-capable MCP tool without an admitted use is unnecessary exposure.

#### My Comments

_(none yet)_

---

### OQ26 — External-liability bound for the shared Apify account

**From:** MS-2 session-2 review (R33). **Decision needed:** set an owner-supplied external-
liability bound for the shared Apify account. Live paid admission — including the Slice D
synthetic proof run — is denied while this is unset (MS2-D-40 check 2 fails closed).

#### Agent notes

- Recommendation (from the plan): set a conservative bound now so the synthetic proof run
  can proceed; the bound can be tightened later without a plan revision.

#### My Comments

_(none yet)_

---

### OQ27 — Retention class for non-first-party reference data

**From:** MS-2 session-2 review (R32). **Decision needed:** decide the retention class for
non-first-party reference data (e.g., the three Micron RAM PDFs hosted on third-party
domains). Non-first-party seeds are refused until this is decided.

#### Agent notes

- Recommendation (from the plan): treat non-first-party reference data as a bounded-
  retention class pending a source-admission-style record, consistent with OQ24's
  bounded-retention handling.

#### My Comments

_(none yet)_

---

### OQ28 — Can MS-2 exit on the synthetic proof alone?

**From:** MS-2 session-2 review (R31). **Decision needed:** whether MS-2 may exit on the
Slice D synthetic proof run alone, with the Actor-backed merchant pilot (task F5b) left
waiting on OQ24's production-source fork, or whether F5b must land first.

#### Agent notes

- Recommendation (from the plan): allow MS-2 to exit on the synthetic proof; F5b is
  gated on OQ24 regardless and should not block the milestone.

#### My Comments

_(none yet)_

---

### OQ29 — Operator allowance size

**From:** MS-2 session-2 review (R36). **Decision needed (optional/advisory):** the size of
the operator allowance (build + inspection reservation class) inside the $20/month ceiling.

#### Agent notes

- Recommendation (from the plan): a small fixed advisory allowance (e.g., low single-digit
  dollars) is sufficient for build/inspection use observed so far; revisit if F5a
  measurements show otherwise.

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
[`resolved-questions.md`](resolved-questions.md#oq22--retention-class-and-expires_at-policy-for-resolver-learned-listing_derived-productalias-rows). OQ23 (Apify base fee vs the
$20/month ceiling) was owner-resolved 2026-09-24 and relocated to
[`resolved-questions.md`](resolved-questions.md#oq23--apify-paid-plan-base-fee-vs-the-20month-hardware-radar-ceiling);
OQ24 was split the same day, with its first-proof half relocated there and only the
production-source fork left open above.

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
