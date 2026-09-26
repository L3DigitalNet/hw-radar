# MS-1e expanded drive corpus: owner audit packet (2026-09-26)

Status (updated 2026-09-26, matcher `2026.09.3`): **owner-audited; composite FAIL on one open
owner question.** The owner ruled on Q1–Q8 and delegated the stratified audit (§7). Measured
precision is 366/366 with every source floor met and rung-0 green; the audit gate fails only
because 7 sampled rows name legacy or rebranded family lines, a question the rulings do not
settle ([OQ33](../open-questions.md#oq33--legacy-and-rebranded-drive-family-names-in-corpus-labels)).
ADR 0019 stays `proposed`. Sections 1–6 below are the pre-ruling packet, kept as the record.

Files:

- Corpus: [`corpus.jsonl`](../../tests/fixtures/matching_corpus/corpus.jsonl)
  (`corpus_version` `ms1e-2026-09-26`). Moved from `docs/evidence/` on 2026-09-26 (session 9) to the
  path `test_ms1_ratification_gate` reads, so the gate runs on every suite run.
- Manifest: [`corpus.meta.json`](../../tests/fixtures/matching_corpus/corpus.meta.json).
- Earlier packet (125 rows, owner audit applied):
  [`2026-09-25-ms1e-audit-packet.md`](2026-09-25-ms1e-audit-packet.md).

## 1. Provenance and anti-anchoring

- **Harvest order:** harvest first, then blind draft labels, then hash, then
  evaluate.
- **eBay:** 514 new rows from bounded queries derived from the seeded
  refdata.
- **goHardDrive:** 79 new rows from extra listing pages.
- **WD recertified:** the store was re-harvested, so 31 of 75 rows now carry
  the connector's promoted `attrs.mpn`.
- **Blind labeling:** the labeler read only listing fields, the drive seed
  JSON, and the corpus contract. It read no matcher code, tests or reports,
  and it ran no matcher.
- **Hashes:** taken at 2026-09-26T11:39:57Z, after writing and before any
  evaluation.
  - `corpus.jsonl` `80ef29785e3f159a2f8fea9c15f23bd390342e1264d57e4d39aef5d7fc02c2ba`.
    The committed corpus is byte-identical.
  - The original manifest was `7d2a7187…98b8`.
- **Manifest amendment:** after hashing, the committed manifest gained exactly
  one field, `refdata_drive_digest`. It pins the drive seed digest at
  `155da77` (`0f2db1a7…c6ce`). No row or label changed.
- **What the hashes prove:** file integrity after hashing. They are not
  independently verified evidence of authorship timing.

Rows by grain: model 241, family 231, none 196, variant 50.

| Source | Rows | Draft | Owner-confirmed | Owner-corrected |
| --- | --- | --- | --- | --- |
| eBay | 543 | 526 | 4 | 13 |
| goHardDrive | 100 | 94 | 6 | 0 |
| WD recertified | 75 | 62 | 13 | 0 |
| **Total** | **718** | **682** | **23** | **13** |

The 13 owner-corrected rows keep `owner_corrected`. Seven have since been
promoted to model grain and three to variant grain. This follows the owner's
"exact model unset unless independently seeded" clause, because the new
first-party seeds now carry those MPNs. Each row's note records the promotion.
Rows ebay-0002, ebay-0007 and ebay-0019 stay at the owner's family grain,
because their MPNs are not seeded.

## 2. Draft labeling rules (audit the rules as well as the rows)

- **R1 (model):** one seeded MPN appears in the title or `attrs.mpn`. Brand,
  capacity and interface all agree, and no contradicting part token is present.
- **V (variant):** R1 plus an explicit condition word. Condition fields that
  the listing does not state stay unknown.
- **F (family):** the title names a family, and that family is a **seeded**
  family of the stated manufacturer.
- **N (none):** compatibility or third-party phrasing, enclosures, a
  contradicted MPN with no family, or an **unseeded** family.
- **Ambiguous rows:** about 30 row-level overrides record each judgment the
  rules could not make. 41 rows carry an `AMBIGUOUS` note.

**Rule F is stricter than the owner's 2026-09-26 family-grain rule.** That rule
counts a correct family resolution "even if the exact model is not present in
the reference catalog", and it does not require the family itself to be
seeded. Under F, "WD Blue", "IronWolf", "SkyHawk" and "BarraCuda" rows became
`none`. Section 4 lists the rows this affects.

## 3. Measurement (production composite path, canonical refdata)

Command: `tests/db/test_ratification_corpus.py::test_ms1e_corpus_measurement`.
The matcher version is 2026.09.2.

| Stage | Auto-accepts | Correct | Precision |
| --- | --- | --- | --- |
| `afc4459`, before the D1–D4 fixes, seeds `31a8ae2e…` | 396 | 308 | 77.8% |
| `155da77`, after D1–D4, seeds `0f2db1a7…` (pinned) | 369 | 271 | 73.4% |

At `155da77` there are 342 `none` and 7 `review` outcomes.

| Source | Accepts | Correct (draft) | Owner-ratified family-or-better floor |
| --- | --- | --- | --- |
| eBay | 332 | 256 | met |
| goHardDrive | 15 | 7 | **not met** |
| WD recertified | 22 | 8 | **not met** |

Gate verdicts: precision FAIL, source floor FAIL (goHardDrive and WD), audit
FAIL (draft labels), rung-0 composite not run.

**The fixes lowered measured precision without adding a single matcher error.**
D1 moved the WD Ultrastar HC5x0 models from per-series family names to the
family "Ultrastar", which is what the grammar decodes and what the owner audit
uses. Draft labels written against the old seed names still expect the series
names. All 41 rows that went from correct to wrong resolve the same model as
before; only the family name changed.

### The original 125 rows and the owner-called rows

| Subset | `afc4459` accepts / correct | `155da77` accepts / correct |
| --- | --- | --- |
| Original 125 rows | 38 / 21 | 37 / 22 |

| Row | Outcome at `155da77` | Label | Matches label |
| --- | --- | --- | --- |
| ebay-0009 | accept, variant ST1000NX0453 (Exos) | variant | yes |
| ebay-0021 | none | none | yes |
| ghd-0006 | none | none | yes |

## 4. Owner decisions needed (98 disagreements among auto-accepts)

| # | Question | Rows | Orchestrator view |
| --- | --- | --- | --- |
| Q1 | Should the draft labels move from the "Ultrastar DC HC550/HC560/HC580" family names to "Ultrastar"? The owner's own audit, the grammar and the seeds all use "Ultrastar" now. | 63 (see list) | Yes: a mechanical taxonomy mapping, not a per-row judgment. |
| Q2 | Is a family named only by the title, and not seeded (WD Blue, IronWolf, SkyHawk, BarraCuda), a correct family result? | 11 WD Blue + 8 others | Yes under the §4 family rule. Rule F was too strict. |
| Q3 | Do HGST-branded Ultrastar drives resolve to WD / Ultrastar? | 6 goHardDrive | Owner call: WD owns the Ultrastar line, but the listing says HGST. |
| Q4 | Does a lot listing ("Lot of 10 …") carry the drive's identity? | 3 | Owner call: identity holds, but the price is per lot. |
| Q5 | wd-0057, wd-0061 and wd-0066 were owner-confirmed `none` before the connector promoted their MPNs, and before Red Plus and Gold were seeded. Should they be re-audited? | 3 | Re-audit: the new evidence postdates the ruling. |
| Q6 | Should the WD recertified store count as `recert_channel` "factory"? | WD rows | Owner call. |
| Q7 | New Pull / "90%NEW" at variant grain against a model label. | 2 (ebay-0038, 0261) | Owner call. |
| Q8 | Two HC550 rows drafted at family grain, while the matcher accepts the seeded model WUH721818ALE6L4. | 2 (ebay-0376, 0382) | Re-audit the row. |

**Q1 rows (63):** ebay-0018, 0345–0353, 0355–0375, 0378–0381, 0383, 0384, 0386, 0389–0392, 0395, 0401–0408, 0410–0414, 0416–0422.

**Q2 rows (19):**

- wd-0036 and wd-0038 to 0047 (WD Blue)
- ebay-0313, 0315, 0335, 0340, 0342 (IronWolf)
- ebay-0415 (Ultrastar HC580, with a garbled MPN)
- ghd-0070 (SkyHawk)
- ghd-0099 (BarraCuda)

**Q3 rows:** ghd-0040, 0042, 0043, 0044, 0054, 0056.

**Q4 rows:** ebay-0199, 0282, 0288.

**If the owner agrees with Q1 and Q2,** 353 of 369 accepts would be correct
(95.7%). The remaining 16 disagreements are Q3–Q8. That is still below the 99.5% threshold, and the draft rows
would still need owner audit before the goHardDrive and WD source floors can
count.

## 5. Matcher defects found by this corpus and fixed (unreleased, matcher 2026.09.2)

- **D1, family taxonomy:**
  - One family identity per product line.
  - The WD grammar asserts "Red Plus" only for EFPX, EFZX, EFZZ, EFGX and
    EFBX, per the first-party WD Red Plus datasheet.
  - EFAX and EFRX assert no family: the first-party WD Red brief lists EFAX
    as SMR "Red".
- **D2, family contradiction:** a family in the title that contradicts the
  decoded or catalog family goes to review at every rung (for example
  ebay-0337 and ebay-0451).
- **D3, two MPNs:** two distinct drive MPNs in one title go to review (for
  example ebay-0445).
- **D4, compatibility phrasing:** "fit for", "suitable for", 适用于, and a
  bare leading "for" mask the referenced identity. This covers 11 rows.

## 6. Owner action

1. Answer Q1–Q7. Labels change only by owner decision, and each change is
   recorded with its reason.
2. Audit a stratified sample of the draft rows, with at least one row per
   source that the matcher resolves at family grain or finer. Otherwise the
   goHardDrive and WD floors cannot be met.
3. Re-measure against the pinned seed digest. After any seed change,
   re-measure with a new pin.

The disagreement lists come from the `predictions` array in the measurement
report, which carries the prediction and label grain for every row.

Only after that does the composite gate (≥100 accepts, ≥99.5% precision,
audit PASS, rung-0 PASS, source floors met) become decidable. Until then the
result is **FAIL on draft labels** and is not a ratification.

## 7. Owner rulings applied and final measurement (2026-09-26, matcher `2026.09.3`)

**Rulings (owner, 2026-09-26), applied in commit `2f75685`:**

| # | Ruling | Rows |
| --- | --- | --- |
| Q1 | HC550/HC560/HC580 series are family "Ultrastar"; exact models unchanged | 63 packet rows `owner_corrected`; 11 more rows got the same taxonomy with status unchanged |
| Q2 | An explicitly named family with a clear manufacturer and no conflict counts at family grain even when unseeded; no fuzzy inference | 19 packet rows, plus 4 found later (wd-0048, ebay-0332, ebay-0338, ebay-0525; commit `55a7ecf`) |
| Q3 | HGST-branded Ultrastar keeps manufacturer `hgst` | 6 |
| Q4 | Lot/multipack listings stay `none` | 3 confirmed |
| Q5 | wd-0057/0061 → Red Plus model with recertified/factory variant; wd-0066 → family Gold (WD8004FRYZ unseeded) | 3 |
| Q6 | WD recertified store listings carry `recert_channel=factory` (source provenance) | 6 variant rows |
| Q7 | "New Pull" = used (ebay-0038 → variant used); "90%NEW" asserts nothing (ebay-0261 model) | 2 |
| Q8 | WUH721818ALE6L4 + WD orderable 0F38467 = one model | 2 |

ebay-0335 ("Seagate IronWolf ST10000NE0008") is labeled IronWolf Pro, not IronWolf: the title names
the umbrella line and the first-party `NE` designation is IronWolf Pro.

**Stratified audit (commit `4701dae`).** 296 unaudited rows — the reproducible 20% sample, every
baseline disagreement, and every row with a lot, condition, 0F, HGST or WD-store feature — were
labeled by two independent auditors who saw only the listing evidence, the seed index and the owner
rules, never matcher output. Where both agreed, their verdict was applied (275 confirmed, 5 corrected).
The orchestrator adjudicated the 8 split rows (6 confirmed, 3 corrected: ghd-0047 Hitachi Ultrastar
SSD → hgst/Ultrastar, wd-0029 WD_BLACK → Black, wd-0075 "WD Red" → Red). The auditors' rules document
listed family names as if closed, so rows naming WD_BLACK or WD Red were re-judged under Q2's open
reading. **Eight rows stay `claude_draft`** because they raise a new question: legacy or rebranded
family names (ebay-0446 "WD Red" on WD30EFRX, ghd-0085 WD RE, ghd-0062 Enterprise Capacity, ghd-0039
Enterprise Performance, ghd-0033/0038 Constellation ES/ES.2, ghd-0011 Pipeline HD, ebay-0017
Barracuda ES.2) → OQ33. Judgement calls recorded in row notes: misspelled "Baracuda" stays `none`
(no fuzzy inference); a seller's wrong form factor does not defeat an exact MPN (R1 checks brand,
capacity, interface).

Final rollup: `claude_draft` 291, `owner_confirmed` 303, `owner_corrected` 124.

**Matcher changes (2026.09.2 → 2026.09.3)** driven by the rulings and five Codex review rounds:
percentage-qualified "new" and "like-new" assert nothing; "new pull" is used; negated condition
words and recert channels are negative evidence (one 3-token negation window shared with the CPU
lock reader, with a shared registry of negator-owning boilerplate such as "no warranty"); a listing
that denies a condition and asserts none withdraws a stored or source-folded condition; a lot/multipack listing (lot of/lot N/N-pack/qty/Npcs/item-tied Nx,
auction lot numbers excluded, memory kits excluded) reviews at every rung; WD-store listings get a
source-proven factory channel unless the listing asserts or denies another condition; model priors
re-decide on a newly asserted condition; brand-contradicted automated priors re-decide; contradicted variant aliases review; resolver and
eligibility read one offer extraction; review dedup keys on a stable reason fingerprint. The eval
harness now admits grammar-decoder vendor keys (`hgst`) as label keys.

**Measurement** — `tests/db/test_ratification_corpus.py::test_ms1e_corpus_measurement`, production
refdata pinned at `0f2db1a7…c6ce`, plus `tests/db/test_rung0_regression.py` in the same run:

| Metric | Result |
| --- | ---: |
| Rows | 718 |
| Auto-accepts | 366 |
| Correct | 366 |
| Precision | 100.0% (≥ 99.5% PASS) |
| False positives | 0 |
| False negatives (label family/model/variant, outcome none/review) | 192 |
| Reviews | 10 |
| eBay floor | met (132 qualifying) |
| WD recertified floor | met (22) |
| goHardDrive floor | met (9) |
| Refdata pinned | yes |
| Audit gate | FAIL — 7 sampled rows unaudited (OQ33); 0 unaudited disagreements |
| Rung-0 suite | PASS (6/6) |
| Composite | **FAIL (audit only)** |

All 7 open rows resolve to `none`, so no answer to OQ33 can change precision: labeling them either
way passes the audit gate, and at most adds recall misses. The false negatives are recall, not
ratification: 184 are family-grain labels (mostly title-named Exos lines with unseeded MPNs, since
`ST…NM…` alone no longer implies Exos) that the matcher leaves unresolved.
