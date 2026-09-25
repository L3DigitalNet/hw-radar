"""Requirement service (MS-2 Slice C2, MS2-D-07): the single writer of a watch's
requirements.

Contract:
- A caller builds one per-category Pydantic `RequirementSpec`
  (`DriveRequirementSpec`, `GpuRequirementSpec`, `RamRequirementSpec`,
  `CpuRequirementSpec`, or `BasicRequirementSpec` for the basic-watch
  categories) and passes it to `save_requirement(watch, spec)`. Construction
  validates element vocabularies and bounds (pydantic `ValidationError`);
  `save_requirement` enforces the cross-table rules the database cannot CHECK
  and raises `RequirementError` for them:
  - the spec's category equals the watch's category (so the satellite written
    is always the watch's own category's satellite);
  - a basic-watch watch names exactly one target, and every target belongs to
    the watch's category.
- A spec is the COMPLETE requirement state, not a patch: every field the spec
  leaves at its default is written as that default.
- `requirement_version` is bumped exactly when a HARD input changes (the
  target, an offer clause, or a satellite field). A save that changes nothing
  writes nothing and does not bump, so re-saving an unchanged form never makes
  stored verdicts pending. A save that changes only the soft
  `target_unit_price_usd` writes it without a bump: the soft threshold never
  reaches the evaluator (MS2-D-07, risk R11), so bumping would force a
  pointless re-evaluation of every row.

Why the bump matters: `WatchEvaluation.requirement_version` must equal the
watch's for a verdict to count as current (MS2-D-20). A requirement write that
bypasses this module keeps the old version, and every stored verdict computed
under the old requirement would keep reading as current — a stale pass.

Normalized storage form: array requirements are stored deduplicated and
sorted, so a re-ordered form is a no-op save. "unknown" is never a legal
required value — the evaluator reads an "unknown" catalog value as missing
evidence, so requiring it could only ever produce `unknown`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from django.db import models, transaction
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hw_radar.catalog.models import (
    Condition,
    CpuRequirement,
    DriveRequirement,
    GpuChipVendor,
    GpuCooling,
    GpuInterface,
    GpuRequirement,
    MediaType,
    ProductFamily,
    ProductModel,
    RamGeneration,
    RamModuleType,
    RamRequirement,
    RecordingTech,
    Watch,
)
from hw_radar.matching.rules import basic
from hw_radar.matching.rules.cpu import socket_key

# Postgres ranges of the satellite columns; bounding here turns an out-of-range
# form value into a validation error instead of a DataError mid-transaction.
_SMALL_INT_MAX: Final = 32767
_INT_MAX: Final = 2147483647

# Drive interface and form-factor requirements are closed token vocabularies
# even though DriveSpec.interface/.form_factor are free text. Cross-file
# contract: these are exactly the tokens matching.resolver._hard_attrs_from_spec
# derives from the free-text spec columns and matching.vocab.extract emits from
# titles, and eligibility.evaluate derives the catalog side the same way — a
# token added in one place must be added in all of them.
DRIVE_INTERFACE_TOKENS: Final = ("nvme", "sas", "sata", "scsi", "usb")
DRIVE_FORM_FACTOR_TOKENS: Final = ("3.5", "2.5", "m.2")

FIRST_CLASS_CATEGORIES: Final = ("drive", "gpu", "ram", "cpu")
BASIC_CATEGORIES: Final = basic.BASIC_CATEGORIES


class RequirementError(ValueError):
    """A spec that is well-formed on its own but invalid for this watch."""


def _unique_sorted[T: str](values: tuple[T, ...], *, unknown: str | None = None) -> tuple[T, ...]:
    if unknown is not None and unknown in values:
        raise ValueError(f"{unknown!r} is not a requirable value")
    return tuple(sorted(set(values)))


class _RequirementSpecBase(BaseModel):
    """Target and offer clauses shared by every category."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_family_id: int | None = Field(default=None, gt=0)
    target_model_id: int | None = Field(default=None, gt=0)
    max_unit_price_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    # Empty = any condition.
    allowed_conditions: tuple[Condition, ...] = ()
    require_in_stock: bool = False
    allow_international: bool = True
    # Soft: annotates shortlist rows only, never decides a verdict.
    target_unit_price_usd: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )

    @field_validator("allowed_conditions")
    @classmethod
    def _conditions(cls, value: tuple[Condition, ...]) -> tuple[Condition, ...]:
        return _unique_sorted(value, unknown=Condition.UNKNOWN)

    @model_validator(mode="after")
    def _at_most_one_target(self) -> _RequirementSpecBase:
        if self.target_family_id is not None and self.target_model_id is not None:
            raise ValueError("a watch targets a family or a model, not both")
        return self


