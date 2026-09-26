# Source admission — ServerPartDeals

- **Source:** ServerPartDeals (serverpartdeals.com); site key `serverpartdeals`
  in the existing local connector. Category coverage on the live site: drive
  (HDD/SSD — existing connector scope), RAM (confirmed collections), GPU
  (individual products/accelerators confirmed, dedicated collection slug not
  confirmed), server/system bundles (not a first-class HW Radar category).
  No standalone CPU collection found.

- **Business value:** ServerPartDeals is one of HW Radar's existing 5
  MS-1 sources (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md:194`)
  and is currently disabled pending the owner-in-the-loop drive-matcher
  ratification (per `AGENTS.md`). It is a specialist/VAR reseller (tier T2,
  `docs/resolved-questions.md:408`) with recertified enterprise drives, RAM,
  and GPU/accelerator inventory that could, in principle, extend the existing
  drive connector to RAM and GPU categories without adding a new source.
  Extending it is not possible under current Terms (see rationale).

- **URLs reviewed:**
  - `https://serverpartdeals.com/robots.txt` (fetched 2026-09-25)
  - `https://serverpartdeals.com/policies/terms-of-service` (fetched
    2026-09-25 — this resolved and returned content, so it is the site's live
    Terms of Service/Terms of Use page)
  - `https://serverpartdeals.com/` (homepage, via search snippet)
  - `https://serverpartdeals.com/collections` and
    `https://serverpartdeals.com/collections/all?page=1` (navigation facets,
    via search snippet)
  - `https://serverpartdeals.com/collections/enterprise-ram`,
    `/collections/8gb-ram`, `/collections/256gb-ram` (RAM collections, via
    search snippet)
  - `https://serverpartdeals.com/pages/enterprise-hardware-procurement`
    (B2B RFQ/procurement page listing category groups, via search snippet)
  - Individual GPU product pages (Tesla V100 PG500-216, PNY A5000, Dell
    PowerEdge XE9680, Gigabyte G292-Z42) — read only as evidence that GPU
    products are catalogued; not crawled as a listing sweep.
  - No account, login, affiliate, or API-key page was reached (public,
    unauthenticated research only).

- **Terms date / retrieval date:** No explicit "last updated" date appeared
  on the fetched Terms of Service page content (retrieval could not surface
  one). Retrieval date: 2026-09-25. This is a gap: the record cannot confirm
  how current the Terms are, only that they were live and reachable at this
  URL on this date.

- **Automated-access language (verbatim quotes, Terms of Service, Section
  12 "Prohibited Uses" and Section 13):**
  - "to bypass any robot exclusion headers or other measures we take to
    restrict access to the Service, or to use any software, technology, or
    device to harvest or manipulate data from the Service"
  - "to spam, phish, pharm, pretext, spider, crawl, or scrape"
  - "to make any automated use of the Service, or take any action that
    imposes or may impose an unreasonable or disproportionately large load
    on our servers or network infrastructure"
  - Section 13 (IP/content protection): "Unauthorized use of any content,
    including reproduction of product listings, pricing data, or images for
    competitive purposes, is strictly prohibited and may result in legal
    action."
  - Enforcement: "We reserve the right to terminate your use of the Service
    or any related website for violating any of the prohibited uses."
  - Verified 2026-09-25 against the raw page (direct HTTP GET, HTML text search): the
    prohibited-uses list items (i) "to spam, phish, pharm, pretext, spider, crawl, or scrape",
    (l) "to make any automated use of the Service …" and (m) "… to use any software,
    technology, or device to harvest or manipulate data from the Service" appear verbatim, and the
    Section 13 sentence on "reproduction of product listings, pricing data, or images for
    competitive purposes" immediately precedes "Section 14 - Copyright Complaints". Section 13 also
    permits reuse only "for temporary caching or as necessary for personal, non-commercial browsing".

- **Robots directives for proposed endpoints:** `robots.txt` (fetched
  2026-09-25) defines an unnamed/general `User-agent: *`-style group with:
  - `Disallow: /admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`,
    `/account`, `/cdn/wpm/*.js`, `/services`, `/sf_*`, `/cart.js`,
    `/recommendations/products`
  - `Disallow: /collections/*sort_by*`, `/collections/*+*`,
    `/collections/*%2B*`, `/collections/*filter*&*filter*`
  - `Disallow: /blogs/*+*`
  - Allow rules carve out specific vendor/filtered collection query patterns
    (hard drive, SSD, JBOD collections) and root/account/checkout-adjacent
    patterns.
  - No explicit `Disallow`/`Allow` line was found matching `*.json`,
    `/products.json`, or a bare `/search` path for the general group.
  - A separate, broader rule set exists for `adsbot-google` (Google's ads
    crawler) permitting deeper product/collection/page/blog access — not
    applicable to a generic HTTP client or an Apify Actor.
  - No `Crawl-delay` directive found. `Sitemap: https://serverpartdeals.com/sitemap.xml`.
  - Per the rules in this directory's README: robots silence on
    `/products.json` is not permission — the Terms of Service govern
    regardless of what robots.txt does or does not disallow.

- **Official API / structured alternatives:** No public API, developer
  documentation, data feed, or affiliate program page was found in this
  session's search budget. The existing local connector uses the Shopify
  storefront `/collections/<handle>/products.json` convention (unauthenticated,
  undocumented as a public API — it is the platform's default
  storefront JSON, not a contractually offered feed). No Google
  Shopping/merchant feed or affiliate network listing was found.

- **Expected cadence and volume:** Not evaluated — moot given the exclude
  recommendation below. (For reference, the existing disabled drive connector
  fetches one collection at `limit=250` per FULL run.)

- **Retention constraints:** Not evaluated — moot given the exclude
  recommendation.

- **Authentication / login:** None of the reviewed endpoints (collections,
  robots.txt, Terms, procurement page) require an account or API key.

- **Anti-bot behavior:** Not probed (task instructions prohibited product/
  JSON endpoint crawling). No Cloudflare/DataDome interstitial markers were
  visible in the fetched robots.txt or Terms pages. **Inferred, not
  confirmed:** the site is Shopify-hosted (standard `/collections/<handle>/
  products.json`, `/cdn/wpm/*.js`, `/checkouts/internal/preloads.js`
  robots.txt paths are Shopify-platform conventions), which typically
  fronts with Shopify's own bot-mitigation/rate-limiting rather than a
  separate named vendor — this is inferred from URL conventions only, not
  from an observed challenge response.

- **Browser / proxy need:** Not evaluated (moot); the existing connector
  uses plain `httpx` JSON GET with no browser or proxy, consistent with a
  Shopify JSON endpoint, but this says nothing about permissibility.

- **Maintenance and cost:** Not evaluated — moot given the exclude
  recommendation.

- **Recommendation:** `exclude`

- **Rationale:** The site's Terms of Service (Section 12/13, retrieved
  2026-09-25) explicitly and unambiguously prohibit the categories of
  activity that either an expanded local connector or an Apify-Actor-based
  sweep would perform: "spider, crawl, or scrape," "bypass any robot
  exclusion headers ... to harvest or manipulate data," "make any automated
  use of the Service," and separately prohibit "reproduction of product
  listings, pricing data ... for competitive purposes." Per this directory's
  own rules, robots.txt's silence on `/products.json` and `*.json` is not
  permission, and the absence of a proxy/anti-bot requirement does not
  override a contractual prohibition. This mirrors the Newegg exclusion
  already on record (`docs/open-questions.md:38-39`, MS-2 plan R1) — a
  clear, quoted ToS prohibition on scraping and automated use, not an
  ambiguous or silent term. Breadth (RAM and GPU collections existing on the
  live site) is therefore moot for F2's decision: even if non-drive
  collections exist, they cannot be swept, hinted or otherwise, without
  violating these Terms. **This also surfaces a conflict with the existing
  disabled local connector** (`src/hw_radar/acquisition/sources/
  serverpartdeals.py`), which already performs exactly this kind of
  automated JSON collection against a drive sub-collection today; per this
  README's "No grandfathering" rule, that conflict should be recorded
  separately (its own finding or open question) rather than resolved by this
  admission record, since ServerPartDeals is an *existing* source, not only
  a *candidate* one.

