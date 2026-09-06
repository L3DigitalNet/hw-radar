# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
# `from __future__ import annotations` keeps the django-types generic form
# (e.g. JSONField[dict[str, object]]) as an unevaluated string: BasedPyright still
# resolves it, but the runtime never subscripts the non-generic JSONField class
# (TypeError under PEP 649 lazy annotations on py3.14). Required, not cosmetic.
from __future__ import annotations

from typing import ClassVar

from django.db import models

from hw_radar.catalog.models.base import RetentionGoverned, TimeStamped, retention_indexes


class Condition(models.TextChoices):
    NEW = "new", "New"
    RECERTIFIED = "recertified", "Recertified"
    REFURBISHED = "refurbished", "Refurbished"
    USED = "used", "Used"
    OPEN_BOX = "open_box", "Open box"
    FOR_PARTS = "for_parts", "For parts / not working"
    UNKNOWN = "unknown", "Unknown"


class Packaging(models.TextChoices):
    RETAIL = "retail", "Retail"
    BULK = "bulk", "Bulk/OEM"
    UNKNOWN = "unknown", "Unknown"


class RecertChannel(models.TextChoices):
    FACTORY = "factory", "Manufacturer recertified"
    SELLER = "seller", "Seller refurbished"
    NONE = "none", "Not recertified"
    UNKNOWN = "unknown", "Unknown"


class WarrantyChannel(models.TextChoices):
    MANUFACTURER = "manufacturer", "Manufacturer"
    SELLER = "seller", "Seller"
    NONE = "none", "None"
    UNKNOWN = "unknown", "Unknown"


class MediaType(models.TextChoices):
    HDD = "hdd", "HDD"
    SSD = "ssd", "SSD"
    UNKNOWN = "unknown", "Unknown"


class RecordingTech(models.TextChoices):
    CMR = "cmr", "CMR"
    SMR_DEVICE_MANAGED = "smr_dm", "SMR (device-managed)"
    SMR_HOST_MANAGED = "smr_hm", "SMR (host-managed)"
    SMR_UNKNOWN = "smr_unknown", "SMR (type unknown)"
    UNKNOWN = "unknown", "Unknown"


class AliasType(models.TextChoices):
    GTIN = "gtin", "GTIN"
    UPC = "upc", "UPC"
    EAN = "ean", "EAN"
    ASIN = "asin", "ASIN"
    EPID = "epid", "eBay ePID"
    MPN = "mpn", "Manufacturer part number"
    OEM_PN = "oem_pn", "OEM part number"
    RETAIL_PN = "retail_pn", "Retail part number"
    REGION_PN = "region_pn", "Region/revision part number"
    OTHER = "other", "Other"


class AliasSourceKind(models.TextChoices):
    CATALOG_AUTHORITATIVE = "catalog_authoritative", "Catalog authoritative"
    LISTING_DERIVED = "listing_derived", "Listing derived"
    MANUAL = "manual", "Manual"


class Manufacturer(TimeStamped):
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "manufacturer"

    def __str__(self) -> str:
        return self.name


class Category(TimeStamped):
    """The extensibility axis: drive in v1; later ram, gpu."""

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "category"
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.slug


class ProductFamily(TimeStamped):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="families")
    manufacturer = models.ForeignKey(
        Manufacturer, on_delete=models.PROTECT, related_name="families"
    )
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200)

    class Meta:
        db_table = "product_family"
        verbose_name_plural = "product families"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["manufacturer", "normalized_name"], name="product_family_unique_per_mfr"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ProductModel(TimeStamped, RetentionGoverned):
    """Physical, condition-free canonical identity anchor."""

    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name="models")
    product_family = models.ForeignKey(
        ProductFamily, on_delete=models.PROTECT, related_name="models", null=True, blank=True
    )
    model_number = models.CharField(max_length=100)
    normalized_model_number = models.CharField(max_length=100)

    class Meta:
        db_table = "product_model"
        # ProductModel, DriveSpec and ProductAlias take retention_indexes() but
        # deliberately NOT the retention_constraints() CHECK pair that every
        # other RetentionGoverned model carries, so the DR-001 invariant
        # "retention_class is set, and it agrees with expires_at" is unenforced
        # at the database on these three tables. The blocker is
        # matching.resolver._emit_learned_aliases, which writes ProductAlias rows
        # with an empty retention_class; the class those learned aliases should
        # carry is an unresolved owner decision (DR-008's six-hour eBay deletion
        # duty against ADR-0019 rule 7's durable learned aliases), tracked in
        # docs/open-questions.md and docs/TODO.md. Adding the pair before that
        # lands makes the resolver raise IntegrityError on every learned alias.
        # The index is independent of that decision and is what keeps the hourly
        # purge_expired sweep an index scan instead of three sequential scans.
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["manufacturer", "normalized_model_number"],
                name="product_model_identity_anchor",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [*retention_indexes("product_model_expires")]

    def __str__(self) -> str:
        return f"{self.manufacturer} {self.model_number}"


