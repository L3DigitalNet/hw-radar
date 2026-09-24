"""Re-evaluate watch eligibility outside the ingestion pipeline (MS2-D-09, MS2-D-20).

Default: evaluate every live listing against the enabled watches of its
category — run it after creating or editing a watch, since a new watch has no
evaluation rows until its listings are next observed. `--pending`: re-evaluate
only listings holding a non-current row (the MS2-D-20 repair path), decided by
the same predicate the shortlist uses; `--watch` narrows that to given watches.

A listing whose evaluation raises is reported and the rest still run; the
command then exits non-zero. Its rows stay `pending` (fail closed).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.eligibility.service import evaluate_all, evaluate_pending


class Command(BaseCommand):
    help = "Evaluate watches against live listings (--pending: only non-current rows)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--pending",
            action="store_true",
            help="re-evaluate only listings with a non-current (pending) evaluation",
        )
        parser.add_argument(
            "--watch",
            type=int,
            action="append",
            dest="watch_ids",
            metavar="WATCH_ID",
            help="with --pending, restrict to this watch (repeatable)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        watch_ids: list[int] | None = options["watch_ids"]
        if watch_ids and not options["pending"]:
            # The default mode evaluates whole listings, and evaluate_listing
            # always covers every enabled watch of the listing's category, so a
            # watch filter there would be a promise the command cannot keep.
            raise CommandError("--watch requires --pending")
        report = evaluate_pending(watch_ids) if options["pending"] else evaluate_all()
        self.stdout.write(
            f"listings: {report.listings}; evaluated: {report.evaluated}; "
            f"skipped (delisted/expired): {report.skipped}; errors: {len(report.errors)}"
        )
        if report.errors:
            raise CommandError(
                "evaluation failed for listing(s) "
                + ", ".join(str(i) for i in report.errors)
                + "; their rows stay pending"
            )
