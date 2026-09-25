# Source admission — Micro Center (microcenter.com)

- **Source:** Micro Center (microcenter.com), operated by Micro Electronics, Inc.; no existing
  hw-radar site key. Category coverage: gpu, ram, cpu, drive (general computer-components
  retailer with in-store pickup and online catalog).
- **Business value:** Large US computer-hardware retailer, well known for aggressive GPU/CPU/RAM
  pricing and in-store-only promotions that often undercut online-only merchants. Would add a
  distinct price signal (and a "unique inventory / price position" one per the template) not
  covered by the eBay/Newegg/drive-connector sources. Without it, the multi-category watch misses
  a merchant frequently cited (by users and by third-party price-tracking services) as a GPU/CPU
  price leader.
- **URLs reviewed:**
  - Terms of Use: `https://www.microcenter.com/site/customer-support/terms-conditions-site.aspx`
    (page titled "Micro Center Terms and Conditions: Online Sales")
  - Privacy policy: `https://www.microcenter.com/site/customer-support/privacy_policy.aspx`
    (referenced but not fetched in full this review; not needed for the automated-access question)
  - `https://www.microcenter.com/robots.txt`
  - Community forum thread asking about an official API:
    `https://community.microcenter.com/discussion/16617/micro-center-api` (unofficial evidence,
    used only to corroborate the absence of a documented API — see below)
- **Terms date / retrieval date:** No "last updated" or effective date is shown on the Terms of
  Use page itself. The page states: "We may revise the Terms of Use at any time without notice to
  you. Any revision or modification of the Terms of Use will be effective immediately upon posting
  of the revision or modification." Retrieved 2026-09-25 (via a third-party scrape tool after
  direct WebFetch and tavily_extract both returned fetch failures on this domain — see Anti-bot
  behavior).
- **Automated-access language:** No clause naming scraping, crawling, robots, bots, spiders,
  data mining, or price monitoring was found anywhere on the reviewed Terms of Use page. Sections
  searched: the entire page body (owner/operator statement, "Use License", "Disclaimer",
  "External Links", "Limitations", "Revisions and Errata", "Insider Accounts", "Reviews, Comments,
  Communications and Other Content"). The closest applicable clause is the general "Use License":
  > "Permission is granted to temporarily download one copy of the materials (information or
  > software) on this Site for personal, non-commercial transitory viewing only. This is the
  > grant of a license, not a transfer of title, and under this license you may not: (1) modify or
  > copy the materials; (2) use the materials for any commercial purpose, or for any public
  > display; (3) attempt to decompile or reverse engineer any software contained on this Site;
  > (4) remove any copyright or other proprietary notations from the materials; and (5) transfer
  > the materials to another person or "mirror" the materials on any other server."
  This does not name automated access specifically, but limits the granted license to "personal,
  non-commercial transitory viewing" and bars "any commercial purpose" — a systematic, repeated,
  stored, non-personal price-monitoring collection is arguably outside that license's scope, even
  without a scraping-specific clause. Per the README rule, absence of an explicit scraping clause
  is not the same as permission.
- **Robots directives for proposed endpoints:** `robots.txt` (retrieved 2026-09-25 via a
  third-party scrape tool):
  ```
  User-agent: discobot
  Disallow: /

  User-agent: *
  Disallow: /admin
  Disallow: /orders
  Disallow: /checkouts
  Disallow: /cart
  Disallow: /account
  Disallow: /quickViewConfigurator
  Disallow: /?
  Disallow: /sendConfigurator.aspx*
  Disallow: /MiniSites/amd/FONTS/Klavika-Regular.eot.woff

  Sitemap: https://www.microcenter.com/sitemap.xml
  ```
  Product, category, and search paths are not disallowed for `User-agent: *`. No `Crawl-delay` is
  set. Notably, one specific named bot (`discobot`, the Discourse forum crawler) is fully
  disallowed site-wide — unusual, and its purpose here is unconfirmed (Micro Center runs a
  Discourse-based community forum at a separate subdomain; this rule may be a leftover from that
  or may target a different concern). Robots directives are not contractual permission (README
  rule) even where they are permissive.
