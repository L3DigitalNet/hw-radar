from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest

from hw_radar.catalog.models import (
    Category,
    CpuRequirement,
    CpuSpec,
    DriveRequirement,
    DriveSpec,
    DriveUnit,
    FxRateDaily,
    GpuRequirement,
    GpuSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RamRequirement,
    RamSpec,
    RefdataConfig,
    ReferenceFetchRequest,
    ScraperRun,
    Seller,
    SourceConfig,
    SourceSite,
    UnknownModelBackfill,
    Watch,
    WatchEvaluation,
)

admin.site.register(Category)
admin.site.register(Manufacturer)
admin.site.register(ProductFamily)
admin.site.register(ProductModel)
admin.site.register(ProductVariant)
admin.site.register(DriveSpec)
admin.site.register(GpuSpec)
admin.site.register(RamSpec)
admin.site.register(CpuSpec)
admin.site.register(ProductAlias)
admin.site.register(DriveUnit)
admin.site.register(SourceSite)
admin.site.register(Seller)
admin.site.register(Listing)
admin.site.register(SourceConfig)
admin.site.register(ScraperRun)
admin.site.register(FxRateDaily)


@admin.register(ListingResolution)
class ListingResolutionAdmin(
    admin.ModelAdmin  # pyright: ignore[reportMissingTypeArgument]
    # django's runtime ModelAdmin isn't subscriptable (no __class_getitem__);
    # only django-types' stub declares it Generic, so the type argument can't
    # be supplied without breaking admin.site.autodiscover() at import time.
):
    """Read-only: listing_resolution is an append-only audit trail (DR-010) —
    admin is a review-queue inspection surface, not an editor."""

    list_display = (
        "listing",
        "grain",
        "method",
        "confidence",
        "matcher_version",
        "resolved_at",
        "superseded_by",
    )
    list_filter = ("grain", "method")
    ordering = ("-resolved_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: ListingResolution | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: ListingResolution | None = None
    ) -> bool:
        return False


@admin.register(UnknownModelBackfill)
class UnknownModelBackfillAdmin(
    admin.ModelAdmin  # pyright: ignore[reportMissingTypeArgument]
    # django's runtime ModelAdmin isn't subscriptable (no __class_getitem__);
    # only django-types' stub declares it Generic, so the type argument can't
    # be supplied without breaking admin.site.autodiscover() at import time.
):
    """Read-only: unknown_model_backfill is a database VIEW (C.3.4) — admin is
    a review-queue inspection surface, not an editor."""

    list_display = (
        "hypothesis_key",
        "mpn_hypothesis",
        "vendor_hint",
        "occurrences",
        "family_grain_count",
        "last_seen",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: UnknownModelBackfill | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: UnknownModelBackfill | None = None
    ) -> bool:
        return False


@admin.register(RefdataConfig)
class RefdataConfigAdmin(
    admin.ModelAdmin  # pyright: ignore[reportMissingTypeArgument]
    # see ListingResolutionAdmin: runtime ModelAdmin isn't subscriptable
):
    list_display = ("pk", "enabled", "discovery_occurrence_threshold", "last_refresh_at")


@admin.register(ReferenceFetchRequest)
class ReferenceFetchRequestAdmin(
    admin.ModelAdmin  # pyright: ignore[reportMissingTypeArgument]
):
    list_display = (
        "hypothesis_key",
        "vendor_hint",
        "occurrences_at_enqueue",
        "status",
        "created_at",
    )
    list_filter = ("status", "vendor_hint")
    search_fields = ("hypothesis_key", "mpn_hypothesis")


class WatchInspectionAdmin(
    admin.ModelAdmin  # pyright: ignore[reportMissingTypeArgument]
    # see ListingResolutionAdmin: runtime ModelAdmin isn't subscriptable
):
    """Read-only inspection of watches, requirement satellites, and verdicts.

    Watch requirements have one writer, the requirement service in
    hw_radar.eligibility. It enforces satellite category = watch category and
    bumps Watch.requirement_version on every edit. An admin edit would skip the
    bump, so verdicts computed against the old requirements would still read as
    current (MS2-D-20). WatchEvaluation rows are evaluator output and are never
    hand-edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


for _watch_model in (
    Watch,
    DriveRequirement,
    GpuRequirement,
    RamRequirement,
    CpuRequirement,
    WatchEvaluation,
):
    admin.site.register(_watch_model, WatchInspectionAdmin)
