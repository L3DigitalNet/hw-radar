# Terms review of the existing local connectors (2026-09-25)

Why: the MS-1e drive-matcher harvest and any future source enablement make automated requests to
the five MS-1d connectors. Existing connectors are not grandfathered (source-admission README), so
each source's current Terms were read before any harvest. Result, and what it changed:

| Source | Terms automated-access clause | Classification | Harvested 2026-09-25? |
| --- | --- | --- | --- |
| ServerPartDeals | prohibits "spider, crawl, or scrape" and "any automated use" ([record](source-admission/2026-09-25-serverpartdeals.md)) | terms prohibit automated access | no |
| Seagate recertified | prohibits "any robot, spider, site search/retrieval application, or other manual or automatic device or process to retrieve, index, 'data mine'" | terms prohibit automated access | no |
| goHardDrive | none found; content-reproduction clause only | no prohibition found (not permission) | yes (after the connector fix) |
| WD recertified | none found (Terms of Use last updated 2020-09-01) | no prohibition found (not permission) | yes |
| eBay | Browse API under the eBay API License Agreement | contractually authorized API | yes |

The two conflicts are the owner decision
[OQ31](../open-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access).
The orchestrator re-verified the ServerPartDeals and Seagate clauses verbatim against the raw pages
by direct HTTP GET on 2026-09-25.

The per-source detail below is the researcher's report (retrieval date 2026-09-25 throughout).

## Detail — goHardDrive, WD recertified, Seagate recertified

Ledger: MS-1e harvest source-permission check. Retrieval date for all live fetches below: **2026-09-25**.

## 1. goHardDrive (`www.goharddrive.com`)

- Connector: `src/hw_radar/acquisition/sources/goharddrive.py` — Scrapy spider over
  `CATEGORY_URL` (Volusion category page; `/hard-drives-s/1.htm` at review time, repointed the same
  day to the Desktop Hard Drive category `/3-5-inch-Desktop-SATA-IDE-SCSI-SAS-Hard-Drive-s/3.htm`
  because the old URL now redirects to External Enclosures).
- **Terms of Use/Service governing this domain:** `https://www.goharddrive.com/terms.php`
  ("Terms & Conditions", official/repository-external primary source, retrieved 2026-09-25). No
  "Last Updated" date is shown on the page itself; the page's internal copyright line reads
  "Copyright 2008-2017, goHardDrive.com ALL RIGHTS RESERVED" and the site footer reads
  "© 2024 goHardDrive Inc" — so the terms text predates 2024 at the latest and carries no
  reliable effective-date marker.
- **Sections searched (full page text, extracted verbatim via Tavily extract):** Price, On Hold
  Status, Lost or Damaged Package, Shipping, Cancellation of Orders, Price Changes, Typographical
  Errors, Return (RMA) Policy, No Defect Found Charges, Refund Policy, Limitations of Liability,
  Copyright, Trademarks, Warranty Disclaimer, Limitation of Liability, Typographical Errors, Term;
  Termination, Notice, Miscellaneous, Use of Site, Participation Disclaimer, Indemnification,
  Third-Party Links. **No clause anywhere uses the words robot, spider, crawl(er), scrape, bot,
  automated, data mine/mining, or harvest.** The closest provision is a copyright/reuse
  restriction, not an access-method restriction:
  > "Any other use, including but not limited to the reproduction, distribution, display or
  > transmission of the content of this site is strictly prohibited, unless authorized by
  > goHardDrive.com."
  That clause is about reproducing/redistributing site content, not about the *method* of
  accessing it (i.e., it does not itself forbid automated GETs of public category/product pages).
