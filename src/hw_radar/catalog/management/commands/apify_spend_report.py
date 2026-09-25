"""Print the Apify spend attribution report (plan E6, AC-7; MS2-D-17).

`apify_spend_report [--cycles N]` prints, per observed billing cycle, the
budget figures, the stored account snapshot, the ledger authority, and
operator, per-source, and per-provider attribution; then unsettled rows
(including `correction_close_overdue`), overruns and latch trips, and a
trailing-31-day trend labelled secondary. `--cycles N` limits the per-cycle
sections to the N most recent cycles.

Read-only: it writes no row and makes no Apify call, so it reports the stored
snapshot as last refreshed (a stale one is flagged, not refreshed). The output
holds no credential or account identifier (see `acquisition.apify.report`).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from hw_radar.acquisition.apify.report import build_report, render_report


class Command(BaseCommand):
    help = "Print the Apify spend attribution report per billing cycle (read-only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cycles", type=int, metavar="N", help="report only the N most recent cycles"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        cycles = options.get("cycles")
        if cycles is not None and cycles < 1:
            raise CommandError("--cycles must be at least 1")
        report = build_report(now=timezone.now(), cycles=cycles)
        self.stdout.write(render_report(report), ending="")
