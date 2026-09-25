"""Owner resets of the Apify budget: the overrun latch, or cycle discovery.

`apify_budget_reset --reason TEXT` clears the overrun latch (MS2-D-26): every
open trip gets `cleared_at` and the owner's reason, and paid admission may
resume (subject to every other check). It is refused when the latch is not
tripped. The owner resets only after resolving what tripped it (a
delete_failed or orphaned run is cleaned up by hand, R21); an estimator
correction clears the latch on its own through an
HW_RADAR_APIFY_ESTIMATOR_VERSION bump, not through this command.

`apify_budget_reset --discovery --reason TEXT` closes the exhausted
`ApifyCycleDiscovery` row (close_reason `owner_reset`, the reason stored on
the row) and opens a fresh one, so discovery can read the account again. It is
refused unless the open row is exhausted: each row's full read allowance is
debited in every cycle its interval touches, so a reset is a spend decision,
not housekeeping.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.ledger import LedgerRefused, clear_latch, reset_discovery

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Owner reset of the Apify overrun latch, or of the exhausted cycle-discovery allowance."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--discovery", action="store_true", help="reset cycle discovery, not the latch"
        )
        parser.add_argument("--reason", required=True, help="why the owner resets it")

    def handle(self, *args: Any, **options: Any) -> None:
        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        try:
            if options["discovery"]:
                new_id = reset_discovery(reason=reason)
                logger.warning("apify cycle discovery reset by owner: %s", reason)
                self.stdout.write(f"discovery reset; new discovery row {new_id} (reason: {reason})")
                return
            cleared = clear_latch(reason)
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused
        logger.warning("apify overrun latch cleared by owner (%s trips): %s", cleared, reason)
        self.stdout.write(f"overrun latch cleared ({cleared} trips; reason: {reason})")
