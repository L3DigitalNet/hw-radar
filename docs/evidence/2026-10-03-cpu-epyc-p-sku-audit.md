# AMD EPYC 9354P / 9654P: seed and agent audit (2026-10-03, matcher `2026.10.1`)

Status: **agent audit, not owner-audited.** The CPU seed gains EPYC 9354P and EPYC 9654P as
their own models. The CPU EPYC corpus is relabeled in a new versioned file, and the matcher fixes
two defects the audit exposed. FP is 0 under production rules. The v2 corpus fails the audit gate
until the owner audits its new sample (§6).

## 1. Facts and sources

The facts come from the first-party amd.com product pages, retrieved 2026-10-03. The decision to
seed them was owner-delegated on 2026-10-03.

| Model | Cores / threads | Socket | Default TDP | Tray OPN | Socket count | Source |
| --- | --- | --- | --- | --- | --- | --- |
| EPYC 9354P | 32 / 64 | SP5 | 280 W | 100-000000805 | 1P only | [amd-epyc-9354p](https://www.amd.com/en/products/processors/server/epyc/4th-generation-9004-and-8004-series/amd-epyc-9354p.html) |
| EPYC 9654P | 96 / 192 | SP5 | 360 W | 100-000000803 | 1P only | [amd-epyc-9654p](https://www.amd.com/en/products/processors/server/epyc/4th-generation-9004-and-8004-series/amd-epyc-9654p.html) |

The non-P contrast parts are already seeded: 9354 has OPN 100-000000798 and 9654 has
100-000000789. The P parts are distinct models, never aliases of the non-P parts. Each P model
gets two `mpn` aliases: the `EPYC 9354P` / `EPYC 9654P` name (primary) and its tray OPN.

- **Not seeded:** WOF/PIB boxed codes. Only retail listings source them, and no first-party page
  verified them.
- **1P only:** `CpuSpec` has no socket-count field, so the fact sits in the provenance source
  titles. No schema field was added.

Seed: `src/hw_radar/refdata/seeds/amd-epyc.json`, now 6 models, sha256
`80499f2e20041dcec2f6c57ba4a29292083519b716f6b9ccfd49c0be58b589c3`. The §9/§10 ratification ran
on `e38e350b…d1e7`.

## 2. Why a new corpus file

`2026-09-26-cpu-epyc-draft-corpus.jsonl` and its meta are unchanged. They are the owner-audited
evidence that ratified AMD EPYC (OQ34, audit packet §9–§10). Relabeling them in place would
rewrite that record, and its owner statuses would then describe labels the owner never saw. The
two code consumers (`tests/unit/test_reference_context_r2.py` and
`tests/unit/test_vocab_owner_rulings.py`) read only titles, which are byte-identical in v2, so
they stay on the ratified file.

The new files:

- [`2026-10-03-cpu-epyc-corpus-v2.jsonl`](2026-10-03-cpu-epyc-corpus-v2.jsonl): 284 rows, same
  ids, titles and listings. Only the 47 relabeled rows differ.
- [`2026-10-03-cpu-epyc-corpus-v2.meta.json`](2026-10-03-cpu-epyc-corpus-v2.meta.json):
  `corpus_version` is `cpu-epyc-v2-2026-10-03`, `matcher_version` is `2026.10.1`,
  `ratification_sources` is `["ebay"]`, and `observed_at` is unchanged. The rollup is
  `claude_draft` 235, `owner_confirmed` 44 and `owner_corrected` 5.

## 3. Relabeled rows (47, all former rule C2 "P-variant → none")

Every relabeled row now has `audit_status: claude_draft` and a per-row rationale in
`label.notes`, which records the prior status. Eleven of these rows were `owner_confirmed` as C2.
The rule they were confirmed under ("unseeded, so none") no longer holds, so they return to
draft.

| Label | Rows |
| --- | --- |
| EPYC 9354P, model grain | cpu-0003, -0006, -0011, -0012, -0014, -0016, -0064, -0065, -0066, -0070, -0072, -0075, -0078, -0079, -0080, -0083, -0085, -0086, -0091, -0094, -0095, -0096, -0100, -0102, -0103, -0105, -0106, -0107, -0108, -0109, -0112, -0113, -0114, -0115, -0117, -0118, -0119, -0122, -0124, -0128, -0129 (41) |
| EPYC 9354P, variant `new` | cpu-0067 ("New …"), as the owner corrected cpu-0013 and cpu-0180 |
| EPYC 9354P, variant `for_parts` | cpu-0069 ("… Processor non-working") |
| EPYC 9654P, model grain | cpu-0024, -0026, -0029, -0038 (4) |

The labels apply the owner's rulings from 2026-09-26:

- Dell branding or a lock is compatibility, not identity: cpu-0003, -0006, -0012, -0024, -0026
  and -0016 keep their P-model identity.
- An explicit supported condition means variant grain.
- Price is not identity.

The B2B "3 yr warranty" rows (cpu-0014 and -0038) are labeled the way the owner-confirmed C1
rows cpu-0031 and -0032 are.

No other row changed its label. The contradiction cases stay out of the corpus as positives, and
the tests pin them (§5).

## 4. Measurement (production rules, `test_ms1e_corpus_measurement`, matcher `2026.10.1`)

All runs use the same 284 rows on a fresh test DB with the production refdata import.

| Run | Seed | Labels | Accepts | Correct | Precision | FP | FN |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Before | 4-model (`e38e350b…`) | v1 | 105 | 105 | 100.0% | 0 | 0 |
| Before | 4-model | v2 | 105 | 105 | 100.0% | 0 | 47 |
| **After** | **6-model (`80499f2e…`)** | **v2** | **152** | **152** | **100.0%** | **0** | **0** |
| After (reference) | 6-model | v1 | 152 | 105 | 69.1% | 47 (labels stale) | 0 |

**Exactly the 47 relabeled rows changed prediction between Before and After**, and all 47 now
agree with their v2 labels. After the change there are 152 accepts:

- by grain: 146 model, 6 variant;
- by model: 7763 ×46, 9354 ×44, 9354P ×43, 9654 ×12, 9654P ×4, 7742 ×3.

Reviews stay at 18, the unchanged `sample` and `bundle` vetoes, and 114 rows resolve to none.
Every accepted family is `amd`/`epyc`.

The matcher bump alone, without the seed change, alters no corpus row: the v1 predictions
are identical under `2026.09.4` and `2026.10.1`.

Under `2026.09.4` with the new seed, the same audit found the two defects that `2026.10.1` fixes:

- **cpu-0069 was a false positive.** The vocab read "non-working" as no condition, so the broken
  unit accepted at model grain. Matcher `2026.10.1` reads the non-working/non-functional
  spellings as `for_parts`, and adds them to the negator-owning registry so they stop the
  negation window.
- **cpu-0016 was a false negative.** The `-DELL` suffix on OPN `100-000000805` read as a sample
  marking, so a retail part was reviewed as a sample. An OEM-brand suffix is no longer a sample
  marking.

Reproduce: point `HW_RADAR_MS1E_CORPUS` at a copy of the v2 JSONL whose sibling `.meta.json`
holds the committed v2 meta. Then run
`uv run pytest tests/db/test_ratification_corpus.py -k test_ms1e_corpus_measurement -s`.

## 5. Contradictions kept

- **9354P named beside OPN 798.** The title "EPYC … 9354P … 100-000000798" now hits two seeded
  models and reviews on the rung-1 target conflict (`conflicting_targets: 2`) before the `model`
  veto runs. `tests/db/test_resolver_cpu_s8_r1_model.py` pins that it reviews with no model or
  variant attached on a fresh listing, on a retitle and on the next poll, and that the conflict
  is 9354 against 9354P.
- **OPN first, then a model name.** In "100-000000798 9354P" and the mirror
  "100-000000805 9354", the named model is not a candidate. These cases still review on
  `veto: model`.
- **Samples.** A suffixed OPN such as `-04`, ES or QS still reads as a sample.

## 6. Audit gate and matcher version

**Audit gate: FAIL until the owner audits.** The new `corpus_version` draws a new 57-id sample:

> cpu-0003, -0004, -0007, -0009, -0010, -0013, -0017, -0027, -0032, -0044, -0053, -0054, -0064,
> -0065, -0071, -0073, -0075, -0080, -0088, -0090, -0091, -0093, -0096, -0112, -0114, -0118,
> -0129, -0139, -0146, -0151, -0165, -0166, -0169, -0170, -0182, -0190, -0194, -0199, -0200,
> -0207, -0208, -0212, -0222, -0223, -0229, -0231, -0232, -0234, -0237, -0238, -0239, -0241,
> -0255, -0258, -0262, -0274, -0279

45 of these 57 are `claude_draft`, and 11 of them are relabeled P rows. The gate also needs the
47 relabeled rows owner-audited. The ratification already granted to AMD EPYC (OQ34) is
family-scoped, so the seed needs no new ratification to take effect. This file is evidence that
the identity decisions on the new rows are correct.

**Matcher version.** A refdata-only change would not have bumped `MATCHER_VERSION`, because seeds
are catalog data, not rules. The audit found two rule defects, and the bump to `2026.10.1`
records those fixes. The seed itself does not need it.

The drive gate stays green: `tests/db/test_ratification_corpus.py` and
`tests/db/test_rung0_regression.py` pass. No drive corpus or fixture title contains the new
spellings, so the drive measurement cannot move.

**Production effect.** In the admitted eBay × CPU cell, the "EPYC 9354" sweeps already return
9354P listings. Until now those resolved to none. They now auto-accept as EPYC 9354P, never as
9354. A broken unit lands on the `for_parts` variant.
