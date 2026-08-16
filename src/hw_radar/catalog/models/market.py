# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
# See identity.py header: future annotations keep JSONField[...] a resolvable
# string for the type-checker without subscripting the class at runtime.
from __future__ import annotations

from copy import copy
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from django.db import models, transaction
from django.db.models.functions import Coalesce, Now
from django.utils import timezone

from hw_radar.catalog.models.base import (
    BOUNDED_RETENTION_CLASSES,
    ResolutionGrain,
    RetentionClass,
    RetentionGoverned,
    TimeStamped,
    retention_constraints,
)
from hw_radar.catalog.models.identity import ProductFamily, ProductModel, ProductVariant


class SourceType(models.TextChoices):
    MANUFACTURER_STORE = "manufacturer_store", "Manufacturer store"
    SPECIALIST_RESELLER = "specialist_reseller", "Storage-specialist reseller"
    MARKETPLACE = "marketplace", "Marketplace"
    RETAILER = "retailer", "Retailer"
    SEARCH_PROVIDER = "search_provider", "Search provider"
    OTHER = "other", "Other"


class SourceSite(TimeStamped):
    """One marketplace/store; Appendix C.1 rows become rows here at MS-1."""

    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, unique=True)
    source_type = models.CharField(
        max_length=30, choices=SourceType.choices, default=SourceType.OTHER
    )
    region = models.CharField(max_length=10, blank=True, default="US")
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "source_site"

    def __str__(self) -> str:
        return self.name


class StockStatus(models.TextChoices):
    IN_STOCK = "in_stock", "In stock"
    OUT_OF_STOCK = "out_of_stock", "Out of stock"
    PREORDER = "preorder", "Pre-order"
    UNKNOWN = "unknown", "Unknown"


class Seller(TimeStamped):
    """Marketplace-scoped merchant identity."""

    source_site = models.ForeignKey(SourceSite, on_delete=models.PROTECT, related_name="sellers")
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "seller"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["source_site", "normalized_name"], name="seller_unique_per_site"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class DelistReason(models.TextChoices):
    """Why a listing was marked terminal (CR-004). The two absence reasons record
    the STRENGTH of the evidence, not just the fact — a complete-sweep miss and a
    stale-absence inference have different false-positive rates, and keeping them
    apart is what makes a later audit of delist decisions possible."""

    ABSENT_FROM_SWEEP = "absent_from_sweep", "Absent from a complete source sweep"
    ABSENT_STALE = "absent_stale", "Absent for longer than the source freshness window"
    # No adapter reports SOURCE_ENDED yet (no current source payload carries an
    # item-ended flag). It is declared up front because widening a choices= list
    # costs an AlterField migration, and this is the reason a source with an
    # explicit end signal should use instead of inferring from absence.
    SOURCE_ENDED = "source_ended", "Source reported the listing ended"
    MANUAL = "manual", "Manually delisted"


# Retention classes whose SOURCE carries the IR-002 / DR-008 delete-on-delist
# obligation. The obligation is a property of the source contract, not of a
# hostname, so it is scoped by retention class: eBay rows are already stamped
# ebay_listing_observation everywhere in the pipeline, and a future source that
# signs the same kind of contract joins by adding its class here rather than by
# threading a new flag through the adapters. A class listed here MUST also be a
# bounded class — an indefinite class cannot express "delete when it ends".
DELETE_ON_DELIST_CLASSES: tuple[RetentionClass, ...] = (RetentionClass.EBAY_LISTING_OBSERVATION,)


