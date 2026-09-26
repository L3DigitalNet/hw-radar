"""Match ladder rungs 0-2 + the hard-attribute contradiction veto (C.3.2) - pure.

The resolver feeds DB state in as plain data (PriorResolution / AliasHit /
HardAttrs); decide() does no I/O, so the golden verdict table runs without a
DB. Only rungs 0-2 auto-accept. The veto runs at EVERY rung - an exact alias
hit that contradicts extracted capacity goes to review, never into the price
history (ADR-0019: false merges poison the moat asymmetrically; missed matches
just queue). Likewise a brand contradiction never falls through to a weaker
rung: exact hits that all contradict the listing's brand go to review, and a
grammar decode whose vendor contradicts it never attaches at rung 2.

Confidence constants are OQ-provisional tunables; ADR-0016 settings-row
versions arrive with the rung-3/occurrence thresholds at MS-1c."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from hw_radar.matching.types import (
    DecodeResult,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    Provenance,
    TokenKind,
)

CONFIDENCE_BY_SOURCE_KIND: dict[str, float] = {
    "catalog_authoritative": 0.98,
    "manual": 0.95,
    "listing_derived": 0.85,
}
CONFIDENCE_BY_PROVENANCE: dict[Provenance, float] = {
    Provenance.VENDOR_OFFICIAL: 0.92,
    Provenance.CORROBORATED_COMMUNITY: 0.85,
    Provenance.INFERRED: 0.75,
}
OEM_FAMILY_FANOUT_CONFIDENCE = 0.8
# Relative tolerance absorbing decimal-vs-marketing rounding (16TB vs 16000GB),
# NOT 14-vs-16 mislabels.
CAPACITY_TOLERANCE = 0.01

# One corporate lineage: WD absorbed HGST; the Jan-2026 "Optimus" rebrand moved
# WD-branded SSD lines under the SanDisk name with unchanged MPNs (spec C.3.1).
# Equivalence is a GATE only — it never merges without an MPN/alias hit.
_BRAND_EQUIV: tuple[frozenset[str], ...] = (frozenset({"western_digital", "hgst", "sandisk"}),)


@dataclass(frozen=True)
class CategoryHardAttrs:
    """Base for a non-drive category's typed catalog-side payload (MS2-D-05).
    Each `matching.rules` module subclasses it; see types.CategoryAttributes for
    the listing-side twin and the isinstance-narrowing contract."""


@dataclass(frozen=True)
class HardAttrs:
    """Catalog-side veto fields (from drive_spec / family agreement set).
    None = unknown on the catalog side → that field cannot veto.

    The five drive fields are read only by `contradictions`; a non-drive
    category leaves them None and carries its satellite payload in `category`,
    which only that category's veto reads."""

    capacity_bytes: int | None = None
    interface: str | None = None
    form_factor: str | None = None
    sector_format: str | None = None
    security: str | None = None
    category: CategoryHardAttrs | None = None


@dataclass(frozen=True)
class TargetRef:
    """Ladder-side identity reference. family_id is populated even for
    model/variant grains (enables the OEM family collapse); family_key names a
    not-yet-materialized provisional family for rung 2 — the resolver
    get_or_creates it (vendor, family_name). category_slug is never set by the
    ladder: the resolver stamps the dispatch category onto every target, so a
    provisional family is created under the category it was matched in and
    materialization applies that category's variant_on_demand setting."""

    grain: Grain
    family_id: int | None = None
    model_id: int | None = None
    variant_id: int | None = None
    family_key: tuple[str, str] | None = None
    category_slug: str | None = None


@dataclass(frozen=True)
class AliasHit:
    target: TargetRef
    source_kind: str
    alias_type: str
    brand: str | None
    hard_attrs: HardAttrs
    # The candidate that produced this hit (Codex CR-003): rung-1 auto-accept is
    # 'brand + full normalized MPN' (C.3.2), so the ladder needs to know what
    # KIND of token collided and what vendor its shape implies.
    candidate_kind: TokenKind = TokenKind.MANUFACTURER_MPN
    candidate_vendor: str = ""
    candidate_structured: bool = False


@dataclass(frozen=True)
class PriorResolution:
    target: TargetRef
    confidence: float
    hard_attrs: HardAttrs


