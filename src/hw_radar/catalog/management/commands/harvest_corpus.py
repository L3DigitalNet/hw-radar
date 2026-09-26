"""Manual harvest of unlabeled staging listings for the MS-1e validation corpus (design §4).

Drives each connector adapter directly — `await adapter.fetch()` → `adapter.parse(batch)` —
deliberately bypassing the poller scheduler and the `SourceConfig.enabled` go-live flag: this
is an owner-run tool for building the ratification corpus, not a scheduled job, and it must be
able to harvest a source whose production ingest is still blocked (eBay, CR-004). Each adapter's
own fetch is reused verbatim, so the robots preflight and Scrapy's ROBOTSTXT_OBEY apply exactly
as they do in production.

Output is *unlabeled* staging: `id`, `source`, `title`, `listing`, plus an `oem_dual_label`
heuristic pre-fill. The command never writes a `label` block — drafting labels is the separate
owner-in-the-loop step (design §6), and a machine-invented label would silently become the
ground truth the ratification gate is measured against.

Writes `<out>/staging.jsonl` (one entry per line) and `<out>/staging.meta.json` (per-source
`harvested` / `skipped_malformed` counts and per-source status), and prints the same summary.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.admission import RETIRED_REASON, is_retired
from hw_radar.acquisition.contracts import ParsedListing, SourceAdapter
from hw_radar.acquisition.sources import HARVEST_ADAPTERS
from hw_radar.matching.mpn import extract_candidates
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.types import TokenKind

# The five real registry keys of SA-002. `demo` and `synthetic` are in ADAPTERS
# but are fixture sources (FIXTURE_SOURCE_KEYS), and corpus schema validation
# rejects any key outside this tuple — a fixture entry would be unloadable by
# the evaluator. The OQ31-retired keys stay listed so `--source <retired>`
# reaches handle()'s explicit refusal instead of argparse's bare "invalid
# choice"; `--all` harvests ACTIVE_HARVEST_SOURCES only.
HARVEST_SOURCES: tuple[str, ...] = (
    "serverpartdeals",
    "goharddrive",
    "wd-recertified",
    "seagate-recertified",
    "ebay",
)
ACTIVE_HARVEST_SOURCES: tuple[str, ...] = tuple(k for k in HARVEST_SOURCES if not is_retired(k))

DEFAULT_OUT = Path(".harvest")

# eBay's adapter reads these at mint time (acquisition/sources/ebay.py) and would
# raise KeyError mid-fetch without them; the command skips the source instead so a
# credential-less run still harvests the other four.
EBAY_ENV_VARS = ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")


def _ebay_credentials_present() -> bool:
    return all(os.environ.get(name) for name in EBAY_ENV_VARS)


def _oem_dual_label(title: str, source_key: str) -> bool:
    """Heuristic pre-fill: the title prints both an OEM part number and a manufacturer MPN.

    A hint for the human labeling pass (ADR-0019 rule 7 spot check), never an
    assertion of ground truth — the labeler is free to overwrite it.
    """
    kinds = {c.kind for c in extract_candidates(canonicalize_title(title), source_key=source_key)}
    return TokenKind.OEM_PN in kinds and TokenKind.MANUFACTURER_MPN in kinds


def _staging_entry(source_key: str, listing: ParsedListing) -> dict[str, Any]:
    # Exactly the ParsedListing fields the eval harness rebuilds from (design §3);
    # `attrs` is the connector's real dict, copied verbatim so the resolver sees
    # production-identical input (SA-004).
    fields: dict[str, Any] = {
        "source_listing_key": listing.source_listing_key,
        "url": listing.url,
        "price": str(listing.price),
        "currency": listing.currency,
        "condition_label": listing.condition_label,
        "attrs": listing.attrs,
    }
    # MS2-D-27: written only when the collector asserted a category, so every
    # unhinted (drive-only MS-1) listing stages byte-identically to before. Its
    # counterpart is corpus.ListingFields.category_hint, which evaluate._ingest
    # replays into ParsedListing; omitting a real hint would replay a non-drive
    # listing through the drive rules.
    if listing.category_hint is not None:
        fields["category_hint"] = listing.category_hint
    return {
        "id": f"{source_key}:{listing.source_listing_key}",
        "source": source_key,
        "title": listing.title,
        "listing": fields,
        "oem_dual_label": _oem_dual_label(listing.title, source_key),
    }


def _requires_repo_opt_in(out_dir: Path) -> bool:
    """Report whether `out_dir` lands on git-tracked ground and so needs `--allow-repo-output`.

    "Tracked ground" is decided by git itself: inside a work tree and not matched by
    an ignore rule. A `rev-parse` failure whose stderr says "not a git repository" means
    we are genuinely outside any work tree — no tracked path can be involved, so no
    opt-in is needed. Any OTHER `rev-parse` failure (e.g. exit 128 "detected dubious
    ownership" from a safe.directory refusal) or an unparseable success (empty stdout)
    means git could not actually answer the question, and is treated as needing the
    opt-in — as is a check-ignore call that itself errors inside a work tree — because
    the failure this guard exists to prevent — raw, unaudited staging output committed
    into the public repo — is far worse than an extra flag on a legitimate run.
    """
    anchor = out_dir
    while not anchor.exists():
        parent = anchor.parent
        if parent == anchor:
            return False
        anchor = parent
    root = subprocess.run(
        ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if root.returncode != 0:
        return "not a git repository" not in root.stderr
    root_dir = root.stdout.strip()
    if not root_dir:
        return True
    if not out_dir.resolve().is_relative_to(Path(root_dir).resolve()):
        return False
    # Trailing separator is load-bearing: check-ignore reads a bare path as a FILE
    # when it does not exist on disk, and a directory-only rule (`.harvest/`) then
    # reports "not ignored" — which would demand --allow-repo-output for the very
    # default output directory this guard is meant to wave through.
    ignored = subprocess.run(
        ["git", "-C", str(anchor), "check-ignore", "-q", "--", f"{out_dir.resolve()}/"],
        capture_output=True,
        check=False,
    )
    return ignored.returncode != 0


async def _fetch_parse(adapter: SourceAdapter) -> tuple[list[ParsedListing], int]:
    """Return this adapter's parsed listings plus the malformed records parse() dropped.

    The skip count must be read from the same adapter instance immediately after
    parse() — `last_parse_skipped` describes only the most recent call.
    """
    batch = await adapter.fetch()
    parsed = adapter.parse(batch)
    return parsed, adapter.last_parse_skipped


class Command(BaseCommand):
    help = "Harvest unlabeled staging listings for the MS-1e validation corpus."

    def add_arguments(self, parser: CommandParser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--source", choices=HARVEST_SOURCES, help="harvest one source")
        group.add_argument(
            "--all", action="store_true", help="harvest every source that is not retired"
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="cap parsed listings per source"
        )
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
        parser.add_argument(
            "--allow-repo-output",
            action="store_true",
            help="permit writing under a git-tracked path (curated corpus only)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # This command bypasses `enabled` on purpose, but OQ31 retirement bars
        # collection itself, harvest included — refused before any request.
        source: str | None = options["source"]
        if source is not None and is_retired(source):
            raise CommandError(f"{source} is retired: {RETIRED_REASON}")
        out_dir: Path = options["out"]
        limit: int | None = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be >= 1")
        if _requires_repo_opt_in(out_dir) and not options["allow_repo_output"]:
            raise CommandError(
                f"{out_dir} is a git-tracked path; raw staging output does not belong in the "
                "repository. Pass --allow-repo-output only for a curated, labeled corpus."
            )

        sources = ACTIVE_HARVEST_SOURCES if source is None else (source,)
        entries: list[dict[str, Any]] = []
        report: dict[str, dict[str, Any]] = {}
        for key in sources:
            source_entries, status = self._harvest(key, limit)
            entries.extend(source_entries)
            report[key] = status

        summary = {
            "harvested_at": datetime.now(UTC).isoformat(),
            "out": str(out_dir),
            "sources": report,
            "total_harvested": len(entries),
        }
        self._write(out_dir, entries, summary)
        self.stdout.write(json.dumps(summary, indent=2))

    def _harvest(self, key: str, limit: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Harvest one source, absorbing its failures.

        Never raises: a source that is unreachable, throttled, or newly
        incompatible reports `status: error` and the sweep continues (NFR-001).
        A harvest is a best-effort snapshot, and losing four healthy sources to
        one broken one would waste the whole manual run.
        """
        if key == "ebay" and not _ebay_credentials_present():
            self.stderr.write(
                f"skipping ebay: {' / '.join(EBAY_ENV_VARS)} not set in the environment"
            )
            return [], {"status": "skipped_no_credentials", "harvested": 0, "skipped_malformed": 0}
        try:
            parsed, dropped_in_parse = asyncio.run(_fetch_parse(HARVEST_ADAPTERS[key]()))
        except Exception as exc:
            self.stderr.write(f"{key} failed: {exc!r}")
            return [], {
                "status": "error",
                "harvested": 0,
                "skipped_malformed": 0,
                # Type name only: a full repr can carry request URLs or
                # token-exchange detail, and this manifest may be written under
                # a tracked path via --allow-repo-output. Full repr goes to
                # stderr above, which is never persisted.
                "error": type(exc).__name__,
            }

        # Truncate BEFORE the staging-validity filter: --limit caps parsed listings
        # (design §4), so a run is reproducible from the same batch. Truncation is
        # NOT malformed and is never counted — dropping the tail of a healthy parse
        # would otherwise read as source rot in the harvest-quality report.
        if limit is not None:
            parsed = parsed[:limit]
        usable = [p for p in parsed if p.title.strip() and p.source_listing_key.strip()]
        # One entry per id, first wins: the corpus loader rejects a duplicate
        # id (matching.eval.corpus), and a source may return one listing twice
        # (eBay: the same item in two sweeps or across pages). The count is
        # recorded only when non-zero, so manifests of duplicate-free runs are
        # unchanged.
        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for listing in usable:
            entry = _staging_entry(key, listing)
            if entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                entries.append(entry)
        report: dict[str, Any] = {
            "status": "ok",
            "harvested": len(entries),
            # Both halves of the malformed picture (B3): records the adapter itself
            # discarded inside parse(), plus listings it returned that carry nothing
            # the matcher could resolve. The adapter half is unaffected by --limit,
            # so `harvested + skipped_malformed` need not equal the capped slice.
            "skipped_malformed": dropped_in_parse + (len(parsed) - len(usable)),
        }
        if len(usable) > len(entries):
            report["duplicates_dropped"] = len(usable) - len(entries)
        return entries, report

    def _write(self, out_dir: Path, entries: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "staging.jsonl").open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        (out_dir / "staging.meta.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
