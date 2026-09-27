# Hardware Radar — Agent Instructions

This is the cross-agent entry point for the repository. Keep it small: durable
project rules live in `docs/handoff/conventions.md`, and live state is injected
by the SessionStart hook.

Session state: Agent Handoff injects `docs/handoff/state.md`; do not reread it when injected.
Full conventions reference: `docs/handoff/conventions.md`
Detailed review workflows: `docs/handoff/specs-plans.md`

## Public-Repo Rule

This is a public repository. Do not commit secrets, credential values, private
hostnames, private IP addresses, or internal infrastructure addresses. Runtime
secrets are referenced by env var or OpenBao path only. Public-safe deployment
shape belongs in repo docs; private fleet details belong outside this repo.

## Current Product State

MS-0, MS-1a..MS-1e, and MS-2 Slices A–F (multi-category watch core, ADRs 0021–0022) are
deployed: drive matching, GPU/RAM/CPU categories with `match | no_match | unknown`
evaluation, and hybrid acquisition under a hard $20/month Apify ceiling. Only the eBay x CPU
admission cell is `ADMITTED` (live since 2026-09-27, EPYC scopes); every other cell and source
stays off. The drive matcher is ratified (ADR-0019); CPU auto-accepts AMD EPYC only (OQ34).
ADR-0011 scoring is deferred.

Hardware Radar's Apify Actors are built and managed in this repository under
`actors/<name>/`, not in the separate `apify-actors` repository.

## Read First

- Spec source of truth: `docs/specs/hw-radar-master-spec.md`
- Open decisions: `docs/open-questions.md`
- Settled decisions: `docs/resolved-questions.md`
- ADRs: `docs/adr/`
- Research reports: `docs/research/`
- Current status: `docs/STATUS.md`
- Work queue: `docs/TODO.md`

## TODO Discipline

When working on an item listed in `docs/TODO.md`, update that item in the same change:
remove completed work, narrow partially completed work, and add newly discovered
follow-ups. Keep user-owned and agent-tracked sections separate.

## Commands

```bash
uv sync --all-groups
podman compose up -d db
uv run python manage.py migrate
uv run python manage.py runserver
uv run python -m scripts.check
uv run ruff format . && uv run ruff check . --fix
```

DB tests need live TimescaleDB. On this workstation, port `5432` may be owned by
host PostgreSQL; use `HW_RADAR_DB_PORT=5433` when the dev container is mapped to
`127.0.0.1:5433` (a local override; the committed `compose.yaml` maps 5432).

## Verification

Before claiming completion after code changes, run `uv run python -m scripts.check`: root
`ruff format --check`, `ruff check`, `basedpyright`, `coverage run -m pytest`,
`coverage report`, `pip-audit --skip-editable`, plus each `actors/<name>/` project's gate.

Use `uv add`, `uv add --dev`, and `uv remove` for dependency changes. Do not
hand-edit `uv.lock`.

## Git Model

Work on `dev` unless the user asks for a feature branch. `main` is protected and
advances by PR from `dev` with a merge commit after CI passes. Use conventional,
GPG-signed commits: both `dev` and `main` reject unverified commits (admins
included), so an unsigned PR cannot merge into either branch.

<!-- prettier-ignore-start -->

<!-- BEGIN project-standards:agent-handoff -->
<!-- markdownlint-disable MD025 -->
# Agent Handoff

Use the repo-local `agent-handoff` skill at session startup and closeout. Do not reread state already injected by SessionStart. Keep project knowledge inside this repository and store credential references only, never values.
<!-- markdownlint-enable MD025 -->
<!-- END project-standards:agent-handoff -->

<!-- prettier-ignore-end -->