class Outcome(StrEnum):
    ACCEPT = "accept"
    REVIEW = "review"
    NONE = "none"


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    grain: Grain
    rung: int | None = None
    method: str = ""
    target: TargetRef | None = None
    confidence: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    # The rung-1 hit an ACCEPT rests on (the highest-confidence single-target
    # hit, or the best hit of an OEM family fan-out). Not evidence: the resolver
    # reads its source_kind for the MS2-D-21 acceptance policy, and keeping it
    # out of `evidence` leaves every persisted drive edge byte-identical.
    winning_hit: AliasHit | None = None


def contradictions(extracted: ExtractedAttributes, catalog: HardAttrs) -> list[str]:
    """Hard-attribute veto (C.3.2): fields where BOTH sides are known and disagree."""

    vetoed: list[str] = []
    if extracted.capacity_bytes is not None and catalog.capacity_bytes is not None:
        a, b = extracted.capacity_bytes.value, catalog.capacity_bytes
        if abs(a - b) > CAPACITY_TOLERANCE * max(a, b):
            vetoed.append("capacity")
    for name in ("interface", "form_factor", "sector_format", "security"):
        extracted_attr = getattr(extracted, name)
        catalog_value = getattr(catalog, name)
        if (
            extracted_attr is not None
            and catalog_value is not None
            and extracted_attr.value != catalog_value
        ):
            vetoed.append(name)
    return vetoed


# A category's hard-attribute veto: the extracted-side fields a catalog target
# must not contradict. Drive's is `contradictions`; the resolver injects the one
# from the dispatch category's rules (MS2-D-02). The rung-2 decoder-capacity
# check below is NOT a Veto — it compares against the decode, not the catalog.
type Veto = Callable[[ExtractedAttributes, HardAttrs], list[str]]


def brands_consistent(extracted_brand: str | None, target_brand: str | None) -> bool:
    if not extracted_brand or not target_brand or extracted_brand == target_brand:
        return True
    return any(extracted_brand in group and target_brand in group for group in _BRAND_EQUIV)


def _hypothesis(candidates: Sequence[MpnCandidate]) -> str:
    for candidate in candidates:
        if candidate.kind is TokenKind.MANUFACTURER_MPN:
            return candidate.normalized
    return candidates[0].normalized if candidates else ""