class DriveRequirementSpec(_RequirementSpecBase):
    category: Literal["drive"] = "drive"
    min_capacity_tb: Decimal | None = Field(default=None, gt=0, max_digits=7, decimal_places=3)
    media_types: tuple[MediaType, ...] = ()
    interfaces: tuple[str, ...] = ()
    form_factors: tuple[str, ...] = ()
    recording_techs: tuple[RecordingTech, ...] = ()

    @field_validator("media_types")
    @classmethod
    def _media(cls, value: tuple[MediaType, ...]) -> tuple[MediaType, ...]:
        return _unique_sorted(value, unknown=MediaType.UNKNOWN)

    @field_validator("recording_techs")
    @classmethod
    def _recording(cls, value: tuple[RecordingTech, ...]) -> tuple[RecordingTech, ...]:
        return _unique_sorted(value, unknown=RecordingTech.UNKNOWN)

    @field_validator("interfaces")
    @classmethod
    def _interfaces(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _tokens(value, DRIVE_INTERFACE_TOKENS)

    @field_validator("form_factors")
    @classmethod
    def _form_factors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _tokens(value, DRIVE_FORM_FACTOR_TOKENS)


def _tokens(value: tuple[str, ...], vocabulary: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(v.strip().casefold() for v in value)
    unknown = sorted(set(normalized) - set(vocabulary))
    if unknown:
        raise ValueError(f"unsupported values {unknown}; expected one of {list(vocabulary)}")
    return _unique_sorted(normalized)


class GpuRequirementSpec(_RequirementSpecBase):
    category: Literal["gpu"] = "gpu"
    min_vram_gb: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)
    chip_vendors: tuple[GpuChipVendor, ...] = ()
    interfaces: tuple[GpuInterface, ...] = ()
    coolings: tuple[GpuCooling, ...] = ()
    max_tdp_w: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)

    @field_validator("chip_vendors")
    @classmethod
    def _vendors(cls, value: tuple[GpuChipVendor, ...]) -> tuple[GpuChipVendor, ...]:
        return _unique_sorted(value, unknown=GpuChipVendor.UNKNOWN)

    @field_validator("interfaces")
    @classmethod
    def _interfaces(cls, value: tuple[GpuInterface, ...]) -> tuple[GpuInterface, ...]:
        return _unique_sorted(value, unknown=GpuInterface.UNKNOWN)

    @field_validator("coolings")
    @classmethod
    def _coolings(cls, value: tuple[GpuCooling, ...]) -> tuple[GpuCooling, ...]:
        return _unique_sorted(value, unknown=GpuCooling.UNKNOWN)


class RamRequirementSpec(_RequirementSpecBase):
    category: Literal["ram"] = "ram"
    generations: tuple[RamGeneration, ...] = ()
    module_types: tuple[RamModuleType, ...] = ()
    # None = no ECC constraint.
    require_ecc: bool | None = None
    # Kit total: modules_per_kit x module_capacity_gb.
    min_total_capacity_gb: int | None = Field(default=None, gt=0, le=_INT_MAX)
    min_speed_mts: int | None = Field(default=None, gt=0, le=_INT_MAX)

    @field_validator("generations")
    @classmethod
    def _generations(cls, value: tuple[RamGeneration, ...]) -> tuple[RamGeneration, ...]:
        return _unique_sorted(value, unknown=RamGeneration.UNKNOWN)

    @field_validator("module_types")
    @classmethod
    def _module_types(cls, value: tuple[RamModuleType, ...]) -> tuple[RamModuleType, ...]:
        return _unique_sorted(value, unknown=RamModuleType.UNKNOWN)


