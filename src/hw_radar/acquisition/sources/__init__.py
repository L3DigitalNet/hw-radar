"""Adapter registry: site_key → adapter factory. MS-1d connectors register here."""

from collections.abc import Callable
from typing import Final

from hw_radar.acquisition.contracts import SourceAdapter
from hw_radar.acquisition.sources.demo import DemoAdapter
from hw_radar.acquisition.sources.ebay import category_sweep_adapter
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.acquisition.sources.seagate import SeagateAdapter
from hw_radar.acquisition.sources.serverpartdeals import ServerPartDealsAdapter
from hw_radar.acquisition.sources.synthetic import SyntheticAdapter
from hw_radar.acquisition.sources.wd import WdAdapter

ADAPTERS: dict[str, Callable[[], SourceAdapter]] = {
    "demo": DemoAdapter,
    "ebay": category_sweep_adapter,
    "goharddrive": GoHardDriveAdapter,
    "seagate-recertified": SeagateAdapter,
    "serverpartdeals": ServerPartDealsAdapter,
    "synthetic": SyntheticAdapter,
    "wd-recertified": WdAdapter,
}

# Registered adapters whose listings are fixtures, not merchant offers. They
# must never enter a precision corpus: tests/unit/test_corpus_schema.py pins
# matching.eval.corpus.CORPUS_SOURCE_KEYS to ADAPTERS minus these keys.
FIXTURE_SOURCE_KEYS: Final = frozenset({"demo", "synthetic"})
