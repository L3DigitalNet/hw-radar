# CPU (AMD EPYC) draft corpus: owner audit packet (2026-09-26)

This packet prepares the first F6 cell ratification: **eBay Browse × CPU × a narrow AMD EPYC
set**. It serves owner gate **R4** (MS2-D-05): CPU ships with `auto_accept=False`, and only an
owner-ratified category corpus may flip it. It stops before the owner audit. Every label is a
Claude draft (`audit_status: claude_draft`). Nothing here ratifies or admits anything.

- Corpus: [`2026-09-26-cpu-epyc-draft-corpus.jsonl`](2026-09-26-cpu-epyc-draft-corpus.jsonl)
  (284 entries) and [`2026-09-26-cpu-epyc-draft-corpus.meta.json`](2026-09-26-cpu-epyc-draft-corpus.meta.json).
  Both are byte-identical to the blind files:
  - JSONL `sha256 e51f76ef5e1fe97204a1ea326cd682ff4b65540588f5a0e81db31243bbb59e53`
  - meta `sha256 395628f57d0f41d3d757792b61afd1ece6456b82410be795120d0ddcc6a7eb6d`
- Anti-anchoring: labels were drafted from listing fields and the seed catalog without running
  the matcher; the files were hashed at 2026-09-26T11:14:20Z before evaluation; the hashes show
  the files did not change after hashing — they are not independent proof of authorship timing.
- The committed meta still reads `"matcher_version": "unevaluated"` and has no
  `ratification_sources`, exactly as hashed. The evaluation copy set `matcher_version` to
  `2026.09.2` and `ratification_sources` to `["ebay"]`; labels were not touched.
- Evaluated twice against: `MATCHER_VERSION` 2026.09.2 (the second run after the §3 matcher
  fixes, same version string because 2026.09.2 is unreleased), a clean migrated DB plus `import_refdata`
  (production seeds; CPU seed `src/hw_radar/refdata/seeds/amd-epyc.json`, 4 models: EPYC 9354,
  9654, 7763, 7742).

## 1. Scopes and harvest provenance

`manage.py harvest_corpus --source ebay --category cpu`, `harvested_at` 2026-09-26T11:02:05Z,
EBAY_US, sources disabled (`harvest_corpus` bypasses `SourceConfig.enabled`). `--category cpu`
skips the drive, GPU, and RAM sweeps, so all 284 rows carry `category_hint: "cpu"`, all are USD,
0 malformed. Six Browse calls; staging stays in the git-ignored `.harvest/`.

| scope | eBay category | q | live total | items | complete |
| --- | --- | --- | --- | --- | --- |
| `ebay:cpu:epyc-9354-56088` | 56088 Server CPUs | EPYC 9354 | 16 | 16 | yes |
| `ebay:cpu:epyc-9654-56088` | 56088 | EPYC 9654 | 22 | 22 | yes |
| `ebay:cpu:epyc-7763-56088` | 56088 | EPYC 7763 | 9 | 9 | yes |
| `ebay:cpu:epyc-7742-56088` | 56088 | EPYC 7742 | 13 | 13 | yes |
| `ebay:cpu:epyc-9354-164` | 164 CPUs/Processors | EPYC 9354 | 74 | 74 | yes |
| `ebay:cpu:epyc-7763-164` | 164 | EPYC 7763 | 152 | 152 | yes |

"Complete" is the connector's own verdict: one page, no `next`, integer `total` ≤ distinct item
ids seen. A confirmation pass 20 s later returned the same totals. 286 scope items, 284 distinct
listings (2 items are dual-listed in 56088 and 164). Two scopes were excluded after the live probe:
(164, EPYC 9654) exceeds one page (227–228 > 200), and (164, EPYC 7742) rotates the returned
variation of multi-variation listings. eBay URLs are trimmed to `https://www.ebay.com/itm/<id>`.

## 2. Labeling rules (audit the rules as well as the rows)

Only a bare, retail, seeded model is a positive. Everything else is `none`.

