"""eBay connector: Browse API item_summary/search over OAuth2 client-credentials.

httpx JSON connector (not Scrapy). Unlike the crawl-style sources, the Browse
API is an AUTHORIZED endpoint — we hold an application token, so the fetch calls
http.get(..., check_robots=False) (the B1 robots guard is for unauthorized
crawls, not a contracted API) while keeping the honest User-Agent.

Config seeded by migration 0011: heartbeat_enabled=True, fast_lane=True. eBay's
0005 seed is volatility_profile="drop_prone", cheap_signal="ebay_browse", which
satisfies the source_config_fast_lane_eligible CHECK. There is no separate cheap
tier here: the Browse poll IS the heartbeat (probe() issues the legacy search
GET alone), so this source is heartbeat-native.

Retention (DR-008): observations are RetentionClass.EBAY_LISTING_OBSERVATION with
a <=6h TTL via expires_policy, which bounds observation STALENESS. Delete-on-
delist is the separate obligation, and delist_scopes() below is this source's half
of it — the pipeline's delist stage turns each swept scope into Listing
soft-deletes.

OAuth2 (CR-007):
  - Token is minted with a client-credentials POST to /identity/v1/oauth2/token
    (Basic b64(client_id:client_secret), form body) and cached per API base in
    _TOKEN_CACHE with a 300s safety skew (treated as expired early) so the
    rate-limited token endpoint is not hit per request.
  - The token is NEVER logged and never placed in exception text — a leaked
    bearer is a live credential.
  - On a 401 from the search GET (token revoked/expired server-side despite the
    skew), the cache is dropped and the token re-minted ONCE, then the GET is
    retried inside fetch() so the batch's final RawItem is a real 200 rather
    than a 401 that _classify_batch would flag.

Category sweeps (MS-2 F1, MS2-D-12/-31): besides the legacy drive keyword
sweep (SEARCH_PARAMS: one GET, no pagination, scope and hint None, unchanged
since MS-1d), an adapter built with category_sweeps runs one paginated,
query-scoped Browse sweep per CategorySweep. Each sweep's listings carry
category_hint=<slug> and collection_scope="ebay:<slug>:<query_id>", and
delist_scopes() reports one scope per sweep, so the pipeline applies absence and
continuity per scope: a complete GPU sweep can never delist RAM, CPU, or legacy
drive (NULL-scope) listings. The class default is legacy-only;
category_sweep_adapter() adds every CATEGORY_SWEEPS entry (harvest), and the
scheduled registry entry, sources.admitted_ebay_adapter(), keeps only the
sweeps the source x category admission matrix admits.

Only a SINGLE-PAGE category sweep can be complete. Browse pages by offset over
a Best Match ranking that moves while we page: a removal before the page
boundary plus an addition after it leaves `total` and the distinct-id count
unchanged while one live item slides across the boundary unseen. No check on
the pages we got can rule that out, and a false complete sweep delists a live
listing on the spot and redacts it (IR-002). A multi-page sweep is therefore
incomplete (`multi_page_unprovable`): its observations and continuity still
count, but its absences prove nothing. Every eBay scope — each category scope
and the legacy NULL drive scope (review r3 R3-F) — sets
stale_absence_allowed=False, so an incomplete sweep never delists, however long
a listing goes unseen; the 6h expires_policy stops showing such an offer
without claiming it ended. One page is a single ranking snapshot, so there is
no boundary to fall through. The legacy sweep is one page too, but its query
normally matches more than the 200 items that page holds (`total` above what
was seen), so it is incomplete on most runs and delists only on the rare run
that provably enumerated its result set.

Category sweeps never cost the drive sweep: the legacy GET runs first with its
own error semantics, and every category page runs under a wall-clock deadline
(CATEGORY_DEADLINE_S) and catches any exception, ending only its own sweep.

Browse facts the sweep code relies on (live eBay API, 2026-09-25, app token):
  - `limit` must be 1..200 (errorId 12006); `offset` must be 0 or a multiple of
    `limit` (12515); `category_ids` accepts ONE id (12030).
  - At most 10,000 items are retrievable (docs: offset <= 9,999). Past the
    window Browse answers 200 with 0 items, total 0 and NO `next` — a SILENT
    end, which is why an empty page after a page that had `next` is incomplete.
  - `total` drifts page to page for large result sets and the docs say not to
    paginate on it, so paging follows `next` only; `total` is an extra guard.
  - The `buy.browse` quota is 5,000 calls per 86,400 s (Analytics getRateLimits).
    Budget: 720 heartbeat probes (1 GET each) + 144 scheduled FULL runs x (1
    legacy GET + at most RUN_PAGE_BUDGET category pages) = 3,024/day, plus one
    legacy GET per heartbeat-fired FULL run, which skips the category sweeps
    (see probe()). Paginating inside probe() or a fired run would exceed it.
    The 144 is the scheduled FULL lane (poller.service.build_scheduler's
    poll-ebay job, registered only while a non-drive eBay category is
    admitted) at cadence_baseline_s 600 s, where scheduling.apply.ramp_floor_s
    pins the full lane of a heartbeat source.
    A 401 re-mint (_search) adds one more GET to the call it affects, plus a
    token POST, which is outside the Browse quota.

Creds/config from env (OpenBao-injected at runtime; never in the repo):
EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_API_BASE (default https://api.ebay.com).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, Literal, cast

import httpx
from pydantic import ValidationError

from hw_radar.acquisition import http
from hw_radar.acquisition.contracts import (
    SCOPE_KEY_MAX_LENGTH,
    SCOPE_KEY_PATTERN,
    DelistScope,
    ParsedListing,
    RawBatch,
    RawItem,
    ScopeSweepReport,
)
from hw_radar.acquisition.heartbeat import HeartbeatReading
from hw_radar.catalog.models import RetentionClass, RunKind
from hw_radar.matching.categories import DRIVE, registered_categories

logger = logging.getLogger(__name__)

SITE_KEY: Final = "ebay"  # == migration-0005 normalized_name
DEFAULT_API_BASE = "https://api.ebay.com"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE_ID = "EBAY_US"
# The legacy drive sweep: one GET, NULL scope, no hint. Browse caps limit at
# 200; q is the recert-drive sweep.
SEARCH_PARAMS = {"q": "recertified enterprise hard drive", "limit": "200"}
# Mint the token this many seconds before its stated expiry so a request never
# rides an about-to-expire token across the eBay boundary.
_TOKEN_SKEW_S = 300
# CR-004 absence grace for a TRUNCATED sweep. Every eBay scope carries it, but
# none uses it: all of them, the legacy drive scope included, set
# stale_absence_allowed=False, so gate_delist_scope drops an incomplete eBay
# sweep before the grace is ever read. The earlier rationale — that a listing
# missing every sweep for the 6h DR-008 window (_expires_in_6h) has no claim to
# still be live — does not hold for a truncated Browse sweep: a live listing
# ranked past the fetched page misses every sweep for as long as it stays live
# (review r3 R3-F). Opting a scope back onto the stale path therefore needs a
# new argument that its misses are evidence, not just a different value here.
# The pipeline's continuity requirement (SourceLaneState.continuous_since)
# would still apply, so even then an outage could never become a mass delist.
DELIST_ABSENCE_GRACE = timedelta(hours=6)

# Browse item_summary/search paging limits (module docstring has the source).
PAGE_LIMIT: Final = 200
BROWSE_RESULT_WINDOW: Final = 10_000
# Most category pages one run may fetch, across all sweeps. Enforced as a
# configuration invariant (sum of max_pages <= budget, checked when sweeps are
# built into an adapter) rather than a runtime counter: a runtime cut would
# starve whichever sweep comes last in every run, while a rejected config fails
# loudly at import. 15 keeps a FULL run at <= 16 Browse calls (quota math above).
RUN_PAGE_BUDGET: Final = 15
# An auction's current bid is not a price anyone can buy at: the watch core
# compares acquirable offer prices, so category sweeps take fixed-price
# (Buy It Now) listings only.
FIXED_PRICE_FILTER: Final = "buyingOptions:{FIXED_PRICE}"

# Sweep stop reasons recorded when fetch() ends a sweep (ScopeSweepReport.reason
# for a failed sweep). A failed page is never turned into a RawItem: its status
# would make pipeline._classify_batch fail the whole run, legacy drive sweep
# included, over one category page. The sweep instead stops, keeps the pages it
# already has, and its completeness check sees the last kept page's `next`.
_STOP_NO_NEXT = "no_next"
_STOP_PAGE_CAP = "page_cap"
_STOP_TIME_BUDGET = "time_budget"
_PAGE_FAILED = "page_failed"
# Stops that end a sweep in the ordinary way; any other stop (a failed page or
# the time budget) is the more specific reason a report records.
_CLEAN_STOPS: Final = frozenset({_STOP_NO_NEXT, _STOP_PAGE_CAP})

# Wall-clock deadline for ALL category pages of one fetch(), measured from the
# start of fetch() so a slow legacy GET shrinks it. Cross-file contract: it must
# sit well inside pipeline.FETCH_TIMEOUT_S (120 s), whose expiry fails the whole
# run — drive sweep included — as a TimeoutError. With the 30 s client timeout,
# 16 sequential calls could otherwise run to 480 s. Each page request is itself
# bounded by the time left, so the sweeps end by this deadline, not after it.
# Pinned below FETCH_TIMEOUT_S by tests/db/test_source_ebay_category_sweeps.py.
CATEGORY_DEADLINE_S: Final = 60.0


@dataclass(frozen=True)
class CategorySweep:
    """One query-scoped Browse sweep inside one eBay leaf category.

    slug is the Category slug the sweep's listings are hinted with (MS2-D-03);
    query_id names the query inside that category, and together they form the
    MS2-D-12 collection scope key. Changing q without changing query_id keeps
    the scope's delist and continuity history, so give a materially different
    query a new query_id. Invalid values raise ValueError on construction, so a
    bad CATEGORY_SWEEPS entry fails at import.
    """

    slug: str
    query_id: str
    category_id: str
    q: str
    filter: str = FIXED_PRICE_FILTER
    max_pages: int = 5

    def __post_init__(self) -> None:
        # The legacy drive sweep is the unhinted NULL scope; a "drive" category
        # sweep would give drive listings a second scope and hint.
        if self.slug == DRIVE or self.slug not in registered_categories():
            raise ValueError(f"CategorySweep slug {self.slug!r} is not a non-drive category")
        if (
            len(self.scope_key) > SCOPE_KEY_MAX_LENGTH
            or re.fullmatch(SCOPE_KEY_PATTERN, self.scope_key) is None
        ):
            raise ValueError(f"CategorySweep scope key {self.scope_key!r} is invalid (MS2-D-12)")
        if not self.category_id.isdigit():
            raise ValueError(f"CategorySweep category_id {self.category_id!r} is not numeric")
        if not self.q.strip():
            raise ValueError("CategorySweep q must be non-blank")
        # Pages past the retrievable window come back empty, so a cap beyond it
        # could only ever end in a silent window end.
        if self.max_pages < 1 or self.max_pages * PAGE_LIMIT > BROWSE_RESULT_WINDOW:
            raise ValueError(f"CategorySweep max_pages {self.max_pages} exceeds the Browse window")

    @property
    def scope_key(self) -> str:
        return f"{SITE_KEY}:{self.slug}:{self.query_id}"

    def params(self, page: int) -> dict[str, str]:
        return {
            "q": self.q,
            "category_ids": self.category_id,
            "filter": self.filter,
            "limit": str(PAGE_LIMIT),
            "offset": str(page * PAGE_LIMIT),
        }


def validate_sweeps(sweeps: Sequence[CategorySweep]) -> tuple[CategorySweep, ...]:
    """Return `sweeps` as a tuple, or raise ValueError if they cannot share one run.

    parse() identifies a page's sweep from its (category_ids, q) request
    parameters, so that pair must be unique; scope keys must be unique so two
    sweeps never write one continuity row; and the pages they may fetch must fit
    RUN_PAGE_BUDGET.
    """
    ordered = tuple(sweeps)
    if len({s.scope_key for s in ordered}) != len(ordered):
        raise ValueError("category sweeps must have unique scope keys")
    if len({(s.category_id, s.q) for s in ordered}) != len(ordered):
        raise ValueError("category sweeps must have unique (category_id, q) pairs")
    if sum(s.max_pages for s in ordered) > RUN_PAGE_BUDGET:
        raise ValueError(f"category sweeps may fetch more than {RUN_PAGE_BUDGET} pages per run")
    return ordered


# Pilot sweeps (MS-2 F1) — defaults the owner may tune. Category ids verified
# 2026-09-25/26 against the live Taxonomy API for EBAY_US (default tree 0,
# version 134): 27386 "Graphics/Video Cards" (leaf; eBay has no separate
# datacenter-accelerator category, and Tesla-class cards suggest into it);
# 11210 "Server Memory (RAM)" and 56088 "Server CPUs/Processors", both leaves
# under 51240 "Server Components"; 164 "CPUs/Processors", a leaf under 175673
# "Computer Components & Parts". 56088 and 164 are leaves in different
# subtrees, not parent and child: a query in one does not return the other's
# listings (live probe: at most 3 dual-listed items per EPYC model), so each
# needs its own sweep. eBay revises categories quarterly; docs/TODO.md tracks
# the re-verification. A query, not a bare category: category-only
# result sets (GPU ~109k, RAM ~1.32M, CPU ~143k) are far past the 10,000-item
# window, so they could never be proven complete.
#
# CPU (F6 pilot, ledger L14): one sweep per (category, seeded EPYC model) whose
# live result set fits ONE page, so every CPU scope can be proven complete
# (see the module docstring on single-page completeness) — max_pages=1 because
# a second page could only ever make the sweep unprovable. Only models in the
# first-party seed (refdata/seeds/amd-epyc.json) are swept: an unseeded model
# has no catalog target to accept against. Live totals 2026-09-26 (EBAY_US,
# fixed price): 56088 — 9354: 16, 9654: 22, 7763: 9, 7742: 13; 164 — 9354: 74,
# 7763: 152 (the closest to the 200-item page; a result set past it turns the
# scope incomplete via `next`, never falsely complete). Deliberately excluded:
#   - (164, "EPYC 9654"): ~228 results, more than one page, so never complete.
#   - (164, "EPYC 7742"): multi-variation listings come back as one variation
#     per listing, and Browse rotates which one between calls (same legacy id,
#     new itemId). source_listing_key is the full itemId, so a complete sweep
#     would delist one variation and ingest another on alternate runs.
# Six CPU pages leave RUN_PAGE_BUDGET 9 for the multi-page pilots, so RAM
# drops from 5 pages to 4 (neither pilot can be complete at any page count).
CATEGORY_SWEEPS: Final = validate_sweeps(
    (
        CategorySweep(slug="gpu", query_id="rtx-3090", category_id="27386", q="RTX 3090"),
        CategorySweep(
            slug="ram",
            query_id="ddr4-ecc-rdimm-32gb",
            category_id="11210",
            q="32GB DDR4 ECC RDIMM",
            max_pages=4,
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-9354-56088", category_id="56088", q="EPYC 9354", max_pages=1
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-9654-56088", category_id="56088", q="EPYC 9654", max_pages=1
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-7763-56088", category_id="56088", q="EPYC 7763", max_pages=1
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-7742-56088", category_id="56088", q="EPYC 7742", max_pages=1
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-9354-164", category_id="164", q="EPYC 9354", max_pages=1
        ),
        CategorySweep(
            slug="cpu", query_id="epyc-7763-164", category_id="164", q="EPYC 7763", max_pages=1
        ),
    )
)

# Process-global cache keyed by API base → (token, expires_at). Mirrors http.py's
# _ROBOTS_CACHE: a long-lived poller reuses the token until it nears expiry
# rather than minting per request against the rate-limited token endpoint.
_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}


def _expires_in_6h(observed: datetime) -> datetime:
    """DR-008 bounded-observation TTL: eBay observations expire 6h after fetch."""
    return observed + timedelta(hours=6)


def _api_base() -> str:
    return os.environ.get("EBAY_API_BASE", DEFAULT_API_BASE)


def _search_url(base: str) -> str:
    return f"{base}/buy/browse/v1/item_summary/search"


def _shipping_cost(summary: dict[str, object]) -> Decimal | None:
    # shippingOptions[0].shippingCost.value when present; isinstance-narrows each
    # level so a listing without a shipping quote degrades to None, not a raise.
    raw_options = summary.get("shippingOptions")
    if not isinstance(raw_options, list) or not raw_options:
        return None
    first = cast("list[object]", raw_options)[0]
    if not isinstance(first, dict):
        return None
    raw_cost = cast("dict[str, object]", first).get("shippingCost")
    if not isinstance(raw_cost, dict):
        return None
    value = cast("dict[str, object]", raw_cost).get("value")
    return Decimal(str(value)) if value is not None else None


def _ships_from(summary: dict[str, object]) -> str:
    raw_loc = summary.get("itemLocation")
    if not isinstance(raw_loc, dict):
        return "US"
    country = cast("dict[str, object]", raw_loc).get("country")
    return country if isinstance(country, str) and country else "US"


def _seller_username(summary: dict[str, object]) -> str:
    raw_seller = summary.get("seller")
    if not isinstance(raw_seller, dict):
        return ""
    username = cast("dict[str, object]", raw_seller).get("username")
    return username if isinstance(username, str) else ""


def _summaries(payload: dict[str, object] | None) -> list[object] | None:
    """A page's itemSummaries, [] when absent, None when present but malformed.

    Browse omits the key entirely for an empty result, so absence is an empty
    page, not a malformed one.
    """
    raw = (payload or {}).get("itemSummaries", [])
    return cast("list[object]", raw) if isinstance(raw, list) else None


def _page_count(payload: dict[str, object] | None) -> int:
    summaries = _summaries(payload)
    return len(summaries) if summaries is not None else 0


def _one_per_key(per_page: list[list[ParsedListing]]) -> list[ParsedListing]:
    """Keep one listing per source_listing_key across all pages of a batch.

    A key can come back from two category sweeps, from the drive keyword search
    and a sweep, or twice from one sweep when the ranking shifts between pages.
    Two records for one key in one batch would each rewrite the listing's
    scope and hint in persist order, and share one observed_at. The winner is
    deterministic: the first category-sweep occurrence (sweeps in configuration
    order, pages in offset order), else the first legacy occurrence. A sweep
    beats the keyword search because its hint comes from the eBay category the
    item is listed in; the unhinted legacy record would resolve it as a drive
    (R14). Output keeps batch order. Dropping a repeat never costs absence
    evidence: each scope's seen keys come from its own pages (delist_scopes),
    and the pipeline excludes every key seen anywhere in the run.
    """
    winners: dict[str, ParsedListing] = {}
    for listings in sorted(
        per_page, key=lambda page: page[0].collection_scope is None if page else True
    ):
        for listing in listings:
            winners.setdefault(listing.source_listing_key, listing)
    kept = {id(listing) for listing in winners.values()}
    return [listing for listings in per_page for listing in listings if id(listing) in kept]


def _legacy_verdict(pages: list[RawItem], skipped: int) -> str | None:
    """None when the legacy sweep provably enumerated its result set, else why not.

    Every page must carry no `next` href and a `total` no larger than the
    summaries it held. Browse's `total` is an estimate for broad queries, so for
    SEARCH_PARAMS' sweep this is normally not proven, and an unproven legacy
    sweep delists nothing (its scope opts out of stale absence) — the intended
    conservative default, not an oversight. A parse drop
    also forfeits the claim: a summary we could not read is not a listing that
    ended.
    """
    if skipped:
        return "parse_drop"
    for page in pages:
        data = page.payload_json
        if not isinstance(data, dict):
            return "unparseable_page"
        if data.get("next"):
            return "next_present"
        total = data.get("total")
        if not isinstance(total, int) or total > _page_count(data):
            return "total_exceeds_seen"
    return None


def _category_verdict(sweep: CategorySweep, pages: list[RawItem], skipped: int) -> str | None:
    """None when a category sweep provably enumerated its result set, else why not.

    Conservative by construction — every condition must hold, and a sweep of
    more than one page is never complete however they come out (module
    docstring: offset paging cannot prove enumeration). The multi-page checks
    still run first so the recorded reason names the more specific defect:
      - pages are the contiguous offsets 0, PAGE_LIMIT, ... (no page skipped);
      - no parse drop in the sweep's pages;
      - no empty page after the first: Browse ends past its window with a 200,
        0 items and no `next` (a SILENT end), which must not read as the end;
      - the final page has no `next` (a sweep stopped by max_pages or by a
        failed page still has one, so both land here);
      - every page reports the same integer `total`, and the distinct item ids
        seen reach it. `total` never drives paging (docs: use `next`); it is a
        second witness. A total that moved mid-sweep means items were added or
        removed while paging, and offset paging can then skip an item without
        any other symptom, so the sweep is not trusted.
    """
    if not pages:
        return "empty"
    if skipped:
        return "parse_drop"
    ids: set[str] = set()
    totals: set[int] = set()
    for index, page in enumerate(pages):
        data = page.payload_json
        if not isinstance(data, dict):
            return "unparseable_page"
        if httpx.URL(page.url).params.get("offset") != str(index * PAGE_LIMIT):
            return "offset_gap"
        summaries = _summaries(data) or []
        if index and not summaries:
            return "silent_window_end"
        total = data.get("total")
        if not isinstance(total, int):
            return "total_missing"
        totals.add(total)
        for summary in summaries:
            if isinstance(summary, dict):
                ids.add(str(cast("dict[str, object]", summary).get("itemId")))
    last = pages[-1].payload_json or {}
    if last.get("next"):
        return _STOP_PAGE_CAP if len(pages) >= sweep.max_pages else "next_present"
    if len(totals) != 1:
        return "total_unstable"
    if totals.pop() > len(ids):
        return "total_exceeds_seen"
    if len(pages) > 1:
        # Every check above can pass under offset-paging churn (module
        # docstring); only a single page proves enumeration.
        return "multi_page_unprovable"
    return None


class EbayAdapter:
    name = SITE_KEY
    site_key = SITE_KEY
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0  # SourceAdapter parse diagnostic; see parse()
    # Consumed by the eBay run's run_source(...) call (D1 poller wiring): bounded
    # retention class + the per-batch TTL policy the pipeline applies as
    # expires_policy(batch.fetched_at).
    retention_class = RetentionClass.EBAY_LISTING_OBSERVATION
    expires_policy = staticmethod(_expires_in_6h)

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        category_sweeps: Sequence[CategorySweep] = (),
        drive_sweep: bool = True,
    ) -> None:
        # Inject-or-own-and-close: tests inject a MockTransport client (not
        # closed by us); production leaves this None and gets a fresh client
        # per fetch, closed on the way out.
        self._client = client
        # Legacy-only by default: every MS-1 caller and frozen test builds
        # EbayAdapter() and expects exactly the single drive GET. Production
        # opts in through sources.admitted_ebay_adapter() (scheduled) or
        # category_sweep_adapter() (harvest).
        self._sweeps = validate_sweeps(category_sweeps)
        # False when (ebay, drive) is not admitted (sources.admitted_ebay_adapter):
        # fetch() then skips the legacy drive GET and probe() sends nothing, since
        # that GET is itself the drive sweep and the heartbeat.
        self._drive_sweep = drive_sweep
        self._sweep_by_query = {(s.category_id, s.q): s for s in self._sweeps}
        # Set by probe(). The heartbeat job probes and then, on a transition,
        # runs the SAME instance as a FULL run; that fired run skips the
        # category sweeps, because the heartbeat can fire on up to every probe
        # (720/day) and 15 extra pages each would exhaust the Browse quota. The
        # scheduled FULL lane builds a fresh adapter per run and sweeps them.
        self._heartbeat_probed = False
        # Why each category sweep of the most recent fetch() stopped, for that
        # batch only (matched by identity in delist_scopes). A failed page
        # leaves no RawItem behind, so this is the only record that a sweep
        # which returned nothing was attempted at all.
        self._last_batch: RawBatch | None = None
        self._last_stops: dict[str, str] = {}

    async def _mint_token(self, client: httpx.AsyncClient, base: str) -> str:
        # Direct POST (not http.get): the token endpoint takes a form body + Basic
        # auth, and is an authorized call, so it bypasses the robots preflight. The
        # token is written to the cache and returned; it is never logged.
        client_id = os.environ["EBAY_CLIENT_ID"]
        client_secret = os.environ["EBAY_CLIENT_SECRET"]
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = await client.post(
            f"{base}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=f"grant_type=client_credentials&scope={OAUTH_SCOPE}",
        )
        resp.raise_for_status()  # raises with URL only — never echoes the token
        data = cast("dict[str, object]", resp.json())
        token = str(data["access_token"])
        expires_in = int(str(data["expires_in"]))
        expiry = datetime.now(UTC) + timedelta(seconds=expires_in - _TOKEN_SKEW_S)
        _TOKEN_CACHE[base] = (token, expiry)
        return token

    async def _token(self, client: httpx.AsyncClient, base: str) -> str:
        cached = _TOKEN_CACHE.get(base)
        if cached is not None and cached[1] > datetime.now(UTC):
            return cached[0]
        return await self._mint_token(client, base)

    async def _search_get(
        self, client: httpx.AsyncClient, base: str, token: str, params: dict[str, str]
    ) -> httpx.Response:
        return await http.get(
            _search_url(base),
            client=client,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            },
            check_robots=False,  # authorized API, not a crawl
        )

    async def _search(
        self, client: httpx.AsyncClient, base: str, params: dict[str, str]
    ) -> httpx.Response:
        token = await self._token(client, base)
        resp = await self._search_get(client, base, token, params)
        if resp.status_code == 401:
            # Token rejected server-side despite the skew; drop the cache and
            # re-mint ONCE so the batch's final RawItem is a real 200.
            _TOKEN_CACHE.pop(base, None)
            token = await self._mint_token(client, base)
            resp = await self._search_get(client, base, token, params)
        return resp

    async def _sweep_pages(
        self, client: httpx.AsyncClient, base: str, sweep: CategorySweep, deadline: float
    ) -> tuple[list[RawItem], str]:
        """Fetch one category sweep's pages; return them and why paging stopped.

        Pages follow `next`, never `total`, up to sweep.max_pages, and stop at
        `deadline` (loop time; see CATEGORY_DEADLINE_S). A page that fails in
        ANY way — transport error, a token re-mint that raises, non-200,
        non-JSON — ends the sweep and is not returned (see _PAGE_FAILED), so
        neither the run's classification nor its survival depends on a
        category page. Only cancellation (a BaseException) propagates.
        """
        loop = asyncio.get_running_loop()
        pages: list[RawItem] = []
        for page in range(sweep.max_pages):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return pages, self._stopped(sweep, page, _STOP_TIME_BUDGET)
            budget = asyncio.timeout(remaining)
            try:
                async with budget:
                    resp = await self._search(client, base, sweep.params(page))
            except TimeoutError as exc:
                if budget.expired():
                    return pages, self._stopped(sweep, page, _STOP_TIME_BUDGET)
                return pages, self._page_failed(sweep, page, type(exc).__name__)
            except Exception as exc:  # any category-page failure is that sweep's alone
                return pages, self._page_failed(sweep, page, type(exc).__name__)
            if resp.status_code != 200:
                return pages, self._page_failed(sweep, page, f"http_{resp.status_code}")
            content_type = resp.headers.get("content-type", "")
            try:
                payload: object = resp.json() if "json" in content_type else None
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                return pages, self._page_failed(sweep, page, "not_json")
            data = cast("dict[str, object]", payload)
            pages.append(
                RawItem(
                    url=str(resp.url),
                    http_status=resp.status_code,
                    content_type=content_type,
                    payload_json=data,
                    payload_text=resp.text,
                    # A sweep's final page is as short as its remainder.
                    body_size_comparable=False,
                )
            )
            if not data.get("next"):
                return pages, _STOP_NO_NEXT
        return pages, _STOP_PAGE_CAP

    @classmethod
    def _page_failed(cls, sweep: CategorySweep, page: int, detail: str) -> str:
        return cls._stopped(sweep, page, f"{_PAGE_FAILED}:{detail}")

    @staticmethod
    def _stopped(sweep: CategorySweep, page: int, reason: str) -> str:
        logger.warning(
            "ebay sweep %s stopped at page %d: %s; sweep marked incomplete",
            sweep.scope_key,
            page,
            reason,
        )
        return reason

    async def fetch(self) -> RawBatch:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        base = _api_base()
        deadline = asyncio.get_running_loop().time() + CATEGORY_DEADLINE_S
        try:
            items: list[RawItem] = []
            legacy_ok = True
            if self._drive_sweep:
                # The legacy GET keeps its MS-1 error semantics: an exception
                # here still fails the run.
                resp = await self._search(client, base, SEARCH_PARAMS)
                items.append(
                    RawItem(
                        url=str(resp.url),
                        http_status=resp.status_code,
                        content_type=resp.headers.get("content-type", "application/json"),
                        payload_json=resp.json() if resp.status_code == 200 else None,
                        payload_text=resp.text,
                    )
                )
                legacy_ok = resp.status_code == 200
            stops: dict[str, str] = {}
            # A failed legacy GET fails the whole run in _classify_batch, as it
            # always has; sweeping categories behind it would only spend quota
            # on pages that run discards. It is also the throttling canary:
            # the quota is per application, so a 429 lands here too. Without
            # the drive sweep there is no canary, and each category page's own
            # failure handling ends only its sweep.
            if legacy_ok and not self._heartbeat_probed:
                for sweep in self._sweeps:
                    pages, stops[sweep.scope_key] = await self._sweep_pages(
                        client, base, sweep, deadline
                    )
                    items.extend(pages)
            batch = RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=items)
            self._last_batch, self._last_stops = batch, stops
            return batch
        finally:
            if owns:
                await client.aclose()

    def _page_sweep(self, item: RawItem) -> CategorySweep | Literal[False] | None:
        """The sweep a page belongs to: None for the legacy sweep, False if unknown.

        Identified from the request parameters Browse echoes in the page URL
        (fetch() builds them from the sweep), never from item titles: a
        category hint must come from the query scope (MS2-D-03).
        """
        params = httpx.URL(item.url).params
        category_id = params.get("category_ids")
        if category_id is None:
            return None
        return self._sweep_by_query.get((category_id, params.get("q", ""))) or False

    def _parse_page(
        self, item: RawItem, sweep: CategorySweep | None
    ) -> tuple[list[ParsedListing], int]:
        # One ParsedListing per itemSummaries[] entry. isinstance/cast narrows the
        # untyped Browse JSON at each level (mirrors wd.py): a summary without a
        # price dict is skipped rather than raising, so a malformed body degrades
        # to "0 records" (run_source's PARSER_ROT guard) instead of a crash.
        #
        # Every `skipped += 1` below is a malformed-record drop, one count per
        # summary that could have become a listing.
        out: list[ParsedListing] = []
        skipped = 0
        raw_summaries = _summaries(item.payload_json)
        if raw_summaries is None:
            return out, 1
        for raw_summary in raw_summaries:
            if not isinstance(raw_summary, dict):
                skipped += 1
                continue
            summary = cast("dict[str, object]", raw_summary)
            item_id = summary.get("itemId")
            if item_id is None:
                skipped += 1
                continue  # no itemId ⇒ can't key the listing; skip this entry
            raw_price = summary.get("price")
            if not isinstance(raw_price, dict):
                skipped += 1
                continue
            price = cast("dict[str, object]", raw_price)
            value = price.get("value")
            if value is None:
                skipped += 1
                continue
            try:
                item_price = Decimal(str(value))
            except InvalidOperation:
                skipped += 1
                continue
            try:
                listing = ParsedListing(
                    source_listing_key=str(item_id),
                    url=str(summary.get("itemWebUrl", "")),
                    title=str(summary.get("title", "")),
                    price=item_price,
                    currency=str(price.get("currency", "USD")),
                    shipping_price=_shipping_cost(summary),
                    # Search returns only active, buyable listings; there is no
                    # per-item stock field, so an active result is IN_STOCK.
                    stock_status="in_stock",
                    seller_name=_seller_username(summary),
                    ships_from_country=_ships_from(summary),
                    raw_url=item.url,  # per-item raw-payload association (Task B4)
                    # R14: every category-sweep listing carries its sweep's
                    # hint, or the resolver would treat it as a drive.
                    category_hint=None if sweep is None else sweep.slug,
                    collection_scope=None if sweep is None else sweep.scope_key,
                )
            except ValidationError, InvalidOperation:
                # A category page's malformed summary (e.g. a zero price, a bad
                # currency code, an unreadable shipping cost) is that sweep's parse
                # drop, which already makes the sweep incomplete. It must not fail
                # the run and take the drive search with it. The legacy drive page
                # keeps its MS-1 behavior: the error propagates (PARSER_ROT).
                if sweep is None:
                    raise
                skipped += 1
                continue
            out.append(listing)
        return out, skipped

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # last_parse_skipped is reset per batch (SourceAdapter contract). A page
        # from a sweep this adapter is not configured with (a replayed batch
        # after the sweep set changed) is dropped whole: its listings would
        # otherwise go unhinted and resolve as drives.
        self.last_parse_skipped = 0
        per_page: list[list[ParsedListing]] = []
        for item in batch.items:
            sweep = self._page_sweep(item)
            if sweep is False:
                self.last_parse_skipped += max(_page_count(item.payload_json), 1)
                continue
            listings, skipped = self._parse_page(item, sweep)
            per_page.append(listings)
            self.last_parse_skipped += skipped
        if not self._sweeps:
            # Legacy-only adapter: exactly the MS-1 output, repeats included.
            return [listing for listings in per_page for listing in listings]
        return _one_per_key(per_page)

    def _legacy_report(
        self, batch: RawBatch, parsed: list[ParsedListing]
    ) -> ScopeSweepReport | None:
        # Seen keys come from the sweep's own pages, not `parsed`: parse()
        # keeps one record per key across sweeps (_one_per_key), so a key the
        # drive search shared with a category sweep is absent from `parsed`'s
        # NULL-scope records although this sweep saw it.
        pages = [item for item in batch.items if self._page_sweep(item) is None]
        parsed_pages = [self._parse_page(p, None) for p in pages]
        keys = frozenset(p.source_listing_key for page, _ in parsed_pages for p in page)
        if not keys:
            # Nothing to conclude from: an empty sweep would otherwise "prove"
            # that every NULL-scope eBay listing we track has ended.
            return None
        why_not = _legacy_verdict(pages, sum(skipped for _, skipped in parsed_pages))
        scope = DelistScope(
            seen_keys=keys,
            observed_at=batch.fetched_at,
            complete=why_not is None,
            absence_grace=DELIST_ABSENCE_GRACE,
            # Same owner invariant as the category scopes (review r3 R3-F): an
            # unprovably complete sweep never delists. The legacy keyword
            # search is one 200-item page of a Best Match ranking over a
            # result set Browse normally reports as larger, so a live listing
            # ranked past that page misses every sweep for as long as it stays
            # live; six hours of continuous misses there says nothing about
            # whether it ended. Evidence expiry (_expires_in_6h) hides such an
            # offer instead. Continuity is still recorded for this scope —
            # counts_toward_sweep_continuity reads run evidence, not this flag.
            stale_absence_allowed=False,
        )
        return ScopeSweepReport(
            scope_key=None, scope=scope, pages=len(pages), reason=why_not or "complete"
        )

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        """Describe what the legacy drive sweep proves about eBay listings it did not return.

        Semantics (CR-004 / IR-002 delete-on-delist): the Browse search returns
        only active, buyable items, so a key the sweep omitted is a delist
        CANDIDATE — never a certainty. Absence is believed only when the page
        provably enumerated the entire result set; an incomplete sweep delists
        nothing however long a listing goes unseen (stale_absence_allowed=False),
        and the 6h evidence expiry hides that offer instead. The mark is
        reversible on re-sight, but IR-002 redaction runs first, so a false
        positive destroys merchant content until the listing is seen again.

        Returns None when there is nothing to conclude from. Covers the legacy
        NULL scope only; the pipeline prefers delist_scopes(), which adds one
        report per category sweep.
        """
        report = self._legacy_report(batch, parsed)
        return None if report is None else report.scope

    def delist_scopes(self, batch: RawBatch, parsed: list[ParsedListing]) -> list[ScopeSweepReport]:
        """One report per scope this batch swept (MultiScopeDelistDetector).

        The legacy NULL scope is reported only when its sweep returned listings.
        A category sweep is reported whenever it returned a page or fetch()
        recorded that it tried; a sweep that returned no listings gets a report
        with scope None, which breaks that scope's continuity. A sweep fetch()
        never attempted (a heartbeat-fired run) is not reported, so its scope's
        continuity is left alone rather than broken.

        Completeness is judged from the batch pages alone (_category_verdict);
        fetch()'s stop record only makes the reason more specific.
        """
        reports: list[ScopeSweepReport] = []
        legacy = self._legacy_report(batch, parsed)
        if legacy is not None:
            reports.append(legacy)
        stops = self._last_stops if batch is self._last_batch else {}
        by_scope: dict[str, list[RawItem]] = {}
        for item in batch.items:
            sweep = self._page_sweep(item)
            if isinstance(sweep, CategorySweep):
                by_scope.setdefault(sweep.scope_key, []).append(item)
        for sweep in self._sweeps:
            key = sweep.scope_key
            pages = by_scope.get(key, [])
            stop = stops.get(key)
            if not pages and stop is None:
                continue
            parsed_pages = [self._parse_page(p, sweep) for p in pages]
            keys = frozenset(p.source_listing_key for page, _ in parsed_pages for p in page)
            stopped = stop if stop is not None and stop not in _CLEAN_STOPS else None
            if not keys:
                reports.append(ScopeSweepReport(key, None, len(pages), stopped or "empty"))
                continue
            skipped = sum(n for _, n in parsed_pages)
            why_not = _category_verdict(sweep, pages, skipped)
            scope = DelistScope(
                seen_keys=keys,
                observed_at=batch.fetched_at,
                complete=why_not is None,
                absence_grace=DELIST_ABSENCE_GRACE,
                scope_key=key,
                # Only a provably complete category sweep may delist (owner
                # invariant; review r2 N1). An incomplete one (a `next`, a
                # total above what was seen, an unstable total, a parse drop,
                # a page cap, any multi-page sweep) keeps no stale path: a
                # listing ranked past the page cap is invisible to every sweep
                # for as long as it stays live, so six hours of misses there
                # is not evidence that it ended. The _expires_in_6h TTL hides
                # such an offer from Listing.objects.active() instead.
                stale_absence_allowed=False,
            )
            reason = stopped or why_not or "complete"
            reports.append(ScopeSweepReport(key, scope, len(pages), reason))
        return reports

    async def probe(self) -> list[HeartbeatReading]:
        # The Browse poll doubles as the heartbeat (fast_lane): ONE legacy GET,
        # parsed into one cheap reading per listing, no DB writes. Marking the
        # instance first makes fetch() skip the category sweeps, so the probe
        # stays one request and a FULL run the heartbeat fires on this same
        # instance costs one more (see __init__).
        self._heartbeat_probed = True
        if not self._drive_sweep:
            # The probe IS the legacy drive GET; with drive not admitted there
            # is nothing this source may probe.
            return []
        batch = await self.fetch()
        endpoint = _search_url(_api_base())
        return [
            HeartbeatReading(
                source_sku=p.source_listing_key,
                price=p.price,
                currency=p.currency,
                stock_status=p.stock_status,
                shipping_price=p.shipping_price,
                http_status=200,
                latency_ms=None,
                endpoint=endpoint,
            )
            for p in self.parse(batch)
        ]


def category_sweep_adapter(category: str | None = None) -> EbayAdapter:
    """The eBay harvest adapter, unfiltered by the admission matrix.

    With no category: the legacy drive sweep plus every CATEGORY_SWEEPS entry.
    With a category slug: only that category's sweeps, and the legacy drive
    GET only when the slug is drive, so a focused harvest spends Browse calls
    on that category alone. A slug no sweep carries yields an adapter that
    fetches nothing (drive aside); callers validate the slug.

    harvest_corpus's entry (sources.HARVEST_ADAPTERS; at most 1 +
    RUN_PAGE_BUDGET Browse calls). The poller never uses it: its entry is
    sources.admitted_ebay_adapter, which drops non-admitted sweeps.
    """
    if category is None:
        return EbayAdapter(category_sweeps=CATEGORY_SWEEPS)
    return EbayAdapter(
        category_sweeps=[s for s in CATEGORY_SWEEPS if s.slug == category],
        drive_sweep=category == DRIVE,
    )
