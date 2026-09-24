"""The ADR-0018 persist stage: validate-then-write, one transaction.

Invariants:
- Conflicts (rung-1 hit-aggregation carry-forward) ABORT the whole import with
  nothing written — two aliases normalizing to one key with different targets
  fail into review, never auto-select. This covers within-seed, across-seed,
  and against-DB collisions, including cross-manufacturer ones (descriptors say
  whether the colliding brands are ladder-equivalent — the SanDisk↔WD lineage —
  so review can decide; real-corpus SanDisk↔WD verification is deferred to the
  first SSD seed, plan D7).
- SINGLE-WRITER assumption (plan D8): the conflict precheck is not safe against
  alias writes racing between _db_conflicts() and _import_alias(). Only the
  monthly poller job and the manual import_refdata command may run this, never
  concurrently; the defensive re-check aborts rather than retargets.
- Keys match the resolver's materialization exactly — family get_or_create on
  (manufacturer, canonicalize_title(name)), so seeding 'Exos' ADOPTS the rung-2
  provisional 'exos' row instead of duplicating it (carry-forward #4).
- DR-009: every written ProductModel/spec-satellite/ProductAlias row carries
  retention_class=manufacturer_reference, expires_at NULL. Absent-from-seed
  models are never deleted (append-only).
- Category-keyed (MS2-D-06): the document's category picks the Category row and
  the spec satellite (_SPEC_MODELS). The drive path writes exactly what it wrote
  before MS-2.
- First-party only (MS2-D-06): catalog_authoritative / manufacturer_reference
  are stamped only from first-party provenance. A `non_first_party` document is
  refused before anything is written (UnratifiedProvenanceError): its aliases
  would be `manual`, but no retention class for non-first-party reference rows
  is ratified — manufacturer_reference asserts first-party provenance (DR-009)
  and listing_derived_alias asserts a listing inference (OQ22). Lifting this
  guard needs that decision first; it is a new RetentionClass, which rewrites
  every *_retention_ttl_coherent CHECK (see migration 0017).
- Never writes offer_snapshot/score/alert/heartbeat rows (ADR-0018 rule 1)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from django.db import transaction

from hw_radar.catalog.models import (
    AliasSourceKind,
    Category,
    CpuSpec,
    DriveSpec,
    GpuSpec,
    Manufacturer,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RamSpec,
    RetentionClass,
)
from hw_radar.matching.ladder import brands_consistent
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.refdata.contracts import SeedAlias, SeedDocument, SeedModel, detect_conflicts

logger = logging.getLogger(__name__)

# Keys must equal contracts.SeedCategory: a seedable category without a
# satellite here would fail every import of its documents with a KeyError.
_SPEC_MODELS: Final[dict[str, type[DriveSpec | GpuSpec | RamSpec | CpuSpec]]] = {
    "drive": DriveSpec,
    "gpu": GpuSpec,
    "ram": RamSpec,
    "cpu": CpuSpec,
}


@dataclass
class ImportReport:
    manufacturers_created: int = 0
    families_created: int = 0
    families_adopted: list[str] = field(default_factory=list)
    models_created: int = 0
    models_seen: int = 0
    specs_written: int = 0
    aliases_created: int = 0
    aliases_adopted: int = 0
    unreconciled_families: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "manufacturers_created": self.manufacturers_created,
            "families_created": self.families_created,
            "families_adopted": list(self.families_adopted),
            "models_created": self.models_created,
            "models_seen": self.models_seen,
            "specs_written": self.specs_written,
            "aliases_created": self.aliases_created,
            "aliases_adopted": self.aliases_adopted,
            "unreconciled_families": list(self.unreconciled_families),
        }


class ImportConflictError(Exception):
    """Alias-key conflicts detected — the import wrote NOTHING (fail into review)."""

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} alias conflict(s); import aborted")


class UnratifiedProvenanceError(ImportConflictError):
    """A non-first-party document was offered — the import wrote NOTHING.

    Subclasses ImportConflictError so refresh and the import command report it
    through the same fail-into-review path instead of crashing the monthly job."""


def _unratified_provenance(docs: Sequence[SeedDocument]) -> list[str]:
    return [
        f"{doc.manufacturer_key}/{doc.family_name}: {doc.provenance.source_kind} provenance "
        "has no ratified retention class (non-first-party rows would be `manual`)"
        for doc in docs
        if not doc.provenance.is_first_party
    ]


def _seed_targets(docs: Sequence[SeedDocument]) -> dict[str, tuple[str, str]]:
    """normalized alias key → (manufacturer_key, normalized model number)."""

    targets: dict[str, tuple[str, str]] = {}
    for doc in docs:
        for model in doc.models:
            for alias in model.aliases:
                targets[alias.normalized] = (
                    doc.manufacturer_key,
                    normalize_alias_text(model.model_number),
                )
    return targets


def _existing_target(row: ProductAlias) -> tuple[str, str] | None:
    model = row.product_model
    if model is None:
        return None  # family/variant-grain alias: always a conflict for a seed key
    return (model.manufacturer.normalized_name, model.normalized_model_number)


def _db_conflicts(docs: Sequence[SeedDocument]) -> list[str]:
    targets = _seed_targets(docs)
    rows = ProductAlias.objects.filter(
        normalized_alias_text__in=list(targets), source_site__isnull=True
    ).select_related("product_model__manufacturer", "product_family__manufacturer")
    conflicts: list[str] = []
    for row in rows:
        seed_target = targets[row.normalized_alias_text]
        existing = _existing_target(row)
        if existing == seed_target:
            continue  # same target → adoption path, not a conflict
        existing_key = existing[0] if existing else "non-model-grain"
        equivalence = (
            "brand-equivalent"
            if existing and brands_consistent(seed_target[0], existing[0])
            else "cross-brand"
        )
        conflicts.append(
            f"[{row.alias_type}] {row.normalized_alias_text!r} → "
            f"seed {seed_target[0]}/{seed_target[1]} vs existing {existing_key}"
            f"/{existing[1] if existing else row.pk} ({equivalence})"
        )
    return conflicts


def import_documents(docs: Sequence[SeedDocument]) -> ImportReport:
    unratified = _unratified_provenance(docs)
    if unratified:
        raise UnratifiedProvenanceError(unratified)
    conflicts = [c.describe() for c in detect_conflicts(docs)]
    conflicts += _db_conflicts(docs)
    if conflicts:
        raise ImportConflictError(conflicts)
    report = ImportReport()
    with transaction.atomic():
        for doc in docs:
            _import_document(doc, report)
        report.unreconciled_families = _unreconciled_families(
            sorted({doc.manufacturer_key for doc in docs})
        )
    return report


def _import_document(doc: SeedDocument, report: ImportReport) -> None:
    manufacturer, created = Manufacturer.objects.get_or_create(
        normalized_name=doc.manufacturer_key, defaults={"name": doc.manufacturer_name}
    )
    if created:
        report.manufacturers_created += 1
    elif manufacturer.name != doc.manufacturer_name:
        manufacturer.name = doc.manufacturer_name  # seed display name is authoritative
        manufacturer.save(update_fields=["name", "updated_at"])
    # Every seedable category row is created by migration (drive 0001, the rest
    # 0019); the default only refills a deleted row, and for drive it is the
    # same "Drive" the pre-MS-2 importer used.
    category, _ = Category.objects.get_or_create(
        slug=doc.category, defaults={"name": doc.category.title()}
    )
    family, created = ProductFamily.objects.get_or_create(
        manufacturer=manufacturer,
        normalized_name=canonicalize_title(doc.family_name),
        defaults={"category": category, "name": doc.family_name},
    )
    if created:
        report.families_created += 1
    else:
        # Rung-2 provisional (or prior-seed) family adopted under its
        # authoritative datasheet name (carry-forward #4).
        report.families_adopted.append(family.normalized_name)
        if family.name != doc.family_name:
            family.name = doc.family_name
            family.save(update_fields=["name", "updated_at"])
    spec_model = _SPEC_MODELS[doc.category]
    for seed_model in doc.models:
        _import_model(manufacturer, family, spec_model, seed_model, report)


def _import_model(
    manufacturer: Manufacturer,
    family: ProductFamily,
    spec_model: type[DriveSpec | GpuSpec | RamSpec | CpuSpec],
    seed_model: SeedModel,
    report: ImportReport,
) -> None:
    report.models_seen += 1
    model, created = ProductModel.objects.get_or_create(
        manufacturer=manufacturer,
        normalized_model_number=normalize_alias_text(seed_model.model_number),
        defaults={
            "model_number": seed_model.model_number,
            "product_family": family,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
            "expires_at": None,
        },
    )
    if created:
        report.models_created += 1
    else:
        changed: list[str] = []
        if model.product_family_id != family.pk:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
            model.product_family = family
            changed.append("product_family")
        if model.retention_class != RetentionClass.MANUFACTURER_REFERENCE:  # pyright: ignore[reportUnnecessaryComparison] - TextChoices runtime-vs-static quirk, see resolver.py
            model.retention_class = RetentionClass.MANUFACTURER_REFERENCE
            model.expires_at = None
            changed += ["retention_class", "expires_at"]
        if changed:
            model.save(update_fields=[*changed, "updated_at"])
    # exclude_none means a re-import cannot RETRACT a typed spec value: if a
    # later seed corrects a field to "unknown" (None), the stale prior value
    # survives untouched. An explicit-clear mechanism is future work, only if
    # a real correction ever needs one.
    # `category` is the contract's union tag, not a satellite column.
    spec_defaults: dict[str, object] = seed_model.spec.model_dump(
        exclude_none=True, exclude={"category"}
    )
    spec_defaults["retention_class"] = RetentionClass.MANUFACTURER_REFERENCE
    spec_defaults["expires_at"] = None
    spec_model.objects.update_or_create(product_model=model, defaults=spec_defaults)
    report.specs_written += 1
    for alias in seed_model.aliases:
        _import_alias(model, alias, report)


def _import_alias(model: ProductModel, alias: SeedAlias, report: ImportReport) -> None:
    row = ProductAlias.objects.filter(
        alias_type=alias.alias_type,
        normalized_alias_text=alias.normalized,
        source_site__isnull=True,
    ).first()
    if row is None:
        ProductAlias.objects.create(
            alias_type=alias.alias_type,
            normalized_alias_text=alias.normalized,
            product_model=model,
            source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
            is_primary=alias.is_primary,
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
            expires_at=None,
        )
        report.aliases_created += 1
        return
    existing_model = row.product_model
    if existing_model is None or existing_model.pk != model.pk:
        # Pre-checked in _db_conflicts; unreachable unless a concurrent write
        # raced the check — abort rather than silently retarget (DR-009 posture).
        raise ImportConflictError(
            [f"[{row.alias_type}] {alias.normalized!r} raced to a different target"]
        )
    changed: list[str] = []
    if row.source_kind != AliasSourceKind.CATALOG_AUTHORITATIVE:  # pyright: ignore[reportUnnecessaryComparison] - TextChoices runtime-vs-static quirk, see resolver.py
        row.source_kind = AliasSourceKind.CATALOG_AUTHORITATIVE
        changed.append("source_kind")
    if row.is_primary != alias.is_primary:
        row.is_primary = alias.is_primary
        changed.append("is_primary")
    if row.retention_class != RetentionClass.MANUFACTURER_REFERENCE:  # pyright: ignore[reportUnnecessaryComparison] - TextChoices runtime-vs-static quirk, see resolver.py
        row.retention_class = RetentionClass.MANUFACTURER_REFERENCE
        row.expires_at = None
        changed += ["retention_class", "expires_at"]
    if changed:
        row.save(update_fields=[*changed, "last_seen"])
        report.aliases_adopted += 1


def _unreconciled_families(manufacturer_keys: list[str]) -> list[str]:
    """Families under seeded manufacturers with no models and no aliases: rung-2
    provisional rows the seed did not adopt. Reported for review, never touched.

    Known artifact, not a bug: the WD grammar decodes to the broad family
    'ultrastar', which seeds never adopt (seeds are per-HC-generation, e.g.
    'Ultrastar DC HC550'). Once any WD listing family-resolves to 'ultrastar',
    this list will PERMANENTLY include it on every future import."""

    return sorted(
        ProductFamily.objects.filter(manufacturer__normalized_name__in=manufacturer_keys)
        .filter(models__isnull=True, aliases__isnull=True)
        .values_list("normalized_name", flat=True)
        .distinct()
    )
