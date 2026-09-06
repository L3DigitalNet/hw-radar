# Hardware Radar

A search-and-monitoring tool that watches ~20 online marketplaces — manufacturer
recertified stores, storage-specialist resellers, eBay/Amazon/Newegg, business VARs,
and refurbished-server sellers — for hard disk drives (HDDs) and solid-state drives
(SSDs), and scores each listing (0–100) to surface the best deals for a
homelab/small-business buyer who favors **enterprise/NAS-grade** and **recertified**
drives. It alerts on availability and price drops.

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
ratification step is pending before any source goes live. Scoring (MS-2) is
designed and owner-ratified but not implemented; alerting (MS-4) and the UI
(MS-3) are not implemented.

## Documentation

| Doc | What it is |
| --- | --- |
| [`docs/specs/hw-radar-master-spec.md`](docs/specs/hw-radar-master-spec.md) | The master specification — requirements, architecture, scoring, deployment, milestones. |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records (MADR) — the authoritative record of significant decisions. |
| [`docs/open-questions.md`](docs/open-questions.md) · [`docs/resolved-questions.md`](docs/resolved-questions.md) | Decision backlog (open) and settled provenance. |
| [`docs/research/index.md`](docs/research/index.md) | Generated index of the research corpus (in-depth context behind decisions). |
| [`AGENTS.md`](AGENTS.md) | The toolchain/agent contract (verification gate, dependency/typing/testing rules). |
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

The verification gate needs the dev database running.

**Branching:** `main` is protected and advances only via a pull request from `dev`. `dev` is the long-lived working branch — **commit and push to it directly** (no PR needed); use a short-lived `feature/*` branch only when you want isolation. To update `main`, open a PR from `dev` and **merge with a merge commit** (not squash — keeps `dev` in sync with `main` and preserves history); the PR must pass CI and carry signed commits.

## Decided stack

Python · Scrapy (HTTP-first / structured-data-first / browser-last) · PostgreSQL +
TimescaleDB · Django + server-rendered templates + HTMX · APScheduler in one
systemd-supervised poller · deployed to a dedicated Debian LXC container · NGINX +
Let's Encrypt · GitHub Actions CD. See the master spec §8.3 for the ADR-backed
rationale behind each choice.
