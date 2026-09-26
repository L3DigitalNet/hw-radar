"""Unit tests for `manage.py harvest_corpus` (MS-1e design §4).

Every adapter here is a local fake: the command drives real connectors against
live marketplaces, so exercising it in the suite must never touch the network,
the database, or a real credential. The fakes are registered under the real
registry keys (SA-002) because the command's source whitelist is keyed off them;
the OQ31-retired keys are faked too, so the refusal tests prove the command, not
a missing registry entry, keeps them from being fetched.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from hw_radar.acquisition import sources
from hw_radar.acquisition.contracts import DelistScope, ParsedListing, RawBatch, ScopeSweepReport
from hw_radar.acquisition.scrapy_support import SpiderResult
from hw_radar.acquisition.sources import ebay, goharddrive_harvest
from hw_radar.acquisition.sources.goharddrive import CATEGORY_URL as GHD_CATEGORY_URL
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.acquisition.sources.goharddrive_harvest import PagingGoHardDriveSpider
from hw_radar.catalog.management.commands import harvest_corpus
from hw_radar.catalog.models import RunKind


class FakeAdapter:
    """Adapter double: `parse()` replays a canned list, `fetch()` records the call.

    `fetch_error` models a source that dies mid-sweep (NFR-001 isolation), and
    `fetched` proves eBay's credential skip happens BEFORE any network call
    rather than after a failed fetch. `parse_skipped` stands in for records a real
    adapter would have dropped inside parse() and reported via last_parse_skipped.
    """

    expects_json = True
    run_kind = RunKind.FULL

    def __init__(
        self,
        site_key: str,
        listings: list[ParsedListing],
        *,
        fetch_error: Exception | None = None,
        parse_skipped: int = 0,
    ) -> None:
        self.name = site_key
        self.site_key = site_key
        self._listings = listings
        self._fetch_error = fetch_error
        self._parse_skipped = parse_skipped
        self.last_parse_skipped = 0
        self.fetched = False

    async def fetch(self) -> RawBatch:
        self.fetched = True
        if self._fetch_error is not None:
            raise self._fetch_error
        return RawBatch(source=self.site_key, fetched_at=datetime.now(UTC))

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        self.last_parse_skipped = self._parse_skipped
        return list(self._listings)


def _listing(key: str, *, title: str = "Seagate Exos X18 ST18000NM000J 18TB") -> ParsedListing:
    return ParsedListing(
        source_listing_key=key,
        url=f"https://example.invalid/{key}",
        title=title,
        price=Decimal("199.00"),
        currency="USD",
        condition_label="Recertified",
        attrs={"sku": key},
    )


def _install(
    monkeypatch: pytest.MonkeyPatch, adapters: dict[str, FakeAdapter]
) -> dict[str, FakeAdapter]:
    monkeypatch.setattr(
        harvest_corpus,
        "HARVEST_ADAPTERS",
        {key: (lambda a=adapter: a) for key, adapter in adapters.items()},
    )
    return adapters


def _read_staging(out: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries = [
        json.loads(line) for line in (out / "staging.jsonl").read_text().splitlines() if line
    ]
    meta = json.loads((out / "staging.meta.json").read_text())
    return entries, meta


def _no_ebay_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)


def test_staging_shape_and_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The blank-title listing is the malformed case: an entry with no title
    # carries nothing for the matcher to resolve, so it is counted, not written.
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified",
                [_listing("WD-1"), _listing("WD-2"), _listing("WD-3", title="   ")],
            )
        },
    )

    call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert [e["id"] for e in entries] == ["wd-recertified:WD-1", "wd-recertified:WD-2"]
    assert {e["source"] for e in entries} == {"wd-recertified"}
    first = entries[0]
    assert first["title"] == "Seagate Exos X18 ST18000NM000J 18TB"
    assert first["listing"] == {
        "source_listing_key": "WD-1",
        "url": "https://example.invalid/WD-1",
        "price": "199.00",
        "currency": "USD",
        "condition_label": "Recertified",
        "attrs": {"sku": "WD-1"},
    }
    # Staging is unlabeled: labeling is the separate owner-in-the-loop step.
    assert "label" not in first
    assert first["oem_dual_label"] is False
    assert meta["sources"]["wd-recertified"] == {
        "status": "ok",
        "harvested": 2,
        "skipped_malformed": 1,
    }
    assert meta["total_harvested"] == 2


def test_oem_dual_label_prefill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An EMC-gated OEM part number alongside a Seagate MPN — the dual-print case
    # the ADR-0019 rule-7 spot check measures.
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified",
                [_listing("WD-9", title="EMC 005051385 Seagate ST18000NM000J 18TB")],
            )
        },
    )

    call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(tmp_path))

    entries, _ = _read_staging(tmp_path)
    assert entries[0]["oem_dual_label"] is True


def test_ebay_skipped_without_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _no_ebay_creds(monkeypatch)
    adapters = _install(
        monkeypatch,
        {
            "serverpartdeals": FakeAdapter("serverpartdeals", [_listing("SPD-1")]),
            "goharddrive": FakeAdapter("goharddrive", [_listing("GHD-1")]),
            "wd-recertified": FakeAdapter("wd-recertified", [_listing("WD-1")]),
            "seagate-recertified": FakeAdapter("seagate-recertified", [_listing("SG-1")]),
            "ebay": FakeAdapter("ebay", [_listing("EBAY-1")]),
        },
    )

    call_command("harvest_corpus", "--all", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert meta["sources"]["ebay"] == {
        "status": "skipped_no_credentials",
        "harvested": 0,
        "skipped_malformed": 0,
    }
    assert not adapters["ebay"].fetched
    # Fakes are installed for the retired keys too, so only the command's own
    # OQ31 filter can keep --all from harvesting them.
    assert not adapters["serverpartdeals"].fetched
    assert not adapters["seagate-recertified"].fetched
    assert set(meta["sources"]) == {"goharddrive", "wd-recertified", "ebay"}
    assert {e["source"] for e in entries} == {"goharddrive", "wd-recertified"}
    assert meta["total_harvested"] == 2


@pytest.mark.parametrize("key", ["serverpartdeals", "seagate-recertified"])
def test_retired_source_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    adapters = _install(monkeypatch, {key: FakeAdapter(key, [_listing("R-1")])})

    with pytest.raises(CommandError, match="retired / permission-required"):
        call_command("harvest_corpus", "--source", key, "--out", str(tmp_path))

    assert not adapters[key].fetched
    assert not (tmp_path / "staging.jsonl").exists()


def test_ebay_harvested_with_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-secret")
    _install(monkeypatch, {"ebay": FakeAdapter("ebay", [_listing("EBAY-1")])})

    call_command("harvest_corpus", "--source", "ebay", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert meta["sources"]["ebay"]["status"] == "ok"
    assert [e["id"] for e in entries] == ["ebay:EBAY-1"]


def test_source_failure_does_not_halt_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_ebay_creds(monkeypatch)
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified", [], fetch_error=RuntimeError("upstream 503")
            ),
            "goharddrive": FakeAdapter("goharddrive", [_listing("GHD-1")]),
        },
    )

    call_command("harvest_corpus", "--all", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert meta["sources"]["wd-recertified"]["status"] == "error"
    # Type name only: the persisted manifest must never carry exception detail
    # (request URLs / token-exchange text); the full repr goes to stderr.
    assert meta["sources"]["wd-recertified"]["error"] == "RuntimeError"
    assert "upstream 503" not in json.dumps(meta)
    assert [e["id"] for e in entries] == ["goharddrive:GHD-1"]


def test_limit_truncates_after_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified", [_listing(f"WD-{n}") for n in range(1, 6)]
            )
        },
    )

    call_command(
        "harvest_corpus", "--source", "wd-recertified", "--limit", "2", "--out", str(tmp_path)
    )

    entries, meta = _read_staging(tmp_path)
    assert [e["id"] for e in entries] == ["wd-recertified:WD-1", "wd-recertified:WD-2"]
    assert meta["sources"]["wd-recertified"]["harvested"] == 2
    # Truncation is not malformed: the three dropped tail listings were healthy.
    assert meta["sources"]["wd-recertified"]["skipped_malformed"] == 0


def test_skipped_malformed_sums_adapter_and_staging_drops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Two distinct malformed populations that must both land in one count: three
    # raw records the adapter never returned (last_parse_skipped) and one returned
    # listing with a blank title that staging validity rejects.
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified",
                [_listing("WD-1"), _listing("WD-2", title="  ")],
                parse_skipped=3,
            )
        },
    )

    call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(tmp_path))

    _, meta = _read_staging(tmp_path)
    assert meta["sources"]["wd-recertified"] == {
        "status": "ok",
        "harvested": 1,
        "skipped_malformed": 4,
    }


def test_adapter_reported_drops_survive_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # --limit slices what parse() returned; it cannot retract what parse() already
    # discarded, so the adapter's count stands in full alongside a truncated harvest.
    _install(
        monkeypatch,
        {
            "wd-recertified": FakeAdapter(
                "wd-recertified",
                [_listing(f"WD-{n}") for n in range(1, 6)],
                parse_skipped=2,
            )
        },
    )

    call_command(
        "harvest_corpus", "--source", "wd-recertified", "--limit", "2", "--out", str(tmp_path)
    )

    _, meta = _read_staging(tmp_path)
    assert meta["sources"]["wd-recertified"]["harvested"] == 2
    assert meta["sources"]["wd-recertified"]["skipped_malformed"] == 2


@pytest.fixture
def sandbox_repo(tmp_path: Path) -> Path:
    """A throwaway git repo mirroring this one's `.harvest/` ignore rule.

    The tracked-path refusal (SA-006) is decided by real `git check-ignore`
    output, so it can only be exercised inside a work tree — and it must never
    be exercised inside the hw-radar checkout, where a regression would write
    staging output into the repository the guard exists to protect.
    """
    repo = tmp_path / "sandbox"
    (repo / "tests" / "fixtures" / "matching_corpus").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".harvest/\n")
    return repo


def _one_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        {"wd-recertified": FakeAdapter("wd-recertified", [_listing("WD-1")])},
    )


def test_tracked_output_refused_without_optin(
    monkeypatch: pytest.MonkeyPatch, sandbox_repo: Path
) -> None:
    _one_fake(monkeypatch)
    tracked = sandbox_repo / "tests" / "fixtures" / "matching_corpus"

    with pytest.raises(CommandError, match="--allow-repo-output"):
        call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(tracked))

    assert not (tracked / "staging.jsonl").exists()


def test_tracked_output_allowed_with_optin(
    monkeypatch: pytest.MonkeyPatch, sandbox_repo: Path
) -> None:
    _one_fake(monkeypatch)
    tracked = sandbox_repo / "tests" / "fixtures" / "matching_corpus"

    call_command(
        "harvest_corpus",
        "--source",
        "wd-recertified",
        "--out",
        str(tracked),
        "--allow-repo-output",
    )

    entries, _ = _read_staging(tracked)
    assert [e["id"] for e in entries] == ["wd-recertified:WD-1"]


def test_ignored_output_dir_needs_no_optin(
    monkeypatch: pytest.MonkeyPatch, sandbox_repo: Path
) -> None:
    _one_fake(monkeypatch)

    call_command(
        "harvest_corpus", "--source", "wd-recertified", "--out", str(sandbox_repo / ".harvest")
    )

    entries, _ = _read_staging(sandbox_repo / ".harvest")
    assert len(entries) == 1


def test_source_and_all_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(CommandError):
        call_command("harvest_corpus", "--out", str(tmp_path))


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_genuinely_outside_a_work_tree_needs_no_optin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A plain non-repo directory: git's own "not a git repository" answer is the
    # one failure that proves no tracked path can be involved. Routed through the
    # public command (not the private guard function) so the harness exercises the
    # same path a real run does.
    _one_fake(monkeypatch)
    out = tmp_path / "plain"
    out.mkdir()

    def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
        assert cmd[3] == "rev-parse"
        return _FakeCompletedProcess(
            128, stderr=f"fatal: not a git repository (or any of the parent directories): {out}\n"
        )

    monkeypatch.setattr(harvest_corpus.subprocess, "run", fake_run)
    call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(out))
    entries, _ = _read_staging(out)
    assert [e["id"] for e in entries] == ["wd-recertified:WD-1"]


def test_dubious_ownership_refusal_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # git refused to answer at all (safe.directory) — this is NOT the "no work
    # tree" case, so the guard must not wave the write through unverified.
    _one_fake(monkeypatch)
    out = tmp_path / "dubious"
    out.mkdir()

    def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
        assert cmd[3] == "rev-parse"
        return _FakeCompletedProcess(
            128, stderr=f"fatal: detected dubious ownership in repository at '{out}'\n"
        )

    monkeypatch.setattr(harvest_corpus.subprocess, "run", fake_run)
    with pytest.raises(CommandError, match="--allow-repo-output"):
        call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(out))
    assert not (out / "staging.jsonl").exists()


def test_empty_rev_parse_stdout_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A 0 exit with nothing on stdout is unparseable: trusting it would resolve
    # Path("") to the CWD and could wrongly wave a tracked path through.
    _one_fake(monkeypatch)
    out = tmp_path / "empty-stdout"
    out.mkdir()

    def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
        assert cmd[3] == "rev-parse"
        return _FakeCompletedProcess(0, stdout="\n")

    monkeypatch.setattr(harvest_corpus.subprocess, "run", fake_run)
    with pytest.raises(CommandError, match="--allow-repo-output"):
        call_command("harvest_corpus", "--source", "wd-recertified", "--out", str(out))
    assert not (out / "staging.jsonl").exists()


def test_duplicate_ids_are_staged_once_first_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # eBay can return one item from two sweeps (or twice across pages); the
    # corpus loader rejects a duplicate id, so staging keeps the first.
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    first = _listing("EBAY-1", title="Seagate Exos X18 18TB first")
    second = _listing("EBAY-1", title="Seagate Exos X18 18TB second")
    _install(monkeypatch, {"ebay": FakeAdapter("ebay", [first, _listing("EBAY-2"), second])})

    call_command("harvest_corpus", "--source", "ebay", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert [(e["id"], e["title"]) for e in entries] == [
        ("ebay:EBAY-1", "Seagate Exos X18 18TB first"),
        ("ebay:EBAY-2", "Seagate Exos X18 ST18000NM000J 18TB"),
    ]
    assert meta["sources"]["ebay"] == {
        "status": "ok",
        "harvested": 2,
        "skipped_malformed": 0,
        "duplicates_dropped": 1,
    }


class ScopedFakeAdapter(FakeAdapter):
    """A multi-scope adapter double: one complete scope and one that saw nothing."""

    def delist_scopes(self, batch: RawBatch, parsed: list[ParsedListing]) -> list[ScopeSweepReport]:
        complete = DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=True,
            absence_grace=timedelta(hours=6),
            scope_key="ebay:cpu:a",
        )
        return [
            ScopeSweepReport("ebay:cpu:a", complete, 1, "complete"),
            ScopeSweepReport("ebay:cpu:b", None, 0, "empty"),
        ]


def test_category_harvest_uses_the_category_adapter_and_records_scopes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    # The unfiltered registry entry must not be touched by a focused harvest.
    unfiltered = _install(monkeypatch, {"ebay": FakeAdapter("ebay", [])})["ebay"]
    requested: list[str] = []
    scoped = ScopedFakeAdapter("ebay", [_listing("CPU-1"), _listing("CPU-2")])

    def factory(category: str) -> ScopedFakeAdapter:
        requested.append(category)
        return scoped

    monkeypatch.setattr(harvest_corpus, "category_sweep_adapter", factory)

    call_command("harvest_corpus", "--source", "ebay", "--category", "cpu", "--out", str(tmp_path))

    entries, meta = _read_staging(tmp_path)
    assert requested == ["cpu"]
    assert not unfiltered.fetched
    assert [e["id"] for e in entries] == ["ebay:CPU-1", "ebay:CPU-2"]
    assert meta["category"] == "cpu"
    assert meta["sources"]["ebay"]["scopes"] == [
        {"scope_key": "ebay:cpu:a", "pages": 1, "complete": True, "reason": "complete", "seen": 2},
        {"scope_key": "ebay:cpu:b", "pages": 0, "complete": False, "reason": "empty", "seen": 0},
    ]


@pytest.mark.parametrize("source_args", [["--source", "goharddrive"], ["--all"]])
def test_category_requires_the_ebay_source(source_args: list[str], tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="--category requires --source ebay"):
        call_command("harvest_corpus", *source_args, "--category", "cpu", "--out", str(tmp_path))


# ── Harvest-only corpus-expansion options ──


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--source", "goharddrive", "--ebay-query", "WD Gold"], "--ebay-query requires --source"),
        (["--all", "--ebay-query", "WD Gold"], "--ebay-query requires --source"),
        (
            ["--source", "ebay", "--category", "cpu", "--ebay-query", "WD Gold"],
            "mutually exclusive",
        ),
        (["--source", "ebay", "--ebay-query-limit", "40"], "requires --ebay-query"),
        (
            ["--source", "ebay", "--ebay-query", "WD Gold", "--ebay-query-limit", "0"],
            "must be 1..200",
        ),
        (
            ["--source", "ebay", "--ebay-query", "WD Gold", "--ebay-query-limit", "201"],
            "must be 1..200",
        ),
        (
            ["--source", "ebay", "--ebay-query", "WD Gold", "--ebay-query", "WD Gold"],
            "duplicate --ebay-query",
        ),
        (["--source", "ebay", "--ghd-max-pages", "2"], "require --source goharddrive"),
        (["--source", "goharddrive", "--ghd-max-pages", "9"], "must be 1..8"),
        (["--source", "goharddrive", "--ghd-max-pages", "0"], "must be 1..8"),
        (
            [
                "--source",
                "goharddrive",
                "--ghd-url",
                "https://www.goharddrive.com/category-s/35.htm",
                "--ghd-max-pages",
                "1",
            ],
            "smaller than the number of category URLs",
        ),
    ],
)
def test_expansion_options_are_validated(args: list[str], message: str, tmp_path: Path) -> None:
    with pytest.raises(CommandError, match=message):
        call_command("harvest_corpus", *args, "--out", str(tmp_path))


def test_ebay_query_count_is_capped(tmp_path: Path) -> None:
    queries = [f"--ebay-query=q{i}" for i in range(harvest_corpus.MAX_EBAY_QUERIES + 1)]
    with pytest.raises(CommandError, match="at most"):
        call_command("harvest_corpus", "--source", "ebay", *queries, "--out", str(tmp_path))


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://www.goharddrive.com/category-s/35.htm",
        "https://www.serverpartdeals.com/category-s/35.htm",
        "https://www.goharddrive.com/some-product-p/abc.htm",
        "https://www.goharddrive.com/category-s/35.htm?page=2",
    ],
)
def test_ghd_url_must_be_a_goharddrive_category(bad_url: str, tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="not a goHardDrive category URL"):
        call_command(
            "harvest_corpus",
            "--source",
            "goharddrive",
            "--ghd-url",
            bad_url,
            "--out",
            str(tmp_path),
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Seagate Exos X18", harvest_corpus.EbayQuery("Seagate Exos X18")),
        ("11200:WD Gold", harvest_corpus.EbayQuery("WD Gold", "11200")),
        # Only a purely numeric prefix is a category id.
        ("WD: Red Plus", harvest_corpus.EbayQuery("WD: Red Plus")),
    ],
)
def test_parse_ebay_query(value: str, expected: harvest_corpus.EbayQuery) -> None:
    assert harvest_corpus.parse_ebay_query(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "11200:", "11200:  "])
def test_parse_ebay_query_rejects_blank(value: str) -> None:
    with pytest.raises(ValueError, match="empty eBay query"):
        harvest_corpus.parse_ebay_query(value)


def _browse_handler(sent: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    """Fake Browse: a token endpoint plus a search that echoes one item per query.

    Item "shared" comes back for every query, so the per-query `new` count and
    the command's first-wins dedupe are both exercised.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        sent.append(request)
        q = request.url.params["q"]
        summaries = [
            {"itemId": f"v1|{q}|0", "title": f"{q} 18TB drive", "price": {"value": "150.00"}},
            {"itemId": "v1|shared|0", "title": "Shared listing", "price": {"value": "99.00"}},
        ]
        return httpx.Response(200, json={"total": 1234, "itemSummaries": summaries})

    return handler


