"""Persist stage: listing upsert + observation append + raw evidence (DR-005).

Re-running acquisition APPENDS offer_snapshot rows and never duplicates
listing rows — the MS-1 acceptance invariant lives here, keyed on the
(source_site, source_listing_key) unique constraint from MS-0.

Two write paths coexist. upsert_listing is the unguarded MS-1 upsert, kept for
its existing callers. observe_listing is the ordering-guarded path the pipeline
and the Apify importer use (MS2-D-30, -35, -37): an asynchronous import can
land after a newer local poll or a newer delist, so current state is written
only by a *current-eligible* observation, while history is still appended with
the observation's own retention deadline. observe_listing never takes locks
itself; its caller (acquisition.stages.persist_observations) holds the scope
and listing row locks in the MS2-D-35 order for the whole transaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR, NormalizedListing, RawItem
from hw_radar.catalog.models import (
    BOUNDED_RETENTION_CLASSES,
    DelistReason,
    Listing,
    OfferSnapshot,
    RawPayload,
    RetentionClass,
    SourceSite,
    StockStatus,
)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def store_raw(
    item: RawItem,
    *,
    fetched_at: datetime,
    retention_class: RetentionClass,
    expires_at: datetime | None = None,
) -> RawPayload:
    """expires_at is REQUIRED (non-None) for bounded retention classes — the DR-001
    check constraints reject the row otherwise. Bounded-class TTL policy arrives
    with the eBay connector (MS-1d); merchant_fact callers pass None."""
    body = item.payload_text or ""
    return RawPayload.objects.create(
        provider="acquisition",
        endpoint=item.url,
        fetched_at=fetched_at,
        response_json=item.payload_json,
        response_text=item.payload_text,
        content_hash=hashlib.sha256((body or str(item.payload_json)).encode()).hexdigest(),
        http_status=item.http_status,
        retention_class=retention_class,
        expires_at=expires_at,
    )


def upsert_listing(
    site: SourceSite,
    normalized: NormalizedListing,
    retention_class: RetentionClass,
    *,
    expires_at: datetime | None = None,
) -> tuple[Listing, bool]:
    defaults: dict[str, object] = {
        "canonical_url": normalized.url,
        "url_hash": url_hash(normalized.url),
        "title_raw": normalized.title,
        "condition_label_raw": normalized.condition_label,
        "is_international": normalized.is_international,
        "retention_class": retention_class,
        "expires_at": expires_at,
    }
    # MS2-D-12: a scope is written only when the observation asserts one, so a
    # scope-less local adapter never moves an imported listing back to the
    # legacy NULL scope (where its own scope's complete sweep could no longer
    # see it, and the NULL scope's could delist it).
    if normalized.collection_scope is not None:
        defaults["collection_scope"] = normalized.collection_scope
    return Listing.objects.update_or_create(
        source_site=site, source_listing_key=normalized.source_listing_key, defaults=defaults
    )


@dataclass(frozen=True, slots=True)
class ObservationRetention:
    """The DR-001 retention one observation carries onto its snapshot (MS2-D-37).

    `expires_at` is the source policy's deadline computed from the observation's
    own observed_at (None for indefinite classes), not the listing's current
    deadline, which a newer observation may have moved.
    """

    retention_class: RetentionClass
    expires_at: datetime | None


def append_snapshot(
    listing: Listing,
    normalized: NormalizedListing,
    *,
    observed_at: datetime,
    raw: RawPayload | None = None,
    retention: ObservationRetention | None = None,
) -> OfferSnapshot:
    """Append one OfferSnapshot for `listing` at `observed_at`.

    `retention=None` copies the listing's class and deadline, byte-identical to
    the MS-1 behavior existing callers rely on. The ordering-guarded path always
    passes the observation's own retention (MS2-D-37): otherwise an older
    observation appended behind a newer one inherits the newer deadline and
    outlives its own freshness window.
    """
    stock = (
        normalized.stock_status
        if normalized.stock_status in StockStatus.values
        else StockStatus.UNKNOWN
    )
    attrs: dict[str, object] = dict(normalized.attrs)
    # Written only when set, so every hint-less row stays byte-identical to MS-1
    # output. Read back by the resolver's category dispatch (MS2-D-03).
    if normalized.category_hint is not None:
        attrs[CATEGORY_HINT_ATTR] = normalized.category_hint
    return OfferSnapshot.objects.create(
        listing=listing,
        observed_at=observed_at,
        currency=normalized.currency,
        item_price=normalized.price,
        shipping_price=normalized.shipping_price,
        stock_status=stock,
        quantity_available=normalized.quantity_available,
        fx_rate=normalized.fx_rate,
        fx_pair=normalized.fx_pair,
        fx_rate_date=normalized.fx_rate_date,
        fx_source=normalized.fx_source,
        attrs_json=attrs,
        raw_payload=raw,
        retention_class=listing.retention_class if retention is None else retention.retention_class,
        expires_at=listing.expires_at if retention is None else retention.expires_at,
    )


def effective_absence(listing: Listing) -> datetime | None:
    """Return greatest(last_absence_at, delisted_at), ignoring NULLs (MS2-D-35).

    delisted_at covers rows delisted before migration 0021 added
    last_absence_at; last_absence_at covers rows a relist has since cleared
    delisted_at on, which is why the guard cannot read delisted_at alone.
    """
    stamps = [s for s in (listing.last_absence_at, listing.delisted_at) if s is not None]
    return max(stamps) if stamps else None


def mark_absent(listing: Listing, reason: DelistReason, *, when: datetime) -> bool:
    """Delist `listing` and raise its absence watermark to `when`; False if already delisted.

    The watermark is raised in the same transaction as the mark (the caller's),
    never lowered. It is written here rather than inside Listing.mark_delisted
    because the ordering-guarded paths (the delist stage and the delisted
    creation anchor) are its only readers today; a delist through any other
    path is still covered by delisted_at in effective_absence.
    """
    # Rejected: moving this into Listing.mark_delisted. That is the MS2-D-35
    # target shape, but D10's scope does not own the catalog model methods;
    # until it moves, every guarded delist must come through this function.
    if not listing.mark_delisted(reason, when=when):
        return False
    if listing.last_absence_at is None or listing.last_absence_at < when:
        listing.last_absence_at = when
        listing.save(update_fields=["last_absence_at"])
    return True


@dataclass(frozen=True, slots=True)
class Observation:
    """What observe_listing did with one record.

    current_eligible — the record wrote current state (content, relist,
        last_observed_at, last_seen) or created an active row.
    snapshot — the appended snapshot, or None when one already existed at
        (listing, observed_at) or newer absence had already retired it.
    """

    listing: Listing
    created: bool
    current_eligible: bool
    snapshot: OfferSnapshot | None


def _at_or_after(when: datetime, bound: datetime | None) -> bool:
    return bound is None or when >= bound


def observe_listing(
    site: SourceSite,
    normalized: NormalizedListing,
    retention: ObservationRetention,
    *,
    observed_at: datetime,
    existing: Listing | None,
    scope_complete_sweep_at: datetime | None,
    raw: RawPayload | None = None,
) -> Observation:
    """Apply one observation under the MS2-D-30/-35 ordering guards.

    `existing` is the row for this key, already locked FOR UPDATE by the
    caller, or None when no row exists. `scope_complete_sweep_at` is the
    complete-sweep watermark of the scope the observation targets, read under
    that scope row's lock. Ties go to presence throughout: a run persists
    before its own delist stage, and fixture adapters reuse one fetched_at
    across runs, so an equal timestamp must still relist.

    An existing row that is not current-eligible is left untouched, last_seen
    included; its snapshot is still appended as history. A missing key older
    than its scope's newest complete sweep is created already delisted as an
    ABSENT_FROM_SWEEP history anchor, never active, so newer complete evidence
    of absence is not undone by a late import.
    """
    now = timezone.now()
    absence_after: datetime | None = None
    if existing is None:
        eligible = _at_or_after(observed_at, scope_complete_sweep_at)
        listing = Listing.objects.create(
            source_site=site,
            source_listing_key=normalized.source_listing_key,
            last_observed_at=observed_at,
            **_content(normalized, retention),
        )
        created = True
        if not eligible:
            assert scope_complete_sweep_at is not None  # implied by not eligible
            mark_absent(listing, DelistReason.ABSENT_FROM_SWEEP, when=scope_complete_sweep_at)
            absence_after = scope_complete_sweep_at
    else:
        listing = existing
        created = False
        absence = effective_absence(listing)
        eligible = (
            _at_or_after(observed_at, listing.last_observed_at)
            and _at_or_after(observed_at, absence)
            and _at_or_after(observed_at, scope_complete_sweep_at)
        )
        if eligible:
            for name, value in _content(normalized, retention).items():
                setattr(listing, name, value)
            listing.last_observed_at = observed_at
            listing.delisted_at = None
            listing.delist_reason = ""
            # last_seen must be in update_fields: it is auto_now, and today's
            # update_or_create bumps it on every upsert. Omitting it would stop
            # the bump, and a listing seen by run N could then stale-delist in
            # a truncated run N+1 inside its grace (MS2-D-30 ED-02, R23).
            listing.save(
                update_fields=[
                    *_content(normalized, retention),
                    "last_observed_at",
                    "delisted_at",
                    "delist_reason",
                    "last_seen",
                ]
            )
        elif absence is not None and absence > observed_at:
            absence_after = absence
    snapshot = _append_history(
        listing,
        normalized,
        retention,
        observed_at=observed_at,
        raw=raw,
        absence_after=absence_after,
        now=now,
    )
    return Observation(
        listing=listing, created=created, current_eligible=eligible, snapshot=snapshot
    )


def _content(normalized: NormalizedListing, retention: ObservationRetention) -> dict[str, object]:
    fields: dict[str, object] = {
        "canonical_url": normalized.url,
        "url_hash": url_hash(normalized.url),
        "title_raw": normalized.title,
        "condition_label_raw": normalized.condition_label,
        "is_international": normalized.is_international,
        "retention_class": retention.retention_class,
        "expires_at": retention.expires_at,
    }
    # Same MS2-D-12 rule as upsert_listing: a scope-less observation never
    # moves a scoped listing back to the NULL scope.
    if normalized.collection_scope is not None:
        fields["collection_scope"] = normalized.collection_scope
    return fields


def _append_history(
    listing: Listing,
    normalized: NormalizedListing,
    retention: ObservationRetention,
    *,
    observed_at: datetime,
    raw: RawPayload | None,
    absence_after: datetime | None,
    now: datetime,
) -> OfferSnapshot | None:
    # Insert-if-absent on the (listing_id, observed_at) key: a replayed dataset
    # read or a duplicate key inside one batch reaches here again, and the
    # listing row lock the caller holds makes the existence check race-free.
    if OfferSnapshot.objects.filter(listing=listing, observed_at=observed_at).exists():
        return None
    expires_at = retention.expires_at
    bounded = retention.retention_class in {c.value for c in BOUNDED_RETENTION_CLASSES}
    if bounded and absence_after is not None and expires_at is not None:
        # MS2-D-37: newer absence retires bounded content, as mark_delisted's
        # pull-forward does for existing snapshots. A deadline already passed
        # means the snapshot would be born expired, so it is not written.
        expires_at = min(expires_at, absence_after)
        if expires_at <= now:
            return None
    return append_snapshot(
        listing,
        normalized,
        observed_at=observed_at,
        raw=raw,
        retention=ObservationRetention(retention.retention_class, expires_at),
    )
