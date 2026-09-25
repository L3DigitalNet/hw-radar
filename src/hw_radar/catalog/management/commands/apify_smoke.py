"""Start one synthetic Actor run through hw-radar's own path and report it (MS2-D-43, F5a).

    apify_smoke --fixture-commit SHA [--fault-mode MODE] [--build TAG]
                [--max-items N] [--max-pages N] [--max-requests N]
                [--max-bytes N] [--time-budget-secs N]
                [--wait-secs N] [--poll-secs N]

The *Smoke run* of MS2-D-43: the synthetic site's run is started by
jobs.start_provider_run (kill switch, retention and deadline checks, budget
admission by the production LedgerAdmission, the provider_run row, one start
request, the MS2-D-26/-38 response check), never by the Console or the MCP,
which bypass the ledger. Then the command drives jobs.apify_poll_tick, the
same tick the poller's apify-poll job runs, until this run's import is
finalized or rejected or --wait-secs expires, and prints the contract checks:
the provider_run's remote status and build, its completeness and truncation
reason, the rows emitted and imported, its reservation's id, amount, and
settlement state, and its storage state. Apify is reached only through those
two functions.

Exit status: non-zero when the start is refused, denied, or fails (the reason
is printed; a refusal or denial leaves no provider_run and admits no spend),
when the wait expires, and when fault mode `none` does not finalize as
`complete`. A run with a fault mode is a classification experiment, so it
exits zero once its import is terminal, whatever the verdict; read the checks.

Scope and safety:
- Refused when settings.IS_PRODUCTION: synthetic rows never enter the
  production catalog (MS2-D-42 *Environment*).
- The site must already exist (apify_synthetic_setup). The command reads its
  SourceConfig and changes nothing on it: the start path does not read
  `enabled`, so the config stays disabled and unscheduled.
- The spec is per invocation, passed through start_provider_run's `run_specs`
  override; jobs.RUN_SPECS and HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT are
  not consulted. --build (default `candidate`, the tag `apify push` builds
  under) is passed as start_provider_run's `build_tag`; the version-line check
  on the resolved build number is unchanged.
- Each tick runs every selector over every provider_run row, exactly as the
  poller's job does, not only this run's. Storage cleanup and settlement of
  this run continue on later ticks (the poller's, or another smoke's); the
  printed settlement state is the state when the import became terminal.

Requirements: the HW_RADAR_APIFY_* settings a live start needs (kill switch,
Actor id, token, prices, caps, configured account state, ledger id) and a
claimed ledger authority for the current cycle (apify_ledger_claim); any one
missing refuses or denies before spend, and the reason is printed.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Final, NoReturn, cast, get_args

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from hw_radar.acquisition.apify import synthetic
from hw_radar.acquisition.apify.contract import FaultMode, SyntheticCollectorInput
from hw_radar.acquisition.apify.jobs import (
    ActorRunSpec,
    ClientFactory,
    StartResult,
    StartStatus,
    apify_poll_tick,
    start_provider_run,
    synthetic_spec,
)
from hw_radar.catalog.models import (
    ApifySpendReservation,
    ProviderKind,
    ProviderRun,
    RunCompleteness,
    RunKind,
    SourceConfig,
)
from hw_radar.catalog.models.provider import ImportState
from hw_radar.matching.resolver import CatalogResolver

_COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
# An Apify build tag or version ("candidate", "prod", "1.0"); it is only ever a
# query parameter, so the pattern exists to reject typos and empty values.
_BUILD_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TERMINAL_IMPORT: Final = frozenset({ImportState.FINALIZED.value, ImportState.REJECTED.value})
# The default wait covers the whole run timeout plus five minutes for the
# terminal poll and the import; the ceiling keeps a forgotten smoke bounded.
DEFAULT_WAIT_S: Final = synthetic.TIMEOUT_S + 300
MAX_WAIT_S: Final = 3600
# Selector 1 spaces a run's polls by timeout_s / (MAX_RUN_POLLS / 2), 10 s at
# the defaults; ticking faster only re-selects nothing.
DEFAULT_POLL_S: Final = 10
MAX_POLL_S: Final = 60


class Command(BaseCommand):
    help = "Start one synthetic Actor run through admission and the ledger, import it, report."

    def __init__(
        self,
        *args: Any,
        client_factory: ClientFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        # Test seams. None binds the production client (ApifyClient from
        # settings) in both the start and the tick; admission is always the
        # production binding, jobs.BUDGET_ADMISSION.
        super().__init__(*args, **kwargs)
        self._client_factory = client_factory
        self._sleep = sleep
        self._monotonic = monotonic

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--fixture-commit",
            required=True,
            help="40-hex commit of the public repository whose fixture pages the Actor fetches",
        )
        parser.add_argument(
            "--fault-mode", choices=list(get_args(FaultMode)), default=synthetic.DEFAULT_FAULT_MODE
        )
        parser.add_argument("--build", default="candidate", help="Actor build tag to start")
        caps = synthetic.DEFAULT_CAPS
        parser.add_argument("--max-items", type=int, default=caps.max_items)
        parser.add_argument("--max-pages", type=int, default=caps.max_pages)
        parser.add_argument("--max-requests", type=int, default=caps.max_requests)
        parser.add_argument("--max-bytes", type=int, default=caps.max_bytes)
        parser.add_argument("--time-budget-secs", type=int, default=caps.time_budget_secs)
        parser.add_argument(
            "--wait-secs",
            type=int,
            default=DEFAULT_WAIT_S,
            help=f"how long to drive the import before giving up (1-{MAX_WAIT_S})",
        )
        parser.add_argument(
            "--poll-secs",
            type=int,
            default=DEFAULT_POLL_S,
            help=f"pause between poll ticks (1-{MAX_POLL_S})",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_PRODUCTION:
            raise CommandError(
                "refused: the synthetic smoke run never runs in production (MS2-D-42)"
            )
        commit = str(options["fixture_commit"])
        if not _COMMIT_RE.fullmatch(commit):
            raise CommandError("--fixture-commit must be a 40-hex lowercase commit SHA")
        build = str(options["build"])
        if not _BUILD_RE.fullmatch(build):
            raise CommandError(f"--build {build!r} is not a build tag")
        wait_s = int(options["wait_secs"])
        poll_s = int(options["poll_secs"])
        if not 1 <= wait_s <= MAX_WAIT_S:
            raise CommandError(f"--wait-secs must be 1-{MAX_WAIT_S}")
        if not 1 <= poll_s <= MAX_POLL_S:
            raise CommandError(f"--poll-secs must be 1-{MAX_POLL_S}")
        fault_mode = str(options["fault_mode"])
        caps = synthetic.SyntheticCaps(
            max_items=int(options["max_items"]),
            max_pages=int(options["max_pages"]),
            max_requests=int(options["max_requests"]),
            max_bytes=int(options["max_bytes"]),
            time_budget_secs=int(options["time_budget_secs"]),
        )
        spec = synthetic_spec(commit, fault_mode=fault_mode, caps=caps)
        # The start job validates too, but a cap over a contract maximum should
        # fail here, naming the field, rather than as a terse start refusal.
        try:
            SyntheticCollectorInput.model_validate(dict(spec.run_input))
        except ValidationError as exc:
            raise CommandError(f"invalid run input: {exc}") from exc

        config = self._synthetic_config()

        def factory(_config: SourceConfig, _kind: RunKind) -> ActorRunSpec:
            return spec

        result = asyncio.run(
            start_provider_run(
                config,
                run_specs={synthetic.SITE_KEY: factory},
                client_factory=self._client_factory,
                build_tag=build,
            )
        )
        self.stdout.write(f"start: {result.status} {result.reason}".rstrip())
        if result.status is not StartStatus.STARTED:
            self._fail_start(result)
        if result.provider_run_id is None:  # STARTED always carries its row
            raise CommandError("start reported STARTED without a provider_run")
        row = self._drive(result.provider_run_id, wait_s=wait_s, poll_s=poll_s)
        self._report(row)
        if row.import_state not in _TERMINAL_IMPORT:
            raise CommandError(
                f"provider_run {row.pk} import still {row.import_state} after {wait_s}s; "
                "the poller's apify-poll job continues it"
            )
        if fault_mode == synthetic.DEFAULT_FAULT_MODE and (
            row.import_state != ImportState.FINALIZED.value
            or row.completeness != RunCompleteness.COMPLETE.value
        ):
            raise CommandError(
                f"smoke failed: fault mode none ended {row.import_state} / "
                f"{row.completeness or 'unclassified'}"
            )

    def _synthetic_config(self) -> SourceConfig:
        try:
            config = SourceConfig.objects.select_related("source_site").get(
                source_site__normalized_name=synthetic.SITE_KEY
            )
        except SourceConfig.DoesNotExist:
            raise CommandError(
                "no synthetic SourceConfig: run apify_synthetic_setup first"
            ) from None
        if config.collection_provider != ProviderKind.APIFY.value:  # .value: django-types quirk
            raise CommandError(
                f"synthetic SourceConfig has collection_provider={config.collection_provider!r}, "
                "not 'apify'"
            )
        return config

    def _fail_start(self, result: StartResult) -> NoReturn:
        if result.provider_run_id is not None:
            # START_FAILED or MISMATCH_ABORTED: a row exists and may carry
            # admitted spend; the poller's selectors own its cleanup.
            self._report(ProviderRun.objects.get(pk=result.provider_run_id))
        raise CommandError(f"smoke start {result.status}: {result.reason}")

    def _drive(self, provider_run_id: int, *, wait_s: int, poll_s: int) -> ProviderRun:
        deadline = self._monotonic() + wait_s
        while True:
            report = asyncio.run(
                apify_poll_tick(resolver=CatalogResolver(), client_factory=self._client_factory)
            )
            if report.error:
                self.stderr.write(f"poll tick: {report.error}")
            row = ProviderRun.objects.get(pk=provider_run_id)
            if row.import_state in _TERMINAL_IMPORT or self._monotonic() >= deadline:
                return row
            self._sleep(poll_s)

    def _report(self, row: ProviderRun) -> None:
        output = row.run_output or {}
        completeness = output.get("completeness")
        emitted = (
            cast(Mapping[str, object], completeness).get("itemsEmitted")
            if isinstance(completeness, Mapping)
            else None
        )
        lines = [
            f"provider_run: {row.pk} (external run {row.external_run_id or '-'})",
            f"remote_status: {row.remote_status or '-'}",
            f"build_number: {row.build_number or '-'}",
            f"import_state: {row.import_state}",
            f"completeness: {row.completeness or '-'} ({row.completeness_reason or '-'})",
            f"truncation_reason: {row.truncation_reason or '-'}",
            f"reject_reason: {row.stage_detail.get('reject_reason', '-')}",
            f"rows_emitted: {'-' if emitted is None else emitted}",
            f"dataset_item_count: {'-' if row.dataset_item_count is None else row.dataset_item_count}",
            f"rows_imported: {len(row.import_listing_ids)}",
            f"storage_state: {row.storage_state}",
        ]
        reservation = ApifySpendReservation.objects.filter(provider_run=row).first()
        if reservation is None:
            lines.append("reservation: none attached")
        else:
            lines.append(
                f"reservation: {reservation.pk} status={reservation.status} "
                f"estimate_usd={reservation.estimate_usd} "
                f"monitoring_bound_usd={reservation.monitoring_bound_usd} "
                f"actual_usd={reservation.actual_usd if reservation.actual_usd is not None else '-'}"
            )
        for line in lines:
            self.stdout.write(line)
