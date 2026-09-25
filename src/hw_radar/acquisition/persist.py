"""Persist stage: listing upsert + observation append + raw evidence (DR-005).

Re-running acquisition APPENDS offer_snapshot rows and never duplicates
listing rows — the MS-1 acceptance invariant lives here, keyed on the
(source_site, source_listing_key) unique constraint from MS-0.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR, NormalizedListing, RawItem
from hw_radar.catalog.models import (
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


def append_snapshot(
    listing: Listing,
    normalized: NormalizedListing,
    *,
    observed_at: datetime,
    raw: RawPayload | None = None,
) -> OfferSnapshot:
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
        retention_class=listing.retention_class,
        expires_at=listing.expires_at,
    )