class ListingQuerySet(models.QuerySet["Listing"]):
    """Live-offer read path. Every caller that means "offers a user could buy"
    must go through active(): a delisted listing keeps its row, its snapshots and
    its resolution edges (DR-010 audit trail) and is excluded by predicate only.

    The three predicates are NOT interchangeable:
      active()       — showable to a user: neither delisted nor past its
                       freshness window.
      not_delisted() — no terminal mark, freshness irrelevant. This, not
                       active(), is the delist-CANDIDATE set: a listing whose
                       TTL lapsed is precisely the one an absence sweep still
                       has to mark, and filtering it out first would make the
                       stale-absence path unreachable.
      delisted()     — carries a terminal mark.
    """

    def active(self) -> ListingQuerySet:
        # Two independent ways to stop being a live offer, and the second is not
        # redundant with the retention sweeper: the sweep runs hourly, so between
        # two passes a bounded eBay listing can sit hours past the DR-008 six-hour
        # freshness window. Showing it would breach the very obligation the TTL
        # encodes, so the read path enforces the window itself.
        #
        # Now() rather than timezone.now(): the comparison is made by the database
        # when the query executes. A Python timestamp is frozen at the moment
        # active() was called, which a cached or reused queryset outlives.
        return self.not_delisted().filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=Now())
        )

    def not_delisted(self) -> ListingQuerySet:
        # NULL expires_at means an indefinite retention class (DR-001 CHECK), so
        # merchant facts are never aged out by the freshness clause in active().
        return self.filter(delisted_at__isnull=True)

    def delisted(self) -> ListingQuerySet:
        return self.filter(delisted_at__isnull=False)


class ListingManager(models.Manager["Listing"]):
    # Written out rather than ListingQuerySet.as_manager(): django-types types
    # as_manager() as a plain BaseManager, which loses active()/delisted() at the
    # type level and would push every call site to an untyped escape hatch.
    def get_queryset(self) -> ListingQuerySet:
        return ListingQuerySet(self.model, using=self._db)

    def active(self) -> ListingQuerySet:
        return self.get_queryset().active()

    def not_delisted(self) -> ListingQuerySet:
        return self.get_queryset().not_delisted()

    def delisted(self) -> ListingQuerySet:
        return self.get_queryset().delisted()


