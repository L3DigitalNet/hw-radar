# Source admission — B&H Photo Video (bhphotovideo.com)

- **Source:** B&H Photo Video (bhphotovideo.com); no existing hw-radar site key. Category coverage:
  gpu, ram, cpu, drive (general computer-components catalog: video cards, memory, processors,
  motherboards, storage — "49K+ Computing Products" per its own affiliate-program marketing copy).
- **Business value:** Large US electronics/computing superstore with broad GPU/CPU/RAM/drive
  selection, competitive pricing, and a strong reputation (rated a top mid-size US e-commerce
  site by third-party reviewers). Would add price/availability coverage outside the eBay/Newegg/
  drive-connector set. Without it, the multi-category watch loses a major mainstream US retailer
  for GPU/CPU/RAM.
- **URLs reviewed:**
  - Terms of Use / User Agreement: `https://www.bhphotovideo.com/find/HelpCenter/UserAgreement.jsp`
    (redirects to/merges with `https://www.bhphotovideo.com/find/HelpCenter/Policies.jsp`, which
    contains both the User Agreement & Disclaimer / Terms of Use and the Privacy, Security and SMS
    Policy on one page)
  - `https://www.bhphotovideo.com/robots.txt`
  - Affiliate program: `https://www.bhphotovideo.com/find/shared/affiliates.jsp`,
    `https://affportal.bhphoto.com/` (affiliate portal login page)
- **Terms date / retrieval date:** Page states "Effective Date: June 30, 2026" for both the
  User Agreement / Terms of Use and the Privacy, Security and SMS Policy sections. Retrieved
  2026-09-25.
- **Automated-access language:** Verbatim, from the User Agreement / Terms of Use section of
  `Policies.jsp` (same content served at the `UserAgreement.jsp` URL), under prohibited uses of
  the site:
  > "Using any deep-link, page-scrape, robot, crawl, index, spider, click spam, macro programs,
  > Internet agent, or other automatic device, program, algorithm or methodology which does the
  > same things, to use, access, copy, acquire information, generate impressions or clicks, input
  > information, store information, search, generate searches, or monitor the website or any
  > portion thereof;"

  Immediately followed by:
  > "Accessing or using the site for competitive purposes or for commercial purposes other than
  > the transaction of business with B&H."

  No non-commercial, research, or price-monitoring carve-out was observed anywhere in the reviewed
  text.
- **Robots directives for proposed endpoints:** `robots.txt` (retrieved 2026-09-25) is a
  search-engine-indexing file, not a scraping permission: `User-agent: *` disallows upload
  directories, search endpoints, account/email paths, product-comparison and cart features, and
  admin/internal paths; it does not generally disallow ordinary product-detail or category paths.
  `CCBot` has extra disallows; `AdsBot-Google-Mobile` is blocked from `*c3api*`. No `Crawl-delay`
  directive was observed. Two sitemaps are listed (`SiteMapIndex.xml`, `explora/sitemap.xml`).
  Per the README rule, this permissive robots posture does not override the ToU prohibition above.
- **Official API / structured alternatives:** B&H runs an approval-gated affiliate program
  (`find/shared/affiliates.jsp`; portal at `affportal.bhphoto.com`) advertising "2% base commission"
  with performance-tier upside and, per third-party affiliate-network listings, "product data
  feeds" for approved affiliates. This is a referral/monetization program governed by its own
  network terms (application and approval required; intended for driving sales via affiliate
  links, not confirmed to license bulk reuse of prices for a price-watch product). It does not
  substitute for the ToU-level prohibition on automated site access for any non-affiliate, non-
  transactional use. No public read-only product/pricing API was found.
- **Expected cadence and volume:** Not evaluated in detail — moot given the recommendation below.
  A useful sweep would need per-category or per-SKU requests at a cadence comparable to the other
  MS-2 sources (multiple times/day) to catch price and stock changes; completeness would need
  category-page pagination plus per-item confirmation, unverified here.
- **Retention constraints:** Not evaluated — moot given the recommendation below.
- **Authentication / login:** No login needed for ordinary product/category browsing. The
  affiliate program requires application, approval, and an affiliate account.
- **Anti-bot behavior:** Not probed (per instructions, only ToU/robots/API pages were fetched).
  The research tools reached the policy pages, but a plain HTTP GET of the same two policy URLs
  (`UserAgreement.jsp`, `Policies.jsp`) returned HTTP 403 on 2026-09-25 (orchestrator re-check), so
  the site does run an access-control layer that blocks simple clients; its triggers were not probed.
- **Browser / proxy need:** Not evaluated — moot. No residential proxy, proxy rotation, CAPTCHA
  solving, or paid unblocker was assumed or investigated, consistent with the "no escalation" rule.
- **Maintenance and cost:** Not evaluated — moot given the recommendation below.
- **Recommendation:** `exclude`
- **Rationale:** The Terms of Use (Effective Date 2026-06-30, retrieved 2026-09-25) explicitly and
  unconditionally prohibit "page-scrape, robot, crawl, ... spider, ... Internet agent" access used
  to "acquire information ... store information ... or monitor the website," and separately
  prohibit "commercial purposes other than the transaction of business with B&H." A price-watch
  Actor performing repeated automated reads to acquire and store pricing/availability data falls
  squarely inside both prohibited categories. Robots.txt permissiveness on product paths does not
  create contractual permission (README rule), and running the same collection on Apify instead of
  locally does not change what the site's contract allows. The only sanctioned structured channel
  found (the affiliate program) is a different legal relationship (referral/commission, approval-
  gated) and was not established to license bulk price-monitoring reuse. This mirrors the already-
  excluded Newegg pattern (MS-2 plan risk R1): a clear, unconditional ToU prohibition with no
  non-commercial carve-out.
- **Owner decision:**
