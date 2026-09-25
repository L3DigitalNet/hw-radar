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

Three decisions remain open. OQ24 was raised by the 2026-09-24 MS-2 multi-category watch-core
plan and its session-2 owner review; OQ25–OQ29, the other five questions from that review, were
owner-resolved 2026-09-25 and relocated to [`resolved-questions.md`](resolved-questions.md).
OQ31 and OQ32 were raised 2026-09-25 by the source-terms review and the MS-1e harvest.

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
- 2026-09-25 admission records (`docs/research/source-admission/2026-09-25-*.md`); none reached
  `eligible`, so no candidate is ready for F5b:

  | Candidate | Categories | Terms on automated access | Recommendation |
  | --- | --- | --- | --- |
  | ServerPartDeals | drive, RAM, some GPU | prohibits scraping and "any automated use" | `exclude` |
  | B&H Photo Video | GPU, RAM, CPU, drive | prohibits robots/spiders/scrapers "to … monitor the website" | `exclude` |
  | Micro Center | GPU, RAM, CPU, drive | no scraping clause; licence is "personal, non-commercial" only | `permission-required` |
  | ServerMonkey | drive, RAM, CPU (refurbished server parts) | terms and robots.txt unreadable to automated fetches (403) | `permission-required` |
  | SabrePC | GPU, CPU, RAM, drive | terms page renders only in a browser; not read | `permission-required` |

  Quick rejects: Amazon and Best Buy offer official APIs, so an Actor is the wrong tool; Newegg is
  already excluded. `permission-required` means written permission from the merchant (or a
  human read of the terms, for the two unreadable ones) before any record can become `eligible`.

#### My Comments

_(none yet)_

---

### OQ31 — Existing local connectors whose Terms prohibit automated access

**From:** the 2026-09-25 terms review of the MS-1d connectors
([`docs/research/2026-09-25-local-connector-terms-review.md`](research/2026-09-25-local-connector-terms-review.md)).
**Decision needed:** what happens to the disabled local ServerPartDeals and Seagate-recertified
connectors, whose current Terms prohibit the automated collection they perform.

#### Agent notes

- ServerPartDeals Terms (retrieved 2026-09-25): prohibited uses include "(i) to spam, phish,
  pharm, pretext, spider, crawl, or scrape" and "(l) to make any automated use of the Service";
  Section 13 bars "reproduction of product listings, pricing data, or images for competitive
  purposes".
- Seagate website Terms (last updated 2024-07-24): users agree not to "use any robot, spider, site
  search/retrieval application, or other manual or automatic device or process to retrieve, index,
  'data mine,' …" any content, and site use is limited to "personal, non-commercial use".
- goHardDrive and WD: no automated-access clause found (not the same as permission).
- Both connectors are disabled in production and have never run there. Neither was harvested for
  MS-1e on 2026-09-25.
- Consequences if they stay unusable: the SA-004 enable order in `docs/handoff/deployed.md` starts
  with ServerPartDeals, and the MS-1e ratification gate requires a family-grain hit from each of
  five named sources, including both (see OQ32).
- Options: (a) retire both connectors (remove from the registry and enable order; keep code history);
  (b) keep them disabled pending written permission from each merchant; (c) owner accepts the risk
  for a private, low-volume personal tool (not recommended: the clauses are explicit).
  Recommendation: (b) now, falling back to (a) if no permission is sought.

#### My Comments

_(none yet)_

---

### OQ32 — MS-1e ratification gate when two named sources are unusable

**From:** the 2026-09-25 MS-1e harvest and audit-packet preparation
([`docs/evidence/2026-09-25-ms1e-audit-packet.md`](evidence/2026-09-25-ms1e-audit-packet.md)).
**Decision needed:** how the ADR 0019 drive-matcher ratification gate should treat the
per-source floor and corpus size when ServerPartDeals and Seagate cannot be harvested (OQ31).

#### Agent notes

- The accepted gate (`src/hw_radar/matching/eval/report.py`, design §5) needs ≥100 auto-accepts,
  precision ≥99.5%, a family-grain hit from every one of the five sources, a passing audit gate,
  and a green rung-0 suite, all in one full test run.
- With only eBay, goHardDrive, and WD usable, the five-source floor cannot pass by construction.
  The audit packet reports the provisional metrics and the exact shortfall.
- Options: (a) re-scope the floor to the sources that are usable (a design change the owner must
  approve, recorded as an ADR 0019 note); (b) keep the gate unchanged and wait for OQ31 permissions;
  (c) widen the harvest (e.g. more eBay drive queries) to reach the auto-accept minimum.

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
production-source fork left open above. OQ25 (Apify credential and MCP tool scope), OQ26
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
