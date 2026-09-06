"""CR-004 Listing-grain soft delete: terminal marks, evidence bounding, and the
guarantee that delisting never touches the DR-010 resolution audit trail."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from hw_radar.catalog.management.commands.purge_expired import sweep_expired
from hw_radar.catalog.models import (
    BOUNDED_RETENTION_CLASSES,
    Category,
    DelistReason,
    Listing,
    ListingResolution,
    Manufacturer,
    OfferSnapshot,
    ProductFamily,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    SourceType,
    StockStatus,
)

T0 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)  # the delist instant these tests stamp


def _live_ttl() -> datetime:
    """A DR-008 six-hour window that is still open against the DATABASE clock.

    active() compares expires_at with SQL Now(), so a TTL frozen in this module's
    2026-07-04 test epoch would read as expired and quietly empty every
    live-offer assertion below.
    """
    return timezone.now() + timedelta(hours=6)


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(
        name="eBay (test)",
        normalized_name="ebay-delist-test",
        source_type=SourceType.MARKETPLACE,
    )


def _listing(
    site: SourceSite,
    key: str,
    *,
    retention_class: RetentionClass = RetentionClass.EBAY_LISTING_OBSERVATION,
    expires_at: datetime | None = None,
) -> Listing:
    if expires_at is None and retention_class in BOUNDED_RETENTION_CLASSES:
        expires_at = _live_ttl()
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://www.ebay.com/itm/{key}",
        url_hash=key.ljust(64, "0"),
        title_raw="Seagate Exos X18 18TB Recertified",
        retention_class=retention_class,
        expires_at=expires_at,
    )


def test_mark_delisted_stamps_time_and_reason(site: SourceSite) -> None:
    listing = _listing(site, "ebay-1")
    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0) is True

    listing.refresh_from_db()
    assert listing.delisted_at == T0
    assert listing.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    # Second call is a no-op: the first delist instant is the one DR-008 cares
    # about, so a later sweep must not overwrite it.
    assert listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0 + timedelta(hours=1)) is False
    listing.refresh_from_db()
    assert listing.delisted_at == T0


def test_delist_does_not_reset_last_seen(site: SourceSite) -> None:
    # last_seen is auto_now, so a careless full save() here would silently reset
    # the absence clock the ABSENT_STALE heuristic reads.
    listing = _listing(site, "ebay-lastseen")
    stale = T0 - timedelta(days=2)
    Listing.objects.filter(pk=listing.pk).update(last_seen=stale)
    listing.refresh_from_db()

    listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0)

    listing.refresh_from_db()
    assert listing.last_seen == stale


def test_delist_pulls_bounded_evidence_ttl_forward(site: SourceSite) -> None:
    listing = _listing(site, "ebay-2")
    snapshot = OfferSnapshot.objects.create(
        listing=listing,
        observed_at=T0 - timedelta(hours=1),
        item_price=Decimal("199.99"),
        stock_status=StockStatus.IN_STOCK,
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=_live_ttl(),
    )

    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0)

    listing.refresh_from_db()
    snapshot.refresh_from_db()
    # DR-008: the offer evidence becomes due for the retention sweeper at the
    # delist instant instead of riding out the rest of the freshness window.
    assert listing.expires_at == T0
    assert snapshot.expires_at == T0


def test_delist_leaves_indefinite_retention_untouched(site: SourceSite) -> None:
    # merchant_fact rows must keep expires_at NULL (DR-001 CHECK); delisting an
    # offer is not licence to retire a merchant fact.
    listing = _listing(site, "spd-1", retention_class=RetentionClass.MERCHANT_FACT, expires_at=None)

    listing.mark_delisted(DelistReason.MANUAL, when=T0)

    listing.refresh_from_db()
    assert listing.delisted_at == T0
    assert listing.expires_at is None


def test_delist_reason_requires_a_timestamp(site: SourceSite) -> None:
    listing = _listing(site, "ebay-3")
    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.filter(pk=listing.pk).update(delist_reason=DelistReason.MANUAL)


def test_delisted_at_requires_a_reason(site: SourceSite) -> None:
    listing = _listing(site, "ebay-4")
    with pytest.raises(IntegrityError), transaction.atomic():
        Listing.objects.filter(pk=listing.pk).update(delisted_at=T0)


def test_active_and_delisted_managers_partition_the_rows(site: SourceSite) -> None:
    live = _listing(site, "ebay-live")
    gone = _listing(site, "ebay-gone")
    gone.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0)

    assert list(Listing.objects.active().filter(source_site=site)) == [live]
    assert list(Listing.objects.delisted().filter(source_site=site)) == [gone]
    assert Listing.objects.filter(source_site=site).count() == 2  # nothing was deleted


def _superseded_chain(listing: Listing) -> tuple[ListingResolution, ListingResolution]:
    """Give `listing` a two-edge resolution history: a superseded edge and the
    current one. The superseded_by link is what makes a hard delete of the
    listing raise ProtectedError rather than silently cascading."""
    seagate = Manufacturer.objects.get_or_create(
        normalized_name="seagate-delist-test", defaults={"name": "Seagate"}
    )[0]
    category = Category.objects.get_or_create(slug="drive", defaults={"name": "Drive"})[0]
    family = ProductFamily.objects.get_or_create(
        manufacturer=seagate,
        normalized_name="exos-x18",
        defaults={"category": category, "name": "Exos X18"},
    )[0]
    old = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.9,
        matcher_version="1.0.0",
        is_current=False,
    )
    current = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.95,
        matcher_version="1.0.1",
        is_current=True,
    )
    old.superseded_by = current
    old.save(update_fields=["superseded_by"])
    return old, current


def test_delist_preserves_the_resolution_audit_trail(site: SourceSite) -> None:
    # The whole reason delisting is a soft delete: a hard delete would cascade
    # into listing_resolution, where superseded_by is PROTECT (DR-010).
    listing = _listing(site, "ebay-5")
    seagate = Manufacturer.objects.create(name="Seagate", normalized_name="seagate-delist-test")
    category = Category.objects.get_or_create(slug="drive", defaults={"name": "Drive"})[0]
    family = ProductFamily.objects.create(
        manufacturer=seagate, category=category, name="Exos X18", normalized_name="exos-x18"
    )
    old = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.9,
        matcher_version="1.0.0",
        is_current=False,
    )
    current = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.95,
        matcher_version="1.0.1",
        is_current=True,
    )
    old.superseded_by = current
    old.save(update_fields=["superseded_by"])

    with pytest.raises(ProtectedError), transaction.atomic():
        listing.delete()  # the unavailable alternative, pinned so it stays unavailable

    listing.refresh_from_db()
    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0) is True

    edges = ListingResolution.objects.filter(listing=listing).order_by("pk")
    assert [e.pk for e in edges] == [old.pk, current.pk]
    old.refresh_from_db()
    current.refresh_from_db()
    assert old.superseded_by_id == current.pk  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
    assert current.is_current is True  # a delisted listing keeps its current edge


def test_mark_relisted_restores_the_listing(site: SourceSite) -> None:
    listing = _listing(site, "ebay-6")
    listing.mark_delisted(DelistReason.ABSENT_STALE, when=T0)

    assert listing.mark_relisted() is True
    listing.refresh_from_db()
    assert listing.delisted_at is None
    assert listing.delist_reason == ""
    # Clearing the mark alone does not make the offer showable: mark_delisted
    # backdated the TTL and mark_relisted deliberately does not restore it, so
    # the row is live again only once a fresh observation re-stamps expires_at
    # (in the pipeline, upsert_listing does that immediately before this call).
    assert list(Listing.objects.not_delisted().filter(source_site=site)) == [listing]
    assert not Listing.objects.active().filter(source_site=site).exists()
    Listing.objects.filter(pk=listing.pk).update(expires_at=_live_ttl())
    assert list(Listing.objects.active().filter(source_site=site)) == [listing]
    assert listing.mark_relisted() is False  # already live


def test_expired_listing_is_not_active_before_the_sweep_runs(site: SourceSite) -> None:
    # The sweeper runs hourly, so a lapsed DR-008 window is not enough on its own
    # to keep a stale offer off the live-offer path; active() applies the window
    # itself. not_delisted() must still see the row — it is the delist-candidate
    # set, and an expired listing is exactly what an absence sweep has to mark.
    stale = _listing(site, "ebay-stale", expires_at=timezone.now() - timedelta(minutes=1))
    fresh = _listing(site, "ebay-fresh")
    forever = _listing(site, "spd-forever", retention_class=RetentionClass.MERCHANT_FACT)

    active = set(Listing.objects.active().filter(source_site=site))
    assert active == {fresh, forever}  # NULL expires_at (indefinite) never ages out
    assert set(Listing.objects.not_delisted().filter(source_site=site)) == {stale, fresh, forever}


def test_delisted_listing_with_history_survives_the_retention_sweep(site: SourceSite) -> None:
    # The CR-004 x DR-001 combination that neither leg's tests covered: delisting
    # backdates the listing's own expires_at, which used to make the sweeper
    # collect the row — cascading away the audit trail, or raising ProtectedError
    # through superseded_by and aborting the entire hourly pass.
    listing = _listing(site, "ebay-swept")
    old, current = _superseded_chain(listing)
    snapshot = OfferSnapshot.objects.create(
        listing=listing,
        observed_at=T0 - timedelta(hours=1),
        item_price=Decimal("199.99"),
        stock_status=StockStatus.IN_STOCK,
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=_live_ttl(),
    )
    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=timezone.now())

    report = sweep_expired()  # must complete: no ProtectedError, no cascade

    assert Listing.objects.filter(pk=listing.pk).exists()
    assert set(ListingResolution.objects.filter(listing=listing).values_list("pk", flat=True)) == {
        old.pk,
        current.pk,
    }
    old.refresh_from_db()
    assert old.superseded_by_id == current.pk  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
    # DR-008 is satisfied on both fronts: the evidence rows are deleted, and the
    # surviving anchor holds no merchant content.
    assert not OfferSnapshot.objects.filter(listing=listing).exists()
    assert report.counts["catalog.OfferSnapshot"] >= 1
    assert "catalog.Listing" not in report.counts
    assert snapshot.pk is not None  # the row existed before the sweep removed it
    listing.refresh_from_db()
    assert listing.is_content_redacted()


def _with_content(listing: Listing) -> Listing:
    """Populate every field redaction is supposed to blank.

    _listing() only fills the columns the pipeline's upsert writes; the rest
    (title_normalized from the resolver, and the two fields no writer sets today)
    are filled here so a redaction assertion cannot pass on a field that was
    already empty.
    """
    Listing.objects.filter(pk=listing.pk).update(
        title_normalized="seagate exos x18 18tb recertified",
        condition_label_raw="Seller refurbished",
        listing_fingerprint="f" * 64,
        page_metadata_json={"seller": "diskdeals_us", "itemWebUrl": "https://www.ebay.com/itm/1"},
    )
    listing.refresh_from_db()
    return listing


def test_delist_redacts_merchant_content_for_an_obligated_source(site: SourceSite) -> None:
    # IR-002 owner ruling: delete-on-delist covers merchant-owned content. The
    # anchor survives for DR-010, stripped of everything that was the merchant's.
    listing = _with_content(_listing(site, "ebay-redact"))
    old, current = _superseded_chain(listing)
    seen_before = listing.last_seen

    assert listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0) is True

    listing.refresh_from_db()
    assert listing.is_content_redacted()
    assert listing.canonical_url == ""
    assert listing.url_hash == ""  # a digest of redacted content is still that content
    assert listing.title_raw == ""
    assert listing.title_normalized == ""
    assert listing.condition_label_raw == ""
    assert listing.listing_fingerprint == ""
    assert listing.page_metadata_json == {}
    # Identity and audit metadata are the point of keeping the row at all.
    assert listing.source_listing_key == "ebay-redact"
    assert listing.delisted_at == T0
    assert listing.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    assert listing.last_seen == seen_before  # the absence clock must not move
    assert set(ListingResolution.objects.filter(listing=listing).values_list("pk", flat=True)) == {
        old.pk,
        current.pk,
    }


def test_delist_keeps_content_for_a_source_without_the_obligation(site: SourceSite) -> None:
    # Scoped by the obligation, not by "is this a delist": a merchant_fact source
    # signs no delete-on-delist contract, and DR-001 keeps its facts indefinitely.
    listing = _with_content(
        _listing(site, "spd-no-obligation", retention_class=RetentionClass.MERCHANT_FACT)
    )

    listing.mark_delisted(DelistReason.MANUAL, when=T0)

    listing.refresh_from_db()
    assert listing.delisted_at == T0
    assert listing.title_raw != ""
    assert listing.canonical_url != ""
    assert not listing.is_content_redacted()


def test_redaction_is_idempotent(site: SourceSite) -> None:
    # The blank state is the fixed point, so the hourly sweep re-reaching an
    # already-redacted row rewrites nothing and reports nothing.
    listing = _with_content(_listing(site, "ebay-twice"))
    listing.mark_delisted(DelistReason.ABSENT_FROM_SWEEP, when=T0)
    listing.refresh_from_db()

    assert listing.redact_merchant_content() is False
    assert Listing.redact_expired(timezone.now()) == 0

    report = sweep_expired()
    assert "catalog.Listing" not in report.redactions


def test_sweep_redacts_an_expired_listing_that_delist_detection_missed(site: SourceSite) -> None:
    # The second trigger, and the reason it exists: absence detection can miss
    # (truncated sweep, paused source, poller outage) but the obligation attaches
    # to the freshness window regardless. No delist mark here — only a lapsed TTL.
    missed = _with_content(
        _listing(site, "ebay-missed", expires_at=timezone.now() - timedelta(minutes=1))
    )
    seen_before = missed.last_seen

    report = sweep_expired()

    missed.refresh_from_db()
    assert report.redactions["catalog.Listing"] == 1
    assert report.total_redacted == 1
    assert report.total == 0  # a redacted row is not a deleted row
    assert missed.is_content_redacted()
    assert missed.delisted_at is None  # redaction is not a terminal mark
    assert missed.source_listing_key == "ebay-missed"
    assert missed.last_seen == seen_before


def test_sweep_dry_run_reports_redactions_without_changing_rows(site: SourceSite) -> None:
    listing = _with_content(
        _listing(site, "ebay-dry", expires_at=timezone.now() - timedelta(minutes=1))
    )

    report = sweep_expired(dry_run=True)

    listing.refresh_from_db()
    assert report.redactions["catalog.Listing"] == 1
    assert listing.title_raw != ""


def test_sweep_leaves_fresh_and_unobligated_listings_intact(site: SourceSite) -> None:
    fresh = _with_content(_listing(site, "ebay-fresh-keep"))
    unobligated = _with_content(
        _listing(site, "spd-keep", retention_class=RetentionClass.MERCHANT_FACT)
    )

    report = sweep_expired()

    fresh.refresh_from_db()
    unobligated.refresh_from_db()
    assert "catalog.Listing" not in report.redactions
    assert fresh.title_raw != ""  # still inside its freshness window
    assert unobligated.title_raw != ""  # never under the obligation