- **Owner decision:** _(left blank)_

## MS-2 F2 breadth finding (2026-09-25)

**Technical breadth exists for GPU and RAM; CPU as a standalone browsable
category is unconfirmed/likely absent.** Observed via public navigation and
search-indexed pages (no `/products.json`, `/collections/*.json`, or product
listings crawled, per task instructions):

- **RAM:** multiple dedicated collections exist — `/collections/enterprise-ram`,
  `/collections/8gb-ram`, `/collections/256gb-ram` (capacity-sliced RAM
  collections; site nav on `/collections/all?page=1` shows a "Ram" facet with
  Generation (DDR4) and Capacity filters: 8GB/16GB/32GB/64GB/128GB).
- **GPU:** individual GPU/accelerator products are listed and indexed
  (e.g., NVIDIA/HP Tesla V100 PG500-216, PNY NVIDIA A5000, plus multi-GPU
  servers like the Dell PowerEdge XE9680 8×H100 and Gigabyte G292-Z42).
  The B2B procurement/RFQ page (`/pages/enterprise-hardware-procurement`)
  names "GPUs & Accelerators — Datacenter GPUs for training and inference,
  single cards to full nodes" (NVIDIA, AMD) as one of its sourced categories.
  A specific `/collections/gpu`-style URL was not directly confirmed by
  search indexing in the time budget, but individual GPU product pages are
  indexed and reachable, so a GPU collection almost certainly exists in the
  site's own navigation even though its exact slug wasn't surfaced.
- **CPU:** no dedicated CPU/processor collection URL was found via search.
  CPUs appear only as components of servers (e.g., "Dual AMD EPYC 7H12") and
  on the sell-side/procurement pages ("Processors: Intel Xeon Scalable
  (3rd/4th/5th Gen), AMD EPYC..."), not as a standalone purchasable CPU
  catalog. This is a **breadth gap**, separate from the eligibility finding
  below.
- Total catalog size: `/collections/all` search snippet reports "215 results
  found for `*`" (Brave search result, retrieved 2026-09-25) — small,
  consistent with the existing drive-only connector's low product count.
- Existing connector (`src/hw_radar/acquisition/sources/serverpartdeals.py:23-25`)
  only targets `/collections/manufacturer-recertified-drives/products.json`,
  confirming today's code covers a single drive sub-collection, not the
  broader catalog.

**However, per F2's decision rule and the owner's admission-record
requirement, breadth does not settle the question.** See part (b): the
source's Terms of Use decisively prohibit the kind of automated collection
an Actor-based (or expanded local) sweep would need, independent of which
collections exist. F2's "add hinted, scoped collection sweeps" branch is
therefore blocked by contract terms, not by an absence of non-drive
collections.

---

F2 outcome: no change to the connector. Technical breadth exists (RAM, some GPU), but the Terms prohibit
the automated collection that any breadth sweep would perform, so the connector is not broadened. The
existing drive connector's own conflict with these Terms is recorded as
[OQ31](../../resolved-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access).