| rule | label | rows | matcher, first run | matcher, after fixes (§3) |
| --- | --- | --- | --- | --- |
| **C1** exact seeded model, bare CPU → `model` grain, `variant: null` | model | 104 | 82 agree; 4 at **variant** grain; 18 missed | 99 agree; 4 at **variant** grain; 1 missed |
| **C1-withheld** reads as C1, price implausible (cpu-0283, $399 for a 7763) | none | 1 | accepted → disagrees | accepted → disagrees |
| **C2** P-variant (9354P, 9654P …): a distinct 1P SKU with its own OPN | none | 47 | 47 agree (none) | 47 agree (none) |
| **C3** engineering/qualification sample (ES, QS, `-04` OPN suffix) | none | 14 | 14 agree (review, `veto: sample`) | 14 agree (review, `veto: sample`) |
| **C4** OEM/cloud variant sold "as" a seeded model (7B13, 7J13, 7T83, 7K83 …) | none | 93 | 93 agree (none) | 93 agree (none) |
| **C5** multi-model listing | none | 2 | 2 agree | 2 agree |
| **C6** bundle, motherboard, or configurator/CTO item | none | 10 | 6 agree; **4 accepted** | 10 agree (4 review, `veto: bundle`) |
| **C7** unseeded EPYC model (7542, 7642, 7452, 9275F, 7D13) | none | 11 | 11 agree | 11 agree |
| **C8** accessory (SP5 carrier frame) | none | 2 | 2 agree | 2 agree |

Rule-audit notes:

- **C1 has no variant rule.** CPU rules keep `variant_on_demand=True`, so a title condition word
  ("+NEW+", "Used", "Pulled from") creates a condition variant. Four C1 rows (cpu-0013, -0071,
  -0149, -0180) hit the right model but land at variant grain. Either C1 gains the drive R1
  clause ("variant grain needs explicit condition evidence") and these labels are corrected, or the
  owner treats the variant as wrong.
