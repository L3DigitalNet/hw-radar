---
schema_version: '1.1'
id: 'adr-0001-hw-radar-decline-markdown-frontmatter-standard'
title: 'ADR 0001: Decline the Markdown Frontmatter Standard'
description: 'Adopt the project-standards ADR format without its prerequisite Markdown Frontmatter Standard; keep ADR frontmatter as an unvalidated local convention.'
doc_type: 'adr'
status: 'active'
created: '2026-07-03'
updated: '2026-07-03'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'project-standards'
  - 'frontmatter'
  - 'tooling'
aliases: []
related:
  - 'docs/adr/README.md'
supersedes: []
superseded_by: null
source:
  - 'https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/adr'
  - 'https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/markdown-frontmatter'
confidence: 'high'
visibility: 'public'
license: null
project:
  decision_makers:
    - 'chris'
  consulted: []
  informed: []
---

# ADR 0001: Decline the Markdown Frontmatter Standard

MADR status: **accepted**.

## Context and Problem Statement

This repository is adopting the project-standards **ADR Standard** (MADR body format + a `docs/adr/` directory and index). That standard's adoption runbook, however, names the **Markdown Frontmatter Standard as a hard prerequisite** — "Adopt the Frontmatter Standard first" — because it treats ADRs as _managed Markdown documents_ validated by shared CI tooling. Adopting the Frontmatter Standard in full means: a root `.project-standards.yml`, a reusable CI validator workflow pinned to `@v3`, and retrofitting a conformant 11-plus-field frontmatter block onto **every** managed document (`README.md` and all of `docs/**/*.md`), then keeping ids, dates, enums, and key order conformant on every future edit.

Should `hw-radar` — a **single-maintainer, design-phase** repo with no application code yet — take on that full standard, or decline it and adopt only the ADR format?

## Decision Drivers

- **Team size and value model.** The Frontmatter Standard's payoff is _machine-enforced consistency across many documents and many contributors_ (a fleet/CI concern). This repo has one maintainer and low doc churn, so that payoff is marginal.
- **Adoption + carrying cost.** Retrofitting validated frontmatter onto the README, the spec, `open-questions.md`, `further-research-needed-prompts.md`, and the research index — plus a CI job that must stay green on every edit — is real friction during a phase whose whole point is fast design iteration.
- **Keep the genuinely useful part.** ADRs benefit from _structured_ `status` / `supersedes` / `superseded_by` metadata (the supersession workflow depends on it). That value is local to the ADRs, not the whole doc tree.
- **Fewer moving CI parts** are easier to reason about on a public repo.
- **Reversibility.** Declining now does not burn the bridge — the standard can be adopted later if the repo grows.

## Considered Options

- **Option 1 — Adopt the Markdown Frontmatter Standard in full** (config + CI validator + retrofit every managed doc), as the ADR Standard's runbook prescribes.
- **Option 2 — Decline the Frontmatter Standard; adopt the ADR format only**, keeping frontmatter as an _unvalidated local convention scoped to ADR files_.
- **Option 3 — Decline frontmatter entirely**, including on ADRs (pure MADR prose, status tracked in body text).

## Decision Outcome

Chosen option: **Option 2 — decline the Markdown Frontmatter Standard and adopt the ADR format only.** It keeps the high-value ADR machinery (structured status and supersession) while shedding the repo-wide enforcement overhead a solo, design-phase repo does not benefit from. Option 1 front-loads cost with little near-term return; Option 3 throws away the supersession metadata that is the main reason to have structured ADRs at all.

**Scope boundary this ADR establishes:**

- **Frontmatter appears only on ADR files** (`docs/adr/adr-*.md`), authored from the project-standards ADR template. It is a **local convention, not CI-validated** — there is no `.project-standards.yml` and no frontmatter-validator workflow in this repo.
- **The ADR index** (`docs/adr/README.md`) and **all non-ADR documents** carry **no required frontmatter**. Existing research reports that already carry frontmatter (from the research tooling) keep it as-is; it is simply not validated.
- **Directory:** ADRs live under **`docs/adr/`**. This itself diverges from the ADR Standard's canonical `docs/decisions/`; `docs/adr/` is the chosen location for this repo (self-identifying, and the directory was already established here).

### Consequences

- Good, because no CI frontmatter validator to maintain and no retrofitting of README/spec/open-questions/research docs — design iteration stays fast.
- Good, because ADRs remain self-describing and **forward-compatible**: if the repo later adopts the Frontmatter Standard, the ADR blocks already match its schema and only the non-ADR docs would need retrofitting.
- Good, because the boundary is crisp and greppable — YAML frontmatter under `docs/adr/` is an ADR; its absence elsewhere is intentional.
- Bad, because ADR frontmatter correctness (id format, date quoting, supersession links) is **not machine-enforced** — it relies on author/template discipline.
- Bad, because this **diverges from the project-standards ecosystem**; a future fleet-wide docs pipeline would require retrofitting then (cost deferred, not eliminated).
- Neutral, because the Python Tooling SSOT Standard is **independent** of the Frontmatter Standard (it ships no shared validator), so declining frontmatter does not block adopting python-tooling.

### Confirmation

Compliance is self-evident from repo state: **no `.project-standards.yml`** and **no frontmatter-validator workflow** exist. New ADRs are authored from the project-standards ADR template but are not CI-validated. This decision is the sanctioned "record an exception as an ADR" mechanism referenced by the Python Tooling SSOT Standard (§20).

## Pros and Cons of the Options

### Option 1 — Adopt the Frontmatter Standard in full

- Good, because it gives machine-enforced consistency and full alignment with the standards ecosystem.
- Good, because ADRs would be validated (id/date/enum/section checks) automatically.
- Bad, because it imposes retrofit + ongoing-conformance cost that a solo, pre-code repo does not recoup.
- Bad, because a red frontmatter CI job during rapid design churn is pure drag.

### Option 2 — ADR format only, unvalidated frontmatter on ADRs (chosen)

- Good, because it keeps structured supersession/status where it matters and nothing where it doesn't.
- Good, because it is low-overhead and forward-compatible.
- Neutral, because it accepts a small, well-scoped inconsistency (frontmatter on ADRs only).
- Bad, because correctness is by discipline, not enforcement.

### Option 3 — No frontmatter anywhere

- Good, because it is maximally simple and maximally consistent.
- Bad, because supersession/status must be tracked in prose, losing the main benefit of structured ADRs and making an eventual migration harder.

## More Information

Revisit this decision if the repo gains additional maintainers, joins a fleet-wide documentation/CI pipeline, or the number of managed docs grows to where manual consistency becomes unreliable — at which point adopting the [Markdown Frontmatter Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/markdown-frontmatter) and retrofitting the non-ADR docs is the migration path. A future ADR would supersede this one.

References:

- [ADR Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/adr)
- [Markdown Frontmatter Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/markdown-frontmatter)
- [Python Tooling SSOT Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/python-tooling) — §20 records exceptions as ADRs.
