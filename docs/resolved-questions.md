# Resolved Questions — `hw-radar.md`

**Companion to [`open-questions.md`](open-questions.md)** — that file holds whatever is currently open plus the shared [maintenance rules](open-questions.md#how-to-maintain-this-document); this file is the **settled record**, split out 2026-07-04 to keep the open-questions doc short.

**Terminology:** an **open question** (`OQ#`) is a decision still to be made (it lives in [`open-questions.md`](open-questions.md)); a **resolved question** (`RQ#`) is one already settled; a **gap** (`gap #`) is one of the twelve original spec-audit findings that seeded these questions. Everything here is settled — retained for provenance and to keep ADR/spec cross-references resolvable; you do not need to read it to know what's still open.

## Table of Contents

- [Resolved Questions — `hw-radar.md`](#resolved-questions--hw-radarmd)
  - [Table of Contents](#table-of-contents)
  - [Resolved questions](#resolved-questions)
  - [Origins — the 12-gap summary](#origins--the-12-gap-summary)
  - [Origins — resolved gap write-ups](#origins--resolved-gap-write-ups)
    - [Gap 1 — Web-app authentication (settled, ADR 0005)](#gap-1--web-app-authentication-settled-adr-0005)
    - [Gap 2 — `.env` secrets model → OpenBao (settled except `secret_id` delivery)](#gap-2--env-secrets-model--openbao-settled-except-secret_id-delivery)
    - [Gap 3 — Currency / landed-cost normalization (settled, ADR 0008)](#gap-3--currency--landed-cost-normalization-settled-adr-0008)
    - [Gap 4 — Deployment & service topology (settled, ADR 0006)](#gap-4--deployment--service-topology-settled-adr-0006)
    - [Gap 5 — Backup / disaster recovery (settled CT path, ADR 0003)](#gap-5--backup--disaster-recovery-settled-ct-path-adr-0003)
    - [Gap 6 — Application self-observability (settled CT path except off-box heartbeat)](#gap-6--application-self-observability-settled-ct-path-except-off-box-heartbeat)
    - [Gap 7 — UI/UX (settled: Django + HTMX + post-alert model; inventory open)](#gap-7--uiux-settled-django--htmx--post-alert-model-inventory-open)
    - [Gap 8 — v1 scope / phasing / acceptance criteria (settled)](#gap-8--v1-scope--phasing--acceptance-criteria-settled)
    - [Gap 9 — Scraper testing strategy (settled stack + amendments; build-time params open)](#gap-9--scraper-testing-strategy-settled-stack--amendments-build-time-params-open)
    - [Gap 10 — Running-cost / budget model (settled approach; pricing pass open)](#gap-10--running-cost--budget-model-settled-approach-pricing-pass-open)
    - [Gap 11 — Shipping (and tax) in the `$/TB` score (settled)](#gap-11--shipping-and-tax-in-the-tb-score-settled)
    - [Gap 12 — Cold-start scoring (settled)](#gap-12--cold-start-scoring-settled)
  - [Scope & research provenance](#scope--research-provenance)
  - [Resolved OQs](#resolved-oqs)
    - [OQ1 — `secret_id` out-of-band delivery to the CT](#oq1--secret_id-out-of-band-delivery-to-the-ct)
    - [OQ2 — Ephemeral-runner tailnet auth](#oq2--ephemeral-runner-tailnet-auth)
    - [OQ3 — DB RPO acceptance (+ TimescaleDB dump handling)](#oq3--db-rpo-acceptance--timescaledb-dump-handling)
    - [OQ4 — DB placement: own CT vs shared datastores CT](#oq4--db-placement-own-ct-vs-shared-datastores-ct)
    - [OQ5 — Off-box heartbeat](#oq5--off-box-heartbeat)
    - [OQ6 — Final UI page inventory + dismiss→suppress feedback + purchase tracking](#oq6--final-ui-page-inventory--dismisssuppress-feedback--purchase-tracking)
    - [OQ7 — Running-cost budget model (build-time pricing pass)](#oq7--running-cost-budget-model-build-time-pricing-pass)
    - [OQ8 — Scraper testing finalization](#oq8--scraper-testing-finalization)
    - [OQ9 — Acquisition cadence, throttle & skip policy](#oq9--acquisition-cadence-throttle--skip-policy)
    - [OQ10 — Reliability / resilient acquisition](#oq10--reliability--resilient-acquisition)
    - [OQ11 — Composite scoring model (adopt research #4)](#oq11--composite-scoring-model-adopt-research-4)
    - [OQ12 — Orchestration engine (APScheduler vs systemd timers)](#oq12--orchestration-engine-apscheduler-vs-systemd-timers)
    - [OQ13 — Notification transport & deliverability (AgentMail vs transactional provider)](#oq13--notification-transport--deliverability-agentmail-vs-transactional-provider)
    - [OQ14 — Scraping runtime escalation stack](#oq14--scraping-runtime-escalation-stack)
    - [OQ15 — Amazon acquisition path after PA-API deprecation](#oq15--amazon-acquisition-path-after-pa-api-deprecation)
    - [OQ16 — SSD cohort-key endurance dimension (DWPD)](#oq16--ssd-cohort-key-endurance-dimension-dwpd)
    - [OQ17 — Heartbeat-grain retention & storage policy](#oq17--heartbeat-grain-retention--storage-policy)
    - [OQ18 — Recovery-time objective (RTO) for v1](#oq18--recovery-time-objective-rto-for-v1)
    - [OQ19 — Accessibility & i18n declaration](#oq19--accessibility--i18n-declaration)
    - [OQ20 — OSS license-compliance posture](#oq20--oss-license-compliance-posture)
    - [OQ21 — `httpx` dependency for API/FX/heartbeat HTTP paths](#oq21--httpx-dependency-for-apifxheartbeat-http-paths)
    - [OQ22 — Retention class and `expires_at` policy for resolver-learned (`listing_derived`) `ProductAlias` rows](#oq22--retention-class-and-expires_at-policy-for-resolver-learned-listing_derived-productalias-rows)

---

## Resolved questions

Cross-cutting questions the research surfaced, now closed. Referenced by ADRs/spec as `RQ#`.

| # | Question | Resolution |
| --- | --- | --- |
| **RQ1** | Framework: Django or FastAPI? | **Django** + server-rendered templates + HTMX — the app's center of gravity is an authenticated listings DB + dashboards + CRUD + alerts, not an API platform. **[ADR 0004](adr/adr-0004-web-framework-django-htmx.md).** Locks in `manage.py migrate`, `contrib.auth`, and the Django admin as back-office. |
| **RQ2** | Public URL required, or Tailscale-only acceptable? | **Public URL required**, with a single strong-password account; Tailscale-only rejected. Drives the auth model (resolved gap #1, [ADR 0005](adr/adr-0005-single-account-session-auth.md)). |
| **RQ3** | Does the Proxmox host give a per-service vTPM? | The host has `swtpm` installed, so a full **VM** could get a vTPM — but hw-radar deploys as a **CT** (RQ5), which has **no per-container TPM** (shared host kernel). So `systemd-creds --with-key=tpm2` is **not** available → secrets go via the local OpenBao Agent (resolved gap #2, [OQ1](#oq1--secret_id-out-of-band-delivery-to-the-ct)). |
| **RQ4** | Existing Hetzner backup & monitoring coverage? | **Characterized on 2026-07-03** (specifics in the private `homelab` repo). **Backup:** file-level restic + hourly logical dumps to two offsite repos, but **opt-in per service via a hardcoded allowlist**, **no PITR** (≤1 h RPO), **no VM-image backup**. **Monitoring:** rich but **on-box only**, **no off-site watchdog**; a CT is auto-discovered by the health check, a VM is not. Net: a CT maximizes infra reuse; an off-box heartbeat ([OQ5](#oq5--off-box-heartbeat)) and a DB-RPO decision ([OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling)) are the two things to add. |
| **RQ5** | Deployment model — CT vs VM? | **Dedicated LXC container**, superseding the spec's "VM". Aligns with the "every service in a dedicated LXC" standard and maximizes infra reuse (fleet-digest auto-discovers the CT; its data is reachable by the host's file-level restic). **[ADR 0003](adr/adr-0003-deploy-as-lxc-container.md).** Consequences: vTPM off the table (RQ3) → local OpenBao Agent; backup is **not** automatic (wire the CT into `backup-restic.sh`/`backup-dumps.sh`); DB on a container Postgres ([OQ4](#oq4--db-placement-own-ct-vs-shared-datastores-ct)). |
| **RQ6** | Datastore: PostgreSQL or MySQL? | **PostgreSQL (system-of-record) + TimescaleDB** for the price-history/observation side. **[ADR 0007](adr/adr-0007-datastore-postgresql-timescaledb.md).** Adds the TimescaleDB-aware-dump driver to [OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling). |

## Origins — the 12-gap summary

Where each of the twelve original spec-audit **gaps** (the provenance the open questions were distilled from) landed. 🔴/🟡/🟢 = original priority.

| # | Gap | Pri | Outcome |
| --: | --- | :-: | --- |
| 1 | Web-app authentication undefined | 🔴 | **Settled** — single-account Argon2id session login ([ADR 0005](adr/adr-0005-single-account-session-auth.md)). |
| 2 | `.env` secrets contradict OpenBao standard | 🔴 | **Settled** ([ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md)) — local bao-agent on the CT consuming the Hetzner **`bao-services`** store; SecretID delivery settled ([OQ1](#oq1--secret_id-out-of-band-delivery-to-the-ct) ✅, verified live 2026-07-04). |
| 3 | No currency / landed-cost normalization | 🔴 | **Settled** ([ADR 0008](adr/adr-0008-currency-landed-cost-normalization.md)) — Frankfurter FX → USD; flag international listings (no fixed haircut). |
| 4 | Deployment & service topology a black box | 🔴 | **Settled** — `rsync` over Tailscale + systemd ([ADR 0006](adr/adr-0006-cd-rsync-over-tailscale-ssh.md)); runner tailnet auth settled → **Tailscale OAuth client** ([OQ2](#oq2--ephemeral-runner-tailnet-auth) ✅, verified live 2026-07-04). |
| 5 | No backup / disaster recovery | 🟡 | **Settled** (CT path) — inherit restic + hourly dumps ([ADR 0003](adr/adr-0003-deploy-as-lxc-container.md)); DB placement settled → **own-CT Postgres** ([OQ4](#oq4--db-placement-own-ct-vs-shared-datastores-ct) ✅). Open part → [OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling) (DB-RPO). |
| 6 | No application self-observability | 🟡 | **Settled** (CT path) — infra health auto-covers the CT; in-app `scraper_runs`/dead-man's-switch; off-box heartbeat settled → **GMK Uptime Kuma** ([OQ5](#oq5--off-box-heartbeat) ✅). |
| 7 | UI/UX specified as one line | 🟡 | **Settled** — Django + HTMX rendering + post-alert state machine. Open part → [OQ6](#oq6--final-ui-page-inventory--dismisssuppress-feedback--purchase-tracking). |
| 8 | No v1 scope / phasing / acceptance criteria | 🟡 | **Settled** — six-milestone MVP plan (MS-0–MS-5) accepted; authoritative phasing to be authored via `spec-pipeline`. |
| 9 | No scraper testing strategy | 🟡 | **Settled** — vcrpy + syrupy + contract canary + Pydantic v2 (+5 amendments). Open part → [OQ8](#oq8--scraper-testing-finalization). |
| 10 | No running-cost / budget model | 🟢 | **Settled** (approach) — free-feed-first, config-driven per-source poll budget. Open part → [OQ7](#oq7--running-cost-budget-model-build-time-pricing-pass). |
| 11 | Shipping/tax not in the `$/TB` score | 🟢 | **Settled** — score on price + shipping (+ tax where known); missing-shipping = penalty/flag. |
| 12 | Cold-start: no history for relative scoring | 🟢 | **Settled** — continuous-shrinkage warm-up (`λ = min(1, n_eff/30)`, ratified by [ADR 0011](adr/adr-0011-composite-deal-score.md)); graded seed sources. |

## Origins — resolved gap write-ups

Full write-ups of the twelve original **gaps** and their decisions (provenance for the questions above). For a split gap, only the **settled** part is here; its remaining fork is the linked open question.

### Gap 1 — Web-app authentication (settled, ADR 0005)

**Was:** the spec asserted "user authentication for secure access" and anticipated future multi-user, but defined no auth model, mechanism, or user schema. Evidence: [`hw-radar.md:7`](archived/hw-radar.md), [`:79`](archived/hw-radar.md).

**Decision → [ADR 0005](adr/adr-0005-single-account-session-auth.md):** a single strong-password account with **Argon2id** session login (Django `contrib.auth`), internet-facing; the load-bearing constraint is that the app holds **no in-app secrets**. Stub a `users` table now; **Authelia forward-auth** reserved for the multi-user end state. Full context and the forward-auth security rules (localhost bind, header overwrite, CVE-pinned gateway) live in the ADR. Research: [`auth-for-self-hosted-single-maintainer-python-app.md`](research/2026-07-03-auth-for-self-hosted-single-maintainer-python-app.md).

### Gap 2 — `.env` secrets model → OpenBao (settled except `secret_id` delivery)

**Was:** the spec repeatedly said secrets live in a committed-excluded `.env`, but the org standard is **OpenBao as the credential store**, and it never answered **how the deployed app obtains secrets at runtime** — a real contradiction, sharpened by the repo being public. Evidence: [`hw-radar.md:62`](archived/hw-radar.md), [`:82`](archived/hw-radar.md), [`:95`](archived/hw-radar.md), [`:107`](archived/hw-radar.md), [`:111`](archived/hw-radar.md).

**Decision:**

- **Runtime injection via OpenBao Agent** (`bao agent`, its own hardened systemd unit) using **AppRole auto-auth**. The agent templates secrets to a root-owned, `0640`, app-group-readable file on **tmpfs** (gone on reboot; exact render path per [ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md)); app services depend on it via `After=`. No plaintext `.env` at rest, no secrets baked into unit files.
- **The Agent runs locally on the CT, fully decoupled from CI.** Because CD is `rsync` over Tailscale SSH from a **GitHub-hosted** runner (gap #4), the public-repo CI job holds **no OpenBao credential at all** — it only rsyncs code and triggers `systemctl restart`; the running services pick up secrets the Agent has already templated. The `role_id` lives in the CT image/config-management.
- **Reconcile the spec's language:** `.env` is acceptable **for local development only**; production resolves secrets from OpenBao at runtime.
- **Settled 2026-07-04 (verified live) → [ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md):** the `secret_id` delivery/renewal path → hw-radar onboards as the next **`bao-services`** consumer via a local **bao-agent**; a persistent, long-lived, **CIDR-bound** SecretID delivered operator→CT out-of-band (issuer script + response-wrap token + `pct push`) → **[OQ1](#oq1--secret_id-out-of-band-delivery-to-the-ct)**. Concrete container IDs, the on-disk SecretID path, the CIDR value, and the issuer-script name live in the private `homelab` repo (per [ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md)). (The earlier "CD job fetches a response-wrapped `secret_id`" mechanism is withdrawn — CI holds no OpenBao credential; the Agent on the CT consumes locally. The spec's `remove_secret_id_file_after_reading=true` is also superseded — it breaks restart safety.)

Research: [`github-actions-cd-private-debian-vm.md`](research/2026-07-03-github-actions-cd-private-debian-vm.md) §3.

### Gap 3 — Currency / landed-cost normalization (settled, ADR 0008)

**Was:** the score is `USD` per `TB`, but several ranked merchants (ETB Technologies, Bargain Hardware) are UK/EU resellers pricing in GBP/EUR, and the buyer is US-based — cross-border listings scored on a false basis. Evidence: [`hw-radar.md:13`](archived/hw-radar.md), merchants at [`:40`–`:41`](archived/hw-radar.md).

**Decision (owner, 2026-07-03 — accepted with changes) → [ADR 0008](adr/adr-0008-currency-landed-cost-normalization.md):**

- **FX:** use **Frankfurter** (ECB-anchored, free, no API key, MIT, self-hostable), refreshed once/day. Store `fx_rate`, `fx_pair`, `fx_rate_date`, `fx_source` **on each observation** so historical scores are auditable and reproducible.
- **Normalize all prices to USD, but do NOT apply a fixed "international overhead" haircut.** Instead **flag** international listings (e.g. `"international — extra shipping/duty likely; verify before buying"`) and let the owner decide. Rationale: a cross-border purchase is unlikely to be worthwhile (shipping + potential customs), and a hardcoded percentage would be false precision — surface the risk rather than bake in a number. HDDs classify under **HTS 8471.70 at a 0% base (MFN) rate**, so the volatile cost is the surcharge the flag defers to the user.
- **Do NOT compute exact duty.** As of 2026-07-03 the US **de-minimis exemption is suspended indefinitely** and the add-on tariff rate for UK/EU goods is in active legal flux — a precise duty figure would be false precision that goes stale.
- **VAT footgun:** UK/EU VAT should be **zero-rated on export**, but many storefronts display VAT-inclusive prices pre-checkout — the scraper must not treat a VAT-inclusive shelf price as the export price.

Research: [`currency-conversion-and-landed-cost-estimation…md`](research/2026-07-03-currency-conversion-and-landed-cost-estimation-for-cross-border-drive-price-scoring.md).

### Gap 4 — Deployment & service topology (settled, ADR 0006)

**Was:** "GitHub Actions → automatic deployment to Hetzner on merge to main" stated the _what_, never the _how_: transport, how the app runs as a service, how CI reaches a non-public target. Evidence: [`hw-radar.md:72`–`:75`](archived/hw-radar.md).

**Decision → [ADR 0006](adr/adr-0006-cd-rsync-over-tailscale-ssh.md):** a **GitHub-hosted `ubuntu-latest`** runner builds/tests, joins the tailnet **ephemerally**, then `rsync`s to the CT and restarts over `tailscale ssh` (self-hosted runner rejected on a public repo). Systemd web + worker units under a dedicated non-root user; **timers** for scrapes; venv built on the CT (`uv sync --frozen`); expand/contract migrations before restart. Full trigger/secret discipline lives in the ADR. **Settled 2026-07-04 (verified live):** the ephemeral-runner tailnet auth → the **Tailscale OAuth client** (`secret/infra/tailscale-oauth`, minting a `tag:ci` ephemeral node) via `tailscale/github-action` v4 → **[OQ2](#oq2--ephemeral-runner-tailnet-auth)**. Research: [`github-actions-cd-private-debian-vm.md`](research/2026-07-03-github-actions-cd-private-debian-vm.md); per-source scheduling refined in [`orchestration-choice…md`](research/orchestration-choice-for-a-single-vm-price-polling-service.md) (and superseded by [OQ12](#oq12--orchestration-engine-apscheduler-vs-systemd-timers)/[ADR 0012](adr/adr-0012-orchestration-apscheduler.md) — systemd supervises one APScheduler poller, not per-scrape timers).

### Gap 5 — Backup / disaster recovery (settled CT path, ADR 0003)

**Was:** the accumulated historical price data _is_ the tool's compounding value; a single box with no backup means one disk failure erases the moat. Evidence: [`hw-radar.md:19`](archived/hw-radar.md), [`:22`](archived/hw-radar.md); DB co-located per [`:69`](archived/hw-radar.md).

**Decision (CT path) — [ADR 0003](adr/adr-0003-deploy-as-lxc-container.md):** add the hw-radar **CT** to the existing Hetzner restic + hourly-dump pipeline; keep the monthly restore-test discipline. **Settled 2026-07-03:** DB placement → **own Postgres inside the hw-radar CT** (self-contained; [OQ4](#oq4--db-placement-own-ct-vs-shared-datastores-ct)). **Still open:** DB-RPO acceptance → **[OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling)** (owner: document requirements in the `homelab` repo first).

**Live-state findings (2026-07-03 — verified on the server; specifics in the private `homelab` repo):**

- **The existing backup is file-level restic + logical DB dumps — there is _no_ VM-image backup.** No scheduled `vzdump`/PBS runs. restic backs up the host + each **LXC container's** app data via ZFS-subvolume paths → local ZFS repo, an **hourly offsite copy** (Hetzner Storage Box), and a **weekly offsite copy of a tier-1 subset** (Backblaze B2). Retention: 48 hourly / 14 daily / 8 weekly / 6 monthly.
- **Databases are dumped _logically, hourly_.** `pg_dump --format=custom` per DB + `pg_dumpall --globals-only`, captured by restic. **No WAL archiving, no PITR** → **RPO up to ~1 hour**.
- **Coverage is a hardcoded allowlist, _not_ auto-discovery.** `backup-restic.sh` and `backup-dumps.sh` name each path/DB by hand; a fail-loud guard aborts on a vanished path, but a **new service is never picked up automatically.**

**Implications:** had it stayed a standalone VM with an in-VM DB, it would have fallen **entirely outside** existing backup coverage — a primary reason the deployment model was decided as a CT (RQ5). **Decided path (CT):** deploy as a dedicated LXC container with its DB on a container Postgres, **and at provisioning add its data paths to `backup-restic.sh` + its DB to `backup-dumps.sh`** — this wiring is mandatory, not automatic. **Caveat (TimescaleDB, ADR 0007):** the inherited logical `pg_dump` needs TimescaleDB-aware dump/restore — a plain allowlist entry is insufficient; otherwise add in-CT physical backup (see OQ3).

**Fallback design if tighter RPO/PITR is wanted:** **pgBackRest** physical backup + continuous **WAL archiving** on the box (`repo1`) with a **second repo (`repo2`) on S3-compatible storage** (Backblaze B2 or Hetzner Storage Box) using pgBackRest **AES-256** — PITR + 3-2-1 offsite. Supplement with a weekly `pg_dumpall`. **TimescaleDB:** physical backups need no special handling; prefer them over logical. **Patch PostgreSQL/tooling** (recent `pg_dump`/`pg_basebackup`/`pg_rewind` CVEs). Research: [`postgresql-backup-disaster-recovery-single-vm.md`](research/2026-07-03-postgresql-backup-disaster-recovery-single-vm.md).

### Gap 6 — Application self-observability (settled CT path except off-box heartbeat)

**Was:** deal alerts tell the _user_ about drives; nothing tells the _operator_ that the app is down, out of disk, a scrape stopped, or alert emails aren't delivered. Evidence: [`hw-radar.md:11`](archived/hw-radar.md).

**Decision (CT path):** split the concern — **infrastructure health** (up/disk/CPU/RAM) rides the existing Hetzner monitoring, which **auto-discovers the CT**; **application-level health** stays in-app via the **`scraper_runs` table** (shared with gap #9 / OQ8), a **dead-man's-switch heartbeat**, and **email-delivery confirmation**. **Settled 2026-07-03:** the off-box heartbeat → the **off-site GMK Uptime Kuma** watches the CT (also swept by the Hetzner Fleet Digest) → **[OQ5](#oq5--off-box-heartbeat)**.

**Live-state findings (2026-07-03 — verified on the server):**

- **Monitoring is rich but _on-box_.** A twice-daily "fleet-digest" runs a ~57-probe health check across every container + the host, an on-box Uptime Kuma, plus CVE scans, AIDE, Lynis. The health check **auto-discovers containers from `pct list`** — so a new **CT** _is_ monitored automatically (a VM would only get a coarse up/down probe).
- **Alert _delivery_ is off-box** (email via MS Graph → M365), so a degraded-but-reachable box can still page out.
- **But there is no off-box _watchdog_.** Every heartbeat is a push monitor to the **on-box** Uptime Kuma; the off-site GMK Uptime Kuma does not monitor Hetzner. **A total-box outage would be caught by no automated off-box observer** — exactly the failure mode the research flagged.

**Implications:** the fleet-digest health check auto-covers a new container; confirm a **disk-space threshold** alert applies (raw payloads grow). **The one real gap was an off-box heartbeat — now settled as the off-site GMK Uptime Kuma** (OQ5). Keep the in-app pieces regardless; error tracking (GlitchTip/Sentry) is not in the existing stack — add if wanted. Research: [`lightweight-observability-and-scraper-health-monitoring.md`](research/2026-07-03-lightweight-observability-and-scraper-health-monitoring.md).

### Gap 7 — UI/UX (settled: Django + HTMX + post-alert model; inventory open)

**Was:** "Provides a user-friendly web-based interface" with no page inventory, flows, or post-alert action model. Evidence: [`hw-radar.md:20`](archived/hw-radar.md).

**Decision (settled parts):** **rendering — [ADR 0004](adr/adr-0004-web-framework-django-htmx.md):** Django + server-rendered templates + HTMX, matching a single-maintainer, data-heavy CRUD+dashboard app without an SPA build chain; use the **Django admin as internal back-office**. **Post-alert model:** a per-watch, per-listing **state machine** (`none / pending / firing / cooling / digested`), first-class snooze at two granularities, one-click HMAC-signed action links, watch-as-unit-of-opt-out; dedup on listing + alert fingerprints. **Watch-rule UI:** hard filters separate from thresholds, no free-text title matching. **Still open:** the final page inventory, the dismiss→suppress feedback path, and purchase-tracking scope → **[OQ6](#oq6--final-ui-page-inventory--dismisssuppress-feedback--purchase-tracking)**. Research: [`opinionated-core-stack-recommendations…md`](research/opinionated-core-stack-recommendations-for-a-python-drive-price-monitor.md), [`designing-a-low-noise-alerting-layer…md`](research/designing-a-low-noise-alerting-layer-for-a-hard-drive-deal-monitor.md).

### Gap 8 — v1 scope / phasing / acceptance criteria (settled)

**Was:** the spec said v1 "will not" optimize for other users, but never stated what v1 **does** include vs defers across 20 marketplaces + scoring + entity resolution + a web UI. Evidence: [`hw-radar.md:7`](archived/hw-radar.md).

**Decision — phased milestones accepted as planning input;** the authoritative phased spec will be authored with the **`spec-pipeline`** plugin (these milestones map onto its phases, and the acceptance criteria are the raw material for each phase's exit gate).

| Milestone | Deliverable | Done when… |
| --- | --- | --- |
| **MS-0 Foundation** | Django stack, DB schema, single-account auth (gap #1), CD via rsync/Tailscale (gap #4) + systemd + OpenBao Agent (gap #2) | A trivial page deploys on merge and the service reads a secret from OpenBao at runtime |
| **MS-1 Ingestion (top 5)** | Acquisition for the 5 primary recert sources → normalized `listing` rows, USD-normalized | Listings from all 5 land in Postgres with FX-normalized (USD) price and an international flag where applicable |
| **MS-2 Scoring** | Scoring engine + `$/TB` baseline w/ warm-up (gap #12) + explainable sub-scores | Every listing has a reproducible 0–100 score with a visible breakdown |
| **MS-3 Web UI** | Dashboard, listing detail, watches manager (gap #7) | Owner can filter deals and create/edit a watch |
| **MS-4 Alerts** | Email alerts via the M365 Graph send path (AgentMail free fallback) with dedup/debounce | A matching drop fires exactly one actionable email |
| **MS-5 Hardening & breadth** | Remaining marketplaces, entity resolution breadth, backups (#5), observability (#6), scraper tests (#9) | Backups restore-tested; scraper-rot alerts fire; ≥15 sources live |

_(Note, added 2026-07-04: the core manufacturer spec catalog seed and the listing→catalog matcher land at MS-1–MS-2, not MS-5, per [ADR 0018](adr/adr-0018-manufacturer-spec-catalog.md)/[ADR 0019](adr/adr-0019-listing-catalog-matching-layer.md), with a coverage gate by end of MS-2. MS-5's "entity resolution breadth" above covers extending that resolver to the remaining marketplaces, not standing it up.)_

**Detailed per-milestone tasks + measurable acceptance criteria:**

- **MS-0 — Foundation.**
  - _Tasks:_ scaffold the Django project (uv-managed, BasedPyright-strict); define the canonical schema per [ADR 0010](adr/adr-0010-canonical-data-model.md) (the multi-grain identity ladder `category → product_family → product_model → product_variant → listing → offer_snapshot`, with the ADR-0015 `availability_heartbeat_observation` grain) as initial migrations; stub the `users` table + single-account session login (Argon2id); stand up the CD workflow (GitHub-hosted `ubuntu-latest` → `rsync` over Tailscale SSH); install systemd web + worker units and the local OpenBao Agent unit.
  - _Acceptance:_ merge to `main` deploys automatically with **zero manual steps**; the running web service serves an authenticated "hello" page and **reads at least one secret sourced from OpenBao** (no plaintext `.env` on the CT); `uv sync --frozen` reproduces the locked env; migrations apply cleanly from empty; a **rollback to the previous SHA** is demonstrated.
- **MS-1 — Ingestion (top 5).**
  - _Tasks:_ implement acquisition for the 5 primary recert sources (WD Recertified, Seagate Recertified, ServerPartDeals, goHardDrive, eBay Browse/Feed) using the structured-data-first tier ladder; normalize into `listing` rows; wire **Frankfurter FX → USD** with `fx_rate`/`fx_pair`/`fx_rate_date`/`fx_source` stamped **on each observation** (gap #3); set the international flag.
  - _Acceptance:_ all **5/5 sources** yield ≥1 normalized `listing` on a scheduled run; **100%** of non-USD listings carry a stored FX rate + date and a normalized USD price; international listings are flagged; a re-run produces new `observation` rows, not duplicate `listing`s.
- **MS-2 — Scoring.**
  - _Tasks:_ implement the scoring engine — log(`$/TB`) cohort percentile with 90-day window + 30-day half-life decay, cohort key = capacity · tier · interface · condition, and the **continuous warm-up** `λ = min(1, n_eff/30)` shrinkage toward 0.5 (gap #12; `/30` ratified by [ADR 0011](adr/adr-0011-composite-deal-score.md)); persist per-sub-score explanation payloads.
  - _Acceptance:_ **every** listing has a 0–100 score that is **reproducible** from stored inputs; the score exposes a **per-factor breakdown**; thin-cohort listings (n_eff < 30) visibly shrink toward neutral and are marked **provisional**; a documented cohort-relaxation fallback fires when a cohort is too small.
- **MS-3 — Web UI.**
  - _Tasks:_ build Dashboard, Listing detail (score breakdown + "why it matched"), Watches manager (hard filters vs thresholds, no free-text), Price-history view, listing-state controls (gap #7); expose the Django admin as back-office.
  - _Acceptance:_ owner can filter the dashboard by brand/capacity/tier/interface/condition and **create, edit, and delete a watch**; a listing detail renders the **pass-margin** explanation for each crossed threshold; state changes persist and re-render.
- **MS-4 — Alerts.**
  - _Tasks:_ email alerts via the **M365 Graph send path** (AgentMail free fallback; [ADR 0013](adr/adr-0013-notification-transport-m365-graph.md)) with the dedup/debounce design — listing + alert fingerprints, cooldown/hysteresis, signed one-click action links, **email-delivery confirmation**.
  - _Acceptance:_ a single qualifying price drop fires **exactly one** email (verified against the alert-fingerprint ledger); one-click snooze/stop links work and are HMAC-verified; a simulated repost under a new URL is **de-duplicated**; a delivery failure is **detectably surfaced**.
- **MS-5 — Hardening & breadth.**
  - _Tasks:_ add the remaining marketplaces, entity resolution breadth, backups (#5), application self-observability (#6), and the scraper test suite (#9 — vcrpy + syrupy + per-tier contract canary).
  - _Acceptance:_ **≥15 sources** live; a backup is **restore-tested into a scratch instance** at least once; a deliberately broken parser trips a **scraper-rot alert within one scheduled cycle**; CI runs the cassette/snapshot suite green.

### Gap 9 — Scraper testing strategy (settled stack + amendments; build-time params open)

**Was:** CI names "testing workflows" but the spec never said **how** to test scrapers against sites that change and fight bots. Evidence: [`hw-radar.md:73`](archived/hw-radar.md).

**Decision (settled):**

- **Recorded fixtures:** **vcrpy cassettes** per source (record once, replay in CI) — deterministic, offline parse tests.
- **Snapshot tests:** **syrupy** golden-file assertions on parsed output.
- **Production canary:** a **scheduled** live **structured-data contract check** per source + a **known-value canary page**.
- **Runtime validation:** **Pydantic v2** per-record validation + `last_success_at` / consecutive-failure counters + a count-vs-rolling-average assertion; alert when a source returns 0/malformed N runs in a row. Shares the `scraper_runs` table from gap #6.

**Five research-confirmed amendments (2026-07-03):** (1) canary must be **per-extraction-tier**, not JSON-LD-only — tiers break independently; (2) **risk-weight canary frequency by tier** — fragility increases down the ladder; (3) **scrub cassettes of PII before commit** (compliance, not hygiene); (4) **do not commit real cassettes for retention-restricted sources** — use synthetic fixtures; real cassettes fine for recert specialists; (5) **classify failure type** — parser rot vs now-anti-bot-protected (a soft-block often returns HTTP 200 + empty body). **Still open:** the concrete build-time parameters (per-tier frequencies; synthetic-vs-real cassette assignment) → **[OQ8](#oq8--scraper-testing-finalization)**. Research: [`lightweight-observability…md`](research/2026-07-03-lightweight-observability-and-scraper-health-monitoring.md), [`pragmatic-architecture…md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md), [`us-scraping-and-data-retention-landscape…md`](research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md).

### Gap 10 — Running-cost / budget model (settled approach; pricing pass open)

**Was:** paid search APIs, AgentMail, object storage, and possible managed-scraping APIs had no aggregate budget or ceiling to design polling frequency against. Evidence: [`hw-radar.md:55`](archived/hw-radar.md), [`:109`–`:111`](archived/hw-radar.md).

**Decision (approach):** **prefer free official feeds** (eBay Browse/Feed, structured-data parsing) over paid search calls — search APIs are for _discovery_, not per-poll refresh. **Config-driven per-source poll budget** held under a stated **monthly ceiling**, reusing the orchestration research's **two-level token buckets** (per-source + per-domain). Track actuals via the `scraper_runs` table. Managed-scraping APIs avoided for this merchant set (free structured data covers it; reserve them for a hostile tail — or skip the source). **Still open:** the build-time pricing pass (current Serper/Brave/Tavily per-call pricing, AgentMail, backup object-storage costs; Brave storage-rights plan) → **[OQ7](#oq7--running-cost-budget-model-build-time-pricing-pass)**. Research: [`programmatic-acquisition…md`](research/programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md), [`tavily-brave-serper.md`](research/tavily-brave-serper.md), [`pragmatic-architecture…md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md), [`orchestration-choice…md`](research/orchestration-choice-for-a-single-vm-price-polling-service.md).

### Gap 11 — Shipping (and tax) in the `$/TB` score (settled)

**Was:** `$/TB` on item price alone misranks a cheap drive with high shipping — even domestically. Evidence: [`hw-radar.md:13`](archived/hw-radar.md).

**Decision (accepted):**

- Score on **price + shipping (+ tax where known)**, not item price alone. Marketplace shipping fields (eBay Browse `shippingOptions`, Serper `delivery`) are reliable **only when the request supplies correct buyer-location context** — pin the buyer location on every query.
- When shipping is unknown, **apply a penalty or flag** rather than silently scoring as if free.
- Composes with the **international flag** from gap #3 (which dropped the fixed overhead haircut): domestic known shipping is folded into `$/TB`; cross-border extra cost stays a flag, not a number.

Research: [`currency-conversion-and-landed-cost-estimation…md`](research/2026-07-03-currency-conversion-and-landed-cost-estimation-for-cross-border-drive-price-scoring.md).

### Gap 12 — Cold-start scoring (settled)

**Was:** the moving-baseline / percentile scoring needs accumulated history that doesn't exist at launch. Evidence: [`hw-radar.md:19`](archived/hw-radar.md), [`:22`](archived/hw-radar.md).

**Decision (resolved via research):**

1. **Replace the hard "switch at N observations" with continuous shrinkage:** `s_price = λ·(1 − q) + (1 − λ)·0.5` with **`λ = min(1, n_eff/30)`**, so a thin cohort is pulled toward neutral **0.5** and reaches full confidence at **≈30 effective observations**. This _is_ the built-in provisional-confidence mechanism (surface `n_eff`/`λ` as the "provisional" indicator). _(Originally spec'd `n_eff = 50`; OQ11 testing lowered the full-confidence target to `n_eff = 30`, ratified by [ADR 0011](adr/adr-0011-composite-deal-score.md) — see also [OQ11](#oq11--composite-scoring-model-adopt-research-4).)_
2. **Threshold on _effective_ sample size:** `n_eff = (Σw)²/Σ(w²)` under a 90-day window with **30-day half-life** decay (`w = 2^(−age_days/30)`).
3. **Fallback shrinks toward 0.5, not toward an absolute `$/TB` verdict** — prefer documented **cohort-relaxation** (relax condition → adjacent capacity → parent tier) over an absolute table; keep dated `$/TB` bands only as a UI reference / sanity floor.
4. **Expand the cohort key** to **capacity · tier · interface/form-factor · condition bucket**; for SSDs add **endurance class (DWPD)** — SSDs must never be compared on capacity alone.

**Seed sources, graded:** **Keepa** is the only sanctioned machine-readable seed (paid API); **ServerPartDeals** is the key recert benchmark (no API — parse its catalog); **diskprices.com / CamelCamelCamel / PCPartPicker** are UI/sanity references only; **r/DataHoarder** is qualitative. Every seed is **market-dated** (the 2026 bands reflect an abnormal ~46% supply-constrained run-up) — timestamp and age out any seeded baseline as real observations accrue. Research: [`principled-deal-score…md`](research/principled-deal-score-for-hard-drive-listings.md), [`drive-deal-tracker…baselines…md`](research/drive-deal-tracker-research-baselines-tools-shucking-and-timing.md).

## Scope & research provenance

This document deliberately **excludes** anything already covered elsewhere:

- **Covered by research docs** — search APIs, official eBay/Amazon/Newegg APIs, storage/caching-compliance ([`research/tavily-brave-serper.md`](research/tavily-brave-serper.md)); DB engine, schema, entity resolution, price-history modelling ([`research/database-architecture.md`](research/database-architecture.md)).
- **Covered by domain research** — the 17 non-API merchants, drive-grading taxonomy, recert risk/warranty, scoring math, `$/TB` baselines, entity resolution, legal/ToS, anti-bot scraping, job scheduling, framework/DB/env `_TBD_`s, notification/dedup design, serial/warranty verification — queued in [`further-research-needed-prompts.md`](further-research-needed-prompts.md); **all landed** under [`research/`](research/) and folded into the gaps above.

Everything above is the **operational / product-engineering** layer a research-first spec tends to leave blank.

**How the solutions were researched:** five parallel sweeps (Tavily-first, Context7-gated, cross-corroborated) on 2026-07-03, each persisted under `docs/research/`:

| Report | Informs |
| --- | --- |
| [`2026-07-03-github-actions-cd-private-debian-vm.md`](research/2026-07-03-github-actions-cd-private-debian-vm.md) | gap #2, #4 · OQ1, OQ2 |
| [`2026-07-03-auth-for-self-hosted-single-maintainer-python-app.md`](research/2026-07-03-auth-for-self-hosted-single-maintainer-python-app.md) | gap #1 |
| [`2026-07-03-currency-conversion-and-landed-cost-estimation-…md`](research/2026-07-03-currency-conversion-and-landed-cost-estimation-for-cross-border-drive-price-scoring.md) | gap #3, #11 |
| [`2026-07-03-postgresql-backup-disaster-recovery-single-vm.md`](research/2026-07-03-postgresql-backup-disaster-recovery-single-vm.md) | gap #5 · OQ3 |
| [`2026-07-03-lightweight-observability-and-scraper-health-monitoring.md`](research/2026-07-03-lightweight-observability-and-scraper-health-monitoring.md) | gap #6, #9 · OQ5, OQ8 |

## Resolved OQs

OQ1–OQ15, relocated here on 2026-07-04 ([OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling) and [OQ15](#oq15--amazon-acquisition-path-after-pa-api-deprecation) on their resolution later the same day), joined by **OQ16–OQ20** from the same-day gap analysis on their owner-resolution ([OQ17](#oq17--heartbeat-grain-retention--storage-policy) and [OQ20](#oq20--oss-license-compliance-posture) research-backed). Their `#oq#` anchors are preserved so ADR/TODO/spec/research back-links keep resolving. **ADR-backed OQs are condensed to a one-line resolution + ADR link** (the ADR is canonical); **OQs with no ADR retain their full decided substance** (this document is their record).

| # | Question | Resolution |
| --- | --- | --- |
| **OQ1** | `secret_id` delivery to the CT | Onboard as the next **`bao-services`** consumer via a local **bao-agent**; SecretID delivered out-of-band (issuer script → response-wrap + `pct push`), persistent + CIDR-bound (container ID, on-disk path, CIDR value, and issuer-script name in the private `homelab` repo). **[ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md)** · verified live 2026-07-04. |
| **OQ2** | Ephemeral-runner tailnet auth | **Tailscale OAuth client** (`secret/infra/tailscale-oauth`, `tag:ci`) via `tailscale/github-action` v4. ⚠️ add a `tag:ci→CT` grant when the wildcard ACL is scoped. **[ADR 0006](adr/adr-0006-cd-rsync-over-tailscale-ssh.md)** · verified live 2026-07-04. |
| **OQ3** | DB RPO + TimescaleDB dump handling | **≤1 h RPO accepted for v1**; **TimescaleDB-aware logical dumps** (`timescaledb_pre_restore()`/`post_restore()`); extended monthly retention; **B2 tier-1 declined** (physical/pgBackRest = Future trigger). _No ADR — substance below._ |
| **OQ4** | DB placement | Own Postgres **inside the hw-radar CT** (self-contained; shared-datastores-CT rejected). **[ADR 0003](adr/adr-0003-deploy-as-lxc-container.md)**. |
| **OQ5** | Off-box heartbeat | **Off-site GMK Uptime Kuma** watches the CT (also swept by the Hetzner Fleet Digest; healthchecks.io rejected). _No ADR — substance below._ |
| **OQ6** | UI inventory + dismiss→suppress | Inventory confirmed as-is; **dismiss = permanent per-listing suppress** via the existing `watch_match_state` enum (no TTL/new table); **purchase analytics deferred** (ship only a `purchased` flag). _No ADR — substance below._ |
| **OQ7** | Running-cost + search self-governance | Per-provider token bucket + **hard PostgreSQL spend-cap circuit-breaker** (reserve-then-call) + failing-provider breaker + per-provider user settings. ⚠️ **Brave killed its free tier (Feb 2026)** → lean on Serper. **[ADR 0016](adr/adr-0016-search-api-self-governance.md)** (starting numbers provisional/tunable — see substance below). |
| **OQ8** | Scraper testing finalization | Per-tier canary cadence (24/12/8/4 h), **synthetic-only cassettes** for all named sources, PII scrub, failure-classification tree, 3-workflow CI. _No ADR — substance below._ |
| **OQ9** | Acquisition cadence, throttle & skip | **baseline → ceiling + earned auto-ramp** per tier (T0–T4), adaptive back-off ladder (24 h cap), soft-block detection, skip decision tree. _No ADR — substance below._ |
| **OQ10** | Reliability / resilient acquisition | Per-source isolation, retry/back-off, `paused_pending_fix` circuit-break, `scraper_runs` health alerts. **[ADR 0017](adr/adr-0017-resilient-acquisition.md)**. |
| **OQ11** | Composite scoring model | Weighted geometric mean over four subscores + non-compensatory veto caps; tested against mock data (passes); warm-up `n_eff 50→30`. **[ADR 0011](adr/adr-0011-composite-deal-score.md)**. |
| **OQ12** | Orchestration engine | **APScheduler 3.11.x** in one systemd-supervised poller. **[ADR 0012](adr/adr-0012-orchestration-apscheduler.md)** · ADR-0006 "timers" amended. |
| **OQ13** | Notification transport | **Reuse the existing M365 Graph send path** (branded, zero marginal cost); AgentMail free fallback. **[ADR 0013](adr/adr-0013-notification-transport-m365-graph.md)**. |
| **OQ14** | Scraping runtime escalation stack | Playwright = code-driven headless (no LLM), selective via `scrapy-playwright`, browser-last; **defer curl_cffi/Playwright to MS-5**. **[ADR 0014](adr/adr-0014-scraping-runtime-escalation-stack.md)**. |
| **OQ15** | Amazon acquisition after PA-API deprecation | **Discovery-only via the search-API stack** (ASIN from `/dp/<ASIN>` URLs; SERP price = low-confidence 24 h hint); SP-API seller-only, Creators API sales-gated, PA-API closed; no direct scraper. _No ADR — substance below._ |
| **OQ16** | SSD cohort-key DWPD dimension | **DWPD folds into the _fitness_ subscore, not the cohort key** — [ADR 0011](adr/adr-0011-composite-deal-score.md)'s four-part key stands for SSDs. _No ADR amendment — substance below._ |
| **OQ17** | Heartbeat-grain retention & storage | **Hypertable + 30-day raw retention + indefinite daily continuous aggregate + 365-day plain `availability_heartbeat_event` table** for non-`unchanged` rows; compression ≈7 d; values tunable. _No ADR (rides [ADR 0015](adr/adr-0015-availability-heartbeat-grain-volatility-scheduling.md)) — substance below._ |
| **OQ18** | RTO for v1 | **≤24 h, manual-runbook restore**; no restore automation for v1; verified by the ≥once-by-MS-5 timed restore test. _No ADR — substance below._ |
| **OQ19** | Accessibility & i18n | **Out of scope for v1** (single sighted user, English-only — Engineered to Needs); spec §11 declaration + WH-008. _No ADR — substance below._ |
| **OQ20** | OSS license-compliance posture | **Allowlist license gate via `dependency-review-action@v4`** (uv.lock-native) + manual `licensecheck` for direct-to-branch dep adds; §16 reworded; TimescaleDB TSL self-hosted posture recorded. _No ADR — substance below._ |

### OQ1 — `secret_id` out-of-band delivery to the CT

**✅ Resolved (verified live 2026-07-04) → [ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md).** hw-radar onboards as the next **`bao-services`** consumer (reusing an existing bao-agent pattern already live for another service on the same host): a local **bao-agent** AppRole-auto-auths and renders secrets to tmpfs; the `secret_id` is delivered operator→CT out-of-band by an issuer script (300 s response-wrap + `pct push`) to a persistent, root-owned `0600` on-disk file (`remove_secret_id_file_after_reading=false`), with a long-lived (`num_uses=0, ttl=0`) **CIDR-bound** consumer AppRole as the active control. Concrete container IDs, on-disk paths, the CIDR value, and the issuer-script name live in the private `homelab` repo. Full write-up in [Resolved gap #2](#gap-2--env-secrets-model--openbao-settled-except-secret_id-delivery). (The spec's `remove_secret_id_file_after_reading=true` is superseded — it breaks restart safety; the vTPM shortcut is out — a CT has no per-container TPM, see [RQ3](#resolved-questions)/[RQ5](#resolved-questions).)

**My Comments:** SSH into Hetzner and look at the existing infra automation, particularly the CT with OpenBao. I believe that bao-agent will be able to resolve this issue.

### OQ2 — Ephemeral-runner tailnet auth

**✅ Resolved (verified against the live ACL 2026-07-04) → [ADR 0006](adr/adr-0006-cd-rsync-over-tailscale-ssh.md).** Use the **Tailscale OAuth client** (`secret/infra/tailscale-oauth`, purpose "GitHub Actions CI/CD deploy via Tailscale") via `tailscale/github-action` **v4**, minting an ephemeral node tagged `tag:ci` — no pre-generated ephemeral auth key. Full write-up in [Resolved gap #4](#gap-4--deployment--service-topology-settled-adr-0006). ⚠️ **Latent dependency:** the tailnet ACL grants are still wildcard; when the pending wildcard→scoped migration lands, add an explicit `{src:["tag:ci"], dst:["<hw-radar CT>"], ip:["22"]}` grant or the deploy silently breaks. (The mechanism is `tailscale ssh` per [ADR 0006](adr/adr-0006-cd-rsync-over-tailscale-ssh.md) — no public SSH port.)

**My Comments:** Use the Tailscale API key from OpenBao to access the tailnet and check the ACLs. If the ACLs allow for OAuth client auth, then use that. Otherwise, provide additional options/alternatives.

### OQ3 — DB RPO acceptance (+ TimescaleDB dump handling)

**✅ Resolved (owner-ratified 2026-07-04) — no ADR; this is the record.** From gap #5 (resolved CT path, [ADR 0003](adr/adr-0003-deploy-as-lxc-container.md)); TimescaleDB driver from [ADR 0007](adr/adr-0007-datastore-postgresql-timescaledb.md). The owner's direction was to **document requirements before picking an RPO**; the requirements analysis was authored and verified live in the private `homelab` repo (`docs/plans/2026-07-04-hw-radar-backup-requirements.md`, verified against `backup-dumps.sh`/`backup-restic.sh`), and its §7 decisions were then ratified.

- **Headline finding:** hw-radar is the fleet's **first TimescaleDB consumer**, but every existing dump is plain `pg_dump --format=custom` with no hypertable awareness → a naïve allowlist entry **restores incorrectly**. So the load-bearing work is **TimescaleDB-correct dumps + wiring coverage, not tighter RPO**.
- **Owner-ratified decisions (2026-07-04, doc §7):**
  1. **RPO ≤ 1 h accepted for v1** — append-heavy time-series makes an hour's loss bounded; revisit only if [OQ9](#oq9--acquisition-cadence-throttle--skip-policy) sets sub-hourly polling.
  2. **B2 tier-1 declined for v1** — rely on local ZFS + the Hetzner Storage Box hourly copy (one offsite). Deviates from the doc's "join B2" recommendation; the second independent offsite is a Future trigger (revisit as the moat grows).
  3. **Dump method: TimescaleDB-aware logical** (`timescaledb_pre_restore()`/`post_restore()`; compression state not preserved) at the inherited hourly cadence — stays inside the existing pipeline. Physical (pgBackRest + WAL/PITR) is deferred to a Future trigger (OQ9 sub-hourly polling, or PITR becoming required).
  4. **Extended monthly retention** — keep monthly snapshots beyond the pipeline default of 6 months to guard the compounding price-history moat; horizon set at provisioning.
- **Residual (implementation, not a decision) — must precede the first backup being taken:** at CT provisioning, (a) confirm the CT `subvol` is in `backup-restic.sh` file-level coverage; (b) add a **TimescaleDB-aware** dump block for the hw-radar DB to `backup-dumps.sh` + document the `pre_restore`/`post_restore` restore path; (c) extend monthly-snapshot retention; (d) confirm the disk-space threshold alert fires (raw scrape payloads grow unbounded); (e) keep the **monthly restore-test** discipline and **patch** `pg_dump`/`pg_basebackup`/`pg_rewind` (CVEs live in the tools). Tracked as a provisioning task in `TODO.md`.
- Research: [`postgresql-backup-disaster-recovery-single-vm.md`](research/2026-07-03-postgresql-backup-disaster-recovery-single-vm.md). Full requirements analysis: `homelab/docs/plans/2026-07-04-hw-radar-backup-requirements.md`. See also [Resolved gap #5](#gap-5--backup--disaster-recovery-settled-ct-path-adr-0003).

**My Comments:** Create a document of all backup requirements and constraints, including RPO, PITR, and TimescaleDB considerations. Evaluate the current backup strategy against these requirements and determine if the current ≤1 h RPO is acceptable or if a more robust solution is needed. Go into the `homelab` repo and create appropriate documentation for hw-radar and document it's backup needs there. The backup strategy will have to be coordinated with the existing Hetzner backup strategy and any other relevant infrastructure. We can expand/improve as necessary, but the first step is to document the requirements and constraints. This is not a blocker, it can be completed in parallel or after the project is deployed, but it should be done before the first backup is taken.

### OQ4 — DB placement: own CT vs shared datastores CT

**✅ Resolved (owner, 2026-07-03) → [ADR 0003](adr/adr-0003-deploy-as-lxc-container.md).** Own Postgres **inside the hw-radar CT** — the app and its database are self-contained in one CT (consistent with the spec's "same container" intent; simpler to deploy and manage). The shared-datastores-CT option is rejected. **Residual (implementation, not a decision):** at provisioning, add the CT's DB to `backup-dumps.sh` with the **TimescaleDB-aware** dump caveat from [OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling). See [Resolved gap #5](#gap-5--backup--disaster-recovery-settled-ct-path-adr-0003).

**My Comments:** The app/project and its associated database should be self-contained in the hw-radar CT. This is consistent with the spec's intent and simplifies deployment and management.

### OQ5 — Off-box heartbeat

**✅ Resolved (owner, 2026-07-03) — no ADR; this is the record.** Use the **off-site GMK Uptime Kuma** to watch the hw-radar CT (reuses existing infra; already alerts by email), additionally swept periodically by the **Hetzner EX130-R · Fleet Digest** (see the `homelab` repo). The **healthchecks.io** option is rejected. **Non-blocking** — land before entering production.

- **Settled and kept regardless** (generic infra monitoring can't see these): the in-app **`scraper_runs` table** (shared with [OQ8](#oq8--scraper-testing-finalization) / gap #9), a **dead-man's-switch heartbeat**, and **email-delivery confirmation**. Infra health (up/disk/CPU/RAM) is auto-covered — the fleet-digest health check auto-discovers the CT from `pct list`; confirm a **disk-space threshold** alert applies since raw scrape payloads grow. Error tracking (GlitchTip/Sentry) is **not** in the existing stack — add it only if wanted.
- Research: [`lightweight-observability-and-scraper-health-monitoring.md`](research/2026-07-03-lightweight-observability-and-scraper-health-monitoring.md). See also [Resolved gap #6](#gap-6--application-self-observability-settled-ct-path-except-off-box-heartbeat).

**My Comments:** We will use the existing GMK Uptime Kuma instance to monitor the hw-radar CT. This is consistent with the existing infrastructure and will provide the necessary off-box heartbeat monitoring. This will also be monitored periodically by the `Hetzner EX130-R · Fleet Digest` (see the `homelab` repo) for details. This is not a blocker, it can be completed in parallel or after the project is deployed, but should be done before entering production.

### OQ6 — Final UI page inventory + dismiss→suppress feedback + purchase tracking

**✅ Resolved (research 2026-07-04, [`mvp-web-ui-inventory-and-dismiss-suppress.md`](research/mvp-web-ui-inventory-and-dismiss-suppress.md); validated against CamelCamelCamel, Keepa, changedetection.io, Slickdeals) — no ADR; this is the record.**

1. **Page inventory confirmed as-is — no additions.** Dashboard · Listing detail (score breakdown + "why it matched") · Watches manager (hard filters vs thresholds, no free-text) · Price-history view · Listing-state controls, with the Django admin as internal back-office.
2. **Dismiss→suppress = a permanent, per-listing suppression flag** — _not_ a TTL, _not_ drive-model-scoped by default — implemented as a **new terminal value on the existing `watch_match_state.current_state` enum**, not a new table. Every comparable tool treats "I'm done with this" as a binary, permanent, per-item action; resist inventing a scoped/TTL suppression system.
3. **Purchase tracking: defer the analytics** (realized-savings, spend history) to post-v1. Ship only a lightweight `purchased` status flag + two optional nullable fields (price, date) as scaffolding — comparable tools use "purchased" only to _stop tracking_, none report savings.

- **Post-alert model (settled):** per-watch, per-listing state machine (`none / pending / firing / cooling / digested`); snooze at two granularities (watch `snoozed_until`; listing 24 h / 7 d); one-click actions as HMAC-signed single-purpose links; the **watch is the unit of opt-out**. The alerting report's "why it matched" block (current facts + per-threshold pass-margin line) is the reusable listing-detail explainability view.
- Research: [`opinionated-core-stack-recommendations-for-a-python-drive-price-monitor.md`](research/opinionated-core-stack-recommendations-for-a-python-drive-price-monitor.md), [`designing-a-low-noise-alerting-layer-for-a-hard-drive-deal-monitor.md`](research/designing-a-low-noise-alerting-layer-for-a-hard-drive-deal-monitor.md). See also [Resolved gap #7](#gap-7--uiux-settled-django--htmx--post-alert-model-inventory-open).

**My Comments:** This question has been open for a while. We need to confirm the final MVP page inventory and flows in case new information changes your recommendation. If necessary, conduct research using /qdev:research and update the open question and existing research doc(s) with the findings.

### OQ7 — Running-cost budget model (build-time pricing pass)

**✅ Resolved (research 2026-07-04, [`search-api-self-governance-and-user-configurable-limits.md`](research/search-api-self-governance-and-user-configurable-limits.md)) → [ADR 0016](adr/adr-0016-search-api-self-governance.md) (ratified 2026-07-04).** ADR-0016 is the authoritative record for the **architecture** (the ordered `SearchBudgetGate`, the persisted reserve-then-call spend ledger, the per-provider settings schema, fail-safe semantics); the **starting numeric values** below (bucket rates, spend caps, the $8–15 envelope, 2026 provider pricing) remain _provisional_ and tunable — re-verify before build.

- **Rate-limit our own search calls per provider** via a **per-provider token bucket** (reusing the orchestration `rate`/`burst` vocabulary). Starting numbers: **Serper** 1 call/2 min, burst 5, 200/day · **Brave** 1/5 min, burst 3, 50/day · **Tavily** 1/5 min, burst 3, 30/day.
- **Hard spend-cap circuit-breaker** as the runaway-bug guard: a **persisted PostgreSQL `daily_spend_cap_usd`/`monthly_spend_cap_usd` counter checked _before every call_** (reserve-then-call), failing safe (`budget_exhausted`) — never rely on the provider's own dashboard cap (2026 reports show those behave as alert-only). Plus a manual `kill_switch`. Reconcile against provider-reported usage; only ever _tighten_ the local counter.
- **Failing-provider circuit-breaker:** closed→open→half-open per provider (5 failures/10 min → open; 5-min cooldown doubling to a 60-min cap; single half-open trial; accelerated trip on 429/`Retry-After`/auth-quota errors). Everything composes into one ordered `SearchBudgetGate`: **kill switch → spend cap → circuit breaker → token bucket.**
- **In-app user limits:** one settings row per provider (`enabled`, `rate_per_min`, `burst`, `daily_call_cap`, `daily_spend_cap_usd`, `monthly_spend_cap_usd`, `alert_threshold_pct=80`, `kill_switch`, breaker params), plus an optional `aggressiveness` enum (conservative/standard/aggressive) scaling the numeric fields.
- **Free-feed-first is confirmed viable:** no target merchant exposes a paid-only retail API; **eBay Browse + Feed** are free official feeds — do not re-poll them via paid search APIs. Search APIs are discovery/spot-check only. _(This line originally also listed **Amazon SP-API** "if seller-authorized" as a free feed; **[OQ15](#oq15--amazon-acquisition-path-after-pa-api-deprecation)** confirmed SP-API is seller-only and unobtainable for a buyer-only tool — Amazon is **discovery-only**, no official feed.)_ **Use search providers purely as a discovery/URL source; do not persist their own snippets/JSON** — the scraper stores the _listing page's_ own content, outside the provider's licensing scope.
- **⚠️ Pricing corrections (2026):** **Brave killed its free tier in Feb 2026** — now **$5/1,000 metered, no free allowance** → weight discovery traffic toward Serper (~5× cheaper than Brave, ~8× than Tavily). Rough envelope ≈ **$8–15/mo** for the three search APIs if Serper-weighted (inside the $10–20 target). **AgentMail free tier (2026):** 3 inboxes, **3,000/mo, 100/day** — effectively free at this workload. **Watch items:** Brave storage-rights is now Enterprise-only (get a live quote before relying on caching); Tavily was acquired by Nebius (2026-02-10) — re-verify pricing before build.
- Research: [`programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md`](research/programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md), [`tavily-brave-serper.md`](research/tavily-brave-serper.md), [`pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md), [`orchestration-choice-for-a-single-vm-price-polling-service.md`](research/orchestration-choice-for-a-single-vm-price-polling-service.md). See also [Resolved gap #10](#gap-10--running-cost--budget-model-settled-approach-pricing-pass-open).

**My Comments:**

_Search:_

_New Comments:_ I am not sure what search rates were assumed for the estimates from agent comments above. Further research should include:

- Should we rate limit our own search calls on a per-source basis to a reasonable time interval (e.g., 1 call per 1 minute per source) to avoid unreasonable usage and to prevent any unexpected costs (i.e. software bug results in excessive calls) or hitting any rate limits?
- Should we implement a circuit breaker for chronically failing sources to prevent unnecessary costs and API abuse?
- User settings in the app to adjust limits, timing, aggressiveness, etc. (e.g., user can set a maximum number of searches per day/week/month, or adjust the aggressiveness of the search frequency)?

Past/resolved comments:

```markdown
I have active accounts for each search service and I keep them topped up with funds. This question will require additional research to find the current per-call pricing for each service and to verify Brave's storage-rights plan requirement. Since I use these services elsewhere we will assume that any monthly free tier limits have already been exceeded and that we will be paying for any calls made. I feel that I will be comfortable with $10 to $20 per month total for all three services combined, but will reconsider after additional research (to be performed by Claude).
```

_AgentMail:_ AgentMail is free; research the free tier limits, but it is unlikely to be a problem.

_Backup Costs:_ The backup object storage costs are already budgeted as part of my general server costs, but I will verify that the current plan is sufficient for the expected data volume. No further action is needed for this, but I will keep an eye on it.

### OQ8 — Scraper testing finalization

**✅ Resolved (research #13 reconciled, 2026-07-04, [`automated-test-policy-for-a-low-volume-scrapy-price-monitor.md`](research/automated-test-policy-for-a-low-volume-scrapy-price-monitor.md)) — no ADR; this is the record.**

- **Per-tier canary cadence:** JSON-LD **24 h** · first-party platform-JSON **12 h** · hidden bootstrap-JSON **8 h** · HTML selectors **4 h** — or `interval_hours = max(4, min(24, 24 / tier_risk_weight / source_business_weight))` with `tier_risk_weight = {jsonld:1, platform_json:2, bootstrap:3, html:6}` and `source_business_weight` = 1 (normal) / 2 (high-value).
- **Degradation is a first-class signal**, distinct from hard failure: alert when `actual_tier_rank > expected_tier_rank` for **≥2 consecutive** runs or **≥3 of last 5**; separate quality alert when `required_fields_present_pct < max(0.90, rolling_30d_median − 0.20)`. The canary emits `selected_tier`/`expected_best_tier`/`required_fields_present_pct`/`record_count`/`content_type`/`body_bytes` per source·URL-class.
- **Cassette policy = synthetic-only for every named commercial source** (WD, Seagate, ServerPartDeals, goHardDrive, eBay, Amazon, Newegg, Google/Serper) — the public-repo commit rule fails for all (redistribution/anti-automation/PII). Keep any real cassettes **private**; derive public **synthetic** fixtures from normalized parser output; **never commit product images**.
- **PII scrubbing** via vcrpy `filter_headers`/`filter_query_parameters`/`filter_post_data_parameters` + `before_record_request`/`before_record_response` (drop auth/cart/checkout paths, strip cookies/tokens/seller-buyer identifiers/image URLs, drop anti-bot interstitials). Ship with `record_mode="none"` in CI.
- **Failure-classification tree:** `transient` (timeout/5xx/DNS/TLS/408) → `anti_bot` (401/403/429/503, `cf-mitigated=challenge`, JSON endpoint returns `text/html`, Cloudflare/DataDome markers) → `parser_rot` (HTTP 200 authentic page but extractor/Pydantic fails) → `degradation` (validates but tier worsened) → else `UNKNOWN`+escalate.
- **CI = three workflows:** offline VCR-replay (`record_mode=none`, **PR-required**) · snapshot-refresh (`workflow_dispatch`, live, non-required) · production-canary (`schedule`, live, **NON-required**, opens/updates a GitHub issue on failure with `issues: write`).
- Shares the `scraper_runs` table with [OQ5](#oq5--off-box-heartbeat) / gap #6. Research: [`automated-test-policy-for-a-low-volume-scrapy-price-monitor.md`](research/automated-test-policy-for-a-low-volume-scrapy-price-monitor.md), [`us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md`](research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md). See also [Resolved gap #9](#gap-9--scraper-testing-strategy-settled-stack--amendments-build-time-params-open). Residual is a spec fold (testing section + MS-5), not a decision.

**My Comments:** Agent comments look good, but I want to conduct a full deep research with ChatGPT looking into this. Add an entry and prompt to `docs/further-research-needed-prompts.md`.

I think this was done? Check `docs/research/` and `docs/further-research-needed-prompts.md` to see if this was already done. If it was, then we can close this OQ. If not, then we need to do the research and update the OQ with the findings.

If additional research is needed, we should use the `/qdev:research` command to conduct the research and update the open question and existing research doc(s) as applicable with the findings.

### OQ9 — Acquisition cadence, throttle & skip policy

**✅ Resolved (research 2026-07-04, [`per-source-polling-cadence-and-skip-policy.md`](research/per-source-polling-cadence-and-skip-policy.md)) — no ADR; this is the record.** "Aggressive but self-moderating" is encoded as **baseline + hard ceiling per tier + earned auto-ramp**, never one fixed number. (Folds General Design Principles findings #1 + #5; the spec's principle is now **"Moderate Aggressive Usage"** at [`hw-radar.md:21`](archived/hw-radar.md). The original "real-time" Features framing was first ruled consistent, then **superseded 2026-07-04**: FR-001 was reworded to a **per-source freshness SLO** governed by a second _volatility_ scheduling axis, per [research prompt #15](further-research-needed-prompts.md#15-per-source-inventory-volatility--fast-lane-polling-affordance) + the [polling-cadence reconciliation](research/2026-07-04-polling-cadence-reconciliation.md).) **Tier ceiling vs fast-lane SLO ([ADR 0015](adr/adr-0015-availability-heartbeat-grain-volatility-scheduling.md) reconciliation):** the per-tier ceilings below bound **full-fetch** cadence only; a fast-lane source (drop-prone ∩ verified cheap signal — e.g. eBay recert, WD/Seagate direct; ServerPartDeals is `churning`, so it keeps its cheap heartbeat at tier cadence without fast-lane membership — corrected 2026-07-05 per the volatility research) additionally runs a cheap `availability_heartbeat_observation` on a separate, faster budget to meet its ≤3-min freshness SLO, so a source's tier ceiling (e.g. T2's 15 min) is **not** a floor on its heartbeat polling. `effective cadence = min(tier ceiling, volatility need)` governs full fetches; the cheap heartbeat is exempt, while a slow full-fetch repair crawl still runs at the tier ceiling.

- **Per-tier cadence (baseline → ceiling):** **T0** eBay API 10 min → 2 min · **T1** WD/Seagate direct 30 min → 5 min · **T2** specialist/VAR resellers (ServerPartDeals, goHardDrive, B&H, CDW, Insight) 1 h → 15 min · **T3** anti-bot-exposed (Newegg, PCNation, Wiredzone; **Amazon is discovery-only via search APIs — [OQ15](#oq15--amazon-acquisition-path-after-pa-api-deprecation), not a direct scrape**) 2 h → 30 min · **T4** refurb/regional (TechMikeNY, ETB, Bargain Hardware, …) 4 h → 1 h.
- **Auto-ramp:** after **N=4** consecutive clean polls (no error/soft-block, latency < 2× rolling median) halve the interval, floored at the tier ceiling; any throttle/soft-block resets to baseline and hands to the back-off ladder.
- **Adaptive back-off ladder:** timeout → 1 in-run retry (full-jitter ≤10 s) · 429 w/ `Retry-After` → honor verbatim (clamp 1 s..baseline) · **429/503 w/o header or soft-block → `random(0,1)×min(24 h, 10 min×2^failures)`** (the 24 h cap = the original daily-check floor) · latency spike (>3× median ×3 polls) → halve cadence (slow-down, not stop).
- **Soft-block detection** (HTTP-200-but-not-your-page): structured-data absence · body-size outlier (<20% of median) · known challenge markers · repeated identical hash despite confirmed price/stock movement → reclassify the fetch as failed.
- **Skip policy:** the ladder is official API → structured data → `curl_cffi` → selective Playwright → managed unblocker (tiny high-value tail) → **SKIP**. A working source whose cooldown repeatedly maxes at 24 h on a **soft-block** (not parser-rot), after exhausting the ladder up to but not including the CAPTCHA/stealth/heavy-proxy rung, becomes a **permanent SKIP** (registry state, human re-review). Parser-rot → `paused_pending_fix` (code fix + daily recovery probe). Legal/ToS triggers force SKIP regardless. **Amazon = discovery-only** (search-API index reads; ASIN parsed from `/dp/` URLs), **superseding the earlier "T3 scrape target" treatment** — SP-API is seller-only and the Creators API is sales-gated, so there is no official Amazon feed and a direct Amazon scraper is avoided for ToS/robots exposure ([OQ15](#oq15--amazon-acquisition-path-after-pa-api-deprecation)).
- Shares the two-level token-bucket substrate with [OQ7](#oq7--running-cost-budget-model-build-time-pricing-pass) and the circuit-breaker with [OQ10](#oq10--reliability--resilient-acquisition). Research: [`per-source-polling-cadence-and-skip-policy.md`](research/per-source-polling-cadence-and-skip-policy.md), [`orchestration-choice-for-a-single-vm-price-polling-service.md`](research/orchestration-choice-for-a-single-vm-price-polling-service.md), [`pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md), [`us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md`](research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md).

**My Comments:** Research any outstanding questions about the per-source cadence/politeness numbers and confirm the skip policy. Do this research using /qdev:research and update the open question and existing research doc(s) as applicable with the findings.

I have changed the spec to remove:

```text
- **Stewardship & Responsibility:** The tool should be designed to minimize the impact on the marketplaces it monitors, avoiding excessive requests or scraping that could be considered abusive or violate terms of service.
```

and replaced it with:

```text
- **Moderate Aggressive Usage:** The tool should be designed to avoid excessive requests or scraping that could be considered abusive or violate terms of service or result in rate limiting.
```

Update the open question to reflect that the spec has been updated to remove the Stewardship & Responsibility language and replace it with Moderate Aggressive Usage. The open question is now focused on confirming the per-source cadence/politeness numbers and confirming the skip policy.

### OQ10 — Reliability / resilient acquisition

**✅ Resolved (2026-07-04) → [ADR 0017](adr/adr-0017-resilient-acquisition.md) (ratified 2026-07-04).** The failure model is fully specified by reconciling [OQ5](#oq5--off-box-heartbeat) (off-box heartbeat) + [OQ8](#oq8--scraper-testing-finalization) (research #13's failure-classification tree); ADR-0017 is the authoritative record for the resulting per-source **state machine** (isolation, `paused_pending_fix` ↔ `active`, `→ SKIP`, silent-degradation detection). (Folds General Design Principles finding #4.)

- **Per-source failure isolation** — each source runs as an independent scheduled unit writing to `scraper_runs`; one marketplace being down, rate-limited, or having changed its markup never halts the others.
- **Retry/back-off** = the adaptive 429/503 cooldown (shared with OQ9); **circuit-break** a source into `paused_pending_fix` on the classification tree's `anti_bot` verdict or sustained `parser_rot` (not on a `transient`, which retries).
- **Silent-degradation detector** = the count-vs-rolling-average + tier-downgrade + empty-result assertions (OQ8), so a source returning stale/empty/challenge bodies is caught rather than silently trusted.
- **Health alerting** wired to `scraper_runs` → off-site GMK Uptime Kuma + Fleet Digest (OQ5).

Only **wiring remains** (MS-5 implementation), not a decision. **Features resilience note to fold into the spec:** _"Acquisition is per-source isolated with retry/back-off and automatic circuit-breaking of failing or anti-bot-protected sources (`paused_pending_fix`), plus operator health alerts — one marketplace failing degrades gracefully without stopping the others."_ Research: [`orchestration-choice-for-a-single-vm-price-polling-service.md`](research/orchestration-choice-for-a-single-vm-price-polling-service.md), [`lightweight-observability-and-scraper-health-monitoring.md`](research/2026-07-03-lightweight-observability-and-scraper-health-monitoring.md).

**My Comments:** See [OQ5](#oq5--off-box-heartbeat) and [OQ8](#oq8--scraper-testing-finalization) for the shared substrate. Then update or close this question based on the decisions made in those two questions.

### OQ11 — Composite scoring model (adopt research #4)

**✅ Resolved (tested 2026-07-04, results: [`drive-deal-scoring-model-test-results.md`](research/drive-deal-scoring-model-test-results.md)) → [ADR 0011](adr/adr-0011-composite-deal-score.md).** Adopt the **weighted geometric mean** over four normalized subscores (price cheapness-percentile 0.50 · fitness-for-purpose 0.25 · seller trust Bayesian+Wilson 0.15 · availability 0.10) with **non-compensatory veto caps** (device-managed SMR → 35; used/no-return → 60; low-trust → 60) and a **glass-box stored per-subscore explanation**. The model was implemented verbatim and run against a seeded mock dataset (5 cohorts, 8 archetypes) **before** the ADR — it passes (all three caps bind; ranking intuitive; 6/8 archetypes hit their a-priori band). One load-bearing calibration adopted: **warm-up full-confidence target `n_eff 50 → 30`** (a 64-observation cohort only reached n_eff ≈ 49 under the 30-day half-life). The price subscore internals come from gap #11 (shipping/tax in `$/TB`) and gap #12 (cohort percentile + warm-up `λ`); ADR-0011 is the outer composite. Fills spec `## Scoring System`; maps onto milestone MS-2.

**My Comments:** Before writing an ADR on the scoring model, I want to test it against mock data to see if it produces reasonable results. Does it actually rate items that are expected to be high or low correctly? Claude will create a small dataset of hard drive listings with various attributes and run the scoring algorithm to see if the output aligns with expectations. Conduct any additional research necessary: do this research using /qdev:research and update the open question and existing research doc(s) with the findings. Also create a comprehensive report/results document at `docs/research/drive-deal-scoring-model-test-results.md` that includes the dataset, the scoring results, and any analysis or conclusions drawn from the test.

### OQ12 — Orchestration engine (APScheduler vs systemd timers)

**✅ Resolved (research 2026-07-04, [`orchestration-engine-reconfirmation-2026.md`](research/orchestration-engine-reconfirmation-2026.md)) → [ADR 0012](adr/adr-0012-orchestration-apscheduler.md).** Use **APScheduler 3.11.x** in **one long-running, systemd-supervised poller process** (3.x not 4.x — 4.0 is still `4.0.0a6`, "do NOT use in production"; 3.x can't safely share a job store across processes, so "one process" is the correct model). Scrapy's default asyncio reactor means the crawler and an `AsyncIOScheduler` share one event loop in one process. Celery/RQ/Dramatiq and the async entrants (Taskiq/Repid/FastStream) are ruled out as distributed-broker solutions this project doesn't need (Redis also adds CVE surface). **Mandatory ADR-0006 amendment:** systemd supervises **one poller service** (`Restart=on-failure`, resource limits, journal) and APScheduler schedules the scrapes — timers remain only for genuinely-independent stateless jobs (nightly VACUUM, backup-verify). Unblocks OQ7/OQ9/OQ10 (which all assume the in-process two-level token-bucket substrate).

**My Comments:** I am leaning toward APScheduler as the orchestration engine. However, the research that labeled the other solutions as over-engineered may have been done prior to the recent changes in the scraping architecture and number of sources. Plus, we need to consider the general design principles of the project and whether APScheduler is consistent with those principles. Further research should be done to confirm that APScheduler is still the best choice given the current architecture and number of sources. Do this research using /qdev:research and update the open question and existing research doc(s) with the findings.

### OQ13 — Notification transport & deliverability (AgentMail vs transactional provider)

**✅ Resolved (research 2026-07-04, [`free-outbound-email-path-for-low-volume-alerts.md`](research/free-outbound-email-path-for-low-volume-alerts.md)) → [ADR 0013](adr/adr-0013-notification-transport-m365-graph.md).** Meets the owner's "must be free right now" constraint: **reuse the existing Microsoft Graph → M365 send path** the homelab already uses for other service alerts (sends as a branded `@l3digital.net` address; creds at OpenBao `secret/apps/microsoft365`). Why it wins: zero marginal cost (M365 is already paid for), branded custom-domain sending, high deliverability (the 10K-recipients/day M365 limit dwarfs this workload), and no dependence on a volatile third-party free tier (SendGrid killed its free plan in 2025; MailerSend cut its free allowance 83% in Oct 2025). **Runner-up / independent fallback:** AgentMail free (`@agentmail.to`, unbranded, 100/day · 3,000/mo). Supersedes the spec's AgentMail-primary lean and the earlier paid Postmark/SES recommendation for the free-v1 case; Postmark-primary/SES-fallback remains documented only as the future paid-upgrade path. `l3digital.net` still needs SPF + DKIM + DMARC (start `p=none`, tighten later). The alert-rule data model, dedup/debounce/digest, and hysteresis are already settled (prompt #11 + [OQ6](#oq6--final-ui-page-inventory--dismisssuppress-feedback--purchase-tracking)).

**My Comments:** I am not going to pay for the email service at this time. Whatever gets implemented must be free for the time being. I will revisit this question if we find that email deliverability is a problem and we need to pay for a service to improve it. Research this problem further to resolve this.

### OQ14 — Scraping runtime escalation stack

**✅ Resolved (research prompt #8, [`pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md)) → [ADR 0014](adr/adr-0014-scraping-runtime-escalation-stack.md).** Adopt the **HTTP-first, structured-data-first, browser-last** tier ladder: Scrapy as orchestrator, a structured-data detector (JSON-LD → platform JSON → bootstrap JSON) in front of every parser, **Playwright via `scrapy-playwright`** for occasional rendering, **`curl_cffi`** for the narrow TLS-fingerprint gap, and **skip / outsource** for targets needing residential-proxy rotation or CAPTCHA solving. **Decision:** adopt the stack but **defer `curl_cffi` + Playwright to MS-5** — ship MS-1's five recert sources on plain HTTP + structured-data parsing, adding the browser/TLS tiers only when a hostile source demands it. Same skip cutoff as [OQ9](#oq9--acquisition-cadence-throttle--skip-policy); dovetails with the spec's Special Considerations guardrails (`ROBOTSTXT_OBEY=True`, AUTOTHROTTLE, no anti-bot bypass).

**My Comments:** Would we actually use Playwright? Can that be used programmatically in a headless way to scrape the sites we need without an AI agent/LLM?

_Answer (2026-07-04):_ **Yes on both counts — and the LLM concern is a category confusion.** Playwright is a code-driven **browser-automation library** (Microsoft); it runs fully **headless** on the server, deterministically, driven entirely by our Python code — **nothing to do with an AI agent or LLM** (the owner is likely thinking of LLM-driven "browser agents," a different category we are **not** using). Used **selectively** via `scrapy-playwright` as a Scrapy download handler — only requests we explicitly mark as needing a browser go through Chromium; it is **browser-last** (tier 3 of 4: HTTP + structured-data → `curl_cffi` → Playwright → managed-API/skip), mostly for one-time endpoint/JS reconnaissance. Most target sources expose the needed fields in the initial HTML (JSON-LD, `__NEXT_DATA__`, Shopify `/products/{handle}.js`), so **no browser is needed at all** for them. Report: [`pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md`](research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md) (§"When HTTP is enough and when a browser is justified", 4-tier ladder).

### OQ15 — Amazon acquisition path after PA-API deprecation

**✅ Resolved (owner-ratified 2026-07-04, research-backed) — no ADR; this is the record.** Research: [`2026-07-04-amazon-data-acquisition-after-paapi-getitems-deprecation.md`](research/2026-07-04-amazon-data-acquisition-after-paapi-getitems-deprecation.md) (live-verified against primary Amazon sources). The PA-API 5 `GetItems` deprecation (banner: "deprecated May 15th, 2026 … migrate to Creators API"; PA-API also **closed to new registrations**) invalidated the earlier assumption that Amazon offers come via PA-API/SP-API. **Decision: Amazon acquisition is discovery-only via the existing search-API stack** (Serper → Brave → Tavily) — parse the ASIN from the discovered `/dp/<ASIN>` URL and persist it indefinitely (DR-001); treat any SERP price/availability as a low-confidence 24 h-TTL hint. **No official-API integration and no direct Amazon scraper.**

- **Both official paths are blocked, not merely harder:**
  - **SP-API** — a **hard categorical block**: private-developer registration requires "Register as an Amazon Seller" with a **Professional Selling Account** first ([official](https://developer.amazonservices.com/private-developer)). hw-radar is buyer-only with no plan to become a seller.
  - **Creators API** (PA-API successor) — data shape is adequate (`offersV2.listings.price`/`availability`/`condition` by ASIN) but access is gated behind **10 qualified affiliate sales / trailing 30 days**, revocable if sales lapse ([official landing page](https://affiliate-program.amazon.com/creatorsapi)). Not realistically clearable by a monitoring tool.
- **DR-001 was never the constraint** — Amazon's Associates License explicitly permits **indefinite ASIN storage** + 24 h TTL on everything else + no image caching, matching DR-001 exactly. Eligibility, not retention ToS, is what closes the official paths.
- **Discovery-only is the lowest-exposure option** — reading the search engine's index of Amazon pages runs ToS/robots exposure against the *search engine's* crawler, not against hw-radar directly (hiQ/public-data reasoning); a direct Amazon scraper would be higher exposure. This is why Amazon is **discovery-only, not a T3 direct-scrape target** (supersedes the [OQ9](#oq9--acquisition-cadence-throttle--skip-policy) cadence table's earlier "Amazon-scrape" placement).
- **Migration effort: zero** — no change from the current architecture (Amazon already rides the search-API discovery row, spec IR-006). Reconciled into the spec: §21 OQ-015 → **Resolved**, the Special-Considerations Amazon bullet, IR-006, and the ToS-review checklist.
- **Carried-forward (non-load-bearing) open items** from the research, not gating this decision: (1) whether PA-API `GetItems` still responds for *existing* credential-holders today — moot, hw-radar holds no PA-API creds; (2) Creators-API-specific rate limits (behind an Associates Central sign-in wall). Both are recorded in the research report's Open Questions table.

**My Comments:** **Task for Claude:** /qdev:research the Creators API and SP-API to determine which is the best replacement for PA-API 5 `GetItems` for our use case. Provide a recommendation with pros and cons for each option, including any limitations or restrictions that may affect our ability to acquire and retain Amazon data. Once a decision is made, update the relevant documentation accordingly.

### OQ16 — SSD cohort-key endurance dimension (DWPD)

**✅ Resolved (owner, 2026-07-04) — no ADR amendment; [ADR 0011](adr/adr-0011-composite-deal-score.md)'s ratified key stands.** From the 2026-07-04 spec gap analysis. **Decision: fold DWPD into the _fitness_ subscore instead of the cohort key.** SSDs cohort on the same four-part key as HDDs (capacity · tier · interface/form · condition); the DWPD endurance class — already a typed `drive_spec` column from the [suitability taxonomy research](research/machine-usable-drive-suitability-taxonomy-for-24-7-nas-and-server-scoring.md) — enters the fitness rubric's suitability (`T`) component, so a read-intensive SSD is price-compared against all SSD peers but scores lower on fitness where endurance matters.

- **Rationale:** partitioning the cohort on DWPD would thin SSD cohorts further, and thin cohorts already force ADR 0011's warm-up/relaxation machinery (`λ = min(1, n_eff/30)`, cohort-relaxation fallback). The fitness path prices the endurance mismatch without starving the price percentile.
- **Reconciled:** spec glossary "Cohort" row + EC-008 (provisional markers dropped, repointed here); §21 OQ-016 → Resolved; ADR 0011 More Information carries the pointer.

**My Comments:** Fold DWPD into the _fitness_ subscore instead of the cohort key.

### OQ17 — Heartbeat-grain retention & storage policy

**✅ Resolved (owner-ratified 2026-07-04, research-backed) — no ADR; rides [ADR 0015](adr/adr-0015-availability-heartbeat-grain-volatility-scheduling.md)'s already-ratified grain; DR-008 carries the policy.** Research: [`2026-07-04-availability-heartbeat-retention-and-storage-policy.md`](research/2026-07-04-availability-heartbeat-retention-and-storage-policy.md). Storage volume is **not** the constraint (~0.7–1.1 GB/yr uncompressed even at 20 fast-lane sources; tens of MB after TimescaleDB's well-corroborated 90%+ columnstore compression on this repetitive row shape) — the design optimizes for the two history consumers ADR 0015 created (p95 transition-to-alert SLO measurement; fingerprint tuning from `ambiguous`/`failed` runs). All values below are **tunables**, not ratified constants.

1. **`availability_heartbeat_observation` is a TimescaleDB hypertable** — for ops/backup consistency with `offer_snapshot`, not because volume forces it. Monthly chunk interval initially (avoids small-chunk compression overhead at 2–5-source volume); revisit toward weekly as the fast-lane set approaches ~20 sources.
2. **Raw retention 30 days** (`add_retention_policy`), compression (`segmentby = source_id, variant_id`) on chunks older than ≈7 days — well inside the retention window, so the retention-vs-continuous-aggregate refresh-ordering footgun is trivially satisfied.
3. **Indefinite daily continuous aggregate** (`availability_heartbeat_daily`: per source/day/decision-class counts, ~29K rows/yr) — the long-horizon SLO-trend consumer reads this, Prometheus-style raw-then-downsample.
4. **Class-differentiated retention via dual-write, not chunk surgery:** the poller (already branching on decision class per ADR 0015) additionally writes every **non-`unchanged`** row to a **plain (non-hypertable) table `availability_heartbeat_event`**, retained **365 days** — fingerprint tuning reads this. TimescaleDB retention is chunk-granular, so "keep rare classes longer" cannot be expressed inside one hypertable; the second table sidesteps every compression/backfill footgun for exactly the rows that matter.
5. **No license/backup posture change:** compression, retention policies, and continuous aggregates are all TimescaleDB Community (TSL) features — the license class the deploy already carries (recorded under [OQ20](#oq20--oss-license-compliance-posture)); the hourly TimescaleDB-aware logical dumps ([OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling)) cover both tables.
6. **eBay carve-out:** eBay-sourced heartbeat rows carry `retention_class = ebay_listing_observation` (≤6 h freshness / delete-on-delist, per [ADR 0010](adr/adr-0010-canonical-data-model.md) rule 6), which caps the 30 d/365 d heartbeat TTLs above for that source. _(Added 2026-07-04 by the spec-hygiene audit.)_

**My Comments:** **Task for Claude:** research further and provide your recommendation on retention and storage policy for heartbeat-grain data. Consider the trade-offs between data retention for SLO measurement and fingerprint tuning versus storage costs and performance.

_Recommendation ratified as presented (2026-07-04): adopt the full design incl. the event table._

### OQ18 — Recovery-time objective (RTO) for v1

**✅ Resolved (owner, 2026-07-04) — no ADR; this is the record.** From the 2026-07-04 spec gap analysis (§18.6 had "RTO: not stated in sources" while [OQ3](#oq3--db-rpo-acceptance--timescaledb-dump-handling) ratified the RPO). **Decision: RTO ≤ 24 hours, manual-runbook restore — no restore/provisioning automation for v1** (the ansible scaffold [ADR 0009](adr/adr-0009-secrets-runtime-openbao-agent.md) mentions stays unbuilt). 24 h covers noticing the outage and running the restore by hand. Deals missed during an outage are unrecoverable regardless of RTO; the durable asset (the price-history moat) is protected by the ≤1 h RPO. Spec §18.6 states the bound; the ≥once-by-MS-5 timed restore test verifies it.

**My Comments:** 24 hours is fine. That gives me time to notice the outage and run the restore. No need to automate the restore for v1.

### OQ19 — Accessibility & i18n declaration

**✅ Resolved (owner, 2026-07-04) — no ADR; this is the record.** From the 2026-07-04 spec gap analysis (spec §11's template placeholder required an explicit statement). **Decision: accessibility and i18n are out of scope for v1** — the UI serves a single sighted user, English-only, consistent with the Engineered-to-Needs principle. No WCAG target, no string externalization. Recorded as the §11 declaration + **WH-008** in §2.3 (revisit if additional or differently-abled users appear). Server-rendered Django templates + HTMX ([ADR 0004](adr/adr-0004-web-framework-django-htmx.md)) keep the markup close to semantic HTML, so a later retrofit is additive rather than structural.

**My Comments:** No plans to implement at this time. Accessibility and i18n are out of scope for v1. The UI is for a single sighted user, English-only. This is consistent with the "Engineered to Needs" principle.

### OQ20 — OSS license-compliance posture

**✅ Resolved (owner-ratified 2026-07-04, research-backed) — no ADR; this is the record.** Research: [`2026-07-04-oss-license-compliance-tooling-for-a-uv-managed-public-python-project.md`](research/2026-07-04-oss-license-compliance-tooling-for-a-uv-managed-public-python-project.md). Spec §16 previously promised license coverage "by the standard toolchain's audit practices" — false (pip-audit checks CVEs, not licenses); §16 now reworded to match the real mechanism.

- **Decision: automated allowlist gate via GitHub's own `actions/dependency-review-action@v4`** ([`.github/workflows/dependency-review.yml`](../.github/workflows/dependency-review.yml)) — as of 2026-04 GitHub's Dependabot-based dependency graph resolves **`uv.lock` natively with per-dependency license data**, so the gate costs zero new Python dependencies. `allow-licenses` covers the decided stack (MIT · BSD-2/3 · Apache-2.0 · ISC · Python/PSF · LGPL-2.1/3.0 · MPL-2.0 · 0BSD/Unlicense); the action tolerates undetectable/`UNKNOWN` licenses rather than hard-failing (the corroborated footgun: a large fraction of PyPI packages lack machine-readable license metadata, PEP 639).
- **Known gap, accepted:** the action is **PR-triggered only** and this repo currently commits directly to branches — so until the branch-protection/PR TODO lands, a dependency added by direct commit bypasses the gate. Mitigation recorded in §16: run a manual `licensecheck` pass whenever adding a dependency outside a PR.
- **Copyleft exposure is structurally low:** GPL/LGPL obligations trigger on *distribution*, and hw-radar distributes nothing (public source ≠ distributing dependencies; the app runs only on the owner's server). psycopg's **LGPL-3.0** therefore imposes nothing; nothing AGPL is in the stack (AGPL is the only common license that closes the SaaS gap, and it would matter only if hw-radar's UI were offered to others).
- **TimescaleDB Community (TSL):** self-hosted internal use of the TSL feature set the deploy relies on (columnstore compression, continuous aggregates, retention policies — per [OQ17](#oq17--heartbeat-grain-retention--storage-policy)) is **expressly permitted**; the TSL's only real prohibition is offering the database as a hosted DBaaS. Recorded in §16 so the reliance is explicit.

**My Comments:** **Task for Claude:** Conduct further research and determine the required tooling and processes to ensure OSS license compliance for the project. Provide a recommendation on whether to implement an automated license check, perform a one-time manual review, or accept the risk and update the documentation accordingly.

_Recommendation ratified as presented (2026-07-04): `dependency-review-action` gate + manual `licensecheck` for non-PR dependency adds + §16 reword + TSL note._

### OQ21 — `httpx` dependency for API/FX/heartbeat HTTP paths

**✅ Resolved (owner, 2026-07-05) — no ADR; this is the record; §8.6 carries the dependency row.** Raised by the MS-1 design brainstorm ([design doc](superpowers/specs/2026-07-05-ms1-ingestion-design.md)). Three MS-1 HTTP paths are single-URL API/plain GETs where Scrapy is the wrong tool: the eBay Browse API (OAuth2 client-credentials + REST calls), the Frankfurter daily FX fetch ([ADR 0008](adr/adr-0008-currency-landed-cost-normalization.md)), and the ADR-0015 heartbeat probes (fixed cheap-signal URLs at 2–5 min cadence, where per-probe Scrapy crawler lifecycle is disproportionate). §8.6 did not list an HTTP client, and Appendix B requires an OQ entry + owner approval for any unlisted dependency.

- **Decision: add `httpx`** (BSD-3, inside the OQ20 license allowlist) as the HTTP client for API-tier calls, FX fetches, and heartbeat probes. Scrape-tier fetching (full page/category crawls) remains Scrapy per [ADR 0014](adr/adr-0014-scraping-runtime-escalation-stack.md) — this does not touch the escalation ladder.
- **Alternatives rejected:** forcing everything through Scrapy (contorts authenticated REST clients into spider shape; heavyweight per-heartbeat) · stdlib `urllib` (no async, no HTTP/2, hand-rolled retries) · `aiohttp` (viable, but `httpx` shares the `requests`-style API and is the ecosystem default for async clients).
- **Politeness parity:** heartbeat probes still honor the C-007 posture — robots verified at source registration, honest UA, hard timeouts, cadence ≥ crawl-delay (Seagate: 20 s).

**My Comments:** Approved 2026-07-05 during the MS-1 brainstorm (substrate section ratified "Approve incl. httpx").

### OQ22 — Retention class and `expires_at` policy for resolver-learned (`listing_derived`) `ProductAlias` rows

**✅ Resolved (owner-ratified 2026-09-06) — no ADR; this is the record.** Raised by the migration-0016 retention-index follow-up: `matching/resolver.py`'s `_emit_learned_aliases` wrote `ProductAlias` rows with `retention_class=""`, which blocked the DR-001 CHECK pair on `product_model` / `drive_spec` / `product_alias`. The two obvious classes both lose: an existing indefinite class (`merchant_fact` / `manufacturer_reference`) stamps false provenance on a token scraped out of a merchant title, and a bounded class (`ebay_listing_observation`) lets the hourly `purge_expired` sweep delete learned aliases and re-opens every resolution the ADR-0019 rule 7 review queue had already settled.

- **Decision: option (a) — a new indefinite retention class `listing_derived_alias`** (`RetentionClass.LISTING_DERIVED_ALIAS`, `expires_at` NULL), stamped by `_emit_learned_aliases` on every row it creates. Option (b) (bounded, re-learned on the next observation) was rejected because the review queue only shrinks if learned aliases persist; option (c) (promote to `manufacturer_reference` once two or more sources corroborate) was rejected as a separate, unbuilt corroboration mechanism — the class keeps that promotion possible later without pretending it has happened.
- **Provenance semantics (the point of the separate class):** the alias token is an **inference from a merchant listing**, not a manufacturer or merchant assertion. It must never be promoted to `manufacturer_reference` without corroboration; today the only writer allowed to promote it is the refdata seed path, which corroborates against a first-party datasheet (`refdata/persist.py::_import_alias`).
- **DR-008 reading (owner's ratified position):** a learned alias is **not** subject to eBay's six-hour deletion duty. The row stores no listing content — only a normalized identifier token and the catalog target it points at — so it is not an eBay observation record. DR-008 continues to govern the `listing` / `offer_snapshot` / heartbeat rows unchanged.
- **Consequence:** the DR-001 CHECK pair now covers **every** `RetentionGoverned` table (migration `0017_identity_retention_checks`, with an idempotent provenance-derived backfill for pre-existing empty-class rows); `tests/unit/test_purge_registry.py` pins that as a registry-derived property.

**My Comments:** "I agree and ratify all, use sane defaults." (2026-09-06, ratifying option (a) with the provenance and DR-008 semantics above.)
