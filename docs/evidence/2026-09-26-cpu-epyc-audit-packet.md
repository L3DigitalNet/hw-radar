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
- Evaluated against: `MATCHER_VERSION` 2026.09.2, a clean migrated DB plus `import_refdata`
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

| rule | label | rows | matcher (would-accept run) |
| --- | --- | --- | --- |
| **C1** exact seeded model, bare CPU → `model` grain, `variant: null` | model | 104 | 82 agree; 4 accepted at **variant** grain; 18 missed |
| **C1-withheld** reads as C1, price implausible (cpu-0283, $399 for a 7763) | none | 1 | accepted → disagrees |
| **C2** P-variant (9354P, 9654P …): a distinct 1P SKU with its own OPN | none | 47 | 47 agree (none) |
| **C3** engineering/qualification sample (ES, QS, `-04` OPN suffix) | none | 14 | 14 agree (review, `veto: sample`) |
| **C4** OEM/cloud variant sold "as" a seeded model (7B13, 7J13, 7T83, 7K83 …) | none | 93 | 93 agree (none) |
| **C5** multi-model listing | none | 2 | 2 agree |
| **C6** bundle, motherboard, or configurator/CTO item | none | 10 | 6 agree; **4 accepted** |
| **C7** unseeded EPYC model (7542, 7642, 7452, 9275F, 7D13) | none | 11 | 11 agree |
| **C8** accessory (SP5 carrier frame) | none | 2 | 2 agree |

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
- **C6 is the only rule with a matcher-side precision problem** (see §4).
- **The drive family-grain rule does not apply to CPU.** CPU has no grammar decode (no rung 2),
  and its AcceptancePolicy admits only `catalog_authoritative` aliases at model or variant grain.
  The drive R2 question (is a provisional family attach correct?) cannot arise here.

## 3. Provisional metrics (PROVISIONAL: `claude_draft` labels, not owner-approved)

Production behavior (orchestrator run, `auto_accept=False`): **0 accepts**, 105 review at rung 1,
179 none. The 105 reviews split into 91 `auto_accept_disabled` (would-accepts) and 14
`veto: ["sample"]`.

Would-accept measurement (`auto_accept` forced True for `cpu` inside the test only; policy, guard,
and vetoes unchanged):

| metric | value |
| --- | --- |
| Labels | model 104 (9354: 44, 9654: 12, 7763: 45, 7742: 3); none 180 |
| Outcomes | 91 accept at rung 1 (87 model, 4 variant grain); 14 review; 179 none (no edge) |
| Correct would-accepts (strict grain + target) | **82 / 91** |
| Provisional precision | **90.11 %** |
| False positives: label `none` | 5: cpu-0207, -0248, -0269, -0279 (board bundles), cpu-0283 (C1-withheld) |
| False positives: different model | 0 |
| Right model, wrong grain (variant vs model label) | 4: cpu-0013, -0071, -0149, -0180 |
| False negatives (label model, no accept) | 18 (§4) |
| Model recall (right model, any grain) | 86 / 104 (82.7 %) |
| Reviews by reason | `veto: ["sample"]` 14 (all C3, all correct); no other reason |
| Harness verdicts (drive-shaped, for completeness) | precision INSUFFICIENT_CORPUS (< 100 accepts); audit gate FAIL (all drafts); rollup consistent |

Per seeded model:

| model | labeled model | would-accepted (right model) | missed |
| --- | --- | --- | --- |
| EPYC 9354 | 44 | 31 | 13 (12 "EPYC Genoa 9354", cpu-0082) |
| EPYC 9654 | 12 | 11 | 1 (cpu-0027 "EPYC GENOA 9654") |
| EPYC 7763 | 45 | 41 | 4 ("EPYC Milan 7763") |
| EPYC 7742 | 3 | 3 | 0 |

Vendor-locked handling (10 rows): 9 would-accept at the seeded model (cpu-0008, -0041, -0044,
-0048, -0052, -0156, -0282 at model grain; cpu-0013, -0149 at variant grain). cpu-0166 is missed,
but only because of the "EPYC Milan" codename, not the lock.

Sensitivity (labels unchanged, computed after evaluation):

| owner rule | would-accepts | correct | precision |
| --- | --- | --- | --- |
| as drafted (locked = model; variant grain wrong) | 91 | 82 | 90.11 % |
| variant grain on the right model counts as correct | 91 | 86 | 94.51 % |
| vendor-locked rows excluded | 82 | 75 | 91.46 % |
| both of the above | 82 | 77 | 93.90 % |

Under every rule the 4 board bundles remain false positives.

## 4. Every disagreement (27)

Notation: `grain/outcome@rung → model`. `none/none` means no candidate and no edge.

