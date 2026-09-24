"""Seed-document schema (plan decision D1): one product family per JSON document,
hand-curated from datasheets/manuals whose URLs + retrieval dates ride the
provenance block. Typed spec fields are populated only when the source states
them — None means UNKNOWN, never guessed (suitability rule).
Pure: no Django imports; persist.py owns the ORM. Alias join keys are
matching.normalize.normalize_alias_text — the ADR-0019 single-normalizer
invariant; never fork a second normalizer.

Categories (MS2-D-06, plan B4a). A document declares its `category`; absent
means `drive`, so every pre-MS-2 drive document validates unchanged. Each
SeedModel.spec is a tagged union over the category satellites, and the tag must
equal the document's category. An untagged spec inherits the document's
category, so authors only write the tag to be explicit; a spec whose fields
belong to another category then fails the variant's extra="forbid".

Non-drive rows carry their own source_url + retrieved_on: IR-007 provenance for
GPU/RAM/CPU is per row, because one family document is typically assembled from
several product pages and the per-document block cannot say which page stated
which row.

Authority (MS2-D-06). Only first-party provenance maps to alias
source_kind=catalog_authoritative; `non_first_party` maps to `manual`. The
MS2-D-21 acceptance policy reads that alias source_kind, so this mapping is what
keeps a reseller or community claim from ever auto-accepting."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from hw_radar.matching.normalize import normalize_alias_text

SEED_SCHEMA = "hw-radar.refdata.seed/v1"

_MANUFACTURER_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

# Slugs with a spec satellite; must stay equal to persist._SPEC_MODELS' keys and
# name Category rows seeded by catalog migrations 0001/0019. Basic-watch
# categories (nic, hba, motherboard, server) have no satellite and no seeds.
SeedCategory = Literal["drive", "gpu", "ram", "cpu"]

FirstPartySourceKind = Literal["first_party_datasheet", "first_party_manual", "first_party_page"]
FIRST_PARTY_SOURCE_KINDS: frozenset[str] = frozenset(
    ("first_party_datasheet", "first_party_manual", "first_party_page")
)
# Values are AliasSourceKind members; kept as literals because this module is
# Django-free.
AliasAuthority = Literal["catalog_authoritative", "manual"]

# PositiveSmallIntegerField's Postgres range; bounding here turns an
# out-of-range seed value into a validation error instead of a DB error mid-import.
_SMALL_INT_MAX = 32767


class SeedSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    title: str
    retrieved: date


class SeedProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_kind: FirstPartySourceKind | Literal["non_first_party"]
    sources: tuple[SeedSource, ...] = Field(min_length=1)

    @property
    def is_first_party(self) -> bool:
        return self.source_kind in FIRST_PARTY_SOURCE_KINDS

    @property
    def alias_source_kind(self) -> AliasAuthority:
        """The alias source_kind every row of this document earns (MS2-D-06)."""
        return "catalog_authoritative" if self.is_first_party else "manual"


class SeedAlias(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # oem_pn and house SKUs are LISTING-DERIVED aliases (ADR-0019 rule 7);
    # the catalog never seeds them — hence the narrow Literal.
    alias_type: Literal["mpn", "retail_pn", "region_pn"]
    text: str = Field(min_length=1)
    is_primary: bool = False

    @property
    def normalized(self) -> str:
        return normalize_alias_text(self.text)


class SeedDriveSpec(BaseModel):
    """Field names mirror catalog.models.DriveSpec verbatim so persist.py can
    model_dump(exclude_none=True) straight into update_or_create defaults.
    The same holds for every Seed*Spec below and its satellite; `category` is
    the union tag only and persist excludes it from the dump."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["drive"] = "drive"
    media_type: Literal["hdd", "ssd"]
    interface: str | None = None
    form_factor: str | None = None
    capacity_tb: Decimal | None = None
    rpm: int | None = None
    cache_mb: int | None = None
    recording_tech: Literal["cmr", "smr_dm", "smr_hm"] | None = None
    sector_format: Literal["512n", "512e", "4kn"] | None = None
    sed: bool | None = None
    workload_tb_year: int | None = None
    market_tier: str = ""
    model_family: str = ""
    spec_json: dict[str, object] = Field(default_factory=dict)


class SeedGpuSpec(BaseModel):
    """Mirrors catalog.models.GpuSpec. Chip-level facts only (MS2-D-06): a
    board partner's page is not a source for these fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["gpu"] = "gpu"
    chip_vendor: Literal["nvidia", "amd", "intel", "other"] | None = None
    vram_gb: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)
    interface: Literal["pcie", "sxm", "oam", "mxm", "other"] | None = None
    cooling: Literal["active", "passive", "liquid"] | None = None
    tdp_w: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)


class SeedRamSpec(BaseModel):
    """Mirrors catalog.models.RamSpec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["ram"] = "ram"
    generation: Literal["ddr3", "ddr4", "ddr5", "other"] | None = None
    module_type: Literal["udimm", "rdimm", "lrdimm", "sodimm", "other"] | None = None
    ecc: bool | None = None
    module_capacity_gb: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)
    modules_per_kit: int = Field(default=1, gt=0, le=_SMALL_INT_MAX)
    speed_mts: int | None = Field(default=None, gt=0)
    ranks: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)


