"""The synthetic proof site's identity and run input (MS2-D-42, -43; F5a).

The synthetic site is the Actor proof source: hw-radar-synthetic-collector
fetches committed fixture pages from the public repository at a pinned commit,
so a run exercises the whole paid path (admission, start, poll, import,
cleanup, settlement) without touching a merchant. This module owns what that
run asks for; acquisition.apify.jobs wraps it into the site's ActorRunSpec and
registers it in RUN_SPECS, and the apify_synthetic_setup and apify_smoke
commands use the same constants, so the site row, the spec, and the smoke
cannot disagree on the key or the scope. The site's local adapter
(acquisition.sources.synthetic, for the AC-4 provider switch) emits its rows
with the same key, hint, scope, and fixture paths.

run_input returns the unvalidated wire (camelCase) dict: validation belongs to
SyntheticCollectorInput, which start_provider_run applies before anything is
reserved, and which apify_smoke applies again up front for a clear error.

Pure: no DB, no settings, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hw_radar.acquisition.apify.contract import INPUT_SCHEMA_VERSION

# Cross-file contract: SOURCE_RETENTION registers this key as merchant_fact
# (acquisition.retention_policy); without that entry every start is refused
# `unknown_retention`.
SITE_KEY: Final = "synthetic"
# `drive` is the seeded first-class HDD/SSD category (migration 0001), and
# every fixture listing is a hard drive, so the resolver runs the real drive
# rules on them. The contract fixtures' `hdd` hint is NOT a seeded Category:
# rows hinted with it would resolve as `unsupported_category` and prove
# nothing about matching.
CATEGORY_HINT: Final = "drive"
COLLECTION_SCOPE: Final = f"{SITE_KEY}:{CATEGORY_HINT}:catalog"
# Relative to the Actor's fixtures/source/ root, which the Actor prepends
# itself (synthetic_collector.core.SOURCE_ROOT); a "source/" prefix here would
# fetch fixtures/source/source/... and 404.
FIXTURE_PATHS: Final = ("catalog/page-1.json", "catalog/page-2.json")
DEFAULT_FAULT_MODE: Final = "none"

# Run options. TIMEOUT_S must stay <= HW_RADAR_APIFY_MAX_TIMEOUT_S, which the
# ledger enforces at admission (unset denies every start, larger denies
# `unbounded_component`), and TIMEOUT_S + HW_RADAR_APIFY_IMPORT_MARGIN must fit
# the storage deadline, which start_provider_run enforces.
MEMORY_MB: Final = 256
TIMEOUT_S: Final = 300


@dataclass(frozen=True, slots=True)
class SyntheticCaps:
    """The Actor input caps; the defaults sit far below the contract maxima.

    Two fixture pages of three listings fit inside them with room to spare, so
    fault mode `none` yields a complete run; the reservation is priced from
    max_requests and max_bytes (budget.RunShape), so small caps keep the proof
    cheap. time_budget_secs stays under TIMEOUT_S so the Actor reports its own
    time truncation before the platform times the run out.
    """

    max_items: int = 10
    max_pages: int = 2
    max_requests: int = 4
    max_bytes: int = 262_144
    time_budget_secs: int = 120


DEFAULT_CAPS: Final = SyntheticCaps()


def run_input(
    fixture_commit: str,
    *,
    fault_mode: str = DEFAULT_FAULT_MODE,
    caps: SyntheticCaps = DEFAULT_CAPS,
) -> dict[str, object]:
    """Return the hw-radar-input/v1 wire input for one synthetic run (unvalidated)."""
    return {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "siteKey": SITE_KEY,
        "collectionScope": COLLECTION_SCOPE,
        "categoryHint": CATEGORY_HINT,
        "maxItems": caps.max_items,
        "maxPages": caps.max_pages,
        "maxRequests": caps.max_requests,
        "maxBytes": caps.max_bytes,
        "timeBudgetSecs": caps.time_budget_secs,
        "fixtureCommit": fixture_commit,
        "fixturePaths": list(FIXTURE_PATHS),
        "faultMode": fault_mode,
    }
