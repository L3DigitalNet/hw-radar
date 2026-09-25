# Architecture Notes

Last updated: 2026-09-24

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
  GoHardDrive, Seagate, ServerPartDeals, WD — plus a `demo` source),
  `scheduling/` (fast/slow lane admission, backoff, checkpoints),
  `deadman`/`heartbeat` (availability monitoring), and `apify/` (`client`,
  the Apify API client, and hw-radar's side of the versioned Actor contract —
  Pydantic models mirroring the committed JSON Schema — and `classify_run`,
  the run-completeness classifier for remote collector runs).
- `refdata`: ADR-0018 truncated fetch→parse→normalize pipeline (seed
  documents, importer, discovery loop, monthly refresh; not a Django app), now
  multi-category with first-party CPU/GPU/RAM seeds alongside the original
  drive seeds.
- `actors/<name>/`: Hardware Radar's own Apify Actors, each a separate uv
  project gated by `scripts/check.py` and CI. First and only Actor so far:
  `actors/hw-radar-synthetic-collector` (D-prep code complete; no Apify
  push/build/run has occurred).
- Runtime jobs: APScheduler poller service (UTC-pinned), daily
  maintenance/recovery jobs, monthly refdata refresh, and dead-man heartbeat
  support.
- Deployment: systemd units, nginx config, and `deploy/deploy-remote.sh`.

## Standing Backlog

- MS-1e owner-in-the-loop drive-matcher ratification (harness/harvest tooling
  implemented; live harvest, label draft, owner audit, and the ADR-0019 flip
  are pending, so all marketplace sources ship disabled)
- MS-2 multi-category watch core (re-baselined by ADR-0021/ADR-0022, replacing
  the old MS-2a scoring-substrate sequencing): Slices A–C and D-prep complete
  on `dev`; Slice D entry-gate design review, D2 onward, and Slice E remain;
  OQ25–OQ29 resolved 2026-09-25, OQ24 (production merchant source) still owner-
  gated. ADR-0011's detailed drive-scoring design is accepted but deferred from
  the immediate critical path.
- MS-3 operator-facing product UI: not implemented
- MS-4 alerting: not implemented
