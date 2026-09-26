"""Manual entry point for ADR-0018 reference ingest. Default: import the seed
documents only. --refresh runs the full monthly loop (import + backfill-queue
reconsider + discovery scan) — the same code path as the poller job. Conflicts
exit non-zero with the full descriptor list (fail into review, D4).

--category <slug> (repeatable) narrows the plain import to seed documents of
those categories, so one category can be seeded in production without also
seeding the others. Valid slugs are the categories the loaded seed documents
declare; a slug with no document is rejected rather than importing nothing.

SCOPE of a category-scoped import: conflict detection covers the selected
documents against each other and against the database, not against seed
documents left out of the selection. A collision with an unselected category
surfaces (and aborts, writing nothing) when that category is imported later."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import ImportConflictError, import_documents
from hw_radar.refdata.refresh import run_refresh


class Command(BaseCommand):
    help = "Import ADR-0018 reference seed documents (--refresh: full monthly loop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--refresh", action="store_true", help="run the full refresh loop")
        parser.add_argument("--seed-dir", type=Path, default=None)
        parser.add_argument(
            "--category",
            action="append",
            dest="categories",
            default=None,
            metavar="SLUG",
            help="import only seed documents of this category (repeatable)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        seed_dir: Path | None = options["seed_dir"]
        categories: list[str] | None = options["categories"]
        if options["refresh"]:
            if categories:
                # Rejected, not threaded through: run_refresh stamps
                # RefdataConfig.last_refresh_at/last_report_json as a completed
                # monthly refresh and reconsiders every pending listing, so a
                # category-filtered refresh would record a partial catalog
                # import as the full one it replaces.
                raise CommandError("--category cannot be combined with --refresh")
            try:
                report = run_refresh(seed_dir)
            except FileNotFoundError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(json.dumps(report.as_json(), indent=2, default=str))
            if report.conflicts:
                raise CommandError(f"{len(report.conflicts)} alias conflict(s) — see report")
            return
        try:
            docs = load_seed_documents(seed_dir)
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        if categories:
            available = sorted({doc.category for doc in docs})
            unknown = sorted(set(categories) - set(available))
            if unknown:
                raise CommandError(
                    f"unknown category slug(s): {', '.join(unknown)}; "
                    f"valid slugs: {', '.join(available)}"
                )
            docs = [doc for doc in docs if doc.category in categories]
        try:
            report = import_documents(docs)
        except ImportConflictError as exc:
            raise CommandError("import aborted:\n" + "\n".join(exc.conflicts)) from exc
        output = {**report.as_json(), "by_category": report.by_category_json()}
        self.stdout.write(json.dumps(output, indent=2, default=str))
