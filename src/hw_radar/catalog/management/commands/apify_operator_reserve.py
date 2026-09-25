"""Reserve an operator Apify operation before performing it (MS2-D-46).

`apify_operator_reserve --kind build|inspect|probe --reason TEXT` creates an
`operator` reservation under the budget lock at the kind's conservative bound
(the build bound plus its call bound, or the inspection / capability-probe
envelope) and passes both account checks. On refusal it exits non-zero, and
the operation must NOT be performed: the Console, CLI, and MCP cannot be
intercepted, so this stop is procedural (R36). The reason is printed and
logged; the schema has no column for it.

SCOPE: reservation only. `--settle` (binding a build id, settling an
inspection or probe envelope) is plan E4.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.budget import OperatorKind
from hw_radar.acquisition.apify.ledger import reserve_operator

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reserve an operator Apify operation (build, inspection, or capability probe)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kind", required=True, choices=[k.value for k in OperatorKind])
        parser.add_argument("--reason", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        reason = str(options["reason"]).strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        kind = OperatorKind(options["kind"])
        outcome = reserve_operator(kind)
        if not outcome.admitted:
            raise CommandError(
                f"refused ({outcome.reason}): {outcome.detail}; do not perform the operation"
            )
        estimate = outcome.estimate
        bound = estimate.admission_usd if estimate is not None else "?"
        logger.info("apify operator %s reserved (%s): %s", kind, outcome.reservation_id, reason)
        self.stdout.write(f"reserved {kind} as reservation {outcome.reservation_id} at ${bound}")