- **Vendor-locked parts are C1 + `AMBIGUOUS`.** Ten rows ("Dell Locked", "Lenovo Locked", "LENOVO
  ONLY", "(*locked*)", Dell-branded with the lock state unstated). The matcher has no lock signal,
  so it treats them as the retail model.
- **C1-withheld is a price judgement.** Price is not a matcher input, so no matcher can agree with
  it. It is withheld conservatively, not asserted wrong.
- **The drive family-grain rule does not apply to CPU.** CPU has no grammar decode (no rung 2),
  and its AcceptancePolicy admits only `catalog_authoritative` aliases at model or variant grain.
  The drive R2 question (is a provisional family attach correct?) cannot arise here.

## 3. Matcher changes after the first measurement

The first would-accept run (91 accepts, 82 correct) exposed two matcher defects, and a Codex
review found two more. All four were fixed in `src/hw_radar/matching/rules/cpu.py`, with a
generic `CategoryRules.fold_structured` hook in `categories.py` and `resolver.py` for the fourth.
`MATCHER_VERSION` stays 2026.09.2 (unreleased); its history comment records the change.
**No label, id, or corpus byte was changed:** the re-run used the same hashed JSONL.

1. **Board/bundle veto.** A CPU listing whose reference-masked title makes it a motherboard,
   mainboard, barebone, combo, kit, or bundle, or that bundles N CPUs ("with 2x", "2x CPUs"),
   vetoes as `bundle` and goes to review. "Server", "board", and "workstation" are deliberately
   not markers, because bare-CPU titles say "Server CPU" and "for 7002/7003 Series Boards".
2. **EPYC codenames.** A first-party codename (Naples, Rome, Milan, Milan-X, Genoa, Genoa-X,
   Bergamo, Siena, Turin) between "EPYC" and the number is skipped when forming the name
   candidate. The P-variant and multi-model guards still apply ("EPYC Genoa 9354P" is not 9354).
3. **Multi-model ambiguity at rung 0 (Codex N3).** A title naming two EPYC models is recorded
   as `multi_model` and vetoes, so an accepted listing re-observed as "7763 / 7742" goes to review
   instead of inheriting its prior.
4. **Structured-MPN samples (Codex N4).** A sample marking in the merchant's structured MPN
   (`100-000000314-04`) sets the `sample` veto even when the title is retail.

Fixes 3 and 4 do not change any row of this corpus. Every row carries only a title, and no
multi-model title had a prior. They are pinned by `tests/db/test_resolver_cpu_identity.py`.

## 4. Provisional metrics (PROVISIONAL: `claude_draft` labels, not owner-approved)

Production behavior (`auto_accept=False`) accepts nothing. Every would-accept below is a review
with `auto_accept_disabled` in production.

Would-accept measurement (`auto_accept` forced True for `cpu` inside the test only; policy, guard,
and vetoes unchanged), before and after §3:

| metric | first run | after fixes |
| --- | --- | --- |
| Labels | model 104, none 180 | same |
| Outcomes | 91 accept (87 model, 4 variant); 14 review; 179 none | 104 accept (100 model, 4 variant); 18 review; 162 none |
| Correct would-accepts (strict grain + target) | 82 / 91 | **99 / 104** |
| Provisional precision | 90.11 % | **95.19 %** |
| False positives: label `none` | 5 (4 board bundles, cpu-0283) | **1** (cpu-0283, C1-withheld) |
| False positives: different model | 0 | 0 |
| Right model, wrong grain (variant vs model label) | 4 | 4 (cpu-0013, -0071, -0149, -0180) |
| False negatives (label model, no accept) | 18 | **1** (cpu-0082) |
| Model recall (right model, any grain) | 86 / 104 (82.7 %) | 103 / 104 (99.0 %) |
| Reviews by reason | `veto: sample` 14 | `veto: sample` 14; `veto: bundle` 4 |
| Harness verdicts (drive-shaped) | precision INSUFFICIENT_CORPUS; audit FAIL | precision FAIL (≥ 100 accepts clears the floor, but 95.19 % < the drive 99.5 % bar); audit FAIL (all drafts) |

Exactly 21 rows changed outcome between the runs: 17 codename rows went from none to a correct
model accept, and the 4 board bundles went from accept to review.

Per seeded model, after fixes:

| model | labeled model | would-accepted (right model) | missed |
| --- | --- | --- | --- |
| EPYC 9354 | 44 | 43 | cpu-0082 |
| EPYC 9654 | 12 | 12 | — |
| EPYC 7763 | 45 | 45 | — |
| EPYC 7742 | 3 | 3 | — |

Vendor-locked handling (10 rows): all 10 now would-accept at the seeded model (cpu-0013 and
cpu-0149 at variant grain). The matcher has no lock signal.

Sensitivity after fixes (labels unchanged):

| owner rule | would-accepts | correct | precision |
| --- | --- | --- | --- |
| as drafted (locked = model; variant grain wrong) | 104 | 99 | 95.19 % |
| variant grain on the right model counts as correct | 104 | 103 | 99.04 % |
| vendor-locked rows excluded | 94 | 91 | 96.81 % |
| both of the above | 94 | 93 | 98.94 % |

Under every rule cpu-0283 stays the one false positive.

## 5. Every disagreement after the fixes (6)

Notation: `grain/outcome@rung → model`. `none/none` means no candidate and no edge.

| id | item | price | title (abridged) | matcher | label | cause |
| --- | --- | --- | --- | --- | --- | --- |
| cpu-0283 | 800713259235 | 399.00 | AMD EPYC 7763 … 280W CPU 100-000000312 | model/accept@1 → epyc7763 | none (C1-withheld, AMBIGUOUS) | price outlier; the title alone is C1 |
| cpu-0013 | 327036584378 | 2297.56 | AMD Dell EPYC 9354 32C … SP5 CPU +NEW+ | variant(new)/accept@1 → epyc9354 | model 9354 (AMBIGUOUS locked) | grain: C1 has no variant rule |
| cpu-0071 | 800679680666 | 1967.54 | Used AMD EPYC 9354 … 100-000000798 … | variant(used)/accept@1 → epyc9354 | model 9354 | grain |
| cpu-0149 | 168355766922 | 950.00 | AMD EPYC 7763 100-000000312 CPU (*locked*) (*Pulled from Cisco UCS …*) | variant(used)/accept@1 → epyc7763 | model 7763 (AMBIGUOUS locked) | grain |
| cpu-0180 | 188858072994 | 2799.00 | New AMD EPYC Milan 7763 … CPU 100-000000312 | variant(new)/accept@1 → epyc7763 | model 7763 | grain |
| cpu-0082 | 168390896730 | 1705.00 | AMD EPYC GENOA SP5 ZEN4 9354 … 100-000000798Open | none/none | model 9354 | recall: "SP5 ZEN4" sits between the codename and the number, and the OPN is fused with "Open" |

## 6. AMBIGUOUS rows (11)

- Vendor-locked, labeled at model grain (10): cpu-0008, cpu-0013, cpu-0041, cpu-0044, cpu-0048,
  cpu-0052, cpu-0149, cpu-0156, cpu-0166, cpu-0282.
- Price outlier withheld to `none` (1): cpu-0283.

## 7. Audit sample (reproducible)

`select_audit_sample(ids, "cpu-epyc-draft-2026-09-26")` → ceil(0.20 × 284) = **57** ids. The
sample depends only on the id set and `corpus_version`, so the matcher fixes did not change it:

`cpu-0123, cpu-0032, cpu-0198, cpu-0052, cpu-0043, cpu-0212, cpu-0273, cpu-0133, cpu-0149,
cpu-0216, cpu-0167, cpu-0119, cpu-0051, cpu-0169, cpu-0026, cpu-0260, cpu-0172, cpu-0175,
cpu-0102, cpu-0071, cpu-0055, cpu-0272, cpu-0129, cpu-0021, cpu-0093, cpu-0137, cpu-0013,
cpu-0234, cpu-0262, cpu-0053, cpu-0199, cpu-0156, cpu-0236, cpu-0033, cpu-0097, cpu-0062,
cpu-0195, cpu-0130, cpu-0176, cpu-0029, cpu-0250, cpu-0064, cpu-0223, cpu-0229, cpu-0251,
cpu-0003, cpu-0085, cpu-0084, cpu-0115, cpu-0268, cpu-0060, cpu-0141, cpu-0069, cpu-0161,
cpu-0011, cpu-0215, cpu-0253`

Every disagreement after the fixes (6): `cpu-0013, cpu-0071, cpu-0082, cpu-0149, cpu-0180,
cpu-0283`.

The owner audits the union, **60 entries** (cpu-0013, cpu-0071, and cpu-0149 are in both sets):

`cpu-0003, cpu-0011, cpu-0013, cpu-0021, cpu-0026, cpu-0029, cpu-0032, cpu-0033, cpu-0043,
cpu-0051, cpu-0052, cpu-0053, cpu-0055, cpu-0060, cpu-0062, cpu-0064, cpu-0069, cpu-0071,
cpu-0082, cpu-0084, cpu-0085, cpu-0093, cpu-0097, cpu-0102, cpu-0115, cpu-0119, cpu-0123,
cpu-0129, cpu-0130, cpu-0133, cpu-0137, cpu-0141, cpu-0149, cpu-0156, cpu-0161, cpu-0167,
cpu-0169, cpu-0172, cpu-0175, cpu-0176, cpu-0180, cpu-0195, cpu-0198, cpu-0199, cpu-0212,
cpu-0215, cpu-0216, cpu-0223, cpu-0229, cpu-0234, cpu-0236, cpu-0250, cpu-0251, cpu-0253,
cpu-0260, cpu-0262, cpu-0268, cpu-0272, cpu-0273, cpu-0283`

Optional: the 21 rows whose outcome the fixes changed (§4) now agree with their labels. They
are worth a glance because a label error there would be hidden by the agreement.

## 8. Owner action

> **Audit:** the 60 ids in §7 and rules C1–C8 (§2). For each audited line in
> `docs/evidence/2026-09-26-cpu-epyc-draft-corpus.jsonl`, set `label.audit_status` to
> `owner_confirmed` when the draft stands, or `owner_corrected` when you change `expected_grain`
> or `expected_target`. Then make `audit_rollup` in the `.meta.json` equal the new per-status
> counts; a stale rollup fails the audit gate.
>
> **Decide:** (1) the vendor-locked rule: count locked parts as the seeded model, or exclude them
> from the corpus; (2) whether C1 rows with a title condition word are variant-grain labels
> (§2); (3) whether the §3 matcher fixes are acceptable as landed.
>
> **Ratify:** a reviewed code change that sets `auto_accept=True` in `_cpu_rules`
> (`src/hw_radar/matching/categories.py`), made only on audited evidence. Collection comes later:
> per-cell admission of `(ebay, cpu)` in `src/hw_radar/acquisition/admission.py` after the live
> SA-004 checklist. Neither step is taken here.

Reproduce the measurement (the corpus copy needs `matcher_version` `2026.09.2` and
`ratification_sources: ["ebay"]` in its sibling `.meta.json`):

```bash
HW_RADAR_CATEGORY_WOULD_ACCEPT=1 \
HW_RADAR_MS1E_CORPUS=<dir>/corpus.jsonl HW_RADAR_MS1E_REPORT=<dir>/report.json \
uv run pytest tests/db/test_ratification_corpus.py -k test_category_would_accept_measurement -s
```
