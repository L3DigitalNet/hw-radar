# Architecture Decision Records

This directory holds the project's **Architecture Decision Records (ADRs)** — the durable, reviewable memory of _why_ the system is built the way it is. Each ADR captures one significant, hard-to-reverse decision: the context that forced it, the options considered, the choice made, and the consequences.

## Conventions

- **Format:** [MADR](https://adr.github.io/madr/), per the [project-standards ADR Standard](https://github.com/L3DigitalNet/project-standards/tree/v5.11.0/standards/adr). Author new ADRs from that standard's template.
- **Filename:** `adr-NNNN-short-title.md` (zero-padded sequence; repo-name omitted).
- **`id` (in ADR frontmatter):** `adr-NNNN-hw-radar-short-title` (embeds the repo name for global uniqueness).
- **Frontmatter:** ADR files carry the ADR template's YAML frontmatter as a **local, unvalidated convention** — this repo deliberately does **not** adopt the enforced Markdown Frontmatter Standard or a CI validator. See [ADR 0001](adr-0001-decline-markdown-frontmatter-standard.md). This index and all non-ADR docs carry no frontmatter.
- **Supersession:** when a new ADR replaces an old one, set `supersedes` on the new record and `superseded_by` + `status: superseded` on the old one, in the same change.

## When to write an ADR

Write one for a **significant** and **costly-to-reverse** decision — datastore, framework, auth model, deployment target, a directory convention many files will follow. Skip it for routine, easily-reversed choices.

## Index

| # | Title | Status | Date |
| --: | --- | --- | --- |
| [0001](adr-0001-decline-markdown-frontmatter-standard.md) | Decline the Markdown Frontmatter Standard | Accepted | 2026-07-03 |
| [0002](adr-0002-python-tooling-standard-local-deviations.md) | Python Tooling Standard — local deviations | Accepted | 2026-07-03 |
| [0003](adr-0003-deploy-as-lxc-container.md) | Deploy as a dedicated LXC container (not a VM) | Accepted | 2026-07-03 |
| [0004](adr-0004-web-framework-django-htmx.md) | Web framework — Django + templates + HTMX | Accepted | 2026-07-03 |
| [0005](adr-0005-single-account-session-auth.md) | Single-account session authentication | Accepted | 2026-07-03 |
| [0006](adr-0006-cd-rsync-over-tailscale-ssh.md) | CD via GitHub-hosted runner + rsync over Tailscale SSH | Accepted | 2026-07-03 |
| [0007](adr-0007-datastore-postgresql-timescaledb.md) | Datastore — PostgreSQL as system-of-record + TimescaleDB | Accepted | 2026-07-03 |
| [0008](adr-0008-currency-landed-cost-normalization.md) | Currency & landed-cost normalization | Accepted | 2026-07-03 |
| [0009](adr-0009-secrets-runtime-openbao-agent.md) | Secrets runtime — local OpenBao Agent on the CT | Accepted | 2026-07-03 |
| [0010](adr-0010-canonical-data-model.md) | Canonical data model — product/variant identity ladder | Accepted | 2026-07-03 |
| [0011](adr-0011-composite-deal-score.md) | Composite deal score — weighted geometric mean with veto caps | Accepted | 2026-07-04 |
| [0012](adr-0012-orchestration-apscheduler.md) | Orchestration engine — APScheduler in one supervised poller | Accepted | 2026-07-04 |
| [0013](adr-0013-notification-transport-m365-graph.md) | Notification transport — reuse the existing M365 Graph send path | Accepted | 2026-07-04 |
| [0014](adr-0014-scraping-runtime-escalation-stack.md) | Scraping runtime — HTTP-first, structured-data-first, browser-last stack | Accepted | 2026-07-04 |
| [0015](adr-0015-availability-heartbeat-grain-volatility-scheduling.md) | Availability-heartbeat grain + volatility-aware scheduling | Accepted | 2026-07-04 |
| [0016](adr-0016-search-api-self-governance.md) | Search-API self-governance — the ordered SearchBudgetGate | Accepted | 2026-07-04 |
| [0017](adr-0017-resilient-acquisition.md) | Resilient acquisition — per-source isolation + circuit-break lifecycle | Accepted | 2026-07-04 |
| [0018](adr-0018-manufacturer-spec-catalog.md) | Manufacturer spec catalog — a first-class reference-data source | Accepted | 2026-07-04 |
| [0019](adr-0019-listing-catalog-matching-layer.md) | Listing→catalog matching layer — grain-elastic resolution against the MPN matrix | Proposed | 2026-07-04 |
| [0020](adr-0020-per-lane-scheduling-state.md) | Per-lane scheduling state — one state row per (source, lane) | Accepted | 2026-08-16 |