class Listing(RetentionGoverned):
    """One merchant offer page at the ADR-0010 listing grain.

    Delisting (IR-002 / DR-008, CR-004) is a SOFT delete: delisted_at + a
    DelistReason mark the row terminal, and the row itself stays. A hard delete is
    not available here — ListingResolution.superseded_by is PROTECT and the edges
    are the DR-010 audit trail, so destroying a listing would either fail or
    destroy resolution history. The mark is reversible (mark_relisted): an
    absence-based delist that turns out to be wrong self-heals the moment the
    source shows the listing again, which is what makes the absence heuristics in
    acquisition.pipeline safe to run.

    RETENTION ROLE — ENTITY, not observation. DR-008 demands that expired eBay
    data be physically deleted; DR-010 demands that resolution edges survive. The
    two are reconciled by splitting the anchor row from the evidence hanging off
    it: the physical-deletion obligation is carried by the OBSERVATION tables
    (OfferSnapshot, RawPayload, the heartbeat tables), each stamped with its own
    expires_at and swept independently, so an offer's prices, payloads and
    heartbeat rows are gone within its TTL whether or not the listing was ever
    marked delisted. What survives is the identity skeleton the audit trail hangs
    from. Without that split the sweeper either cascades a Listing delete into
    listing_resolution (destroying the trail) or trips the superseded_by PROTECT
    and aborts the whole hourly pass.

    REDACTION — what "delete on delist" covers (owner ruling, 2026-08-16):
    merchant-owned CONTENT goes, the anchor stays. For a source under the
    obligation (DELETE_ON_DELIST_CLASSES) the fields in REDACTED_CONTENT_FIELDS
    are blanked in place; pk, source_listing_key, resolution edges, delist marks
    and retention metadata survive, so the DR-010 trail still reads as "this
    listing existed, resolved to X, and ended at T" without holding the
    merchant's title, URL or page payload. Two independent triggers converge on
    that same end state — mark_delisted() redacts immediately, and redact_expired()
    catches obligated rows whose TTL lapsed while delist detection missed them —
    because absence-based detection is a heuristic and the obligation is not.
    """

    # Cross-file contract with catalog.management.commands.purge_expired
    # (is_retention_anchor): the sweeper reads this attribute and never row-
    # deletes a model that sets it. Default is False, so a newly added
    # RetentionGoverned model is swept unless it opts out here deliberately.
    retention_anchor: ClassVar[bool] = True

    # Merchant-owned content, mapped to the blank value redaction writes. Every
    # entry must be a field whose column tolerates the blank at the database
    # level, which is why no migration accompanies redaction: TextField/CharField
    # take "" and the JSONField takes {}.
    #
    # url_hash goes WITH canonical_url rather than surviving as an identity aid:
    # it is a digest of the very content the ruling retires, its only consumer is
    # URL identity, and (source_site, source_listing_key) — the unique constraint
    # the upsert actually keys on — already provides that. Keeping a hash of
    # redacted content would buy nothing and leave us holding the content in
    # derived form. listing_fingerprint goes for the same reason: it digests
    # listing content for change detection that a terminal listing no longer does.
    #
    # NOT redacted, deliberately: source_listing_key (the marketplace item id is
    # the audit trail's subject and the key a re-listing is matched on),
    # is_international (a derived shipping-origin boolean, no merchant text), the
    # resolution FKs and grain (our conclusions, not their content), and the
    # first_seen/last_seen/delist/retention stamps (the audit metadata itself).
    REDACTED_CONTENT_FIELDS: ClassVar[dict[str, object]] = {
        "canonical_url": "",
        "url_hash": "",
        "title_raw": "",
        "title_normalized": "",
        "condition_label_raw": "",
        "listing_fingerprint": "",
        "page_metadata_json": {},
    }

    source_site = models.ForeignKey(SourceSite, on_delete=models.PROTECT, related_name="listings")
    seller = models.ForeignKey(
        Seller, on_delete=models.SET_NULL, related_name="listings", null=True, blank=True
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.SET_NULL, related_name="listings", null=True, blank=True
    )
    # Denormalized CURRENT resolution (C.3.3): most-specific-wins, lower grains
    # NULL; refreshed ONLY by matching.resolver.CatalogResolver on accept. The
    # append-only audit trail lives in ListingResolution — these fields are a
    # read-path convenience, never the source of truth.
    # Explicit type arguments (see resolution.py's superseded_by): django-types
    # cannot infer the generic parameter from the "catalog.X" string form, and
    # silently falls back to a None-only field type — which breaks assignment
    # from matching.resolver on accept.
    product_family: models.ForeignKey[ProductFamily | None] = models.ForeignKey(
        "catalog.ProductFamily",
        on_delete=models.SET_NULL,
        related_name="listings",
        null=True,
        blank=True,
    )
    product_model: models.ForeignKey[ProductModel | None] = models.ForeignKey(
        "catalog.ProductModel",
        on_delete=models.SET_NULL,
        related_name="listings",
        null=True,
        blank=True,
    )
    resolution_grain = models.CharField(
        max_length=10, choices=ResolutionGrain.choices, default=ResolutionGrain.NONE
    )
    resolution_confidence = models.FloatField(null=True, blank=True)
    source_listing_key = models.CharField(max_length=255)
    canonical_url = models.URLField(max_length=1000)
    url_hash = models.CharField(max_length=64)
    title_raw = models.TextField()
    title_normalized = models.TextField(blank=True, default="")
    condition_label_raw = models.CharField(max_length=255, blank=True, default="")
    listing_fingerprint = models.CharField(max_length=64, blank=True, default="")
    is_international = models.BooleanField(default=False)
    page_metadata_json: models.JSONField[dict[str, object]] = models.JSONField(
        default=dict, blank=True
    )
    first_seen = models.DateTimeField(auto_now_add=True)
    # auto_now: bumped by every full save() of the row, so "the source last showed
    # us this listing" only holds because the delist path below saves with
    # update_fields (which skips auto_now for excluded fields). The absence
    # heuristics in acquisition.pipeline compare against this column — a stray
    # full save() of a delisted listing would silently reset its absence clock.
    last_seen = models.DateTimeField(auto_now=True)
    # NULL delisted_at == active. A timestamp (rather than a boolean) because
    # delete-on-delist is an obligation with a clock: DR-008 cares when the offer
    # stopped existing, not merely that it did.
    delisted_at = models.DateTimeField(null=True, blank=True)
    delist_reason = models.CharField(
        max_length=20, choices=DelistReason.choices, blank=True, default=""
    )

    objects: ClassVar[ListingManager] = ListingManager()

    class Meta:
        db_table = "listing"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("listing"),
            models.UniqueConstraint(
                fields=["source_site", "source_listing_key"], name="listing_unique_per_site_key"
            ),
            # Timestamp and reason move together, in the database: a delisted row
            # with no reason is unauditable, and a reason with no timestamp would
            # read as active through active()/delisted() while claiming otherwise.
            models.CheckConstraint(
                condition=(
                    models.Q(delisted_at__isnull=True, delist_reason="")
                    | (models.Q(delisted_at__isnull=False) & ~models.Q(delist_reason=""))
                ),
                name="listing_delist_reason_coherent",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # Partial index on the live-offer predicate: reads are almost always
            # active()-filtered, while delisted rows accumulate as history.
            models.Index(
                fields=["source_site", "source_listing_key"],
                condition=models.Q(delisted_at__isnull=True),
                name="listing_active_by_site_key",
            ),
        ]

    def mark_delisted(self, reason: DelistReason, *, when: datetime | None = None) -> bool:
        """Mark this listing terminal; return False if it already was.

        Also pulls the DR-008 evidence TTLs forward to the delist instant for
        BOUNDED retention classes, so the retention sweeper physically removes the
        offer SNAPSHOTS at its next pass instead of up to a full freshness window
        later. Indefinite classes (merchant facts) are left alone — their
        retention CHECK requires expires_at IS NULL, and delisting an offer is not
        licence to drop a merchant fact.

        The listing's own expires_at moves with them, but for a different reason:
        the Listing is a retention ANCHOR (see the class docstring) and is never
        row-deleted by the sweeper, so on this row expires_at is a freshness bound
        that active() reads, not a deletion trigger.

        RawPayload rows are deliberately NOT touched: one stored payload backs
        every listing in its batch, so expiring it per-listing would destroy the
        provenance of listings that are still live.

        For a source under the delete-on-delist obligation this also redacts the
        merchant content in the same transaction (see REDACTED_CONTENT_FIELDS).
        The three writes are atomic so a crash cannot leave a listing marked
        terminal while still holding content the mark says we have retired.
        """
        if self.delisted_at is not None:
            return False
        stamp = when or timezone.now()
        with transaction.atomic():
            self.delisted_at = stamp
            self.delist_reason = reason
            fields = ["delisted_at", "delist_reason"]
            bounded = self.retention_class in {c.value for c in BOUNDED_RETENTION_CLASSES}
            # A bounded row always has a non-NULL expires_at (retention_ttl_coherent),
            # so this comparison is safe; only ever pull the TTL forward, never extend.
            if bounded and self.expires_at > stamp:
                self.expires_at = stamp
                fields.append("expires_at")
            # update_fields keeps last_seen (auto_now) where the source left it.
            self.save(update_fields=fields)
            if bounded:
                OfferSnapshot.objects.filter(
                    listing=self,
                    retention_class__in=[c.value for c in BOUNDED_RETENTION_CLASSES],
                    expires_at__gt=stamp,
                ).update(expires_at=stamp)
            if self.retention_class in {c.value for c in DELETE_ON_DELIST_CLASSES}:
                self.redact_merchant_content()
        return True

    def redact_merchant_content(self) -> bool:
        """Blank this row's merchant-owned content; return False if already blank.

        Idempotent by construction — the blank state is the fixed point, so the
        hourly sweeper re-reaching an already-redacted row is a no-op. Callers do
        not need to check the obligation first only when they mean "redact this
        specific row" (an operator honoring a takedown, say); the automatic paths
        scope by DELETE_ON_DELIST_CLASSES before calling.

        Writes only the content columns, so last_seen (auto_now) keeps the instant
        the source last showed us the listing — the absence heuristics in
        acquisition.pipeline read that column, and resetting it here would move
        their clock.
        """
        if self.is_content_redacted():
            return False
        for name, blank in self.REDACTED_CONTENT_FIELDS.items():
            # copy(): the JSON blank is a mutable dict living on the class, and
            # assigning it directly would hand every redacted instance the same
            # object to later mutate.
            setattr(self, name, copy(blank))
        self.save(update_fields=list(self.REDACTED_CONTENT_FIELDS))
        return True

    def is_content_redacted(self) -> bool:
        return all(
            getattr(self, name) == blank for name, blank in self.REDACTED_CONTENT_FIELDS.items()
        )

    @classmethod
    def redact_expired(cls, now: datetime, *, dry_run: bool = False) -> int:
        """Redact obligated anchors whose TTL lapsed; return how many rows changed.

        The safety net behind mark_delisted: delist detection is absence-based and
        can miss (a truncated sweep, a paused source, a poller outage), but the
        obligation attaches to the freshness window regardless. Any row whose
        expires_at has passed is one we may no longer show, so it is also one
        whose merchant content we may no longer hold — delist mark or not.

        Bulk .update() rather than per-row saves: this runs hourly over the whole
        table, and .update() also leaves auto_now columns alone, which is exactly
        what the absence clock needs (see redact_merchant_content).
        """
        obligated = cls.objects.filter(
            retention_class__in=[c.value for c in DELETE_ON_DELIST_CLASSES],
            expires_at__lt=now,
        ).exclude(**cls.REDACTED_CONTENT_FIELDS)  # already-blank rows are skipped
        if dry_run:
            return obligated.count()
        return obligated.update(**cls.REDACTED_CONTENT_FIELDS)

    def mark_relisted(self) -> bool:
        """Clear a terminal mark after the source showed the listing again.

        Return False if it was not delisted. The already-expired evidence TTLs set
        by mark_delisted are NOT restored: the next observation writes fresh
        snapshots under the current policy, and reviving a TTL would resurrect
        evidence the delete-on-delist obligation already retired.

        Consequence worth knowing: clearing the mark does not by itself put the
        row back in active(), because this listing's backdated expires_at still
        fails the freshness clause. That is deliberate — an offer with no fresh
        observation must not be shown. The pipeline gets both halves because
        upsert_listing re-stamps expires_at just before calling this.

        Redacted content is likewise not restored here — it is gone, and only a
        new observation can supply it. The same upsert repopulates canonical_url,
        url_hash, title_raw and condition_label_raw from the live payload;
        title_normalized comes back on the next resolve, and listing_fingerprint /
        page_metadata_json stay blank because no writer sets them today.
        """
        if self.delisted_at is None:
            return False
        self.delisted_at = None
        self.delist_reason = ""
        self.save(update_fields=["delisted_at", "delist_reason"])
        return True


