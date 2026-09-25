"""Owner reset of the Apify cycle-discovery allowance (MS2-D-32 *Cycle discovery*).

`apify_budget_reset --discovery --reason TEXT` closes the exhausted
`ApifyCycleDiscovery` row (close_reason `owner_reset`) and opens a fresh one,
so discovery can read the account again. It is refused unless the open row is
exhausted: each row's full read allowance is debited in every cycle its
interval touches, so a reset is a spend decision, not housekeeping. The reason
is printed and logged; the schema has no column for it.

SCOPE: only `--discovery` exists in E3. Clearing the overrun latch
(`apify_budget_reset --reason`) is plan E4, and until it lands this command
refuses any invocation without `--discovery` rather than guessing.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.ledger import LedgerRefused, reset_discovery

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Owner reset of the exhausted Apify cycle-discovery allowance."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--discovery", action="store_true", help="reset cycle discovery")
        parser.add_argument("--reason", required=True, help="why the owner resets it")

    def handle(self, *args: Any, **options: Any) -> None:
        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        if not options["discovery"]:
            raise CommandError("only --discovery is available (the latch reset is plan E4)")
        try:
            new_id = reset_discovery()
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused
        logger.warning("apify cycle discovery reset by owner: %s", reason)
        self.stdout.write(f"discovery reset; new discovery row {new_id} (reason: {reason})")
