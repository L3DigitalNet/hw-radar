"""Run one local collection of the synthetic proof site (AC-4 live provider switch, F5a).

    synthetic_collect_local

The local half of the live AC-4 proof: with the synthetic site switched to
`collection_provider=local` (apify_synthetic_setup --provider local), this
command runs its registered local adapter (acquisition.sources.synthetic) once
through poller.service.run_local_full_lane, the exact body the poller's
full-lane job runs for a `local` source after admission: the adapter's own
retention, run_source (fetch, classify, parse, persist, delist stage, resolve,
evaluate), then apply_run_outcome on the FULL lane. It prints the recorded
ScraperRun and exits non-zero unless the run succeeded.

The adapter fetches the Actor's own fixture pages at the commit
HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT names. For a switch proof, set it to
the commit the apify_smoke run pins (--fixture-commit): the listing URL embeds
the commit, so a different one keeps every listing identity but rewrites its
canonical URL.

Scope and safety:
- Refused when settings.IS_PRODUCTION: synthetic rows never enter the
  production catalog (MS2-D-42 *Environment*).
- Refused unless the site exists (apify_synthetic_setup) and its config is on
  `local`: an `apify` source is collected only by its Actor (MS2-D-24), and
  running the local adapter for it would record local runs against a source
  the poller treats as remote.
- Refused before any fetch when the fixture commit is unset or malformed.
- Skips the poller's admission gate, as apify_smoke skips it on the Actor side:
  the config stays `enabled=False` and unscheduled, and nothing here changes
  `enabled`, the lanes, or collection_provider. The run's outcome is applied
  to the FULL lane exactly as a scheduled run's would be.
"""

from __future__ import annotations

import asyncio
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from hw_radar.acquisition.apify import synthetic
from hw_radar.acquisition.sources.synthetic import FixtureCommitRequired, fixture_commit
from hw_radar.catalog.models import (
    ProviderKind,
    RunStatus,
    SchedulingLane,
    SourceConfig,
)
from hw_radar.poller.service import run_local_full_lane


class Command(BaseCommand):
    help = "Run one local collection of the synthetic site (config must be on `local`)."

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_PRODUCTION:
            raise CommandError(
                "refused: the synthetic proof site must never be collected in production (MS2-D-42)"
            )
        config = (
            SourceConfig.objects.select_related("source_site")
            .filter(source_site__normalized_name=synthetic.SITE_KEY)
            .first()
        )
        if config is None:
            raise CommandError("the synthetic site does not exist; run apify_synthetic_setup")
        if config.collection_provider != ProviderKind.LOCAL.value:  # .value: django-types quirk
            raise CommandError(
                f"refused: collection_provider={config.collection_provider}; switch first with "
                "apify_synthetic_setup --provider local"
            )
        try:
            commit = fixture_commit()
        except FixtureCommitRequired as exc:
            raise CommandError(f"refused: {exc}") from exc
        lane_state = config.lane_state(SchedulingLane.FULL)
        run = asyncio.run(run_local_full_lane(config, lane_state))
        if run is None:
            raise CommandError("no local adapter is registered for the synthetic site")
        config.refresh_from_db()
        detail = run.detail_json or {}
        self.stdout.write(
            f"scraper_run {run.pk}: status={run.status} fixture_commit={commit} "
            f"records_fetched={run.records_fetched} records_valid={run.records_valid} "
            f"listings_upserted={run.listings_upserted} "
            f"snapshots_appended={run.snapshots_appended} "
            f"listings_delisted={detail.get('listings_delisted')} "
            f"lifecycle_state={config.lifecycle_state} enabled={config.enabled}"
        )
        if run.status != RunStatus.SUCCESS.value:  # .value: django-types quirk
            raise CommandError(f"local run {run.pk} did not succeed: {run.error}")
