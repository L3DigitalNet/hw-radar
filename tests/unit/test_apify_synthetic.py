"""The synthetic proof site's registered run spec (MS2-D-42, F5a): valid, bounded, inert by default."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from django.conf import settings
from django.test import override_settings

from hw_radar.acquisition.apify import contract, jobs, synthetic
from hw_radar.acquisition.apify.contract import QueryScope, SyntheticCollectorInput
from hw_radar.acquisition.retention_policy import source_retention
from hw_radar.catalog.models import RunKind, SourceConfig
from hw_radar.matching.categories import registered_categories

COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"
# The Actor's own fixture root (synthetic_collector.core.SOURCE_ROOT).
FIXTURE_ROOT: Final = (
    Path(__file__).resolve().parents[2] / "actors/hw-radar-synthetic-collector/fixtures/source"
)


def _spec() -> jobs.ActorRunSpec:
    with override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT=COMMIT):
        spec = jobs.RUN_SPECS[synthetic.SITE_KEY](SourceConfig(), RunKind.FULL)
    assert spec is not None
    return spec


def test_run_specs_registers_only_the_synthetic_site() -> None:
    assert set(jobs.RUN_SPECS) == {synthetic.SITE_KEY}


def test_registered_spec_is_inert_until_a_fixture_commit_is_configured() -> None:
    # The shipped default: no commit, so no spec, which the start job refuses
    # `no_run_spec` exactly as it refuses an unregistered site.
    assert settings.HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT == ""
    for kind in RunKind:
        assert jobs.RUN_SPECS[synthetic.SITE_KEY](SourceConfig(), kind) is None


def test_registered_spec_validates_against_the_input_model() -> None:
    spec = _spec()
    validated = SyntheticCollectorInput.model_validate(dict(spec.run_input))
    assert spec.input_model is SyntheticCollectorInput
    assert (spec.memory_mb, spec.timeout_s) == (256, 300)
    assert (validated.site_key, validated.collection_scope, validated.category_hint) == (
        "synthetic",
        "synthetic:drive:catalog",
        "drive",
    )
    assert (
        validated.max_items,
        validated.max_pages,
        validated.max_requests,
        validated.max_bytes,
        validated.time_budget_secs,
    ) == (10, 2, 4, 262_144, 120)
    assert (validated.fixture_commit, validated.fault_mode) == (COMMIT, "none")
    # The admitted scope the importer checks the OUTPUT echo against.
    QueryScope.model_validate({name: getattr(validated, name) for name in QueryScope.model_fields})


def test_default_caps_sit_below_the_contract_maxima() -> None:
    caps = synthetic.DEFAULT_CAPS
    assert caps.max_items < contract.MAX_ITEMS_CEILING
    assert caps.max_pages < contract.MAX_PAGES_CEILING
    assert caps.max_requests < contract.MAX_REQUESTS_CEILING
    assert caps.max_bytes < contract.MAX_BYTES_CEILING
    assert contract.MIN_TIME_BUDGET_SECS <= caps.time_budget_secs < synthetic.TIMEOUT_S


def test_fixture_paths_resolve_under_the_actor_fixture_root() -> None:
    # Pins the path trap: the Actor prepends fixtures/source/ itself, so a
    # "source/" prefix would fetch a page that does not exist.
    for path in synthetic.FIXTURE_PATHS:
        assert (FIXTURE_ROOT / path).is_file(), path


def test_site_has_indefinite_retention_and_a_registered_category() -> None:
    # MS2-D-33 refuses a bounded site an Actor path; an unregistered hint
    # would resolve every row as unsupported_category.
    assert source_retention(synthetic.SITE_KEY).expires_policy is None
    assert synthetic.CATEGORY_HINT in registered_categories()


def test_timeout_fits_the_storage_deadline_at_the_defaults() -> None:
    margin = settings.HW_RADAR_APIFY_IMPORT_MARGIN
    assert synthetic.TIMEOUT_S + margin <= settings.HW_RADAR_APIFY_STORAGE_CLEANUP_MAX


@pytest.mark.parametrize("fault_mode", ["none", "truncate_items", "fail"])
def test_synthetic_spec_carries_the_fault_mode_and_caps(fault_mode: str) -> None:
    caps = synthetic.SyntheticCaps(max_items=3, max_pages=1)
    spec = jobs.synthetic_spec(COMMIT, fault_mode=fault_mode, caps=caps)
    validated = SyntheticCollectorInput.model_validate(dict(spec.run_input))
    assert (validated.fault_mode, validated.max_items, validated.max_pages) == (fault_mode, 3, 1)
