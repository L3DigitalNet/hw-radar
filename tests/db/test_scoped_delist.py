"""MS-2 Slice D task D2 (MS2-D-12): the delist stage acts on one collection scope.

A complete per-category sweep on a multi-category site enumerates only its own
category, so without the scope filter it would delist every other category's
listings (Map Q6b). The filter maps a `None` scope key to `collection_scope IS
NULL` rather than to "every scope": every pre-D listing and every pre-D
DelistScope is the NULL scope, so the legacy path keeps acting on exactly the
rows it always did, and a scoped sweep can no longer reach them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hw_radar.acquisition.contracts import DelistScope
from hw_radar.acquisition.pipeline import (
    _apply_delist,  # pyright: ignore[reportPrivateUsage] - the delist stage is the unit under test
)
from hw_radar.catalog.models import (
    DelistReason,
    Listing,
    RetentionClass,
    SourceSite,
    SourceType,
)

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
GPU = "d2delist:gpu:q1"
RAM = "d2delist:ram:q1"
GRACE = timedelta(hours=1)


@pytest.fixture
def site() -> SourceSite:
    return SourceSite.objects.create(
        name="d2delist", normalized_name="d2delist", source_type=SourceType.OTHER
    )


def _listing(site: SourceSite, key: str, scope: str | None) -> Listing:
    # merchant_fact: no delete-on-delist redaction and no TTL, so the assertions
    # below see only the delist mark itself.
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key.ljust(64, "0"),
        title_raw=f"listing {key}",
        retention_class=RetentionClass.MERCHANT_FACT,
        collection_scope=scope,
    )


def _seed(site: SourceSite) -> None:
    for key, scope in (("gpu-1", GPU), ("gpu-2", GPU), ("ram-1", RAM), ("null-1", None)):
        _listing(site, key, scope)
    # Age every row past the grace so the stale-absence case is decided by the
    # scope filter alone, not by last_seen.
    Listing.objects.filter(source_site=site).update(last_seen=T0 - 2 * GRACE)


def _delisted(site: SourceSite) -> set[str]:
    return set(
        Listing.objects.filter(source_site=site, delisted_at__isnull=False).values_list(
            "source_listing_key", flat=True
        )
    )


def test_scoped_complete_sweep_cannot_delist_other_scopes(site: SourceSite) -> None:
    _seed(site)
    scope = DelistScope(
        seen_keys=frozenset({"gpu-1"}),
        observed_at=T0,
        complete=True,
        absence_grace=GRACE,
        scope_key=GPU,
    )

    assert _apply_delist(site, scope, continuous_since=None) == 1

    assert _delisted(site) == {"gpu-2"}
    gpu_2 = Listing.objects.get(source_site=site, source_listing_key="gpu-2")
    assert gpu_2.delist_reason == DelistReason.ABSENT_FROM_SWEEP


def test_scoped_stale_sweep_cannot_delist_other_scopes(site: SourceSite) -> None:
    _seed(site)
    scope = DelistScope(
        seen_keys=frozenset(),
        observed_at=T0,
        complete=False,
        absence_grace=GRACE,
        scope_key=RAM,
    )

    assert _apply_delist(site, scope, continuous_since=T0 - 2 * GRACE) == 1

    assert _delisted(site) == {"ram-1"}


def test_scopeless_sweep_acts_on_the_null_scope_only(site: SourceSite) -> None:
    # DelistScope's default scope_key is None, so every existing adapter keeps
    # building the legacy NULL-scope sweep without a code change.
    _seed(site)
    scope = DelistScope(seen_keys=frozenset(), observed_at=T0, complete=True, absence_grace=GRACE)
    assert scope.scope_key is None

    assert _apply_delist(site, scope, continuous_since=None) == 1

    assert _delisted(site) == {"null-1"}
