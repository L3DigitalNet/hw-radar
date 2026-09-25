# Research

This directory is the project's **research corpus** — 38 research reports that ground the design of Hardware Radar in evidence rather than assumption. Alongside [`../specs/hw-radar-master-spec.md`](../specs/hw-radar-master-spec.md) (the spec) and [`../adr/`](../adr/) (the decisions), these reports are a **design source of truth**: when a decision cites "research says…", this is where it says it.

Each report is a **dated, frozen snapshot** of what was found on the day it was run (the original corpus 2026-07-03, plus a 2026-07-04 follow-up batch resolving OQ3 + OQ6–OQ15: scoring-model test, UI inventory, polling cadence, orchestration, search self-governance, the free email path, DB-backup/RPO, and the Amazon post-PA-API acquisition path), with inline citations. They are not living documents — findings get **reconciled forward** into the spec, the ADRs, and [`../resolved-questions.md`](../resolved-questions.md), rather than edited in place here.

## How to use this directory

- **Browsing by topic?** Read the grouped list below — each report has a one-line "what it answers" and a pointer to where its findings landed.
- **Looking up metadata** (tags, status, confidence)? See [`index.md`](index.md) — a **generated** table (id · title · dates · status · confidence · tags). It is produced by the research tooling; **do not hand-edit it**.
- **Tracing a decision back to evidence?** ADRs and open-questions link to the specific report that informs them; this README is the reverse map (report → where it was used).

> **Freshness caveat.** Several reports contain **time-sensitive facts** — US import de-minimis / tariff status, tool CVEs, library maintenance status, API pricing — that were in active flux as of 2026-07-03. Re-verify anything dated before relying on it. Confidence is graded per report in [`index.md`](index.md) (most `high`; recert-drive and US-scraping-legal are `medium`).

## The reports, by theme

### Drive domain knowledge — what makes a drive worth buying

| Report | Answers | Landed in |
| --- | --- | --- |
| [recertified-enterprise-hard-drives-for-homelab-and-small-business-buyers](recertified-enterprise-hard-drives-for-homelab-and-small-business-buyers.md) | Recert vs refurb vs used risk, warranty reality, reliability (Backblaze/SMART), buyer heuristics | Marketplace tiering, condition scoring |
| [machine-usable-drive-suitability-taxonomy-for-24-7-nas-and-server-scoring](machine-usable-drive-suitability-taxonomy-for-24-7-nas-and-server-scoring.md) | A machine-usable CMR/SMR · NAS/enterprise · DWPD/TBW taxonomy for 24/7 fitness | Fitness-for-purpose scoring factor |
| [programmatic-identity-and-warranty-verification-for-used-enterprise-hdd-listings](programmatic-identity-and-warranty-verification-for-used-enterprise-hdd-listings.md) | Model/serial decoders, SMART attributes, manufacturer warranty lookup | Authenticity/condition verification |
| [drive-deal-tracker-research-baselines-tools-shucking-and-timing](drive-deal-tracker-research-baselines-tools-shucking-and-timing.md) | `$/TB` baselines, seed sources (Keepa/ServerPartDeals), shucking, seasonality | Cold-start seeding — gap #12 |

### Acquisition, scraping & compliance — getting the data

