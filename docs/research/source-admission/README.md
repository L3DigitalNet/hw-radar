# Source Admission Records

This directory holds one **source-admission record** per candidate source that Hardware Radar
might collect through one of its own private Apify Actors in production. The requirement comes
from the owner's 2026-09-24 decisions: [ADR 0021's 2026-09-24 amendment](../../adr/adr-0021-hybrid-acquisition-apify.md#amendment--2026-09-24-actor-ownership-billing-cycle-budget-and-actor-proof-owner-clarification),
[OQ24](../../resolved-questions.md#oq24--production-actor-backed-merchant-source-admission), and
MS2-D-44 in the [MS-2 plan](../../superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md).

A record informs the owner's decision; it does not make it. The owner answers OQ24 for a
candidate. A record whose recommendation is `eligible` is the precondition for plan task F5b.

## Rules

- **Robots permission is not contractual permission.** Record robots directives, and
  separately what the Terms of Use say about automated access.
- **The absence of an obvious prohibition is not permission.** Say what was read and what was
  not found; do not infer consent.
- **Execution venue does not change permissibility.** Running the collector on Apify instead of
  locally changes nothing about what the source allows.
- **No proxy or unblocking escalation.** A source that needs residential proxies, automatic proxy
  rotation, CAPTCHA solving, a paid unblocker, or a paid third-party Actor fails admission
  unless the owner revisits it.
- **Bounded retention needs an enforceable remote expiry.** A source whose data carries a
  bounded retention class cannot use an Actor path until a per-run storage expiry at or below
  its TTL is verified (MS-2 plan MS2-D-33, risk R20).
- **No grandfathering.** An existing local connector is not admitted to an Actor path because it
  exists. If a review finds that an existing, disabled local connector conflicts with this
  acquisition posture, record that finding separately (its own record or an open question). The
  2026-09-25 local-connector Terms review found exactly this for ServerPartDeals and
  Seagate-recertified: [OQ31](../../resolved-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access)
  retired both (permission-required — not production-enableable under current Terms, for any
  execution venue) — `RETIRED_SOURCES` in `src/hw_radar/acquisition/admission.py`. Re-admission
  needs a fresh source-admission review on materially changed Terms or written merchant
  permission; nothing re-admits a retired source automatically.
- **Newegg is excluded** (Terms of Use prohibit automated access and scraping; MS-2 plan R1).
- **Public repository.** Record public URLs and quoted public terms only. No credentials, account
  identifiers, private hostnames, or private addresses.

## File naming

`YYYY-MM-DD-<source-slug>.md`, dated by the review. A re-review is a new dated file that links
the previous one; records are dated snapshots, like the rest of `docs/research/`.

## Template

Copy the block below into a new file and fill every field. Write "not found" or "not
applicable" with a reason rather than leaving a field empty.

```markdown
# Source admission — <Source name>

- **Source:** <name; site key if one exists; category coverage (drive / gpu / ram / cpu / other)>
- **Business value:** <why this source matters: categories, price position, condition channels,
  unique inventory; what the pilot loses without it>
- **URLs reviewed:** <every page read: Terms of Use, privacy policy, robots.txt, API docs,
  affiliate/feed program pages, the proposed endpoints>
- **Terms date / retrieval date:** <the Terms' own "last updated" date; the date you retrieved
  them>
- **Automated-access language:** <verbatim quotes of every clause on automated access,
  scraping, crawling, bots, price monitoring, or data reuse; or "no clause found" with the
  sections searched>
- **Robots directives for proposed endpoints:** <the user-agent groups and Disallow/Allow
  rules that match each proposed endpoint path; crawl-delay>
- **Official API / structured alternatives:** <official APIs, feeds, affiliate data,
  schema.org/JSON-LD, platform JSON; their access conditions; why an Actor is still needed>
- **Expected cadence and volume:** <sweeps per day, pages and requests per sweep, items per
  sweep; how a sweep proves completeness>
- **Retention constraints:** <what the Terms or API license require about storage duration and
  deletion, and how that applies to each place the data lands: Actor memory, Apify dataset,
  Apify key-value store, hw-radar staging, raw payloads, canonical tables>
- **Authentication / login:** <whether any proposed endpoint needs an account, session, or
  API key; the terms attached to that account>
- **Anti-bot behavior:** <observed challenges, rate limiting, fingerprinting, soft blocks>
- **Browser / proxy need:** <whether plain HTTP works; whether a browser is needed; confirm no
  residential proxy, proxy rotation, CAPTCHA solving, or paid unblocker is needed>
- **Maintenance and cost:** <expected parser churn; estimated Apify compute, transfer, and
  storage per sweep and per billing cycle against the ≤$12 target>
- **Recommendation:** `eligible` | `permission-required` | `exclude`
- **Rationale:** <the facts above that decide the recommendation>
- **Owner decision:** <left blank for the owner; record the OQ24 answer and date here>
```
