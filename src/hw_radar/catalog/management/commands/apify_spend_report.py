"""Print the Apify spend attribution report (plan E6, AC-7; MS2-D-17, -48).

`apify_spend_report [--cycles N]` prints, per recorded billing cycle (plus the
current cycle derived from the configured anchor, labelled when admission has
not yet recorded it), the budget figures, the configured account state
(anchor, limit, P, base price, retention, verification date), the declared
external liability and the external-liability headroom `P - HR_cycle - E`,
the ledger authority, and operator, per-source, and per-provider attribution;
then unsettled rows (including `correction_close_overdue`), overruns and latch
trips, and a trailing-31-day trend labelled secondary. `--cycles N` limits the
per-cycle sections to the N most recent recorded cycles.

The current cycle's header carries a non-blocking warning when
HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON predates the cycle's start (E9.6). Other
workloads' spend on the shared account is not observed by the runtime
(MS2-D-48): the report says so rather than estimating it.

Read-only: it writes no row, creates no cycle, and makes no Apify call. The
output holds no credential or account identifier (see
`acquisition.apify.report`).
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