def test_ebay_queries_run_single_fixed_price_pages_without_hints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.harvest-test.invalid")
    sweeps_before = ebay.CATEGORY_SWEEPS
    registry_before = dict(sources.ADAPTERS)
    # The production harvest entry must not be reached when queries are given.
    unfiltered = _install(monkeypatch, {"ebay": FakeAdapter("ebay", [])})["ebay"]
    sent: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_browse_handler(sent)))
    real_adapter = harvest_corpus.EbayQueryHarvestAdapter

    def with_mock_client(
        queries: list[harvest_corpus.EbayQuery], limit: int
    ) -> harvest_corpus.EbayQueryHarvestAdapter:
        return real_adapter(queries, limit, client=client)

    monkeypatch.setattr(harvest_corpus, "EbayQueryHarvestAdapter", with_mock_client)

    call_command(
        "harvest_corpus",
        "--source",
        "ebay",
        "--ebay-query",
        "Seagate Exos X18",
        "--ebay-query",
        "11200:WD Gold",
        "--ebay-query-limit",
        "40",
        "--out",
        str(tmp_path),
    )

    assert not unfiltered.fetched
    assert [dict(r.url.params) for r in sent] == [
        {"q": "Seagate Exos X18", "filter": "buyingOptions:{FIXED_PRICE}", "limit": "40"},
        {
            "q": "WD Gold",
            "filter": "buyingOptions:{FIXED_PRICE}",
            "limit": "40",
            "category_ids": "11200",
        },
    ]
    entries, meta = _read_staging(tmp_path)
    assert [e["id"] for e in entries] == [
        "ebay:v1|Seagate Exos X18|0",
        "ebay:v1|shared|0",
        "ebay:v1|WD Gold|0",
    ]
    assert all("category_hint" not in e["listing"] for e in entries)
    report = meta["sources"]["ebay"]
    assert report["scopes"] == []
    assert report["duplicates_dropped"] == 1
    assert [
        (q["q"], q["total"], q["returned"], q["parsed"], q["new"]) for q in report["queries"]
    ] == [
        ("Seagate Exos X18", 1234, 2, 2, 2),
        ("WD Gold", 1234, 2, 2, 1),
    ]
    # Harvest-only: production sweep configuration and the schedulable
    # registry are exactly what they were.
    assert ebay.CATEGORY_SWEEPS is sweeps_before
    assert registry_before == sources.ADAPTERS
    assert sources.ADAPTERS["ebay"] is sources.admitted_ebay_adapter


