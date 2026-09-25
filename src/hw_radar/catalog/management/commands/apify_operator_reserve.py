"""Reserve an operator Apify operation before performing it, and settle it after (MS2-D-46).

`apify_operator_reserve --kind build|inspect|probe --reason TEXT` creates an
`operator` reservation under the budget lock at the kind's conservative bound
(the build bound plus its call bound, or the inspection / capability-probe
envelope) and passes both account checks. The reason is stored on the row,
admitted or denied. On refusal it exits non-zero, and the operation must NOT
be performed: the Console, CLI, and MCP cannot be intercepted, so this stop is
procedural (R36).

`apify_operator_reserve --settle ID` settles it afterwards:

- a build: `--settle ID --build-id BUILD` only BINDS the provider build id, in
  its own committed transaction, and does not wait for the build. The poller
  then reads the build record, settles the row, and monitors it for
  corrections (selectors 2 and 4). Re-binding the same id is a no-op; a
  different id is refused.
- an inspection envelope: reconciled at once at its full bound.
- a capability probe: as an inspection, but refused until
  `--probe-dataset-id DATASET --deletion-verified` records the throwaway
  dataset and its verified deletion.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from hw_radar.acquisition.apify.budget import OperatorKind
from hw_radar.acquisition.apify.ledger import LedgerRefused, reserve_operator
from hw_radar.acquisition.apify.reconcile import bind_build, settle_operator

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reserve, or settle, an operator Apify operation (build, inspection, or probe)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kind", choices=[k.value for k in OperatorKind])
        parser.add_argument("--reason", help="why the operation is performed (reserve)")
        parser.add_argument("--settle", type=int, metavar="ID", help="settle reservation ID")
        parser.add_argument("--build-id", help="with --settle: bind this provider build id")
        parser.add_argument("--probe-dataset-id", help="with --settle: the probe's dataset id")
        parser.add_argument(
            "--deletion-verified",
            action="store_true",
            help="with --settle: the probe's dataset was verified deleted",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["settle"] is not None:
            self._settle(options)
            return
        reason = str(options["reason"] or "").strip()
        if not reason:
            raise CommandError("--reason must not be empty")
        if options["kind"] is None:
            raise CommandError("--kind is required to reserve")
        kind = OperatorKind(options["kind"])
        outcome = reserve_operator(kind, reason=reason)
        if not outcome.admitted:
            raise CommandError(
                f"refused ({outcome.reason}): {outcome.detail}; do not perform the operation"
            )
        estimate = outcome.estimate
        bound = estimate.admission_usd if estimate is not None else "?"
        logger.info("apify operator %s reserved (%s): %s", kind, outcome.reservation_id, reason)
        self.stdout.write(f"reserved {kind} as reservation {outcome.reservation_id} at ${bound}")

    def _settle(self, options: dict[str, Any]) -> None:
        reservation_id = int(options["settle"])
        now = timezone.now()
        try:
            if options["build_id"]:
                bound = bind_build(reservation_id, str(options["build_id"]), now=now)
                verb = "bound" if bound else "already bound"
                self.stdout.write(
                    f"reservation {reservation_id} {verb} to build {options['build_id']}; "
                    "the poller settles it"
                )
                return
            settle_operator(
                reservation_id,
                now=now,
                probe_dataset_id=options["probe_dataset_id"],
                probe_deletion_verified=bool(options["deletion_verified"]),
            )
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused
        self.stdout.write(f"reservation {reservation_id} settled at its envelope bound")