class ProductVariant(TimeStamped):
    product_model = models.ForeignKey(
        ProductModel, on_delete=models.PROTECT, related_name="variants"
    )
    condition = models.CharField(
        max_length=20, choices=Condition.choices, default=Condition.UNKNOWN
    )
    packaging = models.CharField(
        max_length=20, choices=Packaging.choices, default=Packaging.UNKNOWN
    )
    recert_channel = models.CharField(
        max_length=20, choices=RecertChannel.choices, default=RecertChannel.UNKNOWN
    )
    warranty_channel = models.CharField(
        max_length=20, choices=WarrantyChannel.choices, default=WarrantyChannel.UNKNOWN
    )
    warranty_months = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "product_variant"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=[
                    "product_model",
                    "condition",
                    "packaging",
                    "recert_channel",
                    "warranty_channel",
                ],
                name="product_variant_unique_sellable_identity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_model} [{self.condition}/{self.recert_channel}]"


class DriveSpec(TimeStamped, RetentionGoverned):
    product_model = models.OneToOneField(
        ProductModel, on_delete=models.CASCADE, primary_key=True, related_name="drive_spec"
    )
    media_type = models.CharField(
        max_length=10, choices=MediaType.choices, default=MediaType.UNKNOWN
    )
    interface = models.CharField(max_length=50, blank=True, default="")
    form_factor = models.CharField(max_length=50, blank=True, default="")
    capacity_tb = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    rpm = models.PositiveIntegerField(null=True, blank=True)
    cache_mb = models.PositiveIntegerField(null=True, blank=True)
    recording_tech = models.CharField(
        max_length=20, choices=RecordingTech.choices, null=True, blank=True
    )
    plp = models.BooleanField(null=True, blank=True)
    market_tier = models.CharField(max_length=50, blank=True, default="")
    model_family = models.CharField(max_length=100, blank=True, default="")
    dwpd = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    workload_tb_year = models.PositiveIntegerField(null=True, blank=True)
    tbw = models.PositiveIntegerField(null=True, blank=True)
    sector_format = models.CharField(max_length=20, blank=True, default="")
    sed = models.BooleanField(null=True, blank=True)
    spec_json: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "drive_spec"
        indexes: ClassVar[list[models.Index]] = [*retention_indexes("drive_spec_expires")]


class ProductAlias(RetentionGoverned):
    alias_type = models.CharField(max_length=20, choices=AliasType.choices)
    normalized_alias_text = models.CharField(max_length=200)
    product_model = models.ForeignKey(
        ProductModel, on_delete=models.CASCADE, related_name="aliases", null=True, blank=True
    )
    product_family = models.ForeignKey(
        ProductFamily, on_delete=models.CASCADE, related_name="aliases", null=True, blank=True
    )
    product_variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="aliases",
        null=True,
        blank=True,
    )
    # PROTECT, not SET_NULL: source_site is part of the nulls_distinct=False
    # single-target unique key below, so a SET_NULL cascade could null two
    # same-text aliases into a uniqueness collision mid-delete. Marketplaces are
    # reference data — deletion should be blocked, matching Listing/Seller.
    source_site = models.ForeignKey(
        "catalog.SourceSite",
        on_delete=models.PROTECT,
        related_name="aliases",
        null=True,
        blank=True,
    )
    source_kind = models.CharField(max_length=30, choices=AliasSourceKind.choices)
    is_primary = models.BooleanField(default=False)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_alias"
        verbose_name_plural = "product aliases"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        product_model__isnull=False,
                        product_family__isnull=True,
                        product_variant__isnull=True,
                    )
                    | models.Q(
                        product_model__isnull=True,
                        product_family__isnull=False,
                        product_variant__isnull=True,
                    )
                    | models.Q(
                        product_model__isnull=True,
                        product_family__isnull=True,
                        product_variant__isnull=False,
                    )
                ),
                name="product_alias_exactly_one_grain",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_kind=""), name="product_alias_source_kind_set"
            ),
            models.CheckConstraint(
                condition=~models.Q(alias_type="oem_pn", product_variant__isnull=False),
                name="product_alias_oem_pn_not_variant_grain",
            ),
            models.UniqueConstraint(
                fields=["alias_type", "normalized_alias_text", "source_site"],
                condition=~models.Q(alias_type="oem_pn"),
                name="product_alias_single_target_per_site",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=[
                    "alias_type",
                    "normalized_alias_text",
                    "source_site",
                    "product_model",
                    "product_family",
                ],
                condition=models.Q(alias_type="oem_pn"),
                name="product_alias_oem_multi_target_no_dupes",
                nulls_distinct=False,
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [*retention_indexes("product_alias_expires")]


class DriveUnit(models.Model):
    product_model = models.ForeignKey(ProductModel, on_delete=models.PROTECT, related_name="units")
    serial_number = models.CharField(max_length=100)
    smart_json = models.JSONField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "drive_unit"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["product_model", "serial_number"], name="drive_unit_unique_serial_per_model"
            ),
        ]
