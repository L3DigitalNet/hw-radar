"""C2 requirement service (MS2-D-07): the validation matrix, the cross-table
rules the database cannot CHECK, and requirement_version bump semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from hw_radar.catalog.models import (
    Category,
    CpuRequirement,
    DriveRequirement,
    GpuRequirement,
    Manufacturer,
    ProductFamily,
    ProductModel,
    RamRequirement,
    RetentionClass,
    Watch,
)
from hw_radar.eligibility.requirements import (
    BasicRequirementSpec,
    CpuRequirementSpec,
    DriveRequirementSpec,
    GpuRequirementSpec,
    RamRequirementSpec,
    RequirementError,
    save_requirement,
)

pytestmark = pytest.mark.django_db


def _watch(category: str) -> Watch:
    return Watch.objects.create(
        name=f"{category} watch", category=Category.objects.get(slug=category)
    )


def _family(category: str, name: str = "Fam") -> ProductFamily:
    mfr, _ = Manufacturer.objects.get_or_create(normalized_name="acme", defaults={"name": "Acme"})
    return ProductFamily.objects.create(
        category=Category.objects.get(slug=category),
        manufacturer=mfr,
        name=name,
        normalized_name=f"{category}-{name}".lower(),
    )


def _model(family: ProductFamily | None, number: str = "M1") -> ProductModel:
    mfr, _ = Manufacturer.objects.get_or_create(normalized_name="acme", defaults={"name": "Acme"})
    return ProductModel.objects.create(
        manufacturer=mfr,
        product_family=family,
        model_number=number,
        normalized_model_number=number.lower(),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )


# ── validation matrix: spec construction ─────────────────────────────────────


@pytest.mark.parametrize(
    ("spec_type", "kwargs"),
    [
        (GpuRequirementSpec, {"coolings": ["freon"]}),
        (GpuRequirementSpec, {"coolings": ["unknown"]}),
        (GpuRequirementSpec, {"min_vram_gb": 0}),
        (GpuRequirementSpec, {"min_vram_gb": 40000}),
        (GpuRequirementSpec, {"max_tdp_w": -1}),
        (GpuRequirementSpec, {"vram": 40}),  # extra field
        (DriveRequirementSpec, {"interfaces": ["thunderbolt"]}),
        (DriveRequirementSpec, {"form_factors": ["5.25"]}),
        (DriveRequirementSpec, {"media_types": ["unknown"]}),
        (DriveRequirementSpec, {"recording_techs": ["unknown"]}),
        (DriveRequirementSpec, {"min_capacity_tb": Decimal("1.2345")}),
        (RamRequirementSpec, {"generations": ["ddr9"]}),
        (RamRequirementSpec, {"module_types": ["unknown"]}),
        (RamRequirementSpec, {"min_speed_mts": 0}),
        (CpuRequirementSpec, {"sockets": ["  "]}),
        (CpuRequirementSpec, {"min_cores": 0}),
        (DriveRequirementSpec, {"allowed_conditions": ["unknown"]}),
        (DriveRequirementSpec, {"allowed_conditions": ["mint"]}),
        (DriveRequirementSpec, {"max_unit_price_usd": Decimal(0)}),
        (DriveRequirementSpec, {"max_unit_price_usd": Decimal("1.001")}),
        (DriveRequirementSpec, {"target_unit_price_usd": Decimal(-5)}),
        (DriveRequirementSpec, {"target_family_id": 1, "target_model_id": 2}),
        (BasicRequirementSpec, {"category": "nic"}),  # basic-watch needs a target
        (BasicRequirementSpec, {"category": "gpu", "target_model_id": 1}),
        (BasicRequirementSpec, {"category": "zz-unregistered", "target_model_id": 1}),
    ],
)
def test_invalid_spec_is_rejected_at_construction(
    spec_type: type[DriveRequirementSpec], kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        spec_type(**kwargs)  # pyright: ignore[reportArgumentType] - deliberately invalid input


def test_arrays_are_normalized_deduplicated_and_sorted() -> None:
    drive = DriveRequirementSpec(interfaces=[" SAS", "sata", "sas"], form_factors=["M.2"])  # pyright: ignore[reportArgumentType] - lists coerce to tuples
    assert drive.interfaces == ("sas", "sata")
    assert drive.form_factors == ("m.2",)
    cpu = CpuRequirementSpec(sockets=["FCLGA4677", "LGA 4677", "SP5"])  # pyright: ignore[reportArgumentType] - lists coerce to tuples
    assert cpu.sockets == ("lga4677", "sp5")
    gpu = GpuRequirementSpec(coolings=["passive", "active", "passive"])  # pyright: ignore[reportArgumentType] - lists coerce to tuples
    assert gpu.coolings == ("active", "passive")


# ── cross-table rules ────────────────────────────────────────────────────────


def test_category_must_equal_watch_category() -> None:
    watch = _watch("gpu")
    with pytest.raises(RequirementError, match="does not match"):
        save_requirement(watch, DriveRequirementSpec())
    assert not DriveRequirement.objects.filter(pk=watch.pk).exists()
    assert not GpuRequirement.objects.filter(pk=watch.pk).exists()
    watch.refresh_from_db()
    assert watch.requirement_version == 1


def test_target_family_must_be_in_watch_category() -> None:
    watch = _watch("gpu")
    ram_family = _family("ram")
    with pytest.raises(RequirementError, match="category 'ram'"):
        save_requirement(watch, GpuRequirementSpec(target_family_id=ram_family.pk))


def test_target_model_must_be_in_watch_category() -> None:
    watch = _watch("gpu")
    model = _model(_family("cpu"))
    with pytest.raises(RequirementError, match="category 'cpu'"):
        save_requirement(watch, GpuRequirementSpec(target_model_id=model.pk))


def test_target_model_without_family_is_rejected() -> None:
    watch = _watch("gpu")
    model = _model(None)
    with pytest.raises(RequirementError, match="no family"):
        save_requirement(watch, GpuRequirementSpec(target_model_id=model.pk))


def test_missing_target_is_rejected() -> None:
    with pytest.raises(RequirementError, match="does not exist"):
        save_requirement(_watch("gpu"), GpuRequirementSpec(target_model_id=999_999))
    with pytest.raises(RequirementError, match="does not exist"):
        save_requirement(_watch("gpu"), GpuRequirementSpec(target_family_id=999_999))


def test_basic_watch_saves_target_without_satellite() -> None:
    watch = _watch("nic")
    model = _model(_family("nic"))
    result = save_requirement(watch, BasicRequirementSpec(category="nic", target_model_id=model.pk))
    assert result.bumped
    assert watch.target_model == model
    for satellite in (DriveRequirement, GpuRequirement, RamRequirement, CpuRequirement):
        assert not satellite.objects.filter(pk=watch.pk).exists()


def test_basic_spec_for_first_class_watch_is_rejected() -> None:
    watch = _watch("gpu")
    # A BasicRequirementSpec cannot even be built for gpu, and a nic spec does
    # not fit a gpu watch.
    model = _model(_family("nic"))
    with pytest.raises(RequirementError, match="does not match"):
        save_requirement(watch, BasicRequirementSpec(category="nic", target_model_id=model.pk))


# ── writes and version semantics ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("category", "spec", "satellite", "expected"),
    [
        (
            "drive",
            DriveRequirementSpec(
                min_capacity_tb=Decimal(16),
                interfaces=("sata",),
                recording_techs=("cmr",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            ),
            DriveRequirement,
            {
                "min_capacity_tb": Decimal("16.000"),
                "interfaces": ["sata"],
                "recording_techs": ["cmr"],
            },
        ),
        (
            "gpu",
            GpuRequirementSpec(min_vram_gb=40, coolings=("passive",)),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            GpuRequirement,
            {"min_vram_gb": 40, "coolings": ["passive"]},
        ),
        (
            "ram",
            RamRequirementSpec(generations=("ddr4",), require_ecc=True, min_total_capacity_gb=64),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            RamRequirement,
            {"generations": ["ddr4"], "require_ecc": True, "min_total_capacity_gb": 64},
        ),
        (
            "cpu",
            CpuRequirementSpec(sockets=("LGA4677",), min_cores=32),
            CpuRequirement,
            {"sockets": ["lga4677"], "min_cores": 32},
        ),
    ],
)
def test_first_save_writes_the_satellite_and_bumps(
    category: str,
    spec: DriveRequirementSpec,
    satellite: type[DriveRequirement],
    expected: dict[str, object],
) -> None:
    watch = _watch(category)
    result = save_requirement(watch, spec)
    assert result.changed and result.bumped
    assert result.requirement_version == 2 == watch.requirement_version
    row = satellite.objects.get(pk=watch.pk)
    for name, value in expected.items():
        assert getattr(row, name) == value


def test_offer_clauses_are_written_to_the_watch() -> None:
    watch = _watch("gpu")
    save_requirement(
        watch,
        GpuRequirementSpec(
            max_unit_price_usd=Decimal("1500.00"),
            allowed_conditions=("used", "new"),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            require_in_stock=True,
            allow_international=False,
            target_unit_price_usd=Decimal("1200.00"),
        ),
    )
    assert watch.max_unit_price_usd == Decimal("1500.00")
    assert watch.allowed_conditions == ["new", "used"]
    assert watch.require_in_stock is True
    assert watch.allow_international is False
    assert watch.target_unit_price_usd == Decimal("1200.00")


def test_version_bumps_on_each_hard_edit() -> None:
    watch = _watch("gpu")
    versions = [
        save_requirement(watch, spec).requirement_version
        for spec in (
            GpuRequirementSpec(min_vram_gb=40),
            GpuRequirementSpec(min_vram_gb=48),
            GpuRequirementSpec(min_vram_gb=48, max_unit_price_usd=Decimal(900)),
            GpuRequirementSpec(
                min_vram_gb=48, max_unit_price_usd=Decimal(900), require_in_stock=True
            ),
            GpuRequirementSpec(
                min_vram_gb=48,
                max_unit_price_usd=Decimal(900),
                require_in_stock=True,
                target_family_id=_family("gpu").pk,
            ),
        )
    ]
    assert versions == [2, 3, 4, 5, 6]


def test_no_op_save_does_not_bump_or_write() -> None:
    watch = _watch("gpu")
    spec = GpuRequirementSpec(min_vram_gb=40, coolings=("passive", "active"))  # pyright: ignore[reportArgumentType] - str coerces to the enum
    save_requirement(watch, spec)
    updated_at = watch.updated_at
    reordered = GpuRequirementSpec(min_vram_gb=40, coolings=("active", "passive"))  # pyright: ignore[reportArgumentType] - str coerces to the enum
    result = save_requirement(watch, reordered)
    assert not result.changed and not result.bumped
    assert watch.requirement_version == 2
    assert watch.updated_at == updated_at


def test_decimal_scale_difference_is_a_no_op() -> None:
    watch = _watch("drive")
    save_requirement(watch, DriveRequirementSpec(min_capacity_tb=Decimal(16)))
    result = save_requirement(watch, DriveRequirementSpec(min_capacity_tb=Decimal("16.000")))
    assert not result.changed


def test_soft_threshold_only_edit_writes_without_bump() -> None:
    watch = _watch("gpu")
    save_requirement(watch, GpuRequirementSpec(min_vram_gb=40))
    result = save_requirement(
        watch, GpuRequirementSpec(min_vram_gb=40, target_unit_price_usd=Decimal(500))
    )
    assert result.changed and not result.bumped
    assert watch.requirement_version == 2
    assert watch.target_unit_price_usd == Decimal(500)


def test_spec_is_complete_state_not_a_patch() -> None:
    watch = _watch("gpu")
    save_requirement(watch, GpuRequirementSpec(min_vram_gb=40, coolings=("passive",)))  # pyright: ignore[reportArgumentType] - str coerces to the enum
    save_requirement(watch, GpuRequirementSpec(min_vram_gb=40))
    assert GpuRequirement.objects.get(pk=watch.pk).coolings == []
    assert watch.requirement_version == 3
