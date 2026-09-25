# MS-1e draft corpus: owner audit packet (2026-09-25)

This packet prepares the ADR 0019 drive-matcher ratification (design
`docs/superpowers/specs/2026-07-06-ms1e-validation-corpus-ratification-design.md`, §6
steps 1 and 2). It stops before step 3, the owner audit. Every label is a Claude draft
(`audit_status: claude_draft`). Nothing here ratifies anything.

- Corpus: [`2026-09-25-ms1e-draft-corpus.jsonl`](2026-09-25-ms1e-draft-corpus.jsonl)
  (125 entries) and its sidecar [`2026-09-25-ms1e-draft-corpus.meta.json`](2026-09-25-ms1e-draft-corpus.meta.json).
  The JSONL is the full record; this packet summarizes it.
- Anti-anchoring: all labels were drafted from title, listing fields, and the seeded catalog
  before the matcher ran over those rows, and each blind file was hashed before evaluation:
  - base blind file (eBay + WD, 104 rows), hashed 2026-09-25T22:36:41Z:
    `sha256 3f99b76b1a141883b24c7ff6bb5f65b8e303e6f0d5e0a384938404fa038b038c`
  - goHardDrive addendum (21 rows, harvested after the base hash), hashed 2026-09-25T22:39:58Z:
    `sha256 1acd6daef3a22da497efb80f1b2dc623021db2fd7b1a21dcbdfc9cdc84026601`
  - The committed JSONL is exactly base + addendum concatenated:
    `sha256 1ffa99b9ded0bdf6d8ccfa831974703f704a1bbedc69418d9d287f87df8cedf3`.
    No label was changed after the matcher ran. Re-evaluating the full corpus left all 104 base
    predictions byte-identical to the base-only run.
- Evaluated against: `matcher_version` 2026.09.1. The catalog was a fresh migrated DB plus
  `manage.py import_refdata` (the production seed: 15 drive models in 3 families).

## 1. Corpus summary

| Item | Value |
| --- | --- |
| Total entries | 125 (all HDD; 0 SSD) |
| eBay | 29 (the rows without `category_hint`, out of 1000 staged; the 971 GPU/RAM rows are out of scope) |
| WD recertified store | 75 (12 more were skipped as malformed by the connector at harvest time) |
| goHardDrive | 21. This is **only the first page of the Desktop Hard Drive category** (harvested after the Scrapy fix `b9e01a1`; it is not a site-wide sample) |
| ServerPartDeals, Seagate recertified | 0, not harvested by design: their Terms prohibit automated access (OQ31) |
| Duplicate ids | 0 among drive rows; 6 among the out-of-scope eBay GPU/RAM rows (not used) |
| Draft labels by grain | model 3, none 122 (family 0, variant 0) |
| Labels flagged `AMBIGUOUS:` | 15 |
| Matcher outcome | 17 accepted (3 at rung 1, model grain; 14 at rung 2, provisional family), 108 unresolved (no edge) |
| Label vs matcher | 111 agree, 14 disagree (all 14 are rung-2 family accepts on a `none` label) |

Harvest provenance: `manage.py harvest_corpus`, 2026-09-25, with all sources disabled
(`harvest_corpus` bypasses `SourceConfig.enabled`). eBay ran 22:16Z, bounded at `--limit 1000`
across the drive, GPU, and RAM query scopes. WD ran 22:18Z, 75 rows. goHardDrive's first run
(22:22Z) returned 0 rows. After the Scrapy fix, the re-run (staging `harvested_at` 22:39Z)
returned 21 rows from category page 1. Staging stays in the git-ignored `.harvest/`. The corpus keeps only E-4 fields.
eBay URLs are cut to `https://www.ebay.com/itm/<id>`, because the harvested query string carries
opaque tracking blobs that the matcher never reads.

### Labeling rules used (audit the rules as well as the rows)

- **R1:** the title's MPN is a seeded `ProductModel` and nothing contradicts it → `model` grain.
  `variant` grain needs explicit condition evidence, and none of the three in-catalog rows has any.
