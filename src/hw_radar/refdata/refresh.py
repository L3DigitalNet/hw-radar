"""The monthly ADR-0018 refresh: import seeds → reconsider the backfill queue
(rungs 1-2 against the fresh catalog, C.3.4 way #1) → discovery scan (way #2).
Order matters: reconsider drains the queue before discovery counts it.

Enrich-never-gate (ADR-0018 rule 6): a conflicted import rolls back and is
REPORTED, but the reconsider pass and discovery scan still run against the
previously seeded catalog; one bad listing never halts the loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from django.utils import timezone

from hw_radar.catalog.models import Listing, RefdataConfig, ResolutionGrain
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.matching.types import GRAIN_ORDER, Grain
from hw_radar.refdata.discovery import scan_backfill_queue
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import ImportConflictError, ImportReport, import_documents

logger = logging.getLogger(__name__)


@dataclass
class RefreshReport:
    ran: bool
    conflicts: list[str] = field(default_factory=list)
    import_report: ImportReport | None = None
    reconsidered: int = 0
    upgraded: int = 0
    errors: int = 0
    discovery_enqueued: int = 0

    def as_json(self) -> dict[str, object]:
        return {
            "ran": self.ran,
            "conflicts": list(self.conflicts),
            "import": self.import_report.as_json() if self.import_report else None,
            "reconsidered": self.reconsidered,
            "upgraded": self.upgraded,
            "errors": self.errors,
            "discovery_enqueued": self.discovery_enqueued,
        }


def run_refresh(seed_dir: Path | None = None) -> RefreshReport:
    config = RefdataConfig.current()
    if not config.enabled:
        logger.info("refdata refresh disabled (RefdataConfig.enabled=False)")
        return RefreshReport(ran=False)
    report = RefreshReport(ran=True)
    docs = load_seed_documents(seed_dir)
    try:
        report.import_report = import_documents(docs)
    except ImportConflictError as exc:
        report.conflicts = exc.conflicts
        logger.error("refdata import failed into review: %s conflict(s)", len(exc.conflicts))
    resolver = CatalogResolver()
    # Delisted rows are excluded: delete-on-delist redaction (CR-004) empties
    # their title, so reconsidering them can only regress the grain, never
    # improve it.
    pending = list(
        Listing.objects.not_delisted()
        .filter(resolution_grain__in=[ResolutionGrain.NONE, ResolutionGrain.FAMILY])
        .values_list("pk", "resolution_grain")
    )
    for pk, grain_before in pending:
        try:
            resolver.resolve_listing(pk, reconsider=True)
        except Exception:  # double-fallback failure — never halts the loop
            logger.exception("reconsider failed for listing %s", pk)
            report.errors += 1
            continue
        report.reconsidered += 1
        grain_after = Listing.objects.values_list("resolution_grain", flat=True).get(pk=pk)
        if GRAIN_ORDER[Grain(grain_after)] > GRAIN_ORDER[Grain(grain_before)]:
            report.upgraded += 1
    try:
        report.discovery_enqueued = scan_backfill_queue()
    except Exception:  # never lose the reconsider pass's report over a discovery failure
        logger.exception("discovery scan failed")
        report.errors += 1
    config.last_refresh_at = timezone.now()
    config.last_report_json = report.as_json()
    config.save(update_fields=["last_refresh_at", "last_report_json", "updated_at"])
    return report
