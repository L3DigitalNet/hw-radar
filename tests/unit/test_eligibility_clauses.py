"""Per-clause tri-state tables (MS2-D-08), pure: no database.

Pins the evidence-tier rules that make eligibility conservative:
- only catalog-tier evidence can produce `match`;
- a confident listing-tier (title) contradiction produces `no_match`;
- listing-tier agreement never produces `match`;
- a review/none resolution supplies no catalog evidence, so product clauses are
  `unknown`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hw_radar.catalog.models import EligibilityVerdict, GpuRequirement, StockStatus
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.evaluate import (
    CatalogField,
    CatalogInputs,
    CategoryPolicy,
    EvidenceTier,
    MemberSpec,
    OfferFacts,
    Scalar,
    catalog_fingerprint,
    condition_clause,
    international_clause,
    price_clause,
    product_clauses,
    product_outcome,
    stock_clause,
    target_clause,
)
from hw_radar.matching.rules.gpu import GpuAttributes
from hw_radar.matching.types import Attribute, ExtractedAttributes

M, N, U = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH, EligibilityVerdict.UNKNOWN
POLICY = CategoryPolicy()
_OBSERVED = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _attr[V](value: V, confidence: float = 0.9) -> Attribute[V]:
    return Attribute(value=value, confidence=confidence, layer="test", source_text=str(value))


# ── product_outcome table ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("catalog_ok", "listing_contradicts", "expected", "tier"),
    [
        (True, False, M, EvidenceTier.CATALOG),
        (False, False, N, EvidenceTier.CATALOG),
        (None, False, U, EvidenceTier.NONE),
        (None, True, N, EvidenceTier.LISTING),
        (False, True, N, EvidenceTier.CATALOG),
        # Tier conflict: the default policy follows MS2-D-08's literal rule.
        (True, True, N, EvidenceTier.LISTING),
    ],
)
def test_product_outcome_table(
    catalog_ok: bool | None,
    listing_contradicts: bool,
    expected: EligibilityVerdict,
    tier: EvidenceTier,
) -> None:
    assert product_outcome(catalog_ok, listing_contradicts, POLICY) == (expected, tier)


def test_tier_conflict_outcome_is_a_per_category_policy() -> None:
    lenient = CategoryPolicy(on_tier_conflict=U)
    assert product_outcome(True, True, lenient) == (U, EvidenceTier.LISTING)


def test_every_first_class_category_has_an_explicit_policy() -> None:
    assert set(evaluate.CATEGORY_POLICIES) == {"drive", "gpu", "ram", "cpu"}
    for policy in evaluate.CATEGORY_POLICIES.values():
        assert policy.on_tier_conflict in (N, U)


# ── attribute_clause ─────────────────────────────────────────────────────────


def _vram(catalog: int | None, listing: Attribute[int] | None, minimum: int | None = 40):
    return evaluate.attribute_clause(
        "gpu.min_vram_gb",
        minimum,
        None if minimum is None else (lambda v: v >= minimum),
        CatalogField(catalog, "model spec"),
        listing,
        POLICY,
    )


def test_listing_tier_contradiction_is_no_match() -> None:
    result = _vram(None, _attr(24))
    assert result.outcome is N
    assert result.evidence_tier is EvidenceTier.LISTING


def test_listing_tier_agreement_is_not_match() -> None:
    result = _vram(None, _attr(80))
    assert result.outcome is U
    assert result.evidence_tier is EvidenceTier.NONE


def test_low_confidence_listing_contradiction_is_ignored() -> None:
    assert _vram(None, _attr(24, confidence=0.5)).outcome is U


def test_catalog_tier_decides_match_and_no_match() -> None:
    assert _vram(80, None).outcome is M
    assert _vram(24, None).outcome is N


def test_confident_title_overrides_catalog_match_as_tier_conflict() -> None:
    result = _vram(80, _attr(24))
    assert result.outcome is N
    assert "tier conflict" in result.detail


def test_unconstrained_clause_is_match_without_evidence() -> None:
    result = _vram(None, None, minimum=None)
    assert result.outcome is M
    assert result.evidence_tier is EvidenceTier.NONE


def test_reason_shape_carries_the_mandatory_keys() -> None:
    reason = _vram(80, _attr(80)).as_reason()
    assert set(reason) == {
        "clause",
        "outcome",
        "evidence_tier",
        "required",
        "observed",
        "detail",
        "source",
    }
    assert reason["observed"] == {
        "catalog": 80,
        "catalog_source": "model spec",
        "listing": {"value": 80, "confidence": 0.9, "source_text": "80"},
    }


# ── product clauses over CatalogInputs ───────────────────────────────────────


def _inputs(grain: str, *specs: Mapping[str, Scalar] | None) -> CatalogInputs:
    members = tuple(MemberSpec(model_id=i + 1, spec=s) for i, s in enumerate(specs))
    model_id = 1 if grain in ("model", "variant") else None
    family_id = 7 if grain != "none" else None
    return CatalogInputs("gpu", grain, family_id, model_id, None, members)


_A100: dict[str, Scalar] = {
    "chip_vendor": "nvidia",
    "vram_gb": 80,
    "interface": "pcie",
    "cooling": "passive",
    "tdp_w": 300,
}


def _gpu_requirement(**kwargs: object) -> GpuRequirement:
    return GpuRequirement(**kwargs)


def _gpu_outcomes(
    inputs: CatalogInputs, title: GpuAttributes | None = None, **req: object
) -> dict[str, EligibilityVerdict]:
    extracted = ExtractedAttributes(category_attrs=title or GpuAttributes())
    results = product_clauses(_gpu_requirement(**req), "gpu", inputs, extracted, POLICY, {})
    return {r.clause: r.outcome for r in results}


def test_review_or_unresolved_resolution_makes_product_clauses_unknown() -> None:
    # A review edge and a none edge both reach the evaluator as grain "none":
    # neither supplies catalog evidence.
    outcomes = _gpu_outcomes(_inputs("none"), min_vram_gb=40, coolings=["passive"])
    assert outcomes["gpu.min_vram_gb"] is U
    assert outcomes["gpu.coolings"] is U


def test_review_resolution_with_agreeing_title_is_still_unknown() -> None:
    title = GpuAttributes(vram_gb=_attr(80), cooling=_attr("passive"))
    outcomes = _gpu_outcomes(_inputs("none"), title, min_vram_gb=40, coolings=["passive"])
    assert set(outcomes.values()) == {U, M}  # constrained unknown, unconstrained match
    assert outcomes["gpu.min_vram_gb"] is U


def test_review_resolution_with_contradicting_title_is_no_match() -> None:
    title = GpuAttributes(cooling=_attr("active"))
    outcomes = _gpu_outcomes(_inputs("none"), title, coolings=["passive"])
    assert outcomes["gpu.coolings"] is N


def test_model_grain_spec_decides() -> None:
    outcomes = _gpu_outcomes(
        _inputs("model", _A100),
        min_vram_gb=40,
        chip_vendors=["nvidia"],
        interfaces=["pcie"],
        coolings=["passive"],
        max_tdp_w=300,
    )
    assert set(outcomes.values()) == {M}
    assert _gpu_outcomes(_inputs("model", _A100), max_tdp_w=250)["gpu.max_tdp_w"] is N


def test_absent_spec_row_is_unknown() -> None:
    assert _gpu_outcomes(_inputs("model", None), min_vram_gb=40)["gpu.min_vram_gb"] is U


def test_unstated_spec_value_is_unknown() -> None:
    spec = {**_A100, "cooling": None}
    assert _gpu_outcomes(_inputs("model", spec), coolings=["passive"])["gpu.coolings"] is U


def test_family_agreement_set() -> None:
    agree = _inputs("family", _A100, {**_A100, "tdp_w": 350})
    outcomes = _gpu_outcomes(agree, coolings=["passive"], max_tdp_w=400)
    assert outcomes["gpu.coolings"] is M  # every member agrees
    assert outcomes["gpu.max_tdp_w"] is U  # members disagree: unknown, even though both pass
    contradicted = _gpu_outcomes(agree, coolings=["active"])
    assert contradicted["gpu.coolings"] is N  # the agreed value contradicts


def test_family_member_without_spec_row_makes_every_field_unknown() -> None:
    outcomes = _gpu_outcomes(_inputs("family", _A100, None), coolings=["passive"])
    assert outcomes["gpu.coolings"] is U


def test_missing_satellite_on_first_class_watch_is_unknown() -> None:
    results = product_clauses(
        None, "gpu", _inputs("model", _A100), ExtractedAttributes(), POLICY, {}
    )
    assert [r.outcome for r in results] == [U]


def test_basic_watch_category_has_no_product_clauses() -> None:
    assert (
        product_clauses(None, "nic", _inputs("model", {}), ExtractedAttributes(), POLICY, {}) == []
    )


@pytest.mark.parametrize(
    ("title", "allowed", "contradicts"),
    [
        ("smr", ["smr_dm"], False),
        ("smr", ["cmr"], True),
        ("cmr", ["cmr"], False),
        ("cmr", ["smr_dm", "smr_hm"], True),
    ],
)
def test_title_recording_tech_is_judged_in_its_coarse_vocabulary(
    title: str, allowed: list[str], contradicts: bool
) -> None:
    ok = evaluate._recording_title_ok(allowed)  # pyright: ignore[reportPrivateUsage] - the coarse-vocabulary rule is the unit under test
    assert ok(title) is not contradicts


# ── target clause ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("grain", "family_id", "model_id", "target_family", "target_model", "expected"),
    [
        ("none", None, None, None, 1, U),
        ("model", 7, 1, None, 1, M),
        ("model", 7, 2, None, 1, N),
        ("family", 7, None, None, 1, U),
        ("model", 7, 1, 7, None, M),
        ("model", 8, 1, 7, None, N),
        ("model", None, 1, 7, None, U),
        ("family", 7, None, 7, None, M),
        ("family", 8, None, 7, None, N),
    ],
)
def test_target_clause_table(
    grain: str,
    family_id: int | None,
    model_id: int | None,
    target_family: int | None,
    target_model: int | None,
    expected: EligibilityVerdict,
) -> None:
    inputs = CatalogInputs("nic", grain, family_id, model_id, None, ())
    result = target_clause(target_family, target_model, inputs, {})
    assert result is not None
    assert result.outcome is expected


def test_no_target_means_no_target_clause() -> None:
    assert target_clause(None, None, _inputs("none"), {}) is None


# ── offer clauses ────────────────────────────────────────────────────────────


def _facts(**overrides: object) -> OfferFacts:
    base: dict[str, object] = {
        "snapshot_observed_at": _OBSERVED,
        "price_usd": Decimal("100.00"),
        "shipping_known": True,
        "quantity": None,
        "stock_status": StockStatus.IN_STOCK.value,
        "is_international": False,
        "condition": _attr("used", 0.8),
    }
    base.update(overrides)
    return OfferFacts(**base)  # pyright: ignore[reportArgumentType] - keyword dict built above


@pytest.mark.parametrize(
    ("maximum", "facts", "expected"),
    [
        (None, _facts(price_usd=None), M),
        (Decimal(150), _facts(), M),
        (Decimal(100), _facts(), M),
        (Decimal(50), _facts(), N),
        (Decimal(150), _facts(price_usd=None), U),
        # A confident lot of 4 divides: 100 / 4 = 25.
        (Decimal(30), _facts(quantity=_attr(4, 0.95)), M),
        (Decimal(20), _facts(quantity=_attr(4, 0.95)), N),
        # An uncertain quantity is only an upper bound: it can pass, never fail.
        (Decimal(150), _facts(quantity=_attr(4, 0.7)), M),
        (Decimal(30), _facts(quantity=_attr(4, 0.7)), U),
        # Unknown shipping: the price is a lower bound, so it can fail but
        # never pass.
        (Decimal(150), _facts(shipping_known=False), U),
        (Decimal(50), _facts(shipping_known=False), N),
        # Uncertain quantity AND unknown shipping: neither bound decides.
        (Decimal(150), _facts(shipping_known=False, quantity=_attr(4, 0.7)), U),
        (Decimal(30), _facts(shipping_known=False, quantity=_attr(4, 0.7)), U),
        # A confident divisor with unknown shipping still cannot pass.
        (Decimal(30), _facts(shipping_known=False, quantity=_attr(4, 0.95)), U),
        (Decimal(20), _facts(shipping_known=False, quantity=_attr(4, 0.95)), N),
    ],
)
def test_price_clause(
    maximum: Decimal | None, facts: OfferFacts, expected: EligibilityVerdict
) -> None:
    assert price_clause(maximum, facts, POLICY).outcome is expected


def test_price_reason_records_unknown_shipping() -> None:
    result = price_clause(Decimal(150), _facts(shipping_known=False), POLICY)
    assert result.outcome is U
    assert result.detail.startswith("shipping_unknown")
    assert result.as_reason()["observed"]["reason_code"] == "shipping_unknown"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownMemberType] - observed is a JSON dict here
    assert result.as_reason()["observed"]["shipping_known"] is False  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownMemberType] - observed is a JSON dict here
    assert result.as_reason()["source"] == {"snapshot_observed_at": _OBSERVED.isoformat()}


@pytest.mark.parametrize(
    ("allowed", "condition", "expected"),
    [
        ([], None, M),
        (["used"], None, U),
        (["used"], _attr("unknown"), U),
        (["used"], _attr("used", 0.8), M),
        (["new"], _attr("used", 0.8), N),
    ],
)
def test_condition_clause(
    allowed: list[str], condition: Attribute[str] | None, expected: EligibilityVerdict
) -> None:
    assert condition_clause(allowed, _facts(condition=condition)).outcome is expected


@pytest.mark.parametrize(
    ("required", "status", "expected"),
    [
        (False, None, M),
        (True, StockStatus.IN_STOCK.value, M),
        (True, StockStatus.OUT_OF_STOCK.value, N),
        (True, StockStatus.PREORDER.value, N),
        (True, StockStatus.UNKNOWN.value, U),
        (True, None, U),
    ],
)
def test_stock_clause(required: bool, status: str | None, expected: EligibilityVerdict) -> None:
    assert stock_clause(required, _facts(stock_status=status)).outcome is expected


@pytest.mark.parametrize(
    ("allow", "international", "expected"),
    [(True, True, M), (False, False, M), (False, True, N)],
)
def test_international_clause(
    allow: bool, international: bool, expected: EligibilityVerdict
) -> None:
    assert international_clause(allow, _facts(is_international=international)).outcome is expected


# ── fingerprint ──────────────────────────────────────────────────────────────


def test_fingerprint_is_stable_and_sensitive() -> None:
    a = _inputs("model", _A100)
    assert catalog_fingerprint(a) == catalog_fingerprint(_inputs("model", dict(_A100)))
    assert len(catalog_fingerprint(a)) == 64
    changed = _inputs("model", {**_A100, "cooling": "active"})
    assert catalog_fingerprint(changed) != catalog_fingerprint(a)
    absent = _inputs("model", None)
    assert catalog_fingerprint(absent) != catalog_fingerprint(_inputs("model", {}))


def test_fingerprint_embeds_the_inputs_version(monkeypatch: pytest.MonkeyPatch) -> None:
    before = catalog_fingerprint(_inputs("none"))
    monkeypatch.setattr(evaluate, "CATALOG_INPUTS_VERSION", "test-bump")
    assert catalog_fingerprint(_inputs("none")) != before
