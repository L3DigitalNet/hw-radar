"""Owner reset of the Apify overrun latch.

`apify_budget_reset --reason TEXT` clears the overrun latch (MS2-D-26): every
open trip gets `cleared_at` and the owner's reason, and paid admission may
resume (subject to every other check). It is refused when the latch is not
tripped. The owner resets only after resolving what tripped it (a
delete_failed or orphaned run is cleaned up by hand, R21; an
`account_limit_refused` trip after the configured account state is
re-verified, MS2-D-48); an estimator correction clears the latch on its own
through an HW_RADAR_APIFY_ESTIMATOR_VERSION bump, not through this command.

The former `--discovery` reset is retired with runtime cycle discovery
(MS2-D-48): the billing cycle now comes from the configured anchor.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.ledger import LedgerRefused, clear_latch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Owner reset of the Apify overrun latch."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--reason", required=True, help="why the owner resets it")

    def handle(self, *args: Any, **options: Any) -> None:
        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        try:
            cleared = clear_latch(reason)
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused
        logger.warning("apify overrun latch cleared by owner (%s trips): %s", cleared, reason)
        self.stdout.write(f"overrun latch cleared ({cleared} trips; reason: {reason})")