- **Official API / structured alternatives:** No official public product/pricing API or affiliate
  program was confirmed. A Micro Center staff reply on the company's own community forum
  (`community.microcenter.com/discussion/11793`, undated) states: "I am not 100% familiar about
  this ... but I believe we do not offer an affiliate program on our products." A separate
  unanswered forum thread (`community.microcenter.com/discussion/16617`, undated) asks Micro
  Center directly for "an official public API for accessing product data, pricing, inventory
  availability, or store-specific stock levels" with no official answer visible in the retrieved
  snippet. Third-party "Microcenter affiliate program" pages found in search results
  (e.g., shopper.com) are unofficial affiliate-aggregator marketing pages, not confirmed as an
  official Micro Center program, and were not treated as evidence of a sanctioned feed.
- **Expected cadence and volume:** Not fully scoped this review. A useful sweep would need
  category-page (or search-page, though `Disallow: /?` blocks query-string search) requests across
  GPU/CPU/RAM categories at a cadence comparable to other MS-2 sources; per-item detail-page reads
  would likely be needed for identifiers and full spec text. Completeness would need proof that
  category pagination reaches the full catalog per category, unverified here.
- **Retention constraints:** No Terms-based retention or reuse restriction beyond the general
  "personal, non-commercial" license language quoted above; no bounded-retention class implied by
  the ToU text reviewed.
- **Authentication / login:** No login required for ordinary product/category browsing.
- **Anti-bot behavior:** Observed, not inferred: both `WebFetch` (Claude Code's built-in fetcher)
  and `tavily_extract` returned outright fetch failures (`WebFetch`: "HTTP 403 Forbidden") against
  both the Terms of Use page and `robots.txt` on this domain. A third tool (Serper's `scrape`)
  succeeded on both. This indicates an active bot-detection/WAF layer that blocks at least some
  non-browser HTTP clients even on public, non-sensitive pages (including `robots.txt` itself,
  which sites do not normally gate). This is a real signal of anti-bot posture on this domain,
  though its exact trigger (User-Agent string, TLS/JA3 fingerprint, IP reputation, or something
  else) is unconfirmed from this review.
- **Browser / proxy need:** Plain HTTP access worked from at least one tool (Serper's scrape
  endpoint) without a browser, proxy, or CAPTCHA, so a browser is not confirmed necessary in
  general — but the 403s from two other fetchers mean plain HTTP is not reliably guaranteed to
  work from an arbitrary Apify plain-HTTP Actor without careful request shaping (headers,
  TLS stack). No CAPTCHA, residential proxy, or paid unblocker need was observed in this review;
  none should be added if encountered (per the no-escalation rule, that would fail admission).
- **Maintenance and cost:** Not fully scoped. If plain HTTP proves reliable with ordinary browser-
  like headers, a plain-HTTP Actor at 256 MB would be cheap per the measured 2026-09-25 baseline
  (~$0.0002/run for a trivial page); a real category/detail sweep across GPU+CPU+RAM would cost
  more in proportion to page count and payload size, unmeasured here. If the 403s prove to require
  a browser, cost rises to browser-Actor rates (unmeasured here; assumption only). Parser churn
  risk is unassessed.
- **Recommendation:** `permission-required`
- **Rationale:** No explicit scraping/crawling/bot/data-mining/price-monitoring prohibition was
  found in the Terms of Use, and robots.txt does not disallow product/category paths — but per the
  README rule, the absence of an obvious prohibition is not permission. The general "Use License"
  restricts the granted license to "personal, non-commercial transitory viewing," which a
  systematic commercial price-monitoring Actor plausibly falls outside even without a scraping-
  specific clause; that reading is a legal judgment call this record cannot make. Separately, the
  observed 403s from two of three fetch tools (including on `robots.txt` itself) show an anti-bot
  posture that needs to be characterized (what exactly is blocked, and whether a plain-HTTP Actor
  can reliably pass) before committing to a plain-HTTP collector. Both points need an owner/legal
  read before this becomes `eligible`.
- **Owner decision:**