def decide(
    extracted: ExtractedAttributes,
    candidates: Sequence[MpnCandidate],
    prior: PriorResolution | None,
    alias_hits: Sequence[AliasHit],
    decoded: DecodeResult | None,
    *,
    veto: Veto = contradictions,
) -> Verdict:
    evidence: dict[str, object] = {"mpn_hypothesis": _hypothesis(candidates)}
    if decoded is not None:
        evidence["vendor_hint"] = decoded.vendor

    # Rung 0 — re-observation: inherit after RE-RUNNING the veto (survives
    # relist/edit abuse — C.3.2 requires the check on every re-observation).
    if prior is not None:
        vetoed = veto(extracted, prior.hard_attrs)
        if vetoed:
            return Verdict(
                Outcome.REVIEW, Grain.NONE, rung=0, evidence={**evidence, "veto": vetoed}
            )
        return Verdict(
            Outcome.ACCEPT,
            prior.target.grain,
            rung=0,
            method="source_alias",
            target=prior.target,
            confidence=prior.confidence,
            evidence=evidence,
        )

    # Rung 1 — exact alias against grain-tagged product_alias. The C.3.2 trigger
    # is 'brand + full normalized MPN': a bare text collision is NOT enough
    # (Codex CR-003). Brand evidence = extracted brand, OR a vendor-shaped MPN
    # token whose implied vendor agrees with the target, OR a structured-field
    # MPN (merchant-asserted). Absent all three → review, never auto-accept.
    brand = extracted.brand.value if extracted.brand is not None else None
    viable = [h for h in alias_hits if brands_consistent(brand, h.brand)]
    if alias_hits and not viable:
        # Every exact hit names a brand the listing contradicts. That is a
        # conflict, not 'no catalog hit': falling through would let rung 2
        # accept the SAME token's grammar family without ever checking the
        # exact model's hard attributes (e.g. 'Toshiba ST12000NE0008 SAS'
        # attaching to Seagate IronWolf Pro, whose exact model is SATA).
        return Verdict(
            Outcome.REVIEW,
            Grain.NONE,
            rung=1,
            evidence={
                **evidence,
                "brand_contradicts_exact_alias": {
                    "brand": brand,
                    "alias_brands": sorted({h.brand for h in alias_hits if h.brand}),
                },
            },
        )
    if viable:

        def has_brand_evidence(hit: AliasHit) -> bool:
            if brand is not None:
                return True  # extracted brand, already filtered consistent
            if hit.candidate_structured:
                return True
            return bool(hit.candidate_vendor) and brands_consistent(hit.candidate_vendor, hit.brand)

        targets = {
            (h.target.grain, h.target.family_id, h.target.model_id, h.target.variant_id)
            for h in viable
        }
        if len(targets) == 1:
            best = max(viable, key=lambda h: CONFIDENCE_BY_SOURCE_KIND.get(h.source_kind, 0.5))
            vetoed = veto(extracted, best.hard_attrs)
            if vetoed:
                return Verdict(
                    Outcome.REVIEW, Grain.NONE, rung=1, evidence={**evidence, "veto": vetoed}
                )
            if not has_brand_evidence(best):
                return Verdict(
                    Outcome.REVIEW,
                    Grain.NONE,
                    rung=1,
                    evidence={**evidence, "no_brand_evidence": True},
                )
            return Verdict(
                Outcome.ACCEPT,
                best.target.grain,
                rung=1,
                method="exact_alias",
                target=best.target,
                confidence=CONFIDENCE_BY_SOURCE_KIND.get(best.source_kind, 0.5),
                evidence=evidence,
                winning_hit=best,
            )
        families = {h.target.family_id for h in viable}
        if (
            len(families) == 1
            and None not in families
            and all(h.alias_type == "oem_pn" for h in viable)
        ):
            # OEM N:N fan-out inside one family → attach at family grain (the
            # OEM cross-reference verdict: an OEM PN can never assert a model).
            clean = [h for h in viable if not veto(extracted, h.hard_attrs)]
            if clean:
                family_id = next(iter(families))
                return Verdict(
                    Outcome.ACCEPT,
                    Grain.FAMILY,
                    rung=1,
                    method="exact_alias",
                    target=TargetRef(grain=Grain.FAMILY, family_id=family_id),
                    confidence=OEM_FAMILY_FANOUT_CONFIDENCE,
                    evidence={**evidence, "oem_fanout": len(viable)},
                    winning_hit=max(
                        clean, key=lambda h: CONFIDENCE_BY_SOURCE_KIND.get(h.source_kind, 0.5)
                    ),
                )
        return Verdict(
            Outcome.REVIEW,
            Grain.NONE,
            rung=1,
            evidence={**evidence, "conflicting_targets": len(targets)},
        )

    # Rung 2 — valid grammar decode, no catalog hit → family grain, provisional.
    if decoded is not None and decoded.family_name:
        if not brands_consistent(brand, decoded.vendor):
            # A grammar decode only proves the token is SHAPED like the vendor's
            # MPN; an explicit contrary brand in the title means the shape is
            # coincidental or the listing is mislabeled — either way not an
            # automatic family attach.
            return Verdict(
                Outcome.REVIEW,
                Grain.NONE,
                rung=2,
                evidence={
                    **evidence,
                    "brand_contradicts_decode": {"brand": brand, "vendor": decoded.vendor},
                },
            )
        if (
            extracted.capacity_bytes is not None
            and decoded.capacity_bytes is not None
            and abs(extracted.capacity_bytes.value - decoded.capacity_bytes)
            > CAPACITY_TOLERANCE * max(extracted.capacity_bytes.value, decoded.capacity_bytes)
        ):
            return Verdict(
                Outcome.REVIEW,
                Grain.NONE,
                rung=2,
                evidence={
                    **evidence,
                    "veto": ["capacity"],
                    "decoder_capacity": decoded.capacity_bytes,
                },
            )
        return Verdict(
            Outcome.ACCEPT,
            Grain.FAMILY,
            rung=2,
            method="mpn_decode",
            target=TargetRef(grain=Grain.FAMILY, family_key=(decoded.vendor, decoded.family_name)),
            confidence=CONFIDENCE_BY_PROVENANCE[decoded.provenance],
            evidence={
                **evidence,
                "provisional": True,
                "provenance": decoded.provenance,
                "rule": decoded.rule,
            },
        )

    return Verdict(Outcome.NONE, Grain.NONE, evidence=evidence)
