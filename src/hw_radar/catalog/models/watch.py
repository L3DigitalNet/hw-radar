# pyright: reportIncompatibleVariableOverride=false
# django-types treats concrete nested Meta classes as incompatible with abstract
# base model Meta classes; Django's model metaclass handles this pattern.
"""Saved watches, their typed requirements, and per-(watch, listing) verdicts.

MS-2 Slice C schema (MS2-D-07, MS2-D-09, MS2-D-20, MS2-D-29; migration 0020).

- `Watch` holds identity/scope, the hard OFFER clauses, and the one SOFT
  threshold. Hard PRODUCT clauses live on exactly one typed 1:1 requirement
  satellite per first-class category (`DriveRequirement`, `GpuRequirement`,
  `RamRequirement`, `CpuRequirement`). Basic-watch categories (nic, hba,
  motherboard, server) have no satellite and must name a target instead.
- `WatchEvaluation` is the current verdict per (watch, listing), plus the
  input binding that decides whether that verdict is still *current*.

Single-writer contract: the requirement service (`hw_radar.eligibility`,
`save_requirement`) is the only writer of `Watch` requirement fields and the
satellites. The database cannot enforce two of the rules it owns, because both
span tables: satellite category = watch category, and "a basic-watch watch has
a target". The service also bumps `requirement_version` on every effective
edit, which is what invalidates stored verdicts (MS2-D-20). A write that
bypasses it would leave a stale verdict looking current. For that reason the
admin exposes these models read-only (see catalog/admin.py).

Requirement values are typed columns and ArrayFields of the spec satellites'
own vocabularies, never a JSON document. Watch-critical values must stay
queryable and type-checked (ADR 0022, D10). An empty array means "no
constraint on this attribute", and a NULL bound means "no bound".
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.lookups import Exact
from django.utils import timezone

from hw_radar.catalog.models.base import (
    RetentionGoverned,
    TimeStamped,
    retention_constraints,
    retention_indexes,
)
from hw_radar.catalog.models.identity import (
    Category,
    Condition,
    GpuChipVendor,
    GpuCooling,
    GpuInterface,
    MediaType,
    ProductFamily,
    ProductModel,
    RamGeneration,
    RamModuleType,
    RecordingTech,
)
from hw_radar.catalog.models.market import Listing
from hw_radar.catalog.models.resolution import ListingResolution


class EligibilityVerdict(models.TextChoices):
    """Per-clause and aggregate outcome (MS2-D-08). Deliberately NOT the
    `accept | review | none` vocabulary of ListingResolution: resolution and
    eligibility are separate decisions and must never be mapped onto each other
    (D10)."""

    MATCH = "match", "Match"
    NO_MATCH = "no_match", "No match"
    UNKNOWN = "unknown", "Unknown"


class Watch(TimeStamped):
    name = models.CharField(max_length=200)
    # PROTECT, matching product_family.category: a category row is reference
    # data, and deleting it must not silently take saved watches with it.
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="watches")
    enabled = models.BooleanField(default=True)
    # Optional exact target. PROTECT rather than SET_NULL: nulling the target of
    # a basic-watch watch would silently widen it to "every listing in the
    # category", which is the opposite of what its owner saved.
    target_family = models.ForeignKey(
        ProductFamily, on_delete=models.PROTECT, related_name="watches", null=True, blank=True
    )
    target_model = models.ForeignKey(
        ProductModel, on_delete=models.PROTECT, related_name="watches", null=True, blank=True
    )
    # Bumped by the requirement service on every effective requirement edit.
    # WatchEvaluation.requirement_version must equal it for a verdict to count
    # as current (MS2-D-20).
    requirement_version = models.PositiveIntegerField(default=1)

    # Hard offer clauses (MS2-D-07). Each one decides the verdict.
    max_unit_price_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    allowed_conditions = ArrayField(
        models.CharField(max_length=20, choices=Condition.choices), default=list, blank=True
    )
    require_in_stock = models.BooleanField(default=False)
    allow_international = models.BooleanField(default=True)

    # The one soft threshold. It only annotates shortlist rows (`meets_target`)
    # and never changes a verdict, so the evaluator must not read it (MS2-D-07,
    # risk R11).
    target_unit_price_usd = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "watch"
        verbose_name_plural = "watches"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # At most one target. "Exactly one for basic-watch categories" is a
            # cross-table rule (it depends on the category's slug), so the
            # requirement service enforces that half.
            models.CheckConstraint(
                condition=models.Q(target_family__isnull=True)
                | models.Q(target_model__isnull=True),
                name="watch_at_most_one_target",
            ),
        ]

    def __str__(self) -> str:
        return self.name


# Requirement satellites (MS2-D-07). Each is 1:1 on Watch with the watch as its
# primary key, mirroring the spec satellites' 1:1-on-ProductModel shape. Field
# names and element vocabularies pair with the spec satellite columns in
# catalog/models/identity.py (DriveSpec, GpuSpec, RamSpec, CpuSpec): the
# evaluator compares one against the other, so a vocabulary change there must be
# reflected here. Satellites are watch configuration, not observed evidence, so
# they are not RetentionGoverned.


class DriveRequirement(TimeStamped):
    watch = models.OneToOneField(
        Watch, on_delete=models.CASCADE, primary_key=True, related_name="drive_requirement"
    )
    min_capacity_tb = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    media_types = ArrayField(
        models.CharField(max_length=10, choices=MediaType.choices), default=list, blank=True
    )
    # DriveSpec.interface and .form_factor are free-text columns with no choice
    # set, so these arrays are free text too; the requirement service owns
    # normalization.
    interfaces = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    form_factors = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    recording_techs = ArrayField(
        models.CharField(max_length=20, choices=RecordingTech.choices), default=list, blank=True
    )

    class Meta:
        db_table = "drive_requirement"


class GpuRequirement(TimeStamped):
    watch = models.OneToOneField(
        Watch, on_delete=models.CASCADE, primary_key=True, related_name="gpu_requirement"
    )
    min_vram_gb = models.PositiveSmallIntegerField(null=True, blank=True)
    chip_vendors = ArrayField(
        models.CharField(max_length=10, choices=GpuChipVendor.choices), default=list, blank=True
    )
    interfaces = ArrayField(
        models.CharField(max_length=10, choices=GpuInterface.choices), default=list, blank=True
    )
    coolings = ArrayField(
        models.CharField(max_length=10, choices=GpuCooling.choices), default=list, blank=True
    )
    max_tdp_w = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "gpu_requirement"


class RamRequirement(TimeStamped):
    watch = models.OneToOneField(
        Watch, on_delete=models.CASCADE, primary_key=True, related_name="ram_requirement"
    )
    generations = ArrayField(
        models.CharField(max_length=10, choices=RamGeneration.choices), default=list, blank=True
    )
    module_types = ArrayField(
        models.CharField(max_length=10, choices=RamModuleType.choices), default=list, blank=True
    )
    # NULL = no ECC constraint; True/False require that exact RamSpec.ecc value.
    require_ecc = models.BooleanField(null=True, blank=True)
    # Kit total (RamSpec.modules_per_kit x module_capacity_gb), not per-module
    # size. PositiveInteger because it bounds a product of two SmallInteger
    # columns, which can exceed the SmallInteger range.
    min_total_capacity_gb = models.PositiveIntegerField(null=True, blank=True)
    min_speed_mts = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "ram_requirement"


class CpuRequirement(TimeStamped):
    watch = models.OneToOneField(
        Watch, on_delete=models.CASCADE, primary_key=True, related_name="cpu_requirement"
    )
    # Normalized lowercase socket tokens, matching CpuSpec.socket (open
    # vocabulary, so no choice set).
    sockets = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    min_cores = models.PositiveSmallIntegerField(null=True, blank=True)
    max_tdp_w = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "cpu_requirement"


class WatchEvaluation(RetentionGoverned):
    """Current eligibility verdict for one (watch, listing) pair (MS2-D-09).

    One row per pair, overwritten in place; there is no evaluation history.

    The binding columns record exactly which inputs produced the verdict. A row
    is *current* only while all five still equal their live values:
    `snapshot_observed_at`, `resolution`, `requirement_version`,
    `evaluator_version`, and `catalog_fingerprint` (MS2-D-20). Currency is
    decided at READ time by `hw_radar.eligibility`, never by invalidating rows
    on write. So any input change the evaluator has not yet assessed (a crash,
    an evaluator error, or a catalog edit made with queryset `.update()`)
    leaves the row non-current (`pending`) rather than a stale pass.

    Retention mirrors the listing's class and expiry at evaluation time: eBay
    reasons carry prices, so they must not outlive DR-008.
    """

    watch = models.ForeignKey(Watch, on_delete=models.CASCADE, related_name="evaluations")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="watch_evaluations")
    verdict = models.CharField(max_length=10, choices=EligibilityVerdict.choices)
    # Output evidence (DR-004): the structured per-clause reasons behind the
    # verdict (MS2-D-08: clause, outcome, evidence_tier, required, observed,
    # detail). It is not watch input.
    reasons: models.JSONField[list[dict[str, object]]] = models.JSONField(default=list)

    # Input binding (MS2-D-09 / MS2-D-20).
    requirement_version = models.PositiveIntegerField()
    evaluator_version = models.CharField(max_length=20)
    # A timestamp rather than an FK: offer_snapshot is a hypertable with a
    # composite (listing_id, observed_at) key. NULL records that the listing had
    # no snapshot when evaluated.
    snapshot_observed_at = models.DateTimeField(null=True, blank=True)
    # The ListingResolution edge that was current at evaluation. SET_NULL keeps
    # the verdict row if the edge ever goes away; the currency check then sees a
    # mismatch against any live edge and the row fails closed to `pending`.
    resolution = models.ForeignKey(
        ListingResolution,
        on_delete=models.SET_NULL,
        related_name="watch_evaluations",
        null=True,
        blank=True,
    )
    # sha256 hex of the canonical catalog inputs plus CATALOG_INPUTS_VERSION
    # (MS2-D-29). It is compared against a read-time recomputation, which
    # catches spec corrections that change no listing-side input.
    catalog_fingerprint = models.CharField(max_length=64)
    evaluated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "watch_evaluation"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *retention_constraints("watch_evaluation"),
            models.UniqueConstraint(
                fields=["watch", "listing"], name="watch_evaluation_one_per_listing"
            ),
            models.CheckConstraint(
                condition=models.Q(verdict__in=[v.value for v in EligibilityVerdict]),
                name="watch_evaluation_verdict_valid",
            ),
            # FR-006: reasons are mandatory, so a row must carry a non-empty
            # JSON array. Both halves are needed: `~Q(reasons=[])` alone would
            # admit `{}`, a scalar, or JSON null.
            models.CheckConstraint(
                condition=models.Q(
                    Exact(
                        models.Func(
                            models.F("reasons"),
                            function="jsonb_typeof",
                            output_field=models.CharField(),
                        ),
                        "array",
                    )
                )
                & ~models.Q(reasons=[]),
                name="watch_evaluation_reasons_nonempty",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["watch", "verdict"], name="watch_eval_watch_verdict"),
            *retention_indexes("watch_evaluation_expires"),
        ]

    def __str__(self) -> str:
        return f"watch {self.watch_id} / listing {self.listing_id}: {self.verdict}"  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
