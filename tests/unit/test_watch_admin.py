"""The watch admin surfaces are inspection-only.

Requirement edits must go through the requirement service, which bumps
Watch.requirement_version; an admin edit would skip the bump and leave verdicts
computed against old requirements looking current (MS2-D-20). WatchEvaluation
rows are evaluator output. So every mutation is refused unconditionally, which
is why these are pure unit checks with no DB access."""

import pytest
from django.contrib import admin
from django.db.models import Model
from django.test import RequestFactory

from hw_radar.catalog.admin import WatchInspectionAdmin
from hw_radar.catalog.models import (
    CpuRequirement,
    DriveRequirement,
    GpuRequirement,
    RamRequirement,
    Watch,
    WatchEvaluation,
)

WATCH_MODELS = (
    Watch,
    DriveRequirement,
    GpuRequirement,
    RamRequirement,
    CpuRequirement,
    WatchEvaluation,
)


@pytest.mark.parametrize("model", WATCH_MODELS, ids=lambda m: m.__name__)
def test_watch_models_are_registered_read_only(model: type[Model], rf: RequestFactory) -> None:
    registered = admin.site._registry[model]  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType] - no public registry accessor
    assert isinstance(registered, WatchInspectionAdmin)
    request = rf.get("/admin/")
    assert registered.has_add_permission(request) is False
    assert registered.has_change_permission(request) is False
    assert registered.has_change_permission(request, None) is False
    assert registered.has_delete_permission(request) is False
    assert registered.has_delete_permission(request, None) is False