class CpuRequirementSpec(_RequirementSpecBase):
    category: Literal["cpu"] = "cpu"
    sockets: tuple[str, ...] = ()
    min_cores: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)
    max_tdp_w: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)

    @field_validator("sockets")
    @classmethod
    def _sockets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # socket_key is the one normalizer both sides of the CPU socket
        # comparison use (matching.rules.cpu); storing its output keeps the
        # evaluator's comparison a plain membership test.
        keys = tuple(socket_key(v) for v in value)
        if any(not k for k in keys):
            raise ValueError("a socket requirement must name a socket")
        return _unique_sorted(keys)


class BasicRequirementSpec(_RequirementSpecBase):
    """A basic-watch category (nic, hba, motherboard, server): exact identity
    only, so there are no product-attribute fields and a target is required."""

    category: str

    @field_validator("category")
    @classmethod
    def _basic_category(cls, value: str) -> str:
        if value not in BASIC_CATEGORIES:
            raise ValueError(f"{value!r} is not a basic-watch category")
        return value

    @model_validator(mode="after")
    def _target_required(self) -> BasicRequirementSpec:
        if self.target_family_id is None and self.target_model_id is None:
            raise ValueError("a basic-watch watch must name a target family or model")
        return self


type RequirementSpec = (
    DriveRequirementSpec
    | GpuRequirementSpec
    | RamRequirementSpec
    | CpuRequirementSpec
    | BasicRequirementSpec
)

type _Satellite = DriveRequirement | GpuRequirement | RamRequirement | CpuRequirement

# Satellite model and the spec fields it stores, per first-class category. Field
# names equal the satellite's column names (catalog/models/watch.py).
_SATELLITES: Final[dict[str, tuple[type[_Satellite], tuple[str, ...]]]] = {
    "drive": (
        DriveRequirement,
        ("min_capacity_tb", "media_types", "interfaces", "form_factors", "recording_techs"),
    ),
    "gpu": (
        GpuRequirement,
        ("min_vram_gb", "chip_vendors", "interfaces", "coolings", "max_tdp_w"),
    ),
    "ram": (
        RamRequirement,
        (
            "generations",
            "module_types",
            "require_ecc",
            "min_total_capacity_gb",
            "min_speed_mts",
        ),
    ),
    "cpu": (CpuRequirement, ("sockets", "min_cores", "max_tdp_w")),
}

_OFFER_FIELDS: Final = (
    "max_unit_price_usd",
    "allowed_conditions",
    "require_in_stock",
    "allow_international",
)


@dataclass(frozen=True)
class RequirementSaveResult:
    requirement_version: int
    # True when any stored value changed (hard or soft).
    changed: bool
    # True when requirement_version was bumped (a hard input changed).
    bumped: bool


def _comparable(value: object) -> object:
    """The storage form used for no-op detection: arrays as sorted tuples of
    plain strings, so enum members, lists and tuples of the same values compare
    equal, and a DB row written in another order still reads as unchanged."""
    if isinstance(value, list | tuple):
        return tuple(sorted(str(v) for v in value))  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] - elements are the satellite's str choices
    return value


