# pyright: reportPrivateUsage=false
# Django's public model-introspection surface is spelled with leading underscores
# (`Model._meta`, `Model._default_manager`); a generic sweeper over model classes
# cannot avoid them, so the rule is disabled for this file only.
"""DR-001 bounded-retention enforcement: retire rows past `expires_at` — deleting
observation rows outright and redacting the anchor rows that must survive.

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

Delete or redact, decided PER ROW, never per table. Most expired rows are simply
deleted. A row is kept only if its model claims it through `deletion_exempt_q`
(see `_deletion_exempt_q`), and a kept row is then stripped of merchant content
through `redact_expired` (see `_redact_expired`) — so every expired bounded row
either leaves the database or leaves its content behind. `catalog.Listing` is the
only model that claims any today, and it claims rows, not the table: an eBay row
under the delete-on-delist contract, or any row whose deletion would cascade
`listing_resolution` away and destroy the DR-010 audit trail.

DR-008's physical-deletion obligation is met on both fronts: every piece of
evidence hanging off a kept listing is an observation row with its own
`expires_at` and its own pass here, and the kept row itself holds only identity
and audit metadata — never the merchant's content.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.db.models import Model, Q, QuerySet
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
    """Rows removed and rows redacted (or, in a dry run, matched), by model label.

    `counts` is deletions only, keyed by `app_label.ModelName`, and includes
    cascades, which are reported under the cascaded model rather than the model
    whose delete triggered them.

    `redactions` is the kept half of the same pass — rows retained but stripped of
    merchant content. The two are separate counters, and `total` stays deletions
    only, because a caller reporting "N rows deleted" must not silently include
    rows that are still there. One model can appear in both: exemption is per row,
    so a sweep may delete some of a table's expired rows and redact the rest.
    """

    dry_run: bool
    counts: Counter[str] = field(default_factory=Counter[str])
    redactions: Counter[str] = field(default_factory=Counter[str])

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def total_redacted(self) -> int:
        return sum(self.redactions.values())


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


def _deletion_exempt_q(model: type[Model]) -> Q:
    """Return the model's own predicate for expired rows that must NOT be deleted.

    Cross-file contract: the policy lives on the model — `catalog.Listing.
    deletion_exempt_q` decides row by row, and records why each clause earns the
    exemption. The default is an empty Q, so a model that says nothing has every
    expired row deleted; a model cannot become exempt by accident, only by
    declaring the method. Exemption is per ROW on purpose: a model-wide flag once
    granted every Listing class the audit exemption that only the delete-on-delist
    contract earns, which would have retained amazon_ephemeral and
    transient_discovery listings forever, against DR-001.

    Exempt models stay in `retention_governed_models()`: the registry means "every
    retention-governed table", and narrowing it would hide a real coverage gap
    behind an opt-out.
    """
    predicate = getattr(model, "deletion_exempt_q", None)
    return Q() if predicate is None else predicate()


def _redact_expired(model: type[Model], now: datetime, *, dry_run: bool) -> int:
    """Run a model's own redaction pass over its kept rows; return rows changed.

    Cross-file contract with the model, paired with `_deletion_exempt_q`: a model
    that keeps expired rows exposes `redact_expired(now, dry_run=...)` and decides
    for itself which columns are merchant content (see `catalog.Listing.
    REDACTED_CONTENT_FIELDS`). The sweeper deliberately holds no field list — a
    column added to Listing must not need an edit here to be covered.

    A model with an exemption but no redaction hook keeps its rows whole. That is
    a retention gap by construction, so any new exemption must arrive with this
    method; nothing here can detect the omission.
    """
    redactor = getattr(model, "redact_expired", None)
    if redactor is None:
        return 0
    return int(redactor(now, dry_run=dry_run))


def _expired(model: type[Model], now: datetime) -> QuerySet[Model]:
    # `expires_at__lt` is NULL-rejecting in SQL, so indefinite-class rows (whose
    # expires_at is NULL by CHECK constraint) cannot match even if a future bug
    # let an indefinite class into BOUNDED_RETENTION_CLASSES. The class filter
    # and the timestamp filter are therefore two independent guards, not one.
    return model._default_manager.filter(retention_class__in=_BOUNDED_VALUES, expires_at__lt=now)


def _deletable(model: type[Model], now: datetime) -> QuerySet[Model]:
    """Expired rows this sweep may physically delete.

    Every SELECT, DELETE and stall check in the loop below must use THIS
    predicate, not `_expired`: the exemption is re-evaluated at delete time, so a
    row that gained resolution history between selection and deletion drops out
    and survives instead of cascading its brand-new audit edges away.
    """
    exempt = _deletion_exempt_q(model)
    expired = _expired(model, now)
    return expired.exclude(exempt) if exempt else expired


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
    """Retire every expired bounded-retention row, in batches, and report counts.

    Each expired row is either deleted or redacted, decided per row by the model's
    `deletion_exempt_q` (see `_deletion_exempt_q`) — never left whole.

    With `dry_run=True` nothing is deleted or redacted and the counts are the
    matched-row counts per table — cascade rows are NOT included, because they
    are only discoverable by running the collector.
    """
    now = now or timezone.now()
    report = SweepReport(dry_run=dry_run)
    for model in retention_governed_models():
        label = model._meta.label
        # Redaction runs BEFORE the delete loop: the two row sets are disjoint by
        # construction, so the order cannot change the outcome, but a CommandError
        # from the delete loop must not leave kept rows still holding content.
        redacted = _redact_expired(model, now, dry_run=dry_run)
        if redacted:
            report.redactions[label] += redacted
        deletable = _deletable(model, now)
        if dry_run:
            matched = deletable.count()
            if matched:
                report.counts[label] += matched
            continue
        for pks in _batches(deletable, batch_size):
            with transaction.atomic():
                # Re-apply the whole predicate in the DELETE instead of trusting
                # the pk list: the poller re-observing a listing between this
                # batch's SELECT and its DELETE pushes expires_at into the future,
                # and a pk-only delete would destroy the refreshed row.
                # A cascade spans several statements, so the batch is atomic —
                # a crash mid-collector must not leave orphaned children behind.
                _total, per_model = _deletable(model, now).filter(pk__in=pks).delete()
            report.counts.update(per_model)
            if not per_model.get(label) and _deletable(model, now).filter(pk__in=pks).exists():
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
    help = "Retire rows whose bounded DR-001 retention window has expired."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would be deleted and redacted, changing nothing",
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
        # Redactions are reported on their own lines: an operator reading this
        # output must be able to tell rows that are gone from rows that are still
        # there minus their merchant content.
        redact_prefix = "would redact" if report.dry_run else "redacted"
        for label, count in sorted(report.redactions.items()):
            self.stdout.write(f"{redact_prefix} {count} row(s) in {label}")