- **robots.txt** (`https://www.goharddrive.com/robots.txt`, fetched 2026-09-25, verbatim via
  Tavily extract): a `User-agent: *` group disallows only account/checkout/order/review/mobile
  paths (`/login.asp`, `/checkout/`, `/myaccount.asp`, `/WishList.asp`, `/Mobile/*`, etc.) with
  `Crawl-delay: 2`, plus a `GeedoProductSearch` UA with `Crawl-delay: 10`. **No rule matches
  `/hard-drives-s/1.htm`** (the connector's exact path) or any category/product path — it is not
  disallowed. This matches the connector docstring's own claim ("production spider obeys robots
  ... Crawl-delay 2").
- **Official API/feed/affiliate alternative:** none found; repo research
  (`docs/research/programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md:73,87`)
  independently calls goHardDrive a "legacy ASP/custom catalog" with no API/affiliate program
  identified.
- **Classification: `no-prohibition-found (not permission)`.** The current Terms & Conditions
  contain no automated-access/robots/scraping clause of any kind; robots.txt does not disallow the
  connector's path. Absence of a prohibition is not itself permission — no source expressly
  authorizes bulk automated collection either.

## 2. Western Digital recertified store (`api.westerndigital.com` OCC API, fronted by `www.westerndigital.com`)

- Connector: `src/hw_radar/acquisition/sources/wd.py` — httpx JSON client against
  `API_BASE = "https://api.westerndigital.com"` (`/wdwebservices/v2/us/products/search` and
  per-product `/wdwebservices/v2/us/products/{code}`), an unauthenticated SAP Commerce (hybris)
  OCC API that backs the `www.westerndigital.com` recertified storefront.
- **Terms of Use governing the site/API:** `https://www.westerndigital.com/legal/terms-of-use`
  (official primary source, retrieved 2026-09-25), **"Last Updated September 1, 2020"** (verbatim,
  confirmed by full-text extraction of the page). This is WD's general site Terms of Use, which
  the separate Terms of Sale (Consumer) US page explicitly incorporates ("These Terms are ... part
  of the Terms of Use that apply generally to the use of our Site" — per the parallel Canada Terms
  of Sale text, same structure as the US version).
- **Sections searched:** the full Terms of Use text was extracted and machine-searched for
  `robot|spider|crawl|scrap|data mining|data-mining|harvest|automated means` (case-insensitive) —
  **zero matches** anywhere in the document. The document does contain IP/restricted-use clauses
  (no reverse engineering, no circumventing access controls, no removing notices) but none frame
  the restriction in terms of automated retrieval, bots, or scraping. I also checked
  `https://www.westerndigital.com/legal/terms-and-conditions-of-sale-consumer` (US Terms of Sale)
  and `https://www.westerndigital.com/legal/reseller-supplemental-terms-and-conditions-of-sale-commercial`
  — same negative result (no robot/spider/crawl/scrape/automated/data-mining/harvest language in
  either).
- **robots.txt:**
  - `https://api.westerndigital.com/robots.txt` — **HTTP 404** (confirmed live 2026-09-25),
    reconfirming the 2026-07-04 repo recon
    (`docs/research/2026-07-04-wd-seagate-recert-endpoint-recon.md:5,42,51-56`). Per RFC 9309, a
    missing robots.txt means no crawl restriction is declared for this host.
  - `https://www.westerndigital.com/robots.txt` (fetched 2026-09-25, verbatim via Tavily extract):
    `Disallow: /content/dam/` (with `Allow: /content/dam/store/` and
    `Allow: /content/dam/western-digital/` carve-outs), `Disallow: /etc/` (with client-lib
    carve-outs), `Disallow: /libs/`, plus two URL-parameter-pattern disallows
    (`filterBy` query strings). None of these paths correspond to the OCC API paths the connector
    calls (`/wdwebservices/v2/...` lives on `api.westerndigital.com`, a separate host with its own
    404 robots.txt).
- **Official API/feed/affiliate alternative:** the OCC endpoints used are an unauthenticated public
  commerce API, not a documented/contracted partner API; no WD affiliate/partner data feed for
  recertified pricing was found in this pass.
- **Classification: `no-prohibition-found (not permission)`.** No automated-access clause exists
  in the current (2020-09-01) Terms of Use, the US Terms of Sale, or the Reseller Supplemental
  Terms; the API host itself serves no robots.txt at all. This is a stale-looking Terms of Use
  (5+ years old) — flag for re-check if WD republishes it, since sites commonly add anti-scraping
  clauses in later revisions.

## 3. Seagate recertified store (`www.seagate.com`)

- Connector: `src/hw_radar/acquisition/sources/seagate.py` — httpx client against
  `CATEGORY_URL = "https://www.seagate.com/products/seagate-recertified/exos-recertified/"`
  (bootstrap JSON embedded in the HTML category page). The docstring is explicit that
  `store.seagate.com` (robots `Disallow: /`) is never called — only `www.seagate.com`.
- **Terms of Use governing this domain:** `https://www.seagate.com/legal/website-use-terms-and-conditions/`
  ("Website Use Terms and Conditions", official primary source, retrieved 2026-09-25),
  **"Last Updated: July 24, 2024"** (verbatim, present at the bottom of the page). This explicitly
  covers "Seagate.com ... and their sub-domains."
- **Decisive verbatim clause** — CODE OF CONDUCT section, item (iv):
  > "You further agree not to: ... (iv) use any robot, spider, site search/retrieval application,
  > or other manual or automatic device or process to retrieve, index, "data mine," or in any way
  > reproduce or circumvent the navigational structure or presentation of this Site or any content
  > or other materials on this Site."
  This is an unambiguous, currently-effective contractual prohibition on exactly the kind of
  automated, bounded HTTP GET the `seagate.py` connector and `harvest_corpus` would perform against
  `www.seagate.com`. The same CODE OF CONDUCT section also restricts use of the Site generally to
  "your personal, non-commercial use" — a second, independent problem for a commercial
  price-monitoring tool, separate from the robots/data-mining clause.
- **robots.txt** (`https://www.seagate.com/robots.txt`, fetched 2026-09-25, verbatim via Tavily
  extract): the general wildcard group (`User-agent: \`) sets `Crawl-delay: 20` and disallows only
  `/product-finder/...` paths (plus regional variants) and `/resources/unlisted/`; `/promos/` is
  disallowed except for two named promo pages. **No rule matches
  `/products/seagate-recertified/exos-recertified/`** — the connector's exact path is not
  disallowed by robots.txt. This matches the repo's 2026-07-04 recon finding
  (`docs/research/2026-07-04-wd-seagate-recert-endpoint-recon.md:43,58-65`) that the category page
  is "robots-allowed" at `Crawl-delay: 20`. **That prior repo research recorded only the robots
  posture, not the Terms of Use clause above — it did not check contractual ToS for
  automated-access prohibitions**, so this is new evidence, not a correction of a prior finding.
- **Official API/feed/affiliate alternative:** repo research notes Rakuten-style affiliate
  parameters observed on Seagate store URLs
  (`docs/research/programmatic-acquisition-research-for-enterprise-and-nas-drive-merchants.md:71`)
  but no confirmed public/partner pricing API or feed.
- **Classification: `terms-prohibit-automated-access`.** The Website Use Terms and Conditions
  (2024-07-24, currently effective, applies to `www.seagate.com` and sub-domains) expressly and
  unambiguously prohibit robots/spiders/automated retrieval and data mining of exactly the pages
  the connector fetches, independent of and in addition to any robots.txt posture.

## Prior repo findings (context, not new)

- `docs/research/2026-07-04-wd-seagate-recert-endpoint-recon.md` (2026-07-04, private/internal
  research) recorded **robots.txt posture only** for WD and Seagate — it did not review either
  site's Terms of Use/Sale for automated-access clauses. Its `store.seagate.com` `Disallow: /`
  finding is reconfirmed as still irrelevant here because the connector never calls that host.
- `docs/research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md`
  (2026-07-03) is a general US legal-landscape memo; it does not name goHardDrive, WD, or Seagate
  specifically. It generically classifies "Merchant public pages (recert specialists, resellers,
  VARs)" as the "legally cleanest persistable core" under generic scraping guardrails
  (`docs/research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md:120`),
  but that predates and does not substitute for a per-source ToS check.
- `docs/resolved-questions.md` and `docs/adr/` contain no prior ToS findings naming goHardDrive,
  WD, or Seagate. The only related precedent is Newegg, excluded from the MS-2 Actor track because
  "its Terms of Use prohibit automated access and scraping"
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md:1326`) — establishing that
  this repo already treats an explicit ToS automated-access prohibition as an exclusion trigger,
  which is directly on point for Seagate's clause found here.
- Today's parallel finding that ServerPartDeals' ToS prohibit "any automated use of the Service"
  and "spider, crawl, or scrape" (per the dispatch brief) is a third, independent instance of the
  same pattern now seen at Newegg and Seagate.

## Summary table

| Source | Terms URL + retrieval date | Decisive verbatim clause | robots.txt for connector's exact path | Classification |
| --- | --- | --- | --- | --- |
| goHardDrive (`www.goharddrive.com/hard-drives-s/1.htm`) | https://www.goharddrive.com/terms.php — retrieved 2026-09-25, no reliable "last updated" date on page (copyright text says 2008-2017; footer says © 2024) | none found (searched all 20 named sections of the Terms & Conditions; only a content-reproduction/copyright clause exists, not an access-method clause) | `/hard-drives-s/1.htm` not listed in any `Disallow`; general `Crawl-delay: 2` | `no-prohibition-found (not permission)` |
| WD recertified (`api.westerndigital.com/wdwebservices/v2/...`, fronted by `www.westerndigital.com`) | https://www.westerndigital.com/legal/terms-of-use — retrieved 2026-09-25, "Last Updated September 1, 2020"; also checked US Terms of Sale (Consumer) and Reseller Supplemental Terms | none found (full-text search of Terms of Use + Terms of Sale + Reseller Supplemental Terms for robot/spider/crawl/scrape/data-mining/harvest/automated-means — zero matches) | `api.westerndigital.com/robots.txt` → HTTP 404 (no file, reconfirms 2026-07-04 recon); `www.westerndigital.com/robots.txt` disallows only `/content/dam/`, `/etc/`, `/libs/`, and `filterBy` query patterns — none match the OCC API paths | `no-prohibition-found (not permission)` |
| Seagate recertified (`www.seagate.com/products/seagate-recertified/exos-recertified/`) | https://www.seagate.com/legal/website-use-terms-and-conditions/ — retrieved 2026-09-25, "Last Updated: July 24, 2024" | "You further agree not to: ... (iv) use any robot, spider, site search/retrieval application, or other manual or automatic device or process to retrieve, index, 'data mine,' or in any way reproduce or circumvent the navigational structure or presentation of this Site or any content or other materials on this Site." (Code of Conduct, item iv) — also restricts Site use to "personal, non-commercial use" | `/products/seagate-recertified/exos-recertified/` not in any `Disallow`; general wildcard group `Crawl-delay: 20` (matches 2026-07-04 repo recon) | `terms-prohibit-automated-access` |

## Confidence and gaps

- High confidence on the Seagate finding: the clause is unambiguous, current (2024-07-24), and
  the page explicitly names Seagate.com and its sub-domains, which includes `www.seagate.com`.
- Medium-high confidence on goHardDrive and WD: full-text search found no automated-access clause,
  but goHardDrive's terms page carries no reliable effective date (stale copyright markers, 2008-2017
  vs. 2024 footer) and WD's Terms of Use is itself over 5 years old (2020-09-01) — either could be
  superseded by an unlinked/unindexed newer page I did not find. Recommend a follow-up check if
  either site republishes its terms before MS-1e harvest runs.
  Did not attempt to enumerate every possible terms/legal subpage on either domain (e.g. WD has a
  large `/legal/` tree with jurisdiction-specific variants); the general Terms of Use / Terms of
  Sale pages checked are the ones a US-based automated buyer/monitor would be bound by.
- Per the task's own rule: robots.txt permission is not contractual permission, and the absence of
  a prohibition (goHardDrive, WD) is not itself permission to run `harvest_corpus` against those
  endpoints — it only means this pass found no *documented* contractual bar. The orchestrator
  should treat goHardDrive and WD as "no known ToS bar, proceed under the repo's general
  scraping-guardrails policy" and Seagate as "known ToS bar — do not harvest via
  `www.seagate.com` without an owner risk-acceptance decision," consistent with how Newegg was
  already excluded on the same basis.
