"""Export or import the Apify ledger authority handoff record (MS2-D-45).

`--export --to LEDGER_ID [--output FILE]` hands this cycle's authority to one
destination ledger, only when the whole ledger is drained and
HW_RADAR_APIFY_ENABLED is false, and writes the record as JSON (stdout by
default). A repeated export to the same destination prints the same record.
`--import FILE` installs a record addressed to this ledger as the cycle's
`handoff` authority; re-importing the same record is a no-op.

The record carries only ledger ids, a cycle start, dollar lines, and closing-
read evidence: no credential or account identifier.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.acquisition.apify.ledger import LedgerRefused, export_handoff, import_handoff


class Command(BaseCommand):
    help = "Export or import the Apify ledger authority handoff record."

    def add_arguments(self, parser: CommandParser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--export", action="store_true")
        mode.add_argument("--import", dest="import_path", metavar="FILE")
        parser.add_argument("--to", help="destination ledger id (with --export)")
        parser.add_argument("--output", metavar="FILE", help="write the record here")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            if options["export"]:
                self._export(options)
            else:
                self._import(Path(str(options["import_path"])))
        except LedgerRefused as refused:
            raise CommandError(str(refused)) from refused

    def _export(self, options: dict[str, Any]) -> None:
        if not options.get("to"):
            raise CommandError("--export needs --to LEDGER_ID")
        exported = export_handoff(str(options["to"]))
        text = json.dumps(exported.record, sort_keys=True, indent=2) + "\n"
        output = options.get("output")
        if output:
            Path(str(output)).write_text(text, encoding="utf-8")
            self.stdout.write(f"handoff exported (digest {exported.digest}) to {output}")
        else:
            self.stdout.write(text, ending="")

    def _import(self, path: Path) -> None:
        try:
            loaded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"cannot read handoff record: {exc}") from exc
        if not isinstance(loaded, dict):
            raise CommandError("handoff record must be a JSON object")
        created = import_handoff(cast(dict[str, object], loaded))
        self.stdout.write("handoff imported" if created else "handoff already imported (no-op)")
