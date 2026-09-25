"""MS2-D-25 source retention registry: the retention a remote import must use.

A remote import never falls back to run_collection's merchant_fact default, so
the registry lookup is the only way an import learns its retention; an
unregistered site must fail loudly rather than persist under the default.
"""

from __future__ import annotations

import pytest

from hw_radar.acquisition.contracts import AdapterRetention, adapter_retention
from hw_radar.acquisition.retention_policy import (
    SOURCE_RETENTION,
    UnknownSourceRetention,
    source_retention,
)
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.catalog.models import RetentionClass


def test_unregistered_site_raises_unknown_source_retention() -> None:
    with pytest.raises(UnknownSourceRetention, match="not-a-registered-site"):
        source_retention("not-a-registered-site")


def test_synthetic_actor_site_is_registered_as_indefinite_merchant_fact() -> None:
    # MS2-D-42: the synthetic proof site is Actor-collected, and MS2-D-33 denies
    # a bounded class an Actor path, so it is merchant_fact with no TTL.
    assert source_retention("synthetic") == AdapterRetention(
        retention_class=RetentionClass.MERCHANT_FACT, expires_policy=None
    )


@pytest.mark.parametrize("site_key", sorted(ADAPTERS))
def test_registry_agrees_with_each_local_adapter_retention(site_key: str) -> None:
    # The local path still reads adapter_retention(adapter) in MS-2; the two
    # sources of truth must not drift, or the same site's rows would expire
    # differently depending on who collected them.
    assert site_key in SOURCE_RETENTION
    assert source_retention(site_key) == adapter_retention(ADAPTERS[site_key]())
