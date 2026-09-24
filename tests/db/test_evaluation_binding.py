"""MS2-D-20 / MS2-D-29 input binding: a stored verdict is current only while
its snapshot, resolution edge, requirement version, evaluator version and
catalog fingerprint all still equal their live values.

Scope: the evaluator side of the binding (what `evaluate_listing` stamps and
what `live_binding` / `is_current` recompute). The shortlist and review-queue
halves of the plan's binding tests (and the heartbeat/probe wiring) belong to
the read model and pipeline legs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Category,
    EligibilityVerdict,
    GpuSpec,
    Listing,
    ListingResolution,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.evaluate import evaluate_listing, is_current, live_binding, stored_binding
from hw_radar.eligibility.requirements import GpuRequirementSpec, save_requirement
from hw_radar.matching import MATCHER_VERSION
from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db

M, N = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH
_T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
_A100 = "A100 80GB PCIe"


@pytest.fixture
def seeded(db: None) -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _snapshot(listing: Listing, *, price: Decimal, observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=price,
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
            category_hint="gpu",
        ),
        observed_at=observed_at,
    )


def _listing(site: SourceSite, key: str, title: str = "NVIDIA A100 80GB PCIe") -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _snapshot(listing, price=Decimal("900.00"), observed_at=_T0)
    return listing


def _edge(
    listing: Listing, *, model: ProductModel | None = None, family: ProductFamily | None = None
) -> ListingResolution:
    ListingResolution.objects.filter(listing=listing, is_current=True).update(is_current=False)
    return ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL if model is not None else ResolutionGrain.FAMILY,
        product_model=model,
        product_family=family,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept"},
    )


def _model(number: str) -> ProductModel:
    return ProductModel.objects.get(model_number=number)


def _gpu_watch(spec: GpuRequirementSpec) -> Watch:
    watch = Watch.objects.create(name="gpu", category=Category.objects.get(slug="gpu"))
    save_requirement(watch, spec)
    return watch


_PASSIVE_UNDER_1000 = GpuRequirementSpec(
    coolings=("passive",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
    max_unit_price_usd=Decimal("1000.00"),
)


def _row(watch: Watch, listing: Listing) -> WatchEvaluation:
    return WatchEvaluation.objects.select_related("watch", "listing").get(
        watch=watch, listing=listing
    )


@pytest.fixture
def matched(seeded: None, site: SourceSite) -> tuple[Watch, Listing]:
    """An A100 listing accepted at model grain and evaluated `match`."""
    listing = _listing(site, "a100")
    _edge(listing, model=_model(_A100))
    watch = _gpu_watch(_PASSIVE_UNDER_1000)
    evaluate_listing(listing.pk)
    assert _row(watch, listing).verdict == M
    return watch, listing


def test_fresh_evaluation_is_current(matched: tuple[Watch, Listing]) -> None:
    watch, listing = matched
    row = _row(watch, listing)
    assert is_current(row)
    assert stored_binding(row) == live_binding(listing, watch)


def test_prior_match_then_contradictory_evidence_updates_verdict(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    later = _T0 + timedelta(hours=1)
    _snapshot(listing, price=Decimal("1500.00"), observed_at=later)
    assert not is_current(_row(watch, listing))  # pending until re-evaluated

    evaluate_listing(listing.pk)

    row = _row(watch, listing)
    assert row.verdict == N
    assert row.snapshot_observed_at == later
    assert is_current(row)
    assert WatchEvaluation.objects.filter(watch=watch, listing=listing).count() == 1


def test_evaluator_failure_after_prior_match_leaves_row_pending(
    matched: tuple[Watch, Listing], monkeypatch: pytest.MonkeyPatch
) -> None:
    watch, listing = matched
    _snapshot(listing, price=Decimal("1500.00"), observed_at=_T0 + timedelta(hours=1))

    def boom(_results: object) -> EligibilityVerdict:
        raise RuntimeError("evaluator crashed")

    monkeypatch.setattr(evaluate, "aggregate", boom)
    with pytest.raises(RuntimeError):
        evaluate_listing(listing.pk)

    # Fail closed with no write: the old `match` row still carries the old
    # snapshot binding, so it is not current and cannot reach the shortlist.
    row = _row(watch, listing)
    assert row.verdict == M
    assert row.snapshot_observed_at == _T0
    assert not is_current(row)


def test_resolution_change_makes_evaluation_pending(matched: tuple[Watch, Listing]) -> None:
    watch, listing = matched
    _edge(listing, model=_model("H100 PCIe"))
    assert not is_current(_row(watch, listing))


def test_requirement_edit_makes_evaluation_pending(matched: tuple[Watch, Listing]) -> None:
    watch, listing = matched
    save_requirement(watch, _PASSIVE_UNDER_1000.model_copy(update={"min_vram_gb": 40}))
    assert not is_current(_row(watch, listing))


def test_soft_threshold_edit_keeps_evaluation_current(matched: tuple[Watch, Listing]) -> None:
    watch, listing = matched
    save_requirement(
        watch, _PASSIVE_UNDER_1000.model_copy(update={"target_unit_price_usd": Decimal(500)})
    )
    assert is_current(_row(watch, listing))


def test_evaluator_version_change_makes_evaluation_pending(
    matched: tuple[Watch, Listing], monkeypatch: pytest.MonkeyPatch
) -> None:
    watch, listing = matched
    monkeypatch.setattr(evaluate, "EVALUATOR_VERSION", "next")
    assert not is_current(_row(watch, listing))


def _corrected_seed(model_number: str, **spec: object) -> SeedDocument:
    for doc in load_seed_documents():
        data = doc.model_dump(mode="json")
        for model in data["models"]:
            if model["model_number"] == model_number:
                model["spec"].update(spec)
                return SeedDocument.model_validate(data)
    raise AssertionError(f"{model_number} not in the seeds")


def test_same_target_catalog_correction_via_refdata_persist_makes_evaluation_pending(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    edge_before = ListingResolution.objects.get(listing=listing, is_current=True)

    # Same model, same alias, same target: only the spec value changes. No new
    # observation and no resolver run.
    import_documents([_corrected_seed(_A100, cooling="active")])
    assert GpuSpec.objects.get(product_model=_model(_A100)).cooling == "active"

    row = _row(watch, listing)
    assert stored_binding(row).resolution_id == edge_before.pk
    assert stored_binding(row).snapshot_observed_at == _T0
    assert not is_current(row)

    evaluate_listing(listing.pk)
    assert _row(watch, listing).verdict == N


def test_catalog_correction_by_queryset_update_is_detected(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    # .update() bypasses save() and every model signal: detection must not
    # depend on a writer hook.
    GpuSpec.objects.filter(product_model=_model(_A100)).update(cooling="active")
    assert not is_current(_row(watch, listing))


def test_family_membership_change_makes_family_grain_evaluation_pending(
    seeded: None, site: SourceSite
) -> None:
    family = _model("GeForce RTX 4090").product_family
    assert family is not None
    listing = _listing(site, "rtx", title="NVIDIA GeForce RTX")
    _edge(listing, family=family)
    watch = _gpu_watch(GpuRequirementSpec(coolings=("active",)))  # pyright: ignore[reportArgumentType] - str coerces to the enum
    evaluate_listing(listing.pk)
    assert is_current(_row(watch, listing))

    ProductModel.objects.filter(model_number="RTX A6000").update(product_family=family)

    assert not is_current(_row(watch, listing))


def test_model_family_reassignment_makes_model_grain_evaluation_pending(
    matched: tuple[Watch, Listing],
) -> None:
    # The target clause reads the resolved model's family, so moving the model
    # to another family is a catalog-input change.
    watch, listing = matched
    other = _model("GeForce RTX 4090").product_family
    ProductModel.objects.filter(model_number=_A100).update(product_family=other)
    assert not is_current(_row(watch, listing))


def test_unrelated_catalog_edit_keeps_evaluation_current(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    GpuSpec.objects.filter(product_model=_model("H100 PCIe")).update(cooling="active", tdp_w=700)
    # Re-importing the A100's own document (unchanged A100 row, corrected
    # sibling) must not churn the A100 fingerprint either.
    import_documents([_corrected_seed("Tesla V100 PCIe (32GB)", cooling="passive")])
    assert is_current(_row(watch, listing))