class OfferSnapshot(RetentionGoverned):
    """Time-series offer observation; converted to a TimescaleDB hypertable."""

    pk = models.CompositePrimaryKey("listing_id", "observed_at")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="snapshots")
    observed_at = models.DateTimeField()
    currency = models.CharField(max_length=3, default="USD")
    item_price = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tax_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_landed_price = models.GeneratedField(
        expression=models.F("item_price")
        + Coalesce(models.F("shipping_price"), models.Value(Decimal("0")))
        + Coalesce(models.F("tax_price"), models.Value(Decimal("0"))),
        output_field=models.DecimalField(max_digits=12, decimal_places=2),
        db_persist=True,
    )
    stock_status = models.CharField(
        max_length=20, choices=StockStatus.choices, default=StockStatus.UNKNOWN
    )
    quantity_available = models.PositiveIntegerField(null=True, blank=True)
    fx_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    fx_pair = models.CharField(max_length=7, blank=True, default="")
    fx_rate_date = models.DateField(null=True, blank=True)
    fx_source = models.CharField(max_length=50, blank=True, default="")
    usd_item_price = models.GeneratedField(
        # NULL fx_rate propagates to NULL — never silently treat a foreign-currency
        # amount as USD (ADR-0008; USD rows carry the identity stamp fx_rate=1).
        expression=models.F("item_price") * models.F("fx_rate"),
        output_field=models.DecimalField(max_digits=14, decimal_places=4),
        db_persist=True,
    )
    extraction_method = models.CharField(max_length=50, blank=True, default="")
    confidence_score = models.FloatField(null=True, blank=True)
    attrs_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    raw_payload = models.ForeignKey(
        "catalog.RawPayload",
        on_delete=models.SET_NULL,
        related_name="snapshots",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "offer_snapshot"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("offer_snapshot"),
            models.CheckConstraint(
                condition=(
                    models.Q(currency="USD")
                    | (
                        models.Q(fx_rate__isnull=False)
                        & ~models.Q(fx_pair="")
                        & models.Q(fx_rate_date__isnull=False)
                    )
                ),
                name="offer_snapshot_fx_stamped_non_usd",
            ),
        ]
