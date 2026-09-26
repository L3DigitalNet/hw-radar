# Architecture Notes

Last updated: 2026-09-25

## Component Graph

- Django project core: `src/hw_radar/{settings,urls,wsgi}.py`. Only `accounts`,
  `catalog`, and `web` are registered Django apps (`INSTALLED_APPS`);
  `acquisition`, `matching`, `eligibility`, `refdata`, and `poller` are plain
  packages whose models live in `catalog`.
- Data model: ADR-0010 identity ladder plus market/evidence tables and
  TimescaleDB `offer_snapshot` observations, now with GPU/RAM/CPU spec
  satellites and first-class category rows (migrations 0018–0019) and watch
  requirements/eligibility verdicts (migration 0020).
- `matching`: category-dispatched resolution (`categories.py` registry) over
  drive's existing ADR-0019 vocab/mpn/grammar/ladder path plus `rules/`
  (`drive`, `gpu`, `ram`, `cpu`, `basic`) for the new first-class categories,
  each with its own extraction and acceptance policy.
- `eligibility`: persisted watch requirements and `match | no_match | unknown`
  verdicts per (watch, listing) — `requirements` (the single writer),
  `evaluate` (the evaluator the ingestion pipeline calls), `service` (batched
  is-this-verdict-still-current checks and re-evaluation), and `shortlist`
  (the `shortlist`/`review_queue` read model). No ADR-0011 scoring artifact is
  read or written here.
- `acquisition`: `sources/` (five drive-focused direct connectors — eBay,
  GoHardDrive, Seagate, ServerPartDeals, WD — plus a `demo` source;
  ServerPartDeals and Seagate are retired as of 2026-09-26 and no longer
  schedulable, `admission.py` `RETIRED_SOURCES`/`sources/__init__.py`
  `RETIRED_ADAPTERS`, though their code and history stay in the tree),
  `scheduling/` (fast/slow lane admission, backoff, checkpoints),
  `deadman`/`heartbeat` (availability monitoring), `stages` (transactional
  persist/delist stages shared by the local and remote paths), `persist`
  (ordering-guarded observation writes), `retention_policy` (per-source
  retention registry; remote retention never defaults), and `apify/`:
  `client` (byte-capped Apify API client), `contract` + `classify_run`
  (hw-radar's side of the versioned Actor contract), `provider` + `importer`
  (durable staged import state machine), `jobs` (start job behind
  `LedgerAdmission`; `apify-poll` selectors), `storage_cleanup` (deadline-bound
  run-storage deletion), `budget` (DB-free cost/admission policy), `ledger`
  (advisory-lock reservations, cycle snapshot, authority handoff, overrun
  latch), `reconcile` (settlement, correction monitoring), `report`
  (`apify_spend_report`). Ledger tables live in `catalog/models/provider.py`.
- `refdata`: ADR-0018 truncated fetch→parse→normalize pipeline (seed
  documents, importer, discovery loop, monthly refresh; not a Django app), now
  multi-category with first-party CPU/GPU/RAM seeds alongside the original
  drive seeds.
- `actors/<name>/`: Hardware Radar's own Apify Actors, each a separate uv
  project gated by `scripts/check.py` and CI. First and only Actor so far:
  `actors/hw-radar-synthetic-collector` (deployed privately, build `1.0.1`;
  F5a proof runs 2026-09-25 in a non-production environment only). A local
  `synthetic` adapter over the same pinned fixtures supports the live
  provider-switch proof; the site stays disabled and never scheduled.
- Runtime jobs: APScheduler poller service (UTC-pinned), daily
  maintenance/recovery jobs, monthly refdata refresh, and dead-man heartbeat
  support.
- Deployment: systemd units, nginx config, and `deploy/deploy-remote.sh`.

## Standing Backlog

- MS-1e owner-in-the-loop drive-matcher ratification (harness/harvest tooling
  implemented; live harvest, label draft, owner audit, and the ADR-0019 flip
  are pending, so all marketplace sources ship disabled)
- MS-2 multi-category watch core (re-baselined by ADR-0021/ADR-0022, replacing
  the old MS-2a scoring-substrate sequencing): Slices A–E complete on `dev`
  (migrations 0021/0022 undeployed); plan rev 12 (no runtime account reads)
  landed; F5a synthetic Actor proof executed 2026-09-25; Slice F pilot sources
  (F1–F3) and the owner-gated F6 remain;
  OQ25–OQ29 resolved 2026-09-25; OQ24 part (b) (production merchant source)
  resolved 2026-09-26 — none is admitted now, F5b deferred, not a blocker.
  ADR-0011's detailed drive-scoring design is accepted but deferred from
  the immediate critical path.
- MS-3 operator-facing product UI: not implemented
- MS-4 alerting: not implemented
