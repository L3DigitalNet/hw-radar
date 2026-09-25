"""The synthetic site's local path: the explicit provider switch and one local collection (AC-4).

apify_synthetic_setup --provider switches collection_provider and nothing else;
synthetic_collect_local runs the registered local adapter once through the
poller's full-lane body (poller.service.run_local_full_lane). The pages are the
Actor's committed source fixtures, served by a MockTransport: no test touches
the network. The switch across both providers over the same pages is proven in
tests/db/test_apify_import.py's D7 section.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Final

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from hw_radar.acquisition import sources
from hw_radar.acquisition.apify import synthetic
from hw_radar.acquisition.sources import synthetic as local
from hw_radar.acquisition.sources.synthetic import SyntheticAdapter
from hw_radar.catalog.models import (
    Listing,
    ProviderKind,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScraperRun,
    SourceConfig,
    SourceSite,
)

# transaction=True: the local run writes from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded rows (categories, sites).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

SOURCE_DIR: Final = (
    Path(__file__).resolve().parents[2] / "actors/hw-radar-synthetic-collector/fixtures/source"
)
COMMIT: Final = "1" * 40
PAGE_PREFIX: Final = f"/L3DigitalNet/hw-radar/{COMMIT}/{local.SOURCE_ROOT}/"


class FakeRaw:
    """raw.githubusercontent.com over the committed pages; responses are handed back unread.

    The adapter streams raw chunks to count wire bytes, and a response built
    with content= is already consumed, so each one is re-served as a stream.
    """

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = b"404: Not Found"
        if self.status == 200 and request.url.path.startswith(PAGE_PREFIX):
            body = (SOURCE_DIR / request.url.path.removeprefix(PAGE_PREFIX)).read_bytes()
        served = httpx.Response(
            self.status, content=body, headers={"content-type": "text/plain; charset=utf-8"}
        )
        return httpx.Response(served.status_code, headers=served.headers, stream=served.stream)


@pytest.fixture
def raw(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRaw]:
    """Serve the pages to the registered adapter, with the fixture commit configured."""
    fake = FakeRaw()

    def factory() -> SyntheticAdapter:
        return SyntheticAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(fake)))

    monkeypatch.setitem(sources.ADAPTERS, synthetic.SITE_KEY, factory)
    with override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT=COMMIT):
        yield fake


def _setup(*args: str) -> str:
    out = StringIO()
    call_command("apify_synthetic_setup", *args, stdout=out)
    return out.getvalue()


def _collect() -> str:
    out = StringIO()
    call_command("synthetic_collect_local", stdout=out)
    return out.getvalue()


def _config() -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name=synthetic.SITE_KEY
    )


def _unscheduled(config: SourceConfig) -> tuple[bool, bool, bool]:
    return (config.enabled, config.heartbeat_enabled, config.fast_lane)


# ── apify_synthetic_setup --provider ─────────────────────────────────────────


def test_provider_switch_is_explicit_logged_and_idempotent() -> None:
    _setup()
    before = _config()

    switched = _setup("--provider", "local")
    after = _config()
    repeated = _setup("--provider", "local")
    unchanged = _config()
    back = _setup("--provider", "apify")

    assert "collection_provider: apify -> local" in switched
    assert "provider switched" in switched
    assert after.collection_provider == ProviderKind.LOCAL
    assert after.updated_at > before.updated_at
    assert "collection_provider unchanged: local" in repeated
    assert "already present (no change)" in repeated
    # The no-op writes nothing at all.
    assert unchanged.updated_at == after.updated_at
    assert "collection_provider: local -> apify" in back
    final = _config()
    assert final.collection_provider == ProviderKind.APIFY
    # Only the provider ever moves: the site stays disabled and lane-free.
    assert (
        _unscheduled(before)
        == _unscheduled(after)
        == _unscheduled(final)
        == (
            False,
            False,
            False,
        )
    )


def test_provider_flag_on_a_fresh_database_creates_then_switches() -> None:
    out = _setup("--provider", "local")

    assert "created" in out
    assert "collection_provider: apify -> local" in out
    assert _config().collection_provider == ProviderKind.LOCAL


def test_switch_is_refused_on_other_drift_and_changes_nothing() -> None:
    _setup()
    SourceConfig.objects.filter(pk=_config().pk).update(enabled=True)

    with pytest.raises(CommandError, match=r"drift.*enabled=True"):
        _setup("--provider", "local")

    config = _config()
    assert (config.collection_provider, config.enabled) == (ProviderKind.APIFY, True)


def test_plain_setup_reports_a_local_provider_as_drift() -> None:
    # No silent rewrite: only --provider may move the provider, either way.
    _setup("--provider", "local")

    with pytest.raises(CommandError, match=r"drift.*collection_provider='local'"):
        _setup()

    assert _config().collection_provider == ProviderKind.LOCAL


def test_provider_flag_rejects_an_unknown_provider() -> None:
    with pytest.raises(CommandError, match="invalid choice"):
        _setup("--provider", "scrapy")
    assert not SourceSite.objects.filter(normalized_name=synthetic.SITE_KEY).exists()


# ── synthetic_collect_local ──────────────────────────────────────────────────


def test_local_collection_runs_the_poller_full_lane_path_and_leaves_config_disabled(
    raw: FakeRaw,
) -> None:
    _setup("--provider", "local")

    out = _collect()

    run = ScraperRun.objects.get(source_site__normalized_name=synthetic.SITE_KEY)
    assert (run.status, run.run_kind) == (RunStatus.SUCCESS, RunKind.FULL)
    assert (run.records_fetched, run.records_valid, run.snapshots_appended) == (2, 3, 3)
    assert run.detail_json["listings_delisted"] == 0
    provider = run.detail_json["provider"]
    assert isinstance(provider, dict)
    assert provider["provider_kind"] == ProviderKind.LOCAL
    assert f"scraper_run {run.pk}: status=success" in out
    assert len(raw.requests) == 2
    listings = Listing.objects.filter(source_site__normalized_name=synthetic.SITE_KEY)
    assert sorted(listings.values_list("source_listing_key", flat=True)) == [
        "syn-hdd-0001",
        "syn-hdd-0002",
        "syn-hdd-0003",
    ]
    assert set(listings.values_list("collection_scope", flat=True)) == {synthetic.COLLECTION_SCOPE}
    config = _config()
    # apply_run_outcome ran on the FULL lane, as after a scheduled poll.
    assert config.lane_state(SchedulingLane.FULL).clean_polls == 1
    assert config.collection_provider == ProviderKind.LOCAL
    assert _unscheduled(config) == (False, False, False)


def test_local_collection_is_refused_while_the_site_is_on_apify(raw: FakeRaw) -> None:
    _setup()

    with pytest.raises(CommandError, match="collection_provider=apify"):
        _collect()

    assert raw.requests == []
    assert not ScraperRun.objects.filter(source_site__normalized_name=synthetic.SITE_KEY).exists()


def test_local_collection_is_refused_without_a_fixture_commit(raw: FakeRaw) -> None:
    _setup("--provider", "local")

    with (
        override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT=""),
        pytest.raises(CommandError, match="HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT is unset"),
    ):
        _collect()

    assert raw.requests == []
    assert not ScraperRun.objects.filter(source_site__normalized_name=synthetic.SITE_KEY).exists()


def test_local_collection_without_the_site_names_the_setup_command(raw: FakeRaw) -> None:
    with pytest.raises(CommandError, match="apify_synthetic_setup"):
        _collect()
    assert raw.requests == []


def test_a_failed_local_run_exits_non_zero_and_is_recorded(raw: FakeRaw) -> None:
    _setup("--provider", "local")
    raw.status = 404

    with pytest.raises(CommandError, match="did not succeed"):
        _collect()

    run = ScraperRun.objects.get(source_site__normalized_name=synthetic.SITE_KEY)
    assert run.status == RunStatus.FAILED
    assert not Listing.objects.filter(source_site__normalized_name=synthetic.SITE_KEY).exists()
    assert _config().consecutive_failures == 1


# ── production refusal ───────────────────────────────────────────────────────


def test_switch_and_local_collection_refuse_in_production(raw: FakeRaw) -> None:
    _setup("--provider", "local")

    with override_settings(IS_PRODUCTION=True):
        with pytest.raises(CommandError, match="production"):
            _setup("--provider", "apify")
        with pytest.raises(CommandError, match="production"):
            _collect()

    assert _config().collection_provider == ProviderKind.LOCAL
    assert raw.requests == []
    assert not ScraperRun.objects.exists()
