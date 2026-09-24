"""Shared value types for the matching layers (N1-N4) and the ladder.

Pure data, frozen: layers exchange these; only resolver.py materializes them
against the DB. Every extracted attribute carries per-attribute confidence and
the producing layer, so match evidence stays explainable (DR-004 applied to
identity, ADR-0019 rule 2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Grain(StrEnum):
    NONE = "none"
    FAMILY = "family"
    MODEL = "model"
    VARIANT = "variant"


GRAIN_ORDER: dict[Grain, int] = {
    Grain.NONE: 0,
    Grain.FAMILY: 1,
    Grain.MODEL: 2,
    Grain.VARIANT: 3,
}


class Provenance(StrEnum):
    """Grammar-rule authority tiers (ADR-0019 rule 3); flow into match confidence."""

    VENDOR_OFFICIAL = "vendor_official"
    CORROBORATED_COMMUNITY = "corroborated_community"
    INFERRED = "inferred"


@dataclass(frozen=True)
class Attribute[T]:
    value: T
    confidence: float
    layer: str
    source_text: str = ""


@dataclass(frozen=True)
class CategoryAttributes:
    """Base for a non-drive category's typed listing-side payload (MS2-D-05).

    Each `matching.rules` module subclasses it; the category's veto narrows with
    isinstance, so a payload from another category reads as all-unknown and can
    never veto (or pass) a comparison it was not built for."""


@dataclass(frozen=True)
class ExtractedAttributes:
    """N2 output. None means UNKNOWN — never guessed (suitability-research rule).

    condition/recert_channel/packaging/warranty_channel values are the
    catalog TextChoices literals (identity.Condition et al.) so the resolver
    can pass them straight into variant-on-demand creation."""

    capacity_bytes: Attribute[int] | None = None
    interface: Attribute[str] | None = None  # sata | sas | nvme | scsi | usb
    link_speed_gbps: Attribute[float] | None = None
    form_factor: Attribute[str] | None = None  # 3.5 | 2.5 | m.2
    rpm: Attribute[int] | None = None
    cache_mb: Attribute[int] | None = None
    sector_format: Attribute[str] | None = None  # 512n | 512e | 4kn
    recording_tech: Attribute[str] | None = None  # cmr | smr
    security: Attribute[str] | None = None  # sed | fips | ise
    condition: Attribute[str] | None = None
    recert_channel: Attribute[str] | None = None  # factory | seller
    packaging: Attribute[str] | None = None  # retail | bulk
    warranty_months: Attribute[int] | None = None
    warranty_channel: Attribute[str] | None = None  # manufacturer | seller | none
    quantity: Attribute[int] | None = None
    brand: Attribute[str] | None = None  # canonical brand key (see plan Interfaces)
    # Non-drive categories only; the drive fields above stay the drive contract
    # and vocab.extract never sets this.
    category_attrs: CategoryAttributes | None = None


class TokenKind(StrEnum):
    MANUFACTURER_MPN = "manufacturer_mpn"
    OEM_PN = "oem_pn"
    HOUSE_SKU = "house_sku"
    UNKNOWN_CODE = "unknown_code"


@dataclass(frozen=True)
class MpnCandidate:
    raw: str
    normalized: str  # normalize_alias_text(raw) — the product_alias join key
    kind: TokenKind
    vendor_hint: str = (
        ""  # seagate|western_digital|toshiba|samsung|dell_emc|hpe|netapp|lenovo_ibm|""
    )
    confidence: float = 0.5
    from_structured_field: bool = False


@dataclass(frozen=True)
class DecodeResult:
    """N4 output. Family/capacity/generation ONLY (ADR-0019 rule 3): variant
    semantics come from the catalog, never the code string."""

    vendor: str
    family_name: str | None
    capacity_bytes: int | None
    generation: str | None
    provenance: Provenance
    rule: str  # which grammar rule fired — goes into edge evidence