| Report | Answers | Landed in |
| --- | --- | --- |
| [programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants](programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md) | Per-merchant acquisition methods (structured data, platform JSON, feeds) across the target merchants | Acquisition tiering |
| [pragmatic-architecture-for-low-volume-python-e-commerce-scraping](pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md) | The structured-data-first tier ladder (JSON-LD → platform JSON → HTML), Scrapy/curl-cffi, anti-bot posture | `fetch`/`parse` stages, scraper testing — gap #9 |
| [tavily-brave-serper](tavily-brave-serper.md) | Search APIs (Tavily/Brave/Serper) + official eBay Browse/Feed, Amazon SP-API, Newegg APIs | Discovery tier |
| [us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor](us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md) | CFAA / ToS / copyright / data-retention footing for a US price monitor | Legal footing, cassette/PII rules — gap #9 |
| [orchestration-choice-for-a-single-vm-price-polling-service](orchestration-choice-for-a-single-vm-price-polling-service.md) | Scheduling, rate-limiting, two-level token buckets on a single VM | Orchestration + poll budget — gap #10 / OQ7 |
| [automated-test-policy-for-a-low-volume-scrapy-price-monitor](automated-test-policy-for-a-low-volume-scrapy-price-monitor.md) | Build-time scraper-test finalization: per-tier canary cadence, real-vs-synthetic cassette policy, PII scrubbing, parser-rot-vs-anti-bot classification, CI wiring | Scraper testing — OQ8 / gap #9 |
| [per-source-polling-cadence-and-skip-policy](per-source-polling-cadence-and-skip-policy.md) | Concrete per-tier poll cadence (baseline/ceiling), 429/503/soft-block back-off ladder, and the tier-ladder skip/pause cutoff; eBay Browse/Feed + Amazon SP-API published rate limits | Acquisition cadence, throttle & skip — [OQ9](../resolved-questions.md#oq9--acquisition-cadence-throttle--skip-policy) |
| [search-api-self-governance-and-user-configurable-limits](search-api-self-governance-and-user-configurable-limits.md) | Self-governing our own Serper/Brave/Tavily spend: per-provider token bucket, hard PostgreSQL spend-cap circuit-breaker (reserve-then-call), failing-provider breaker, per-provider user settings; AgentMail free-tier caps | Search self-governance + budget — [OQ7](../resolved-questions.md#oq7--running-cost-budget-model-build-time-pricing-pass) |
| [orchestration-engine-reconfirmation-2026](orchestration-engine-reconfirmation-2026.md) | Reconfirms APScheduler 3.11.x in one supervised poller vs systemd timers vs task queues; 4.0-alpha status, 2025–26 ecosystem churn, ADR-0006 "timers" reconciliation | Orchestration engine — [OQ12](../resolved-questions.md#oq12--orchestration-engine-apscheduler-vs-systemd-timers) |

### Data model, entity resolution & scoring — making sense of it

| Report | Answers | Landed in |
| --- | --- | --- |
| [database-architecture](database-architecture.md) | PostgreSQL + TimescaleDB schema, the canonical drive-model / listing / observation model | [ADR 0007](../adr/adr-0007-datastore-postgresql-timescaledb.md); canonical-entity ADR 0008 (pending) |
| [entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking](entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking.md) | Matching the same drive across merchants: MPN/GTIN, blocking, title parsing, record linkage | `entity-resolve` stage |
| [principled-deal-score-for-hard-drive-listings](principled-deal-score-for-hard-drive-listings.md) | The 0–100 deal score: log-`$/TB` percentile, warm-up shrinkage, explainability | Scoring engine; cold-start — gap #12 |
| [drive-deal-scoring-model-test-results](drive-deal-scoring-model-test-results.md) | Empirical validation of the deal-score model against a seeded mock dataset (5 cohorts, 8 archetypes): vetoes bind, ranking intuitive; recommends `n_eff 50→30` before ADR-0011 | Scoring-model sign-off — [OQ11](../resolved-questions.md#oq11--composite-scoring-model-adopt-research-4) |

### Alerting & web stack — surfacing it

| Report | Answers | Landed in |
| --- | --- | --- |
| [designing-a-low-noise-alerting-layer-for-a-hard-drive-deal-monitor](designing-a-low-noise-alerting-layer-for-a-hard-drive-deal-monitor.md) | Dedup fingerprints, cooldown/hysteresis, email deliverability, the post-alert state machine | Alerting — gap #7 post-alert model / OQ6 |
| [choosing-an-outbound-email-path-for-a-low-volume-alerting-system](choosing-an-outbound-email-path-for-a-low-volume-alerting-system.md) | Outbound email path: Postmark primary, SES fallback, AgentMail as secondary agent-inbox tool; custom-domain SPF/DKIM/DMARC, why datacenter SMTP fails | Notification transport & deliverability — [OQ13](../resolved-questions.md#oq13--notification-transport--deliverability-agentmail-vs-transactional-provider) / [prompt #14](../further-research-needed-prompts.md#14-agentmail-deliverability--sending-domain-model) |
| [free-outbound-email-path-for-low-volume-alerts](free-outbound-email-path-for-low-volume-alerts.md) | The **free** email path (owner won't pay yet): reuse the existing M365 Graph send path (branded, zero marginal cost); AgentMail free (`@agentmail.to`) fallback; supersedes the paid pick for v1 | Free notification transport — [OQ13](../resolved-questions.md#oq13--notification-transport--deliverability-agentmail-vs-transactional-provider) |
| [mvp-web-ui-inventory-and-dismiss-suppress](mvp-web-ui-inventory-and-dismiss-suppress.md) | Validates the MVP page inventory; dismiss = permanent per-listing suppress via the existing `watch_match_state` enum; defer purchase-savings analytics to post-v1 | UI inventory + dismiss/purchase — [OQ6](../resolved-questions.md#oq6--final-ui-page-inventory--dismisssuppress-feedback--purchase-tracking) |
| [opinionated-core-stack-recommendations-for-a-python-drive-price-monitor](opinionated-core-stack-recommendations-for-a-python-drive-price-monitor.md) | Framework/stack fit: Django + server-rendered templates + HTMX, PostgreSQL, uv | [ADR 0004](../adr/adr-0004-web-framework-django-htmx.md); open-questions RQ1 |

### Deployment & operations — running it

Five parallel operational sweeps (dated filenames), run to ground the gap analysis in current tooling:

| Report | Answers | Landed in |
| --- | --- | --- |
| [2026-07-03-auth-for-self-hosted-single-maintainer-python-app](2026-07-03-auth-for-self-hosted-single-maintainer-python-app.md) | Auth for an internet-facing single-maintainer app (session login, forward-auth) | [ADR 0005](../adr/adr-0005-single-account-session-auth.md); gap #1 |
| [2026-07-03-github-actions-cd-private-debian-vm](2026-07-03-github-actions-cd-private-debian-vm.md) | CD to a private target + OpenBao secret injection (rsync over Tailscale SSH, systemd) | [ADR 0006](../adr/adr-0006-cd-rsync-over-tailscale-ssh.md); gaps #2/#4, OQ1/OQ2 |
| [2026-07-03-currency-conversion-and-landed-cost-estimation-for-cross-border-drive-price-scoring](2026-07-03-currency-conversion-and-landed-cost-estimation-for-cross-border-drive-price-scoring.md) | FX normalization to USD, landed-cost / duty / VAT footguns | Gaps #3/#11 |
| [2026-07-03-postgresql-backup-disaster-recovery-single-vm](2026-07-03-postgresql-backup-disaster-recovery-single-vm.md) | Backup/DR for a single small VM: pgBackRest, WAL/PITR, TimescaleDB caveats | Gap #5 / OQ3 |
| [2026-07-03-lightweight-observability-and-scraper-health-monitoring](2026-07-03-lightweight-observability-and-scraper-health-monitoring.md) | Uptime, error tracking, scraper-rot detection at single-VM scale | Gaps #6/#9, OQ5/OQ8 |

### 2026-07-04 follow-up batch — MS-1 acquisition / matching deep-dives

Nine reports run 2026-07-04 to close the remaining domain/operational questions before MS-0 planning (the matching layer, polling cadence, heartbeat retention, licensing, and the Amazon path):

| Report | Answers | Landed in |
| --- | --- | --- |
| [2026-07-04-wd-seagate-recert-endpoint-recon](2026-07-04-wd-seagate-recert-endpoint-recon.md) | WD & Seagate direct-recert stores — cheap-signal endpoint recon spike (both fast-lane eligible; `store.seagate.com` robots-disallowed → use the category-page bootstrap JSON) | Fast-lane eligibility — [ADR 0015](../adr/adr-0015-availability-heartbeat-grain-volatility-scheduling.md) / §8.1 |
| [2026-07-04-availability-heartbeat-retention-and-storage-policy](2026-07-04-availability-heartbeat-retention-and-storage-policy.md) | Retention/storage policy for the high-frequency availability-heartbeat table (PostgreSQL + TimescaleDB) | Heartbeat retention — DR-008 / [OQ17](../resolved-questions.md#oq17--heartbeat-grain-retention--storage-policy) |
| [2026-07-04-per-source-inventory-volatility-and-fast-lane-polling](2026-07-04-per-source-inventory-volatility-and-fast-lane-polling.md) | Per-source inventory volatility ↔ fast-lane polling axis (anti-correlation: fast-lane = drop-prone ∩ cheap signal) | Cadence / volatility — [OQ9](../resolved-questions.md#oq9--acquisition-cadence-throttle--skip-policy) (FR-002) |
| [hardware-radar-polling-cadence](hardware-radar-polling-cadence.md) | Companion volatility / cheap-signal run feeding the cadence reconciliation | Cadence — [OQ9](../resolved-questions.md#oq9--acquisition-cadence-throttle--skip-policy) |
| [2026-07-04-polling-cadence-reconciliation](2026-07-04-polling-cadence-reconciliation.md) | Reconciles the two parallel cadence runs into the FR-001 freshness SLO + FR-002 volatility axis + heartbeat grain | Cadence reconciliation — [OQ9](../resolved-questions.md#oq9--acquisition-cadence-throttle--skip-policy) |
| [2026-07-04-amazon-data-acquisition-after-paapi-getitems-deprecation](2026-07-04-amazon-data-acquisition-after-paapi-getitems-deprecation.md) | Amazon product-data path after PA-API 5 GetItems deprecation → discovery-only posture | Amazon acquisition — [OQ15](../resolved-questions.md#oq15--amazon-acquisition-path-after-pa-api-deprecation) |
| [2026-07-04-oem-part-number-cross-referencing-for-server-pull-drives](2026-07-04-oem-part-number-cross-referencing-for-server-pull-drives.md) | OEM part-number cross-referencing for server-pull drives (Dell/EMC, HPE, Lenovo/IBM, NetApp); OEM PN ↔ MPN is N:N | Matching-layer OEM aliases — [ADR 0019](../adr/adr-0019-listing-catalog-matching-layer.md) |
| [2026-07-04-ssd-vendor-part-number-decoding-and-spec-catalog-bootstrap-datasets](2026-07-04-ssd-vendor-part-number-decoding-and-spec-catalog-bootstrap-datasets.md) | SSD vendor MPN decoding schemes + prior-art model→spec bootstrap datasets (never vendor `drivedb.h` — GPL) | Matching layer / catalog bootstrap — [ADR 0019](../adr/adr-0019-listing-catalog-matching-layer.md) |
| [2026-07-05-ms1c-catalog-seed-inputs](2026-07-05-ms1c-catalog-seed-inputs.md) | MS-1c manufacturer catalog seed inputs: live recert inventory, first-party datasheets, seed-row mapping, and MS-1b carry-forward checks | Catalog seed — [ADR 0018](../adr/adr-0018-manufacturer-spec-catalog.md) / MS-1c design |
| [2026-07-04-oss-license-compliance-tooling-for-a-uv-managed-public-python-project](2026-07-04-oss-license-compliance-tooling-for-a-uv-managed-public-python-project.md) | OSS license-compliance tooling/process for a small public uv-managed project | License gate — [OQ20](../resolved-questions.md#oq20--oss-license-compliance-posture) (dependency-review) |

### 2026-09-24 — MS-2 planning (code-verified map)

| Report | Answers | Landed in |
| --- | --- | --- |
| [2026-09-24-ms2-code-dependency-map](2026-09-24-ms2-code-dependency-map.md) | Code-verified seams for the multi-category watch core: unchanged abstractions, drive assumptions, fetch/parse coupling, source-vs-provider conflation, duplicate-observation and delist-completeness flow, category hints, watch attach point | [MS-2 plan](../superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md) |

## Provenance

Reports were produced with dual-source web research (Tavily-first, cross-corroborated, library facts routed through Context7) and persisted here with per-report YAML frontmatter that the tooling reads to regenerate [`index.md`](index.md). Filenames without a date prefix were imported earlier and stamped with frontmatter after the fact; the five `2026-07-03-…` files are the operational sweeps described above. Per [ADR 0001](../adr/adr-0001-decline-markdown-frontmatter-standard.md), that frontmatter is a **local, unvalidated convention** — this README (a non-ADR doc) deliberately carries none.
