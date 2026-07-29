---
schema_version: '1.1'
id: 'adr-0002-hw-radar-python-tooling-standard-local-deviations'
title: 'ADR 0002: Python Tooling Standard — local deviations'
description: 'Adopt the Python Tooling SSOT Standard with two scoped exceptions: defer the verification gate/CI until code exists (retired at scaffold), and keep .vscode/ git-ignored (CLAUDE.md half retired 2026-07-05h).'
doc_type: 'adr'
status: 'active'
created: '2026-07-03'
updated: '2026-07-05'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'project-standards'
  - 'python'
  - 'tooling'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0001-decline-markdown-frontmatter-standard.md'
  - 'pyproject.toml'
  - 'AGENTS.md'
supersedes: []
superseded_by: null
source:
  - 'https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/python-tooling'
confidence: 'high'
visibility: 'public'
license: null
project:
  decision_makers:
    - 'chris'
  consulted: []
  informed: []
---

# ADR 0002: Python Tooling Standard — local deviations

MADR status: **accepted**.

## Context and Problem Statement

This repository adopts the project-standards **Python Tooling SSOT Standard** (uv · Ruff · BasedPyright strict · pytest + coverage · pip-audit). Two facts about _this_ repo collide with the standard as written, and the standard (§20) requires any deviation to be recorded as an ADR:

1. **No application code exists yet.** The repo is in the design phase. The standard's core is a **verification gate** (`ruff format --check → ruff check → basedpyright → coverage run -m pytest → coverage report → pip-audit`) and a CI workflow that runs it. With no `src/` package and no tests, the gate cannot pass (pytest fails on no tests; `coverage report` fails `fail_under = 85`) and a committed CI workflow would paint a permanent failing check on this **public** repo.
2. **Public-repo file hygiene.** The standard expects `.vscode/` and `CLAUDE.md` committed to version control. This repo's established convention keeps agent/editor local config **out** of the public repo — `.gitignore` excludes `.vscode/`, `CLAUDE.md`, `.claude/`, `docs/handoff.md`, and `TODO.md`.

How should the standard be adopted without either shipping a broken gate or reversing the repo's public hygiene?

## Considered Options

- **Option 1 — Conform fully now:** create a `src/` package + smoke test, commit `.vscode/` and `CLAUDE.md`, add the CI workflow, and run the gate to green.
- **Option 2 — Adopt the configuration now; defer the gate/CI; keep `.vscode/`/`CLAUDE.md` local.** (chosen)
- **Option 3 — Do not adopt until the repo is scaffolded.**

## Decision Outcome

Chosen option: **Option 2.** Lay down the toolchain **configuration** now so it governs code the instant it lands, without fabricating a package just to make a gate green or degrading public-repo hygiene.

**Adopted now (tracked):** `pyproject.toml` (the standard's §6 tool tables — Ruff, BasedPyright strict, pytest, coverage — plus `[project]`, `[dependency-groups].dev`, `[build-system]`), `.python-version` (3.14), `.editorconfig` (the shared superset), `AGENTS.md` (the tracked, self-contained agent entry point), and `scripts/check.py`.

**Deviation A — verification gate and CI deferred.** `.github/workflows/check.yml`, the `src/<package>/` tree, `tests/`, `uv.lock`, and the first green gate are created by the separate "scaffold the repo" task. Until then `uv sync` and the gate are inert by design.

**Deviation B — `.vscode/` and `CLAUDE.md` stay git-ignored.** They are created for local use but not committed. `AGENTS.md` (committed) is the canonical, self-contained instruction source a fresh clone sees; it documents the gate, the rules, and the VS Code task names so nothing is lost by `.vscode/` being local. This deliberately reverses the standard's "commit them" expectation, scoped to this repo's public-hygiene convention. **(Partially retired 2026-07-05h — see Confirmation: `CLAUDE.md` is now tracked as a thin `@AGENTS.md` import; `.vscode/` remains git-ignored.)**

Option 1 was rejected because a broken CI check and reversed hygiene are worse than a briefly-deferred gate. Option 3 was rejected because it leaves the toolchain undefined during the design phase, when the config's whole value is to be ready before the first line of code.

### Consequences

- Good, because no failing CI on a public repo, and public-repo hygiene is preserved.
- Good, because the toolchain (versions, strictness, rules) is fixed and reviewable now; scaffolding only has to _activate_ it, not decide it.
- Good, because `AGENTS.md` carries the full contract, so a fresh clone with no `CLAUDE.md`/`.vscode/` is still fully instructed.
- Bad, because the gate is **not enforced** until scaffolding — correctness during design relies on author discipline.
- Bad, because contributors don't get the shared `.vscode/` config from the repo; they must recreate it from the standard (mitigated: `AGENTS.md` lists the tasks/commands).
- Neutral, because both deviations are time-boxed to the design phase; the gate/CI deviation ends at scaffold.

### Confirmation

Repo state confirms the decision: `pyproject.toml` carries the standard's tool tables; `AGENTS.md` is tracked; `.vscode/` and `CLAUDE.md` remain in `.gitignore`.

**Update (2026-07-03) — Deviation A retired.** The "scaffold the repo" task landed in the same session: `src/hw_radar/` (version-only skeleton) + `tests/`, `uv.lock`, and `.github/workflows/check.yml` were added, and the full verification gate now runs **green** (`uv run python -m scripts.check`, exit 0). Deviation A (deferred gate/CI) no longer applies. **Deviation B remains in force** — `.vscode/` and `CLAUDE.md` stay git-ignored as long as the public-hygiene convention holds — so this ADR stays `active`.

**Update (2026-07-05h) — Deviation B partially retired: `CLAUDE.md` now tracked.** Current Claude Code guidance (`code.claude.com/docs/en/claude-md`) documents `@AGENTS.md` import as the recommended pattern for repos maintaining both files, specifically to avoid duplicating instructions — the concern Deviation B was scoped around (reversing the standard's "commit them" expectation for public-hygiene reasons) no longer applies to `CLAUDE.md` once it holds no content of its own. Reviewed the file for anything the "no secrets/hostnames/IPs" banner would forbid — clean. Moved essentially all substantive content (project status, architecture, key documents, git/decision conventions) into `AGENTS.md`, which is now the single self-contained canonical source for any agent; `CLAUDE.md` is reduced to `@AGENTS.md` plus a `## Claude Code` section carrying only genuinely Claude-Code-specific rituals (the `docs/handoff.md` session-start read and session-closeout steps, both tied to local-only files that don't exist on a fresh clone). Removed from `.gitignore` and from the `scripts/transfer-ignored.sh` manifest (no longer a per-workstation local file). `.vscode/` is unaffected and remains git-ignored — Deviation B stays in force for that half only. ADR stays `active`.

## More Information

- [Python Tooling SSOT Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/python-tooling) — §20 records exceptions as ADRs; §15 is the CI workflow deferred here.
- Related: [ADR 0001](adr-0001-decline-markdown-frontmatter-standard.md) (declining the Markdown Frontmatter Standard) and the tracked `AGENTS.md` / `pyproject.toml`.