class SeedCpuSpec(BaseModel):
    """Mirrors catalog.models.CpuSpec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["cpu"] = "cpu"
    # Lowercase token, the shape the category rules compare against; the rules
    # module owns the vocabulary itself (MS2-D-04: no DB enum).
    socket: str | None = Field(default=None, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    cores: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)
    tdp_w: int | None = Field(default=None, gt=0, le=_SMALL_INT_MAX)


def _spec_tag(value: object) -> str:
    # An untagged spec is a drive spec: that is what every pre-MS-2 document
    # contains. SeedDocument injects its own category into untagged specs
    # before this runs, so the fallback only matters for a bare SeedModel.
    if isinstance(value, dict):
        return str(cast("dict[str, object]", value).get("category", "drive"))
    return str(getattr(value, "category", "drive"))


SeedSpec = Annotated[
    Annotated[SeedDriveSpec, Tag("drive")]
    | Annotated[SeedGpuSpec, Tag("gpu")]
    | Annotated[SeedRamSpec, Tag("ram")]
    | Annotated[SeedCpuSpec, Tag("cpu")],
    Discriminator(_spec_tag),
]


class SeedModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_number: str = Field(min_length=1)
    spec: SeedSpec
    aliases: tuple[SeedAlias, ...] = Field(min_length=1)
    # Per-row IR-007 provenance; required for every non-drive row by
    # SeedDocument, optional (and so far unused) for drive rows.
    source_url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    retrieved_on: date | None = None

    @model_validator(mode="after")
    def _aliases_coherent(self) -> SeedModel:
        primaries = [a for a in self.aliases if a.is_primary]
        if len(primaries) != 1:
            msg = f"{self.model_number}: exactly one primary alias required"
            raise ValueError(msg)
        own_key = normalize_alias_text(self.model_number)
        if all(a.normalized != own_key for a in self.aliases):
            # Rung-1 joinability guarantee: the model number itself must be
            # an alias, or listings printing it can never exact-match.
            msg = f"{self.model_number}: no alias covers the model number"
            raise ValueError(msg)
        if (self.source_url is None) != (self.retrieved_on is None):
            msg = f"{self.model_number}: source_url and retrieved_on go together"
            raise ValueError(msg)
        return self


class SeedDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_id: Literal["hw-radar.refdata.seed/v1"] = Field(alias="schema")
    category: SeedCategory = "drive"
    manufacturer_name: str = Field(min_length=1)
    manufacturer_key: str
    family_name: str = Field(min_length=1)
    provenance: SeedProvenance
    models: tuple[SeedModel, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _tag_untagged_specs(cls, data: object) -> object:
        """Give each untagged spec the document's category (copying, never
        mutating the caller's input). Tagged specs are left alone so a
        mismatch surfaces in _document_coherent instead of being overwritten."""
        if not isinstance(data, dict):
            return data
        raw = cast("dict[str, object]", data)
        category = raw.get("category", "drive")
        models = raw.get("models")
        if not isinstance(models, list | tuple):
            return raw
        tagged: list[object] = []
        for item in cast("list[object]", models):
            if isinstance(item, dict):
                row = cast("dict[str, object]", item)
                spec = row.get("spec")
                if isinstance(spec, dict) and "category" not in spec:
                    row = {**row, "spec": {**cast("dict[str, object]", spec), "category": category}}
                tagged.append(row)
            else:
                tagged.append(item)
        return {**raw, "models": tagged}

    @model_validator(mode="after")
    def _document_coherent(self) -> SeedDocument:
        if not _MANUFACTURER_KEY.fullmatch(self.manufacturer_key):
            msg = f"manufacturer_key {self.manufacturer_key!r} is not a normalized key"
            raise ValueError(msg)
        for model in self.models:
            if model.spec.category != self.category:
                msg = (
                    f"{model.model_number}: {model.spec.category} spec in a "
                    f"{self.category} document"
                )
                raise ValueError(msg)
            if self.category != "drive" and (
                model.source_url is None or model.retrieved_on is None
            ):
                msg = f"{model.model_number}: non-drive rows need source_url and retrieved_on"
                raise ValueError(msg)
        return self


@dataclass(frozen=True)
class SeedConflict:
    """Two aliases normalize to one key but point at different targets — the
    rung-1 hit-aggregation carry-forward: fail into review, never pick one."""

    alias_type_set: tuple[str, ...]
    normalized_text: str
    targets: tuple[str, ...]  # "manufacturer_key/model_number", sorted

    def describe(self) -> str:
        types = ",".join(self.alias_type_set)
        targets = " vs ".join(self.targets)
        return f"[{types}] {self.normalized_text!r} → {targets}"


def detect_conflicts(docs: Sequence[SeedDocument]) -> tuple[SeedConflict, ...]:
    """Pure within/across-document conflict scan. DB-side conflicts (existing
    aliases, cross-manufacturer collisions) live in persist._db_conflicts."""

    targets_by_key: dict[str, set[str]] = {}
    types_by_key: dict[str, set[str]] = {}
    for doc in docs:
        for model in doc.models:
            target = f"{doc.manufacturer_key}/{model.model_number}"
            for alias in model.aliases:
                targets_by_key.setdefault(alias.normalized, set()).add(target)
                types_by_key.setdefault(alias.normalized, set()).add(alias.alias_type)
    return tuple(
        SeedConflict(
            alias_type_set=tuple(sorted(types_by_key[key])),
            normalized_text=key,
            targets=tuple(sorted(targets)),
        )
        for key, targets in sorted(targets_by_key.items())
        if len(targets) > 1
    )