- **R2:** the model is not in the seeded catalog → `none`, following the brief ("must not
  auto-accept"). Where the title names the seeded family `Exos`, the row is flagged
  `AMBIGUOUS: family-attach candidate`. The design's own synthetic oracle
  (`tests/fixtures/matching_corpus/synthetic.jsonl`, `ghd-0001`) treats a rung-2 provisional
  family attach as correct, so R2 conflicts with it. **The owner must pick the rule** (§6).
- **R3:** capacity ranges, lots or multipacks, family-only titles, OEM/FRU-only titles,
  "comparable to" MPNs, and consumer external enclosures → `none`.

## 2. Candidate metrics: PROVISIONAL (draft labels, not owner-approved)

| Metric | Value | Threshold / note |
| --- | --- | --- |
| Auto-accepts (rung 1/2 ACCEPT) | **17** | ≥ 100 required → shortfall **83** |
| Correct auto-accepts | 3 | the 3 rung-1 model hits |
| Precision | **17.65 %** (3/17) | ≥ 99.5 %; not meaningful below 100 accepts |
| Precision verdict | **INSUFFICIENT_CORPUS** | |
| False positives (accepted, disagrees with label) | 14 | all rung-2 provisional family attaches (see §4) |
| False negatives (label ≥ model, matcher none/review) | 0 of 3 | |
| Ambiguous / unratable (`AMBIGUOUS:` flag) | 15 | 11 of them are also disagreements |
| Model-grain coverage | eBay 10.3 %; all others 0 % | reported, not gated |
| Per-source family floor | ebay ✅; goharddrive ✅ (**only through ghd-0006, a likely wrong attach**); wd-recertified ❌; serverpartdeals ❌ (not harvested); seagate-recertified ❌ (not harvested) | all 5 required → **not met** |
| OEM dual-label rate (eBay) | 0 % | no row prints an OEM token and a manufacturer MPN together |
| Audit gate | **FAIL** | expected: every label is `claude_draft`; rollup is consistent |
| Rung-0 regression suite | **PASS** (6 passed, `tests/db/test_rung0_regression.py`) | |
| Composite `ms1_ratification_gate` | **FAIL** | |

Specific shortfalls: 83 auto-accepts short of 100. Family floor missing `serverpartdeals` and
`seagate-recertified` (not harvestable, OQ31) and `wd-recertified` (0 of 75 resolve; see finding
F2). goHardDrive's floor passes only through `ghd-0006`: a Constellation ES.3 attached to
`Exos`, which is probably wrong even under the lenient rule. If that attach is vetoed,
goHardDrive fails the floor too. The audit is not done.

**Sensitivity (computed after evaluation, labels unchanged).** Suppose the owner instead rules
that a rung-2 provisional family attach counts as correct when the manufacturer and product line
are right. Then 15 of 17 accepts are correct (88.24 %). The two remaining false positives are
`ebay-0021` (a third-party drive "comparable to" a Seagate MPN, attached to Seagate Exos) and
`ghd-0006` (the pre-Exos Constellation ES.3 ST1000NM0001, attached to Exos by the `st:nm`
grammar). The blind label predicted both traps before the matcher ran. The verdict is still
INSUFFICIENT_CORPUS.

### Findings the owner should see before any gate run

- **F1: the in-repo gate test uses a different catalog.** `test_ms1_ratification_gate` evaluates
  against the test-only `seeded_catalog` fixture (4 models: ST16000NM001G, ST12000NE0008,
  WUH721816ALE6L4, HUS724040ALS640), not the production refdata. Re-evaluating this corpus under
  that fixture gives **0 of 17 correct**. The three refdata model hits (ST22/26/28000NM000C)
  become family attaches because those models are not in the fixture. A real corpus labeled
  against production refdata cannot pass the gate test as written. This needs a decision
  (§6, item 4). Nothing was changed here.
- **F2: WD titles carry no MPN.** WD store titles are product-line names ("WD Red Plus Internal
  NAS HDD 3.5\" - Recertified"). The MPN exists only in the SKU key (`RWD40EFPX` is the
  recertified WD40EFPX), and the connector's `attrs` holds only `saleable`. Title-driven
  resolution therefore can never resolve a WD-store row, whatever the catalog seeds. This is an
  extraction gap (design §6 step 7), not a labeling issue.
- **F3: the catalog is thin for this market.** Of the 29 eBay drive rows, 3 hit one of the 15
  seeded drive models. None of the 21 goHardDrive rows does: page 1 is dominated by white-label
  and legacy consumer drives. Reaching 100 correct rung-1/2 accepts needs a wider catalog seed, rung-2
  family attaches accepted as correct (R2 vs oracle), or a much larger harvest. It likely needs
  more than one of these.

## 3. Entries

Outcome notation: `grain/outcome@rung → predicted target`. `–` means no resolution edge.
Listing key = eBay item id (full `v1|…` key and URL are in the JSONL) or the goHardDrive SKU.
Prices are USD.

### 3a. Obvious pass candidates (label = model; matcher agrees)

| id | item | price | title | matcher | proposed label | reason |
| --- | --- | --- | --- | --- | --- | --- |
| ebay-0003 | 296799857691 | 769.00 | Seagate Exos 26TB … Enterprise HDD - ST26000NM000C | model/accept@1 → seagate, exos, st26000nm000c | model: seagate / Exos / ST26000NM000C | R1: seeded MPN, 26TB agrees, no condition evidence |
| ebay-0004 | 296879436001 | 839.00 | Seagate Exos 28TB … Enterprise HDD - ST28000NM000C | model/accept@1 → seagate, exos, st28000nm000c | model: seagate / Exos / ST28000NM000C | R1: same |
| ebay-0010 | 305750340124 | 669.00 | Seagate Exos 22TB … Enterprise HDD - ST22000NM000C | model/accept@1 → seagate, exos, st22000nm000c | model: seagate / Exos / ST22000NM000C | R1: same |

### 3b. Obvious reject / no-match (label = none, no ambiguity flag; matcher = none, no edge)

| id | item | price | title | reason |
| --- | --- | --- | --- | --- |
| ebay-0006 | 147198086418 | 29.99 | Western Digital 1 TB WD1002F9YZ Recertified Enterprise Class HDD | not in catalog, no seeded family |
| ebay-0011 | 278347915868 | 18.88 | LOT OF 4 Enterprise 3.5 Hard Drives Used Make Offer | lot, no MPN |
| ebay-0015 | 256488423098 | 69.97 | Recertified* Seagate ST600MM0158 600GB 2.5" 12Gbps 10K Enterprise SAS HDD | not in catalog |
| ebay-0016 | 287604419657 | 50.00 | Seagate Enterprise Capacity 3.5 HDD V5 | family-only, no MPN or capacity |
| ebay-0017 | 377350242671 | 40.00 | Dell Certified Enterprise Class Seagate Barracuda ES.2 1000GB … | OEM-only, no MPN |
| ebay-0020 | 226849070225 | 24.99 | IBM 42D0673 - Recertified 73GB 15K SAS 2.5" HD | OEM FRU only; IBM not seeded |
| ebay-0023 | 226849069298 | 19.99 | IBM 42C0268, FRU 43W7537 - Recertified 73GB 10K SAS 2.5" HD | OEM FRU only |
| ebay-0024 | 226849071492 | 24.99 | IBM 43X0839 - Recertified 73GB 15K SAS 2.5" HD | OEM FRU only |
| ebay-0027 | 318730424561 | 60.00 | Seagate Enterprise Performance 15K v5 ST600MP0005 600GB SAS 2.5" | not in catalog |
| ebay-0029 | 256331688094 | 86.68 | 4x Hard Disk 3,5 " Western Digital Enterprise Storage 500GB SATA | multipack |

The 75 WD store rows are all labeled `none` and all predicted `none` (no edge), grouped by title:

| ids | title | reason |
| --- | --- | --- |
| wd-0001..0008 | My Book (Recertified) | consumer USB enclosure |
| wd-0009 | My Passport for Mac (Recertified) | consumer USB enclosure |
| wd-0010..0019 | My Passport (Recertified) | consumer USB enclosure |
| wd-0020..0023 | WD Elements Portable (Recertified) | consumer USB enclosure |
| wd-0024..0026 | WD Elements SE (Recertified) | consumer USB enclosure |
| wd-0027..0028 | WD Blue PC Mobile Hard Drive - Recertified | not in catalog; MPN only in SKU key (F2) |
| wd-0029 | WD_BLACK Performance Mobile Hard Drive - Recertified | same |
| wd-0030..0034 | My Passport Ultra (Recertified) | consumer USB enclosure |
| wd-0035..0048 | WD Blue PC Desktop Hard Drive - Recertified | not in catalog; F2 |
| wd-0049..0056 | WD Elements Desktop Hard Drive (Recertified) | consumer USB enclosure |
| wd-0057..0062 | WD Red Plus Internal NAS HDD 3.5" - Recertified | not in catalog; F2 |
| wd-0063..0066 | WD Gold Enterprise Class SATA HDD - Recertified | not in catalog; F2 |
| wd-0067 / 0068 / 0069 / 0070 | Ultrastar DC HC580 / HC530 / HC520 / HC330 … - Recertified | not in catalog (seeded WD family is HC550 only); F2 |
| wd-0071..0074 | WD Red Pro NAS Hard Drive - Recertified | not in catalog; F2. wd-0074 (key `WD240KFGX`, no `R` prefix) duplicates wd-0073's title and price, possibly a store-side duplicate |
| wd-0075 | WD Red NAS Hard Drive - Recertified | not in catalog; F2 |

goHardDrive rows labeled `none` and predicted `none` (no edge):

| ids | key(s) | title (abridged) | reason |
| --- | --- | --- | --- |
| ghd-0001..0004, 0009, 0010, 0012, 0014, 0015, 0017, 0019..0021 | g01-0057, -0054, -0101, -0663, -0637, -0638, -0165, -0643, -0644, -1008, -0454, -0645, -0646 | White Label / WL 80GB-500GB SATA drives | white-label, no manufacturer or MPN |
| ghd-0005 | g01-1411 | Western Digital VelociRaptor WD2500BHTZ 250GB 10K | not in catalog, no seeded family |
| ghd-0007 | g01-2161 | Hitachi CinemaStar 5K1000 HCS5C1050CLA382 500GB | not in catalog; Hitachi/HGST not seeded |
| ghd-0008 | g01-0084-crb | Western Digital Caviar Blue WD5000AAJS 500GB (Refurbished Grade B) | not in catalog |
| ghd-0011 / ghd-0013 | g01-0523 / g01-0611 | Seagate Pipeline HD ST3500312CS / ST3320311CS | not in catalog |
| ghd-0016 | g01-0815 | TOSHIBA MK1001TRKB 1TB SAS (Factory Refurbished) | not in catalog; Toshiba not seeded |

### 3c. Ambiguous or disputed: needs owner judgment

| id | item | price | title | matcher | proposed label | reason / flags |
| --- | --- | --- | --- | --- | --- | --- |
| ebay-0001 | 127047114304 | 669.00 | Seagate Exos 12TB-28TB … Recertified Enterprise HDD | none (no edge) | none | AMBIGUOUS: capacity-range multi-variation listing; agrees |
| ebay-0008 | 306609507820 | 679.00 | Western Digital Ultrastar 10TB - 24TB … Enterprise HDD | none (no edge) | none | AMBIGUOUS: capacity range; agrees |
| ebay-0009 | 267604989377 | 34.99 | Recertified Seagate Enterprise Exos 7E2000 1 TB, 2.5, SAS ST1000NX0453 | none (no edge) | none | AMBIGUOUS: Exos family candidate; not decoded; agrees |
| ebay-0002 | 306560691875 | 679.00 | WD DC HC580 24TB WUH722424ALE604 0F62798 … | family/accept@2 → western_digital, ultrastar (rule hgst:wuh) | none | not in catalog (R2); **DISAGREES** |
| ebay-0005 | 125735863196 | 539.00 | Seagate Exos X20 18TB … - ST18000NM003D | family/accept@2 → seagate, exos (st:nm) | none | AMBIGUOUS family candidate; **DISAGREES** |
| ebay-0007 | 147533787753 | 300.00 | Seagate Exos X14 12TB … ST12000NM0538 Factory Recert sealed | family/accept@2 → seagate, exos | none | AMBIGUOUS family candidate; **DISAGREES** |
| ebay-0012 | 287594890424 | 650.00 | Seagate Exos X20 ST20000NM007D 20TB … Recertified | family/accept@2 → seagate, exos | none | AMBIGUOUS family candidate; **DISAGREES** |
| ebay-0013 | 306816189259 | 829.00 | Seagate Exos 28TB ST28000NM001C … | family/accept@2 → seagate, exos | none | AMBIGUOUS + NEAR-MISS of seeded ST28000NM000C; **DISAGREES** |
| ebay-0014 | 298183706835 | 779.00 | Seagate Exos 26TB ST26000NM001C … | family/accept@2 → seagate, exos | none | AMBIGUOUS + NEAR-MISS of seeded ST26000NM000C; **DISAGREES** |
| ebay-0018 | 307081493079 | 789.00 | WD DC HC580 22TB WUH722422AL5201 0F62790 … SAS | family/accept@2 → western_digital, ultrastar | none | not in catalog (R2); **DISAGREES** |
| ebay-0019 | 307151394718 | 599.00 | WD DC HC560 20TB WUH722020BLE601 0F38781 … | family/accept@2 → western_digital, ultrastar | none | not in catalog (R2); **DISAGREES** |
| ebay-0021 | 128021517668 | 379.00 | WL OEM 16TB … HDD - Comparable to ST16000NM002G | family/accept@2 → seagate, exos | none | AMBIGUOUS: "comparable to" MPN names another product; **DISAGREES, likely a true matcher FP** |
| ebay-0022 | 307111684275 | 534.00 | Seagate Exos X18 16TB ST16000NM000J … | family/accept@2 → seagate, exos | none | AMBIGUOUS family candidate; **DISAGREES** |
| ebay-0025 | 318765294438 | 450.00 | Seagate Exos X18 ST18000NM000J 18TB … HDD OEM | family/accept@2 → seagate, exos | none | AMBIGUOUS family candidate; **DISAGREES** |
| ebay-0026 | 136902975856 | 332.65 | ST4000NM000B Enterprise 3.5 inch 4T hard disk … | family/accept@2 → seagate, exos | none | AMBIGUOUS: unbranded title, brand only from MPN; **DISAGREES** |
| ebay-0028 | 336792704007 | 774.99 | Seagate Exos X18 16TB … ST16000NM000J Recertified Inch | family/accept@2 → seagate, exos | none | AMBIGUOUS family candidate; **DISAGREES** |
| ghd-0006 | g01-1794-cr | 19.95 | Seagate Constellation ES.3 ST1000NM0001 1TB … SAS (Refurbished) | family/accept@2 → seagate, exos (st:nm) | none | AMBIGUOUS + DECODE-TRAP (flagged blind): pre-Exos Constellation line; **DISAGREES, likely a true matcher FP** |
| ghd-0018 | g02-0815-mdd | 29.95 | MDD / Seagate ST1200MM0009 2.5-inch 1.2TB SAS 10K (Renewed) | none (no edge) | none | AMBIGUOUS: relabeled ("MDD") Seagate 10K SAS, not seeded; agrees |

## 4. Error table: every disagreement and uncertain case

All 14 disagreements have the same shape: label `none`, matcher `family/accept` at rung 2 with
`provisional: true`, evidence provenance `corroborated_community`. No rung-1 hit disagrees, and
no model-grain label was missed.

| id | label → matcher | decision context | if the owner rules "provisional family attach is correct" |
| --- | --- | --- | --- |
| ebay-0005 | none → family seagate/exos | Exos X20 ST18000NM003D; MPN decodes cleanly to Exos | correct as family `Exos` |
| ebay-0007 | none → family seagate/exos | Exos X14 ST12000NM0538 | correct |
| ebay-0012 | none → family seagate/exos | Exos X20 ST20000NM007D | correct |
| ebay-0013 | none → family seagate/exos | ST28000NM001C, one char from seeded ST28000NM000C; the matcher correctly did **not** attach the seeded model | correct |
| ebay-0014 | none → family seagate/exos | ST26000NM001C, same near-miss pattern; model correctly not attached | correct |
| ebay-0022 | none → family seagate/exos | Exos X18 ST16000NM000J | correct |
| ebay-0025 | none → family seagate/exos | Exos X18 ST18000NM000J ("OEM" is a packaging word, not an OEM part token) | correct |
| ebay-0028 | none → family seagate/exos | Exos X18 ST16000NM000J, "Recertified" | correct |
| ebay-0026 | none → family seagate/exos | no brand in title; ST4000NM000B is Seagate Exos 7E10 by MPN grammar | correct if an MPN alone may establish brand |
| ebay-0002 | none → family western_digital/ultrastar | HC580 WUH722424ALE604; rule `hgst:wuh`. The predicted family `ultrastar` is broader than the title's "DC HC580" | correct at family `Ultrastar`, if that family name is acceptable |
| ebay-0018 | none → family western_digital/ultrastar | HC580 SAS WUH722422AL5201 | same as ebay-0002 |
| ebay-0019 | none → family western_digital/ultrastar | HC560 WUH722020BLE601 | same as ebay-0002 |
| ebay-0021 | none → family seagate/exos | third-party "WL OEM" drive; ST16000NM002G appears only after "Comparable to" | **still wrong**: attaches a non-Seagate product to Seagate Exos price history. A veto candidate ("comparable to" / "compatible with" context) under the C.3.5 loop |
| ghd-0006 | none → family seagate/exos | Constellation ES.3 ST1000NM0001 is the pre-Exos enterprise line. The `st:nm` grammar maps every ST…NM… MPN to Exos, but Constellation is not Exos. The goHardDrive floor currently rests on this row | **still wrong**: a family-grammar over-reach. Candidate fix: restrict the rule by MPN generation, or seed Constellation as its own family |
| ebay-0001, ebay-0008 | none = none | capacity-range listings; check that "none" is the intended treatment for multi-variation parents | n/a |
| ebay-0009 | none = none | Exos 7E2000 ST1000NX0453 not decoded (NX series); with R2 flipped this would become a false negative at family grain | would become a miss |

## 5. Audit sample (reproducible)

`select_audit_sample(ids, "ms1e-draft-2026-09-25")` → ceil(0.20 × 125) = **25** ids:

`wd-0057, ebay-0009, ghd-0002, ebay-0022, wd-0008, wd-0017, ebay-0021, wd-0061, wd-0005,
ghd-0015, ghd-0018, wd-0052, ebay-0025, ebay-0015, wd-0066, wd-0013, wd-0001, wd-0007, wd-0048,
wd-0015, ebay-0024, wd-0047, ebay-0011, ghd-0003, ghd-0008`

Every disagreement (14): `ebay-0002, ebay-0005, ebay-0007, ebay-0012, ebay-0013, ebay-0014,
ebay-0018, ebay-0019, ebay-0021, ebay-0022, ebay-0025, ebay-0026, ebay-0028, ghd-0006`.

The owner must audit the union, **36 entries** (`ebay-0021`, `ebay-0022`, and `ebay-0025` are
in both sets). The sample is a pure function of the id set and `corpus_version`. Adding rows
(for example, more goHardDrive pages) redraws it. Re-labeling under the same `corpus_version`
keeps it.

## 6. Owner decision required

> **Inspect:** the 36 ids in §5, the R2 labeling rule (§1), and findings F1–F3 (§2).
>
> **Mark:** edit each audited line's `label.audit_status` in
> `docs/evidence/2026-09-25-ms1e-draft-corpus.jsonl`. Use `owner_confirmed` when the draft stands
> and `owner_corrected` when you change `expected_grain` or `expected_target`. Then make
> `audit_rollup` in the `.meta.json` equal the new per-status counts. The audit gate checks this,
> and a stale rollup fails it. The corpus becomes the gate input only when copied to
> `tests/fixtures/matching_corpus/corpus.jsonl` (+ `corpus.meta.json`). That copy activates
> `test_ms1_ratification_gate`, so make it only when a PASS is plausible.
>
> **Flip rule:** ADR 0019 moves `proposed → accepted` only on a composite PASS from **one full
> test run**, with `test_ratification_corpus.py` and `test_rung0_regression.py` both green. It
> never flips on INSUFFICIENT_CORPUS or FAIL, and the gate is never loosened.
>
> **Under the current five-source floor the gate cannot PASS.** ServerPartDeals and Seagate
> cannot be harvested (OQ31), so their floor entries are false by construction. OQ32 options:
> (a) **re-scope the floor to usable sources.** This needs an owner-approved design change
> recorded as an ADR 0019 note, a code change to the floor's source set in
> `src/hw_radar/matching/eval/report.py` with its tests, WD hitting family grain (blocked by F2),
> and goHardDrive holding its floor through a correct attach rather than `ghd-0006`.
> (b) **Keep the gate and wait for OQ31 permissions.** This needs written permission from
> ServerPartDeals and Seagate, then a harvest of both. (c) **Widen the harvest** (more eBay drive
> queries, more goHardDrive category pages). This addresses only the 83-accept shortfall, never
> the floor, so it has to be combined with (a) or (b).
>
> **Also decide:** (1) R2 vs the synthetic oracle, i.e. whether a rung-2 provisional family
> attach is a correct outcome for an unseeded model. This moves precision from 17.65 % to
> 88.24 % on this corpus. (2) Whether `ebay-0021` ("comparable to") and `ghd-0006` (`st:nm`
> mapping Constellation to Exos) warrant veto or grammar fixes and a `matcher_version` bump.
> (3) The WD MPN-in-SKU extraction gap (F2). (4) F1: which catalog the in-repo gate evaluates
> against. The test fixture and the production refdata give opposite answers on the same corpus.