_PAGED_CATEGORY_HTML = """<html><body>
<script>var SearchParams = 'searching=Y&sort=1&cat=3&show=21&page={page}';</script>
<b>Page <input type="text" value="{page}" title="Go to page" /> of 6  </b>
</body></html>"""


def _category_page(url: str, page: int) -> HtmlResponse:
    body = _PAGED_CATEGORY_HTML.format(page=page).encode()
    return HtmlResponse(url=url, body=body, encoding="utf-8")


def _paging_spider(start_urls: list[str], max_pages: int) -> PagingGoHardDriveSpider:
    crawler = get_crawler(PagingGoHardDriveSpider)
    return PagingGoHardDriveSpider.from_crawler(crawler, start_urls=start_urls, max_pages=max_pages)


def _followed(spider: PagingGoHardDriveSpider, response: HtmlResponse) -> list[str]:
    """URLs of the page requests parse() schedules (product items are ignored)."""
    # Scrapy ships no type for Response, so parse()'s signature is partly Unknown.
    out = spider.parse(response)  # pyright: ignore[reportUnknownMemberType]
    return [r.url for r in out if isinstance(r, Request)]


def test_ghd_pagination_respects_the_total_page_cap() -> None:
    base = "https://www.goharddrive.com/3-5-inch-Desktop-SATA-IDE-SCSI-SAS-Hard-Drive-s/3.htm"
    other = "https://www.goharddrive.com/category-s/35.htm"
    # Two start pages + a cap of 5 leaves 3 follow-ups in total, although the
    # first category alone advertises 6 pages.
    spider = _paging_spider([base, other], max_pages=5)

    first = _followed(spider, _category_page(base, 1))
    second = _followed(spider, _category_page(other, 1))

    assert first == [
        f"{base}?searching=Y&sort=1&cat=3&show=21&page=2",
        f"{base}?searching=Y&sort=1&cat=3&show=21&page=3",
        f"{base}?searching=Y&sort=1&cat=3&show=21&page=4",
    ]
    assert second == []


