"""MS-2 Slice C1 (MS2-D-07, -09, -29): watch, requirement satellites, and
watch_evaluation constraints, proven at the database rather than by writer
convention.

The cross-table rules (satellite category = watch category, basic-watch target
required) belong to the requirement service and are tested there (C2), not here.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, models, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from hw_radar.catalog.models import (
    Category,
    Condition,
    CpuRequirement,
    DriveRequirement,
    EligibilityVerdict,
    GpuCooling,
    GpuRequirement,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductFamily,
    ProductModel,
    RamRequirement,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)

REQUIREMENTS: tuple[type[models.Model], ...] = (
    DriveRequirement,
    GpuRequirement,
    RamRequirement,
    CpuRequirement,
)
FINGERPRINT = "0" * 64

pytestmark = pytest.mark.django_db


def _family_and_model(category_slug: str = "gpu") -> tuple[ProductFamily, ProductModel]:
    mfr, _ = Manufacturer.objects.get_or_create(
        normalized_name="nvidia", defaults={"name": "NVIDIA"}
    )
    family = ProductFamily.objects.create(
        category=Category.objects.get(slug=category_slug),
        manufacturer=mfr,
        name="Tesla",
        normalized_name="tesla",
    )
    model = ProductModel.objects.create(
        manufacturer=mfr,
        product_family=family,
        model_number="P40",
        normalized_model_number="p40",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return family, model


def _watch(**fields: Any) -> Watch:
    fields.setdefault("category", Category.objects.get(slug="gpu"))
    return Watch.objects.create(name="24GB inference card", **fields)


def _listing(key: str = "k1") -> Listing:
    site, _ = SourceSite.objects.get_or_create(
        normalized_name="watchdemo", defaults={"name": "Demo"}
    )
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=f"h-{key}",
        title_raw="NVIDIA Tesla P40 24GB",
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _evaluation(watch: Watch, listing: Listing, **overrides: Any) -> WatchEvaluation:
    fields: dict[str, Any] = {
        "verdict": EligibilityVerdict.UNKNOWN,
        "reasons": [{"clause": "vram", "outcome": "unknown"}],
        "requirement_version": watch.requirement_version,
        "evaluator_version": "1",
        "catalog_fingerprint": FINGERPRINT,
        "retention_class": RetentionClass.MERCHANT_FACT,
        **overrides,
    }
    return WatchEvaluation.objects.create(watch=watch, listing=listing, **fields)


def _rejected(**overrides: Any) -> None:
    watch, listing = _watch(), _listing()
    # Savepoint: the IntegrityError aborts only this statement, not the test's
    # wrapping transaction.
    with pytest.raises(IntegrityError), transaction.atomic():
        _evaluation(watch, listing, **overrides)


# ── Watch ────────────────────────────────────────────────────────────────────


def test_watch_with_both_targets_rejected() -> None:
    family, model = _family_and_model()
    with pytest.raises(IntegrityError):
        _watch(target_family=family, target_model=model)


@pytest.mark.parametrize("target", ["none", "family", "model"])
def test_watch_with_at_most_one_target_accepted(target: str) -> None:
    family, model = _family_and_model()
    fields = {"none": {}, "family": {"target_family": family}, "model": {"target_model": model}}
    watch = _watch(**fields[target])
    watch.refresh_from_db()
    assert watch.requirement_version == 1
    assert watch.enabled is True


def test_watch_hard_offer_clauses_round_trip_as_typed_values() -> None:
    watch = _watch(
        max_unit_price_usd=Decimal("250.00"),
        allowed_conditions=[Condition.USED, Condition.REFURBISHED],
        require_in_stock=True,
        allow_international=False,
        target_unit_price_usd=Decimal("180.00"),
    )
    watch.refresh_from_db()
    assert watch.max_unit_price_usd == Decimal("250.00")
    assert watch.allowed_conditions == ["used", "refurbished"]
    assert watch.target_unit_price_usd == Decimal("180.00")


def test_watch_defaults_are_unconstrained() -> None:
    # An empty allow-list and NULL bounds mean "no constraint" (MS2-D-07); the
    # boolean defaults are the no-constraint values too.
    watch = _watch()
    watch.refresh_from_db()
    assert watch.allowed_conditions == []
    assert watch.max_unit_price_usd is None
    assert watch.require_in_stock is False
    assert watch.allow_international is True


def test_watch_target_is_protected_from_deletion() -> None:
    # Deleting a target must not silently widen or drop a saved watch.
    _, model = _family_and_model()
    _watch(target_model=model)
    with pytest.raises(ProtectedError):
        model.delete()


# ── Requirement satellites ───────────────────────────────────────────────────


@pytest.mark.parametrize("req_cls", REQUIREMENTS, ids=lambda c: c.__name__)
def test_requirement_satellite_is_one_to_one_on_watch(req_cls: type[models.Model]) -> None:
    watch = _watch()
    req_cls.objects.create(watch=watch)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types does not expose .objects on type[Model]
    with pytest.raises(IntegrityError):
        req_cls.objects.create(watch=watch)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


@pytest.mark.parametrize("req_cls", REQUIREMENTS, ids=lambda c: c.__name__)
def test_requirement_satellite_cascades_with_its_watch(req_cls: type[models.Model]) -> None:
    watch = _watch()
    req_cls.objects.create(watch=watch)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    watch.delete()
    assert not req_cls.objects.exists()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


@pytest.mark.parametrize("model_cls", (Watch, *REQUIREMENTS), ids=lambda c: c.__name__)
def test_requirements_are_typed_columns_not_a_json_bag(model_cls: type[models.Model]) -> None:
    # ADR 0022 / D10: watch-critical values must never sit in a JSON document.
    fields = model_cls._meta.get_fields()  # pyright: ignore[reportPrivateUsage]
    assert not [f for f in fields if isinstance(f, models.JSONField)]


def test_requirement_arrays_round_trip() -> None:
    gpu = GpuRequirement.objects.create(
        watch=_watch(), min_vram_gb=24, coolings=[GpuCooling.ACTIVE], max_tdp_w=250
    )
    ram = RamRequirement.objects.create(
        watch=_watch(category=Category.objects.get(slug="ram")),
        generations=["ddr4"],
        module_types=["rdimm", "lrdimm"],
        require_ecc=True,
        min_total_capacity_gb=512,
    )
    cpu = CpuRequirement.objects.create(
        watch=_watch(category=Category.objects.get(slug="cpu")), sockets=["sp3"], min_cores=32
    )
    drive = DriveRequirement.objects.create(
        watch=_watch(category=Category.objects.get(slug="drive")),
        min_capacity_tb=Decimal("16"),
        media_types=["hdd"],
        recording_techs=["cmr"],
    )
    for row in (gpu, ram, cpu, drive):
        row.refresh_from_db()
    assert gpu.coolings == ["active"]
    assert ram.module_types == ["rdimm", "lrdimm"]
    assert ram.require_ecc is True
    assert cpu.sockets == ["sp3"]
    assert drive.min_capacity_tb == Decimal("16.000")
    assert drive.interfaces == []


# ── WatchEvaluation ──────────────────────────────────────────────────────────


def test_evaluation_accepts_a_well_formed_row() -> None:
    evaluation = _evaluation(_watch(), _listing(), verdict=EligibilityVerdict.MATCH)
    evaluation.refresh_from_db()
    assert evaluation.verdict == "match"
    assert evaluation.resolution is None
    assert evaluation.snapshot_observed_at is None


def test_evaluation_without_retention_class_rejected() -> None:
    _rejected(retention_class="")


def test_evaluation_bounded_class_without_expiry_rejected() -> None:
    _rejected(retention_class=RetentionClass.EBAY_LISTING_OBSERVATION)


def test_evaluation_indefinite_class_with_expiry_rejected() -> None:
    _rejected(expires_at=timezone.now() + timedelta(hours=6))


def test_evaluation_bounded_class_with_expiry_accepted() -> None:
    evaluation = _evaluation(
        _watch(),
        _listing(),
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=timezone.now() + timedelta(hours=6),
    )
    assert evaluation.pk is not None


@pytest.mark.parametrize("verdict", ["accept", "review", "none", "pending", ""])
def test_evaluation_verdict_outside_vocabulary_rejected(verdict: str) -> None:
    # Resolution outcomes and the read-model `pending` state are not verdicts.
    _rejected(verdict=verdict)


@pytest.mark.parametrize("reasons", [[], {}, {"clause": "vram"}, "vram", None])
def test_evaluation_reasons_must_be_a_nonempty_array(reasons: object) -> None:
    # FR-006: every verdict carries its reasons. `None` is JSON null here, not
    # SQL NULL, because JSONField stores Python None as the JSON literal.
    _rejected(reasons=reasons)


def test_evaluation_unique_per_watch_and_listing() -> None:
    watch, listing = _watch(), _listing()
    _evaluation(watch, listing)
    with pytest.raises(IntegrityError):
        _evaluation(watch, listing)


def test_evaluation_same_listing_under_two_watches_allowed() -> None:
    listing = _listing()
    _evaluation(_watch(), listing)
    _evaluation(_watch(), listing)
    assert WatchEvaluation.objects.filter(listing=listing).count() == 2


def test_evaluation_resolution_is_set_null_on_edge_delete() -> None:
    watch, listing = _watch(), _listing()
    edge = ListingResolution.objects.create(listing=listing, matcher_version="t")
    evaluation = _evaluation(watch, listing, resolution=edge)
    edge.delete()
    evaluation.refresh_from_db()
    assert evaluation.resolution is None


def test_evaluation_cascades_with_listing_and_with_watch() -> None:
    watch, listing, other = _watch(), _listing("a"), _listing("b")
    _evaluation(watch, listing)
    _evaluation(watch, other)
    listing.delete()
    assert WatchEvaluation.objects.count() == 1
    watch.delete()
    assert not WatchEvaluation.objects.exists()


def test_evaluation_indexes_exist_in_the_database() -> None:
    with connection.cursor() as cursor:
        found = connection.introspection.get_constraints(cursor, "watch_evaluation")
    assert found["watch_eval_watch_verdict"]["index"] is True
    assert found["watch_eval_watch_verdict"]["columns"] == ["watch_id", "verdict"]
    assert found["watch_evaluation_expires"]["columns"] == ["expires_at"]
    assert found["watch_evaluation_one_per_listing"]["unique"] is True
    assert found["watch_evaluation_one_per_listing"]["columns"] == ["watch_id", "listing_id"]


def test_evaluation_has_every_binding_column() -> None:
    # MS2-D-20's five currency inputs plus evaluated_at must all be persisted;
    # the read-time currency check (C4) compares each one.
    names = {f.name for f in WatchEvaluation._meta.get_fields()}  # pyright: ignore[reportPrivateUsage]
    assert {
        "snapshot_observed_at",
        "resolution",
        "requirement_version",
        "evaluator_version",
        "catalog_fingerprint",
        "evaluated_at",
    } <= names
    fingerprint = WatchEvaluation._meta.get_field("catalog_fingerprint")  # pyright: ignore[reportPrivateUsage]
    assert isinstance(fingerprint, models.CharField)
    assert fingerprint.max_length == 64
    resolution = WatchEvaluation._meta.get_field("resolution")  # pyright: ignore[reportPrivateUsage]
    assert isinstance(resolution, models.ForeignKey)
    assert resolution.null is True
    # SET_NULL itself is proven behaviorally by
    # test_evaluation_resolution_is_set_null_on_edge_delete.
