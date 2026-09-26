"""Adapter registries: site_key → adapter factory. MS-1d connectors register here.

ADAPTERS is the schedulable registry: every run path the poller owns (scheduled
polls, heartbeats, recovery probes) looks sources up here, so a key absent from
it cannot be collected by the service. RETIRED_ADAPTERS keeps the OQ31-retired
connectors importable for history, their tests and corpus provenance; nothing
that collects reads it, and their fetch()/probe() raise RetiredSourceError
(parse() stays usable offline). The two are disjoint by construction, and
admission.RETIRED_SOURCES is exactly RETIRED_ADAPTERS' keys (pinned by
tests/unit/test_source_admission.py).

eBay's ADAPTERS entry builds only the sweeps the admission matrix admits. The
unfiltered category_sweep_adapter stays reachable through HARVEST_ADAPTERS for
harvest_corpus, which deliberately bypasses the go-live gate: harvesting a
not-yet-admitted category is how the evidence for admitting it gets built.
"""

from collections.abc import Callable
from typing import Final

from hw_radar.acquisition.admission import is_admitted
from hw_radar.acquisition.contracts import SourceAdapter
from hw_radar.acquisition.sources.demo import DemoAdapter
from hw_radar.acquisition.sources.ebay import (
    CATEGORY_SWEEPS,
    EbayAdapter,
    category_sweep_adapter,
)
from hw_radar.acquisition.sources.ebay import SITE_KEY as EBAY_SITE_KEY
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.acquisition.sources.seagate import SeagateAdapter
from hw_radar.acquisition.sources.serverpartdeals import ServerPartDealsAdapter
from hw_radar.acquisition.sources.synthetic import SyntheticAdapter
from hw_radar.acquisition.sources.wd import WdAdapter
from hw_radar.matching.categories import DRIVE


def admitted_ebay_adapter() -> EbayAdapter:
    """The scheduled eBay adapter: only the sweeps whose (ebay, category) cell is ADMITTED.

    The matrix is read when the factory runs, not at import, so each poll
    reflects the matrix of the running release. With every cell NOT_ADMITTED
    the adapter would sweep nothing; the poller never gets that far, because
    admitted_categories("ebay") is empty and no eBay job is built.
    """
    return EbayAdapter(
        category_sweeps=[s for s in CATEGORY_SWEEPS if is_admitted(EBAY_SITE_KEY, s.slug)],
        drive_sweep=is_admitted(EBAY_SITE_KEY, DRIVE),
    )


ADAPTERS: dict[str, Callable[[], SourceAdapter]] = {
    "demo": DemoAdapter,
    "ebay": admitted_ebay_adapter,
    "goharddrive": GoHardDriveAdapter,
    "synthetic": SyntheticAdapter,
    "wd-recertified": WdAdapter,
}

RETIRED_ADAPTERS: Final[dict[str, Callable[[], SourceAdapter]]] = {
    "seagate-recertified": SeagateAdapter,
    "serverpartdeals": ServerPartDealsAdapter,
}

# harvest_corpus's lookup: the schedulable sources with eBay's full sweep set.
# Retired sources are absent, so the harvest cannot reach them either.
HARVEST_ADAPTERS: Final[dict[str, Callable[[], SourceAdapter]]] = {
    **ADAPTERS,
    EBAY_SITE_KEY: category_sweep_adapter,
}

# Registered adapters whose listings are fixtures, not merchant offers. They
# must never enter a precision corpus: tests/unit/test_corpus_schema.py pins
# matching.eval.corpus.CORPUS_SOURCE_KEYS to ADAPTERS | RETIRED_ADAPTERS minus
# these keys (retired sources' historical corpus entries stay loadable).
FIXTURE_SOURCE_KEYS: Final = frozenset({"demo", "synthetic"})