def test_ghd_later_pages_schedule_nothing() -> None:
    base = "https://www.goharddrive.com/category-s/35.htm"
    spider = _paging_spider([base], max_pages=8)

    assert _followed(spider, _category_page(base + "?page=2", 2)) == []


def test_ghd_expansion_leads_with_the_production_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    extra = "https://www.goharddrive.com/category-s/69.htm"

    async def fake_run_spider(spider_cls: type, **kwargs: object) -> SpiderResult:
        captured.update(kwargs, spider_cls=spider_cls)
        item: dict[str, object] = {
            "url": "https://www.goharddrive.com/x-p/sku-1.htm",
            "title": "WD Ultrastar DC HC550 18TB",
            "price_text": "$189.99",
        }
        return SpiderResult(items=[item], stats={"harvest/pages": [GHD_CATEGORY_URL, extra]})

    monkeypatch.setattr(goharddrive_harvest, "run_spider", fake_run_spider)

    call_command(
        "harvest_corpus",
        "--source",
        "goharddrive",
        "--ghd-url",
        extra,
        "--ghd-max-pages",
        "6",
        "--out",
        str(tmp_path),
    )

    assert captured == {
        "spider_cls": PagingGoHardDriveSpider,
        "start_urls": [GHD_CATEGORY_URL, extra],
        "max_pages": 6,
    }
    entries, meta = _read_staging(tmp_path)
    assert [e["id"] for e in entries] == ["goharddrive:sku-1"]
    assert meta["sources"]["goharddrive"]["pages_fetched"] == [GHD_CATEGORY_URL, extra]
    # The registered adapter stays the plain first-page connector.
    assert sources.ADAPTERS["goharddrive"] is GoHardDriveAdapter
