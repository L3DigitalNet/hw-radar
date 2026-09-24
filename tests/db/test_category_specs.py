"""MS-2 Slice B1 (MS2-D-04): typed GPU/RAM/CPU spec satellites on the one spine.

Each satellite must behave like DriveSpec: 1:1 on ProductModel and governed by
the DR-001 CHECK pair at the database, not by writer convention.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, models
from django.utils import timezone

from hw_radar.catalog.models import (
    Category,
    Condition,
    CpuSpec,
    DriveSpec,
    GpuChipVendor,
    GpuCooling,
    GpuInterface,
    GpuSpec,
    Manufacturer,
    MediaType,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RamGeneration,
    RamModuleType,
    RamSpec,
    RetentionClass,
)

SATELLITES: tuple[type[GpuSpec | RamSpec | CpuSpec], ...] = (GpuSpec, RamSpec, CpuSpec)


def _model(category_slug: str, mfr: str, model_number: str) -> ProductModel:
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name=mfr, defaults={"name": mfr.title()}
    )
    family = ProductFamily.objects.create(
        category=Category.objects.get(slug=category_slug),
        manufacturer=manufacturer,
        name=f"{model_number} family",
        normalized_name=f"{model_number.lower()} family",
    )
    return ProductModel.objects.create(
        manufacturer=manufacturer,
        product_family=family,
        model_number=model_number,
        normalized_model_number=model_number.lower(),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )


def _create_spec(
    spec_cls: type[models.Model], model: ProductModel, **retention: object
) -> models.Model:
    return spec_cls.objects.create(product_model=model, **retention)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType] - django-types does not expose .objects on type[Model]


@pytest.mark.django_db
@pytest.mark.parametrize("spec_cls", SATELLITES, ids=lambda c: c.__name__)
def test_satellite_without_retention_class_rejected(spec_cls: type[models.Model]) -> None:
    model = _model("gpu", "nvidia", "NOCLASS-1")
    with pytest.raises(IntegrityError):
        _create_spec(spec_cls, model)


@pytest.mark.django_db
@pytest.mark.parametrize("spec_cls", SATELLITES, ids=lambda c: c.__name__)
def test_satellite_bounded_class_without_expiry_rejected(spec_cls: type[models.Model]) -> None:
    model = _model("gpu", "nvidia", "BOUNDED-1")
    with pytest.raises(IntegrityError):
        _create_spec(spec_cls, model, retention_class=RetentionClass.EBAY_LISTING_OBSERVATION)


@pytest.mark.django_db
@pytest.mark.parametrize("spec_cls", SATELLITES, ids=lambda c: c.__name__)
def test_satellite_indefinite_class_with_expiry_rejected(spec_cls: type[models.Model]) -> None:
    model = _model("gpu", "nvidia", "INDEF-1")
    with pytest.raises(IntegrityError):
        _create_spec(
            spec_cls,
            model,
            retention_class=RetentionClass.MANUFACTURER_REFERENCE,
            expires_at=timezone.now() + timedelta(days=1),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("spec_cls", SATELLITES, ids=lambda c: c.__name__)
def test_satellite_is_one_to_one_on_model(spec_cls: type[models.Model]) -> None:
    model = _model("gpu", "nvidia", "ONE-TO-ONE-1")
    _create_spec(spec_cls, model, retention_class=RetentionClass.MANUFACTURER_REFERENCE)
    with pytest.raises(IntegrityError):
        _create_spec(spec_cls, model, retention_class=RetentionClass.MANUFACTURER_REFERENCE)


@pytest.mark.django_db
def test_unstated_values_default_to_unknown_not_a_guess() -> None:
    gpu = GpuSpec.objects.create(
        product_model=_model("gpu", "nvidia", "DEFAULTS-GPU"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ram = RamSpec.objects.create(
        product_model=_model("ram", "micron", "DEFAULTS-RAM"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    cpu = CpuSpec.objects.create(
        product_model=_model("cpu", "intel", "DEFAULTS-CPU"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    assert (gpu.chip_vendor, gpu.interface, gpu.cooling) == ("unknown", "unknown", "unknown")
    assert (gpu.vram_gb, gpu.tdp_w) == (None, None)
    assert (ram.generation, ram.module_type, ram.ecc) == ("unknown", "unknown", None)
    # A single module, not "unknown count": kit total = modules_per_kit x size.
    assert ram.modules_per_kit == 1
    assert (cpu.socket, cpu.cores, cpu.tdp_w) == ("", None, None)


@pytest.mark.django_db
def test_four_categories_coexist_on_one_spine() -> None:
    # AC-1: one model per first-class category, each with its own satellite and a
    # sellable variant, all on the unchanged category → family → model → variant
    # spine. Each model carries exactly its own satellite and no other.
    drive = _model("drive", "seagate", "ST16000NM001G")
    DriveSpec.objects.create(
        product_model=drive,
        media_type=MediaType.HDD,
        capacity_tb="16.000",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    gpu = _model("gpu", "nvidia", "900-2G500-0010-000")
    GpuSpec.objects.create(
        product_model=gpu,
        chip_vendor=GpuChipVendor.NVIDIA,
        vram_gb=32,
        interface=GpuInterface.PCIE,
        cooling=GpuCooling.PASSIVE,
        tdp_w=250,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ram = _model("ram", "micron", "MTA36ASF4G72PZ-3G2R")
    RamSpec.objects.create(
        product_model=ram,
        generation=RamGeneration.DDR4,
        module_type=RamModuleType.RDIMM,
        ecc=True,
        module_capacity_gb=32,
        speed_mts=3200,
        ranks=2,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    cpu = _model("cpu", "intel", "Xeon Gold 6448Y")
    CpuSpec.objects.create(
        product_model=cpu,
        socket="lga4677",
        cores=32,
        tdp_w=225,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    for model in (drive, gpu, ram, cpu):
        ProductVariant.objects.create(product_model=model, condition=Condition.USED)

    by_category = {
        m.product_family.category.slug: m  # pyright: ignore[reportOptionalMemberAccess] - every model above has a family
        for m in ProductModel.objects.select_related("product_family__category")
    }
    assert set(by_category) == {"drive", "gpu", "ram", "cpu"}
    satellites = {
        "drive": DriveSpec,
        "gpu": GpuSpec,
        "ram": RamSpec,
        "cpu": CpuSpec,
    }
    for slug, model in by_category.items():
        assert ProductVariant.objects.filter(product_model=model).count() == 1
        for other_slug, spec_cls in satellites.items():
            has_spec = spec_cls.objects.filter(product_model=model).exists()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType] - django-types does not expose .objects on the union of model classes
            assert has_spec == (other_slug == slug), (slug, other_slug)
    assert GpuSpec.objects.get(product_model=gpu).vram_gb == 32
    assert RamSpec.objects.get(product_model=ram).module_capacity_gb == 32
    assert CpuSpec.objects.get(product_model=cpu).socket == "lga4677"


def test_satellites_are_registered_in_admin() -> None:
    # B5: reference rows for the new categories must be inspectable and
    # correctable the same way drive_spec is.
    from django.contrib import admin

    for spec_cls in SATELLITES:
        assert admin.site.is_registered(spec_cls), spec_cls.__name__
