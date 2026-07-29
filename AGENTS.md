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

MS-0 is implemented and deployed: Django 6, TimescaleDB, ADR-0010 identity
ladder, `accounts` / `web` / `poller`, deploy artifacts, and the full Python
verification gate. MS-1 ingestion substrate and matching work have begun; the
next major work is the catalog seed and connector path described in the MS-1
design and plans under `docs/superpowers/`.

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
`127.0.0.1:5433`.

## Verification

Before claiming completion after code changes:

```bash
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run coverage run -m pytest
uv run coverage report
uv run pip-audit
```

Use `uv add`, `uv add --dev`, and `uv remove` for dependency changes. Do not
hand-edit `uv.lock`.

## Git Model

Work on `dev` unless the user asks for a feature branch. `main` is protected and
advances by PR from `dev` with a merge commit after CI passes. Use conventional,
GPG-signed commits.

<!-- prettier-ignore-start -->

<!-- BEGIN project-standards:agent-handoff -->
<!-- markdownlint-disable MD025 -->
# Agent Handoff

Use the repo-local `agent-handoff` skill at session startup and closeout. Do not reread state already injected by SessionStart. Keep project knowledge inside this repository and store credential references only, never values.
<!-- markdownlint-enable MD025 -->
<!-- END project-standards:agent-handoff -->

<!-- prettier-ignore-end -->
