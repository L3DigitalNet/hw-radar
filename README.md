# Hardware Radar

A personal/business PC and server hardware search-and-monitoring tool. The first
release is **multi-category and watch-first**: HDD/SSD, GPUs/compute accelerators,
RAM, and CPUs are first-class categories, with selected server/PC components
supported at a basic exact/curated-watch depth. Hardware Radar evaluates saved
requirements, tracks price/availability/history, surfaces an evidence-backed
shortlist, and alerts on qualifying opportunities. Category-specific scoring can
deepen after the core watch workflow is useful; unrelated hardware categories are
not forced into one universal score.

Personal/business use; single maintainer.

## Status

**MS-0 through MS-1e are implemented, merged, and deployed — live in
production.** The Django foundation, TimescaleDB-backed schema (the ADR-0010
identity ladder through the `offer_snapshot` hypertable), the ingestion
substrate, matching layer, catalog seed, five marketplace connectors, and
availability heartbeat all run on a dedicated Debian LXC container, deployed by
GitHub Actions CD. The Python verification gate (uv · Ruff · BasedPyright
strict · pytest + coverage · pip-audit) is green locally and in CI. All
marketplace sources still ship disabled: the MS-1e evaluation harness and
harvest tooling are merged and deployed, but the owner-in-the-loop
ratification step is pending before any source goes live. The detailed
drive-scoring design (former MS-2 next step) is owner-ratified but
**deferred from the immediate critical path**. The 2026-09-24 strategy re-baseline
([ADR 0021](docs/adr/adr-0021-hybrid-acquisition-apify.md),
[ADR 0022](docs/adr/adr-0022-multi-category-watch-first-v1.md)) moves next work to
multi-category requirement matching plus a complete watch → shortlist → alert
workflow, using hybrid acquisition: inexpensive direct/local collectors where they
fit and self-owned private Apify Actors selectively under a hard **$20/month**
Hardware Radar Apify ceiling.

**The multi-category watch core is under way and not yet deployed:** GPU/RAM/CPU
first-class categories, category-specific `match | no_match | unknown`
requirement evaluation, and Hardware Radar's first self-owned Apify Actor project
(`actors/hw-radar-synthetic-collector`; nothing pushed to or run on Apify yet).

## Documentation

| Doc | What it is |
| --- | --- |
| [`docs/specs/hw-radar-master-spec.md`](docs/specs/hw-radar-master-spec.md) | The master specification — requirements, architecture, scoring, deployment, milestones. |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records (MADR) — the authoritative record of significant decisions. |
| [`docs/open-questions.md`](docs/open-questions.md) · [`docs/resolved-questions.md`](docs/resolved-questions.md) | Decision backlog (open) and settled provenance. |
| [`docs/research/index.md`](docs/research/index.md) | Generated index of the research corpus (in-depth context behind decisions). |
| [`AGENTS.md`](AGENTS.md) | The toolchain/agent contract (verification gate, dependency/typing/testing rules). |
| [`docs/STATUS.md`](docs/STATUS.md) · [`docs/TODO.md`](docs/TODO.md) | Current status snapshot and the open work queue. |
| [`actors/`](actors/) | Hardware Radar's own Apify Actors, one uv project per Actor. |

## Development

```bash
podman compose up -d db        # TimescaleDB dev database (docker works too)
uv sync --all-groups            # create the env from uv.lock
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
uv run python -m scripts.check  # full verification gate (fmt · lint · types · test · cov · audit)
uv run ruff format . && uv run ruff check . --fix   # fix pass
```

The verification gate needs the dev database running. Actor projects under
`actors/<name>/` are separate uv projects with their own lockfiles;
`scripts.check` gates them too.

**Branching:** `main` is protected and advances only via a pull request from `dev`. `dev` is the long-lived working branch — **commit and push to it directly** (no PR needed); use a short-lived `feature/*` branch only when you want isolation. To update `main`, open a PR from `dev` and **merge with a merge commit** (not squash — keeps `dev` in sync with `main` and preserves history); the PR must pass CI and carry signed commits.

## Decided stack

Python · hybrid acquisition (official APIs / local HTTP+Scrapy where inexpensive;
self-owned private Apify Actors selectively; HTTP-first / structured-data-first /
browser-last) · PostgreSQL + TimescaleDB · Django + server-rendered templates +
HTMX · APScheduler as the scheduling/admission owner · dedicated Debian LXC ·
NGINX + Let's Encrypt · GitHub Actions CD. See the master spec §8.3 and ADRs
0021–0022 for the current direction.