def _stored(value: object) -> object:
    if isinstance(value, tuple):
        return [str(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] - elements are str choices
    return value


def _resolve_targets(
    spec: RequirementSpec, category_slug: str
) -> tuple[ProductFamily | None, ProductModel | None]:
    family: ProductFamily | None = None
    model: ProductModel | None = None
    if spec.target_family_id is not None:
        family = (
            ProductFamily.objects.select_related("category")
            .filter(pk=spec.target_family_id)
            .first()
        )
        if family is None:
            raise RequirementError(f"target family {spec.target_family_id} does not exist")
        if family.category.slug != category_slug:
            raise RequirementError(
                f"target family {family.pk} is in category {family.category.slug!r}, "
                f"not {category_slug!r}"
            )
    if spec.target_model_id is not None:
        model = (
            ProductModel.objects.select_related("product_family__category")
            .filter(pk=spec.target_model_id)
            .first()
        )
        if model is None:
            raise RequirementError(f"target model {spec.target_model_id} does not exist")
        # A model's category is only known through its family. A family-less
        # model cannot be proven to be in the watch's category, and a
        # cross-category target would never match anything the watch sees.
        if model.product_family is None:
            raise RequirementError(f"target model {model.pk} has no family; category unknown")
        if model.product_family.category.slug != category_slug:
            raise RequirementError(
                f"target model {model.pk} is in category "
                f"{model.product_family.category.slug!r}, not {category_slug!r}"
            )
    return family, model


def _pk(obj: models.Model | None) -> object:
    return None if obj is None else obj.pk


@transaction.atomic
def save_requirement(watch: Watch, spec: RequirementSpec) -> RequirementSaveResult:
    """Write `spec` as `watch`'s complete requirement state.

    Raises RequirementError for a category mismatch, a missing basic-watch
    target, or a target outside the watch's category; nothing is written then.
    The watch row is locked for the whole read-compare-write, so two concurrent
    saves cannot both read version N and both write N+1. `watch` is refreshed
    from the database before returning.
    """
    locked = Watch.objects.select_for_update().select_related("category").get(pk=watch.pk)
    slug = locked.category.slug
    if spec.category != slug:
        raise RequirementError(
            f"spec category {spec.category!r} does not match watch category {slug!r}"
        )
    if slug not in _SATELLITES and slug not in BASIC_CATEGORIES:
        raise RequirementError(f"category {slug!r} has no requirement support")
    family, model = _resolve_targets(spec, slug)

    new_hard: dict[str, object] = {
        "target_family": _pk(family),
        "target_model": _pk(model),
        **{name: _comparable(getattr(spec, name)) for name in _OFFER_FIELDS},
    }
    old_hard: dict[str, object] = {
        "target_family": _pk(locked.target_family),
        "target_model": _pk(locked.target_model),
        **{name: _comparable(getattr(locked, name)) for name in _OFFER_FIELDS},
    }
    satellite_values: dict[str, object] = {}
    satellite_model: type[_Satellite] | None = None
    if slug in _SATELLITES:
        satellite_model, fields = _SATELLITES[slug]
        satellite_values = {name: getattr(spec, name) for name in fields}
        existing = satellite_model.objects.filter(pk=locked.pk).first()
        new_hard["satellite"] = {n: _comparable(v) for n, v in satellite_values.items()}
        # A missing satellite compares unequal to any spec, including an
        # all-defaults one, so the first save always creates it.
        old_hard["satellite"] = (
            None if existing is None else {n: _comparable(getattr(existing, n)) for n in fields}
        )

    hard_changed = new_hard != old_hard
    soft_changed = spec.target_unit_price_usd != locked.target_unit_price_usd
    if not hard_changed and not soft_changed:
        watch.refresh_from_db()
        return RequirementSaveResult(
            requirement_version=locked.requirement_version, changed=False, bumped=False
        )

    locked.target_family = family
    locked.target_model = model
    for name in _OFFER_FIELDS:
        setattr(locked, name, _stored(getattr(spec, name)))
    locked.target_unit_price_usd = spec.target_unit_price_usd
    if hard_changed:
        locked.requirement_version += 1
    locked.save(
        update_fields=[
            "target_family",
            "target_model",
            *_OFFER_FIELDS,
            "target_unit_price_usd",
            "requirement_version",
            "updated_at",
        ]
    )
    if satellite_model is not None:
        satellite_model.objects.update_or_create(
            watch=locked,
            defaults={name: _stored(value) for name, value in satellite_values.items()},
        )
    watch.refresh_from_db()
    return RequirementSaveResult(
        requirement_version=locked.requirement_version, changed=True, bumped=hard_changed
    )
