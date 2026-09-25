# Source admission — ServerMonkey (servermonkey.com)

- **Source:** ServerMonkey (servermonkey.com), operated by ServerMonkey.com LLC; no existing
  hw-radar site key. Category coverage: drive, ram, cpu (refurbished/used enterprise-server
  hardware retailer: "new, used, and refurbished Dell and HPE servers" and server components).
- **Business value:** A refurbished/used server-parts channel distinct from the new-retail sources
  already in scope, potentially useful for the condition-channel and unique-inventory value the
  README template calls out (enterprise CPUs, server RAM, and enterprise drives at
  secondary-market pricing). Requested as an evaluation candidate alongside TechMikeNY.
- **URLs reviewed:**
  - `https://www.servermonkey.com/default/terms-and-conditions-of-sale` and
    `https://www.servermonkey.com/terms-and-conditions-of-sale` (both titles resolve to the same
    "Terms and Conditions of Sale" content per search-snippet evidence)
  - `https://www.servermonkey.com/privacy-policy` / `https://www.servermonkey.com/default/privacy-policy`
  - `https://www.servermonkey.com/robots.txt`
  - `https://www.servermonkey.com/partners` (partner/vendor page, checked for a data-feed
    program; found none)
- **Terms date / retrieval date:** No "last updated" or effective date was visible in any
  retrieved excerpt of the Terms and Conditions of Sale. Retrieval attempted 2026-09-25; the page
  body could not be fully read this session (see Automated-access language and Anti-bot behavior
  below) — this date reflects the attempt, not a confirmed successful full-text read.
- **Automated-access language:** **Not confirmed either way.** Search-engine-indexed snippets of
  the "Terms and Conditions of Sale" page (retrieved via Brave/Serper search results, not a full
  page fetch) show only B2B sales-contract language — quotation validity, payment terms, pricing
  adjustment, and disclaimer-of-warranty clauses (e.g., "NEITHER SERVERMONKEY NOR BUYER IS BOUND
  BY ANY TERMS AND CONDITIONS IMPRINTED OR IMBEDDED IN ORDERS..."). No general "Terms of Use" /
  website-use-agreement page (of the kind B&H and Micro Center have) was found in this review;
  ServerMonkey appears to publish only a sales contract and a privacy policy. Three independent
  fetch tools (`WebFetch`, `tavily_extract`, Serper `scrape`) all failed to retrieve either the
  Terms and Conditions of Sale page or `robots.txt` directly in this session (see Anti-bot
  behavior), so the full text of the sales terms was not read, and no automated-access clause was
  confirmed present or absent by direct inspection. Sections searched: search-engine snippets of
  the sales-terms page, and the full text of the privacy policy (which discusses automatically-
  collected browser/IP/cookie data for analytics, not scraping of the site by others). No clause
  addressing scraping, crawling, robots, bots, spiders, data mining, or price monitoring was found
  in any material actually read.
- **Robots directives for proposed endpoints:** **Unconfirmed.** `https://www.servermonkey.com/robots.txt`
  returned HTTP 403 Forbidden to `WebFetch`, and outright fetch failures to `tavily_extract` and
  Serper `scrape` (500 error), on every attempt in this session. No robots directives could be
  read for this domain in this review.
- **Official API / structured alternatives:** None found. The `/partners` page describes
  IT-service/technology partners, not a data feed or affiliate program. No affiliate program or
  public API was found in search.
- **Expected cadence and volume:** Not scoped — moot pending resolution of the blockers below.
- **Retention constraints:** Not scoped — moot pending resolution of the blockers below.
- **Authentication / login:** Ordinary product browsing does not appear to require login (not
  independently confirmed this session, since the storefront itself was not fetched — only
  policy/robots pages were, per instructions).
- **Anti-bot behavior:** Observed, not inferred: `robots.txt` itself — normally an unauthenticated,
  unblocked static file on essentially every site — returned HTTP 403 from `WebFetch`, and hard
  fetch failures from `tavily_extract` and Serper `scrape`. The Terms and Conditions of Sale page
  also 403'd from `WebFetch`. This is a stronger and more consistent block than Micro Center's
  (which at least one tool could read); it suggests an aggressive WAF/anti-bot layer (e.g.,
  Cloudflare or similar) in front of this entire domain that rejects non-browser HTTP clients by
  default, independent of any specific path.
- **Browser / proxy need:** **Unconfirmed but concerning.** Plain HTTP access from three different
  automated tools failed uniformly on this domain, including on files sites do not normally gate.
  This raises real doubt that a plain-HTTP Apify Actor could reliably collect from this domain
  without a browser-rendered (headless-browser) Actor at minimum — and even then, whether a
  legitimate browser Actor (no CAPTCHA solving, no proxy rotation) can pass this WAF is unverified.
  No CAPTCHA, residential proxy, or paid-unblocker need was confirmed, but none can be ruled out
  either from this review; per the no-escalation rule, if manual verification later shows any of
  those are required, this source fails admission outright.
- **Maintenance and cost:** Not scoped. If a browser Actor turns out to be required, cost is
  materially higher than the plain-HTTP baseline (~$0.0002/run measured 2026-09-25 for a trivial
  static page); browser-Actor cost is unmeasured here and would need its own F5a-style
  measurement before it could fit inside the ≤$12/cycle target alongside other sources.
- **Recommendation:** `permission-required`
- **Rationale:** Neither an automated-access prohibition nor an affirmative permission was
  confirmed, because the Terms and Conditions of Sale and `robots.txt` could not be read directly
  in this session — every fetch tool used was blocked (403 or fetch failure) on this domain,
  including on `robots.txt` itself. Per the README rule, an unconfirmed absence of prohibition is
  not permission, and this record cannot respect "robots permission is not contractual permission"
  or record actual robots directives when robots.txt itself was unreadable. The observed anti-bot
  blocking of every non-browser fetch attempt, on a domain that would need a browser at minimum to
  even read its own robots.txt, is itself a strong caution sign for the "no CAPTCHA solving, no
  proxy rotation, no paid unblocker" rule — this needs a manual (human-browser or authorized
  operator) read of the Terms and robots.txt, and confirmation that plain requests or a plain
  browser Actor (not a CAPTCHA-solving or proxy-rotating one) can reach the site, before any
  further admission step.
- **Owner decision:**
