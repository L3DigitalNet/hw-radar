"""Print the pilot acquisition evidence report (MS-2 plan F3, Task 6).

`pilot_report [--since ISO | --days N] [--source SITE_KEY ...] [--json]`
summarizes, per source -> provider -> collection scope -> category, the runs
and their completeness outcomes, identifier (MPN / model id) coverage,
condition and shipping presence, freshness lag, parser and matcher unknowns,
failures and recovery, and cost. The window defaults to the last 7 days.
`--json` prints the stable schema documented in
`hw_radar.acquisition.pilot_report`.

Read-only: it writes no row and makes no network call (see the module's SCOPE).
An empty database prints an explicit empty report and exits 0.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from hw_radar.acquisition.pilot_report import build_report, render_text

DEFAULT_DAYS = 7


class Command(BaseCommand):
    help = "Summarize pilot acquisition evidence per source/provider/scope/category (read-only)."

    def add_arguments(self, parser: CommandParser) -> None:
        window = parser.add_mutually_exclusive_group()
        window.add_argument("--since", metavar="ISO", help="window start (ISO-8601)")
        window.add_argument(
            "--days",
            type=int,
            metavar="N",
            help=f"window of the last N days (default {DEFAULT_DAYS})",
        )
        parser.add_argument(
            "--source",
            action="append",
            metavar="SITE_KEY",
            help="limit to this source site key (repeatable)",
        )
        parser.add_argument("--json", action="store_true", help="print the JSON schema form")

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        since = _window_start(now, options.get("since"), options.get("days"))
        sources: list[str] | None = options.get("source")
        report = build_report(now=now, since=since, sources=sources)
        if options["json"]:
            self.stdout.write(json.dumps(report.to_json(), indent=2, sort_keys=False))
        else:
            self.stdout.write(render_text(report), ending="")


def _window_start(now: datetime, since: str | None, days: int | None) -> datetime:
    if since is not None:
        try:
            parsed = datetime.fromisoformat(since)
        except ValueError as exc:
            raise CommandError(f"--since is not ISO-8601: {since!r}") from exc
        # A naive timestamp is read as UTC, the project's storage zone, rather
        # than rejected: operators paste dates, and UTC is the only sane default.
        if timezone.is_naive(parsed):
            parsed = parsed.replace(tzinfo=UTC)
        if parsed > now:
            raise CommandError("--since is in the future")
        return parsed
    if days is not None and days < 1:
        raise CommandError("--days must be at least 1")
    return now - timedelta(days=DEFAULT_DAYS if days is None else days)
