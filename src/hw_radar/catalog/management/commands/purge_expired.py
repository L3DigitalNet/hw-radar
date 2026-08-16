# pyright: reportPrivateUsage=false
# Django's public model-introspection surface is spelled with leading underscores
# (`Model._meta`, `Model._default_manager`); a generic sweeper over model classes
# cannot avoid them, so the rule is disabled for this file only.
"""DR-001 bounded-retention enforcement: physically delete rows past `expires_at`.

`expires_at` is stamped at write time on every bounded-class row, but a stamp is
not enforcement — without this sweeper the data simply accumulates. That makes
this command a go-live gate for any bounded source, eBay above all: DR-008 caps
eBay observation rows at six hours, and only a deletion pass keeps that promise.
TimescaleDB's chunk-retention policy on `availability_heartbeat_observation`
covers exactly one table at chunk granularity and nothing else.

The swept tables are derived from Django's app registry rather than listed here
(see `retention_governed_models`), so a new RetentionGoverned model is swept the
day it is added; `tests/unit/test_purge_registry.py` pins that property.

The sweep deletes only rows whose `retention_class` is in
BOUNDED_RETENTION_CLASSES *and* whose `expires_at` is in the past. Indefinite
classes carry NULL `expires_at` (enforced by the `*_retention_ttl_coherent`
CHECK) and can never match; see `_expired`.

Entity vs observation: retention-governed models split into OBSERVATION tables,
whose expired rows this command physically deletes, and audit ANCHORS, whose rows
it must never delete (see `is_retention_anchor` and the retention-role paragraph
on `catalog.Listing`). Deleting an anchor would cascade into `listing_resolution`
and destroy the DR-010 audit trail, or trip its `superseded_by` PROTECT and abort
the whole pass. DR-008's physical-deletion obligation is still met, because every
piece of evidence hanging off an anchor is an observation row with its own
`expires_at` and its own pass here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.db.models import Model, QuerySet
from django.utils import timezone

from hw_radar.catalog.models.base import BOUNDED_RETENTION_CLASSES, RetentionGoverned

if TYPE_CHECKING:
    from collections.abc import Iterator

# Rows per DELETE round trip. Large enough that a backlog of millions of eBay
# observations drains in a bounded number of statements, small enough that the
# collector's in-memory pk list and the row locks each statement holds stay
# modest against a live poller writing to the same tables.
DEFAULT_BATCH_SIZE = 5_000

_BOUNDED_VALUES: tuple[str, ...] = tuple(c.value for c in BOUNDED_RETENTION_CLASSES)


@dataclass
class SweepReport:
    """Rows removed (or, in a dry run, matched) keyed by model label.

    Counts are keyed by `app_label.ModelName` and include cascades, which are
    reported under the cascaded model rather than the model whose delete
    triggered them. Anchor models never appear at all: their rows are not
    deletable, so reporting a matched count for them would promise a deletion
    that never happens.
    """

    dry_run: bool
    counts: Counter[str] = field(default_factory=Counter[str])

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def retention_governed_models() -> list[type[Model]]:
    """Return every installed concrete model that inherits RetentionGoverned.

    Derived from the app registry on purpose: a hand-maintained table list is the
    one failure mode that matters here, because a model missing from it retains
    expired rows forever while the sweep reports success.
    """
    return sorted(
        (m for m in apps.get_models() if issubclass(m, RetentionGoverned)),
        key=lambda m: m._meta.label,
    )


def is_retention_anchor(model: type[Model]) -> bool:
    """Whether this model's rows are audit anchors that must never be deleted.

    Cross-file contract: the flag is declared on the model itself — `catalog.
    Listing` sets `retention_anchor = True` and records the DR-008/DR-010
    reasoning — and defaults to False here, so a new RetentionGoverned model is
    swept unless its author opts out deliberately. Anchors stay in
    `retention_governed_models()`: the registry means "every retention-governed
    table", and narrowing it would hide a real coverage gap behind an opt-out.
    """
    return bool(getattr(model, "retention_anchor", False))


def _expired(model: type[Model], now: datetime) -> QuerySet[Model]:
    # `expires_at__lt` is NULL-rejecting in SQL, so indefinite-class rows (whose
    # expires_at is NULL by CHECK constraint) cannot match even if a future bug
    # let an indefinite class into BOUNDED_RETENTION_CLASSES. The class filter
    # and the timestamp filter are therefore two independent guards, not one.
    return model._default_manager.filter(retention_class__in=_BOUNDED_VALUES, expires_at__lt=now)


def _batches(queryset: QuerySet[Model], batch_size: int) -> Iterator[list[Any]]:
    """Yield successive primary-key batches of `queryset`, re-querying each time.

    Re-querying is what makes the loop terminate: the previous batch's rows are
    gone, so the same LIMIT window returns the next set. Yields tuples for models
    with a CompositePrimaryKey (OfferSnapshot, AvailabilityHeartbeatObservation).
    """
    while True:
        pks = list(queryset.values_list("pk", flat=True)[:batch_size])
        if not pks:
            return
        yield pks


def sweep_expired(
    *, now: datetime | None = None, dry_run: bool = False, batch_size: int = DEFAULT_BATCH_SIZE
) -> SweepReport:
    """Delete every expired bounded-retention row, in batches, and report counts.

    Anchor models are skipped entirely (`is_retention_anchor`), in dry runs too.

    With `dry_run=True` nothing is deleted and the counts are the matched-row
    counts per table — cascade rows are NOT included, because they are only
    discoverable by running the collector.
    """
    now = now or timezone.now()
    report = SweepReport(dry_run=dry_run)
    for model in retention_governed_models():
        if is_retention_anchor(model):
            continue
        label = model._meta.label
        expired = _expired(model, now)
        if dry_run:
            matched = expired.count()
            if matched:
                report.counts[label] += matched
            continue
        for pks in _batches(expired, batch_size):
            with transaction.atomic():
                # Re-apply the whole expiry predicate in the DELETE instead of
                # trusting the pk list: the poller re-observing a listing between
                # this batch's SELECT and its DELETE pushes expires_at into the
                # future, and a pk-only delete would destroy the refreshed row.
                # A cascade spans several statements, so the batch is atomic —
                # a crash mid-collector must not leave orphaned children behind.
                _total, per_model = _expired(model, now).filter(pk__in=pks).delete()
            report.counts.update(per_model)
            if not per_model.get(label) and _expired(model, now).filter(pk__in=pks).exists():
                # Rows still selectable as expired after a delete that removed
                # none of them: the SELECT and the DELETE disagree, so the next
                # iteration would hand back the same pks forever. (Rows that
                # merely stopped being expired are the normal race above — they
                # drop out of the re-query, so the loop still makes progress.)
                raise CommandError(
                    f"{label}: {len(pks)} expired rows selected but none deleted; "
                    "aborting to avoid an unbounded retry loop"
                )
    return report


class Command(BaseCommand):
    help = "Delete rows whose bounded DR-001 retention window has expired."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would be deleted without deleting anything",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"rows per delete statement (default {DEFAULT_BATCH_SIZE})",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size: int = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be >= 1")
        report = sweep_expired(dry_run=options["dry_run"], batch_size=batch_size)
        prefix = "would delete" if report.dry_run else "deleted"
        for label, count in sorted(report.counts.items()):
            self.stdout.write(f"{prefix} {count} row(s) from {label}")
        self.stdout.write(f"{prefix} {report.total} row(s) total")