| id | item | price | title (abridged) | matcher | label | cause |
| --- | --- | --- | --- | --- | --- | --- |
| cpu-0207 | 336073823424 | 3529.00 | Supermicro H12DSi-N6 Motherboard With 2x AMD EPYC 7763 … | model/accept@1 → epyc7763 | none (C6) | **matcher FP**: no bundle/board veto for CPU |
| cpu-0248 | 127877501027 | 6143.00 | same title | model/accept@1 → epyc7763 | none (C6) | same |
| cpu-0269 | 127877603096 | 6143.00 | same title | model/accept@1 → epyc7763 | none (C6) | same |
| cpu-0279 | 206289606775 | 6143.00 | same title | model/accept@1 → epyc7763 | none (C6) | same |
| cpu-0283 | 800713259235 | 399.00 | AMD EPYC 7763 … 280W CPU 100-000000312 | model/accept@1 → epyc7763 | none (C1-withheld, AMBIGUOUS) | price outlier; the title alone is C1 |
| cpu-0013 | 327036584378 | 2297.56 | AMD Dell EPYC 9354 32C … SP5 CPU +NEW+ | variant(new)/accept@1 → epyc9354 | model 9354 (AMBIGUOUS locked) | grain: C1 has no variant rule |
| cpu-0071 | 800679680666 | 1967.54 | Used AMD EPYC 9354 … 100-000000798 … | variant(used)/accept@1 → epyc9354 | model 9354 | grain |
| cpu-0149 | 168355766922 | 950.00 | AMD EPYC 7763 100-000000312 CPU (*locked*) (*Pulled from Cisco UCS …*) | variant(used)/accept@1 → epyc7763 | model 7763 (AMBIGUOUS locked) | grain |
| cpu-0180 | 188858072994 | 2799.00 | New AMD EPYC Milan 7763 … CPU 100-000000312 | variant(new)/accept@1 → epyc7763 | model 7763 | grain (found via the OPN) |
| cpu-0073, -0077, -0087, -0088, -0090, -0092, -0097, -0120, -0121, -0125, -0126, -0127 | (JSONL) | 1980.00–3313.09 | AMD EPYC Genoa 9354 280W 3.25GHz 32-Core … | none/none | model 9354 | **recall**: a codename between "EPYC" and the number yields no candidate |
| cpu-0082 | 168390896730 | 1705.00 | AMD EPYC GENOA SP5 ZEN4 9354 … 100-000000798Open | none/none | model 9354 | codename, and the OPN is fused with "Open" |
| cpu-0027 | 115850432637 | 3150.00 | AMD EPYC GENOA 9654 CPU SP5 … Unlocked | none/none | model 9654 | codename |
| cpu-0166 | 407014509038 | 1096.99 | Dell Locked AMD EPYC Milan 7763 CPU … | none/none | model 7763 (AMBIGUOUS locked) | codename |
| cpu-0226, -0241, -0266 | (JSONL) | 2195.99–2998.00 | AMD EPYC Milan 7763 CPU 64 Cores SP3 … | none/none | model 7763 | codename |

The recall misses are safe (no wrong price history) but cost 18 of 104 positives. Fixing the
codename gap or adding a CPU bundle veto is a matcher change with a `matcher_version` bump. It is
not part of this packet.

## 5. AMBIGUOUS rows (11)

- Vendor-locked, labeled at model grain (10): cpu-0008, cpu-0013, cpu-0041, cpu-0044, cpu-0048,
  cpu-0052, cpu-0149, cpu-0156, cpu-0166, cpu-0282.
- Price outlier withheld to `none` (1): cpu-0283.

## 6. Audit sample (reproducible)

`select_audit_sample(ids, "cpu-epyc-draft-2026-09-26")` → ceil(0.20 × 284) = **57** ids:

`cpu-0123, cpu-0032, cpu-0198, cpu-0052, cpu-0043, cpu-0212, cpu-0273, cpu-0133, cpu-0149,
cpu-0216, cpu-0167, cpu-0119, cpu-0051, cpu-0169, cpu-0026, cpu-0260, cpu-0172, cpu-0175,
cpu-0102, cpu-0071, cpu-0055, cpu-0272, cpu-0129, cpu-0021, cpu-0093, cpu-0137, cpu-0013,
cpu-0234, cpu-0262, cpu-0053, cpu-0199, cpu-0156, cpu-0236, cpu-0033, cpu-0097, cpu-0062,
cpu-0195, cpu-0130, cpu-0176, cpu-0029, cpu-0250, cpu-0064, cpu-0223, cpu-0229, cpu-0251,
cpu-0003, cpu-0085, cpu-0084, cpu-0115, cpu-0268, cpu-0060, cpu-0141, cpu-0069, cpu-0161,
cpu-0011, cpu-0215, cpu-0253`

Every disagreement of the would-accept run (27): `cpu-0013, cpu-0027, cpu-0071, cpu-0073,
cpu-0077, cpu-0082, cpu-0087, cpu-0088, cpu-0090, cpu-0092, cpu-0097, cpu-0120, cpu-0121,
cpu-0125, cpu-0126, cpu-0127, cpu-0149, cpu-0166, cpu-0180, cpu-0207, cpu-0226, cpu-0241,
cpu-0248, cpu-0266, cpu-0269, cpu-0279, cpu-0283`.

The owner audits the union, **80 entries** (cpu-0013, cpu-0071, cpu-0097, and cpu-0149 are in
both sets). The sample depends only on the id set and `corpus_version`. Relabeling under the same
`corpus_version` keeps it; adding rows redraws it. Under production behavior (`auto_accept=False`)
every one of the 104 model labels also disagrees, because nothing accepts. The would-accept
disagreements are the ones that decide the flip.

## 7. Owner action

> **Audit:** the 80 ids in §6 and rules C1–C8 (§2). For each audited line in
> `docs/evidence/2026-09-26-cpu-epyc-draft-corpus.jsonl`, set `label.audit_status` to
> `owner_confirmed` when the draft stands, or `owner_corrected` when you change `expected_grain`
> or `expected_target`. Then make `audit_rollup` in the `.meta.json` equal the new per-status
> counts; a stale rollup fails the audit gate.
>
> **Decide:** (1) the vendor-locked rule: count locked parts as the seeded model, or exclude them
> from the corpus; (2) whether C1 rows with a title condition word are variant-grain labels
> (§2); (3) whether the board-bundle false positives (cpu-0207/-0248/-0269/-0279) and the codename
> recall gap need a matcher fix and `matcher_version` bump before any flip.
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
