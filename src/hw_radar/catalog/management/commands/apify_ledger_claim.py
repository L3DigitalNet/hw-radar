"""Owner claim of the current cycle's Apify ledger authority (MS2-D-45 `origin`).

`apify_ledger_claim --origin --reason TEXT` attests that no other environment
admitted paid Hardware Radar work in the current billing cycle and records
this environment (HW_RADAR_APIFY_LEDGER_ID) as the cycle's authority. It is the
one authority whose truth rests on the owner's word rather than a check (R35),
so the attestation text is stored in `attested_by`. The claim itself
materializes the billing cycle derived from HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR
(MS2-D-48), so no earlier start is needed. It is refused `cycle_unknown` when
the anchor is unset, invalid, in the future, or conflicts with a recorded
cycle, and refused when the cycle already has an authority row here.
"""

from __future__ import annotations

import getpass
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.ledger import LedgerRefused, claim_origin


class Command(BaseCommand):
    help = "Claim the current billing cycle's Apify ledger authority (owner attestation)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--origin", action="store_true", required=True)
        parser.add_argument("--reason", required=True, help="the owner's attestation")

    def handle(self, *args: Any, **options: Any) -> None:
        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        try:
            row = claim_origin(f"{getpass.getuser()}: {reason}")
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused
        self.stdout.write(f"claimed origin authority for cycle {row.cycle_start.isoformat()}")
