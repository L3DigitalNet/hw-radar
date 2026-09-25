"""Print a watch's shortlist and review-queue summary (MS2-D-01, MS2-D-09).

The MS-2 read-model surface: no UI and no alert. Shows only current `match`
rows on live listings, cheapest landed USD first, with freshness and the soft
target annotation; `--review` also lists the `unknown` / `pending` rows. There
is no score column (ADR 0022).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.catalog.models import Watch
from hw_radar.eligibility.shortlist import review_queue, shortlist


def _flag(value: bool | None) -> str:
    return "?" if value is None else ("yes" if value else "no")


class Command(BaseCommand):
    help = "Show a watch's shortlist (current matches) and review queue."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("watch_id", type=int)
        parser.add_argument(
            "--review", action="store_true", help="also list unknown and pending rows"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        watch_id: int = options["watch_id"]
        watch = Watch.objects.filter(pk=watch_id).first()
        if watch is None:
            raise CommandError(f"no watch with id {watch_id}")
        rows = shortlist(watch_id)
        queue = review_queue(watch_id)
        self.stdout.write(f"watch {watch_id} ({watch.name}): {len(rows)} shortlisted")
        for r in rows:
            landed = "n/a" if r.landed_usd is None else f"${r.landed_usd}"
            shipping = "" if r.shipping_known else " (+shipping?)"
            observed = "never" if r.observed_at is None else r.observed_at.isoformat()
            self.stdout.write(
                f"  {landed}{shipping}  target:{_flag(r.meets_target)}  {r.freshness}  "
                f"observed {observed}  [{r.source}#{r.listing_id}] {r.title}  {r.url}"
            )
        pending = sum(1 for q in queue if q.state == "pending")
        self.stdout.write(
            f"review queue: {len(queue) - pending} unknown, {pending} pending"
            + ("" if pending == 0 else " (run evaluate_watches --pending)")
        )
        if options["review"]:
            for q in queue:
                stale = f" stale:{','.join(q.stale_fields)}" if q.stale_fields else ""
                self.stdout.write(
                    f"  {q.state} (stored {q.verdict}){stale}  "
                    f"[{q.source}#{q.listing_id}] {q.title}"
                )
