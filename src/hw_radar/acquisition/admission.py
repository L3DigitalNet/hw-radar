"""Source x category admission matrix: the canonical production enablement framework.

Owner decisions of 2026-09-26 (OQ31 and the matrix decision) replace the global
SA-004 enable order with one explicit cell per (source registry key, category).
Each combination is admitted on its own evidence: an unrelated source or
category is never a prerequisite, and a retired combination is unavailable.

Contract for callers:
- `is_admitted(source, category)` is True only for an ADMITTED cell. Every other
  status, a source key or category the matrix does not name, and a category
  outside MATRIX_CATEGORIES (the basic-watch slugs, for example) all read as
  not admitted: the matrix fails closed.
- `admitted_categories(source)` empty means the source has nothing it may
  collect, so the poller builds no job for it however its SourceConfig reads.
- `is_retired(source)` is independent of the matrix contents: a retired source
  stays unrunnable even if someone later edits its cells.
- `ensure_not_retired(source)` raises RetiredSourceError for a retired source.
  It is the collection-boundary check (run_collection, run_heartbeat and the
  retired adapters' fetch/probe), below the orchestration gates that merely
  decline to schedule a retired source.
- `SourceConfig.enabled` remains the operator's go-live switch; the matrix is the
  ceiling above it. An admitted cell does not enable anything by itself, and an
  enabled row does not admit anything the matrix does not.

Changing a cell or the retired set is a code change: a reviewed commit and a
release, never a settings value, environment variable or database row. That is
deliberate — admission records a source-admission review (Terms, robots,
evidence gates), and a runtime override would let an operator flip a
permission-bearing decision with no review trail. Re-admitting a retired source
requires a fresh source-admission review on materially changed Terms or
written permission; nothing here ever re-admits one automatically.

Pure module (no Django, no ORM, no adapter imports): the model layer, the
adapter registry and the eval tooling all import it, so it must not import any
of them back. The source keys are therefore literals; tests pin them against
`acquisition.sources.ADAPTERS | RETIRED_ADAPTERS`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from hw_radar.matching.categories import DRIVE
from hw_radar.matching.rules import cpu, gpu, ram

# OQ31 (owner decision 2026-09-26): both sites' reviewed current Terms conflict
# with the automated collection their connectors perform. This bars every
# collection path — local adapter, private scheduling, and Apify alike, since a
# different collector does not change the source's policy eligibility.
# matching.eval imports this exact name and type; keep both stable.
RETIRED_SOURCES: Final[frozenset[str]] = frozenset({"serverpartdeals", "seagate-recertified"})
RETIRED_REASON: Final = (
    "retired / permission-required — not production-enableable (OQ31: the site's "
    "reviewed current Terms conflict with automated collection; re-admission needs "
    "a fresh source-admission review or written permission)"
)


class CellStatus(StrEnum):
    RETIRED = "retired"
    NOT_ADMITTED = "not_admitted"
    ADMITTED = "admitted"
    NOT_APPLICABLE = "not_applicable"


# The owner's matrix columns: drive (HDD and SSD share the single `drive` slug),
# CPU, GPU and RAM. The basic-watch categories are registered for matching but
# are not acquisition columns, so they are never admitted.
MATRIX_CATEGORIES: Final[tuple[str, ...]] = (DRIVE, cpu.SLUG, gpu.SLUG, ram.SLUG)

_R, _N, _X = CellStatus.RETIRED, CellStatus.NOT_ADMITTED, CellStatus.NOT_APPLICABLE


def _row(
    source: str, drive: CellStatus, cpu_: CellStatus, gpu_: CellStatus, ram_: CellStatus
) -> dict[tuple[str, str], CellStatus]:
    return {
        (source, category): status
        for category, status in zip(MATRIX_CATEGORIES, (drive, cpu_, gpu_, ram_), strict=True)
    }


# Current truth (2026-09-26): no combination has passed its gates, so every
# live cell is NOT_ADMITTED; admitting one means writing CellStatus.ADMITTED
# into that cell in a reviewed commit. eBay is the only multi-category source;
# the recertified-drive storefronts sell drives only.
#
# demo and synthetic are fixture sources (sources.FIXTURE_SOURCE_KEYS): their
# listings are test fixtures, not merchant offers, so no category applies and
# the scheduler never builds a job for them. synthetic_collect_local and the
# Apify smoke commands drive the synthetic site directly and are unaffected.
ADMISSION_MATRIX: Final[Mapping[tuple[str, str], CellStatus]] = MappingProxyType(
    {
        #                  drive cpu gpu ram
        **_row("ebay", _N, _N, _N, _N),
        **_row("wd-recertified", _N, _X, _X, _X),
        **_row("goharddrive", _N, _X, _X, _X),
        **_row("serverpartdeals", _R, _R, _R, _R),
        **_row("seagate-recertified", _R, _R, _R, _R),
        **_row("demo", _X, _X, _X, _X),
        **_row("synthetic", _X, _X, _X, _X),
    }
)


class RetiredSourceError(RuntimeError):
    """A collection was attempted for a retired source; nothing was fetched."""

    def __init__(self, source: str) -> None:
        super().__init__(f"{source} is {RETIRED_REASON}")
        self.source = source


def is_retired(source: str) -> bool:
    return source in RETIRED_SOURCES


def ensure_not_retired(source: str) -> None:
    """Raise RetiredSourceError when `source` is retired; otherwise do nothing.

    The collection-boundary counterpart of the orchestration gates: the
    pipeline's run_collection and run_heartbeat, and the retained retired
    adapters' own network entry points, call it before any request or run row,
    so a caller that bypasses the scheduler still cannot collect.
    """
    if is_retired(source):
        raise RetiredSourceError(source)


def is_admitted(source: str, category: str) -> bool:
    # Both guards precede the lookup so a stray ADMITTED entry — for a retired
    # source, or for a slug that is not a matrix column — can never admit.
    if is_retired(source) or category not in MATRIX_CATEGORIES:
        return False
    return ADMISSION_MATRIX.get((source, category)) is CellStatus.ADMITTED


def admitted_categories(source: str) -> frozenset[str]:
    return frozenset(c for c in MATRIX_CATEGORIES if is_admitted(source, c))


def scheduling_block(source: str) -> str | None:
    """Why the poller must not run `source`, or None when the matrix allows it.

    The poller logs this string verbatim when it skips an enabled source, so an
    operator sees "retired" rather than a silent absence.
    """
    if is_retired(source):
        return f"retired: {RETIRED_REASON}"
    if not admitted_categories(source):
        return "no admitted category in the source x category admission matrix"
    return None
