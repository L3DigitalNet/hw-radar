"""Provider-independent source retention registry (MS2-D-25).

SOURCE_RETENTION maps a SourceSite `normalized_name` (the site_key) to the DR-001
retention every row collected for that site must carry, whoever collected it.
A remote import has no adapter to read a retention from, so this registry is its
only source: source_retention() raises UnknownSourceRetention for an
unregistered site, and the import must be rejected rather than fall back to
run_collection's merchant_fact default, which would persist bounded evidence
indefinitely where the DR-001 sweeper can never reach it.

The local path keeps adapter_retention(adapter) in MS-2. The two must agree for
every registered adapter (tests/unit/test_retention_policy.py pins it), so a
retention change edits the adapter and this table together.

Rejected: a retention column on SourceConfig. Legal retention would become
admin-editable, and expires_policy is a callable a column cannot hold; a code
table is reviewed and tested.

Pure: no DB access, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from hw_radar.acquisition.contracts import AdapterRetention
from hw_radar.acquisition.sources.ebay import EbayAdapter
from hw_radar.catalog.models import RetentionClass

_MERCHANT_FACT: Final = AdapterRetention(
    retention_class=RetentionClass.MERCHANT_FACT, expires_policy=None
)

SOURCE_RETENTION: Final[Mapping[str, AdapterRetention]] = MappingProxyType(
    {
        "demo": _MERCHANT_FACT,
        # DR-008 bounded class with a <=6h TTL. The policy callable is the eBay
        # adapter's own, not a copy, so both paths stamp the identical expiry.
        # MS2-D-33 denies a bounded site an Actor path, so no import reads this
        # entry in MS-2; it is registered so the table covers every site.
        "ebay": AdapterRetention(
            retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
            expires_policy=EbayAdapter.expires_policy,
        ),
        "goharddrive": _MERCHANT_FACT,
        "seagate-recertified": _MERCHANT_FACT,
        "serverpartdeals": _MERCHANT_FACT,
        "wd-recertified": _MERCHANT_FACT,
        # Actor-only synthetic proof site (MS2-D-42); indefinite because MS2-D-33
        # denies bounded classes an Actor path.
        "synthetic": _MERCHANT_FACT,
    }
)


class UnknownSourceRetention(LookupError):
    """The site has no registered retention, so nothing collected for it may persist."""


def source_retention(site_key: str) -> AdapterRetention:
    """Return the registered retention for a site, or raise UnknownSourceRetention.

    Never defaults: an unregistered site is a configuration error the caller
    must surface as a rejected import (MS2-D-25).
    """
    try:
        return SOURCE_RETENTION[site_key]
    except KeyError:
        raise UnknownSourceRetention(f"no registered retention for site {site_key!r}") from None
