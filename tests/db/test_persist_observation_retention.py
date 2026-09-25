"""MS2-D-37: a snapshot carries its own observation's retention, not the listing's.

Once an older observation can be appended behind a newer one (MS2-D-30), copying
the listing's deadline would let the older snapshot outlive its own freshness
window. AMAZON_EPHEMERAL stands in for a bounded, non-delete-on-delist class
with the six-hour window the plan's fixture source uses.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from ordering_support import HOUR, SIX_HOURS, listing, make_site, observe, record, sweep

from hw_radar.acquisition.persist import append_snapshot, upsert_listing
from hw_radar.catalog.management.commands.purge_expired import sweep_expired
from hw_radar.catalog.models import Listing, OfferSnapshot, RetentionClass

pytestmark = pytest.mark.django_db

BOUNDED = RetentionClass.AMAZON_EPHEMERAL


def test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline() -> None:
    site = make_site()
    t1 = timezone.now() - HOUR
    t0 = t1 - HOUR
    observe(site, t1, [record("x")], BOUNDED)
    observe(site, t0, [record("x")], BOUNDED)

    x = listing(site, "x")
    assert x.last_observed_at == t1
    assert x.expires_at == t1 + SIX_HOURS
    assert OfferSnapshot.objects.get(listing=x, observed_at=t0).expires_at == t0 + SIX_HOURS
    assert OfferSnapshot.objects.get(listing=x, observed_at=t1).expires_at == t1 + SIX_HOURS

    sweep_expired(now=t0 + SIX_HOURS + timedelta(minutes=1))

    assert Listing.objects.filter(pk=x.pk).exists()
    assert list(OfferSnapshot.objects.filter(listing=x).values_list("observed_at", flat=True)) == [
        t1
    ]


def test_older_bounded_observation_after_newer_delist_is_not_snapshotted() -> None:
    site = make_site()
    now = timezone.now()
    t0, t1, t2 = now - 3 * HOUR, now - 2 * HOUR, now - HOUR
    observe(site, t0, [record("x")], BOUNDED)
    sweep(site, t2, scope_key=None, seen=set())

    observe(site, t1, [record("x")], BOUNDED)

    x = listing(site, "x")
    assert not OfferSnapshot.objects.filter(listing=x, observed_at=t1).exists()


def test_merchant_fact_older_observation_appends_history_with_null_expiry() -> None:
    site = make_site()
    now = timezone.now()
    t0, t1, t2 = now - 3 * HOUR, now - 2 * HOUR, now - HOUR
    observe(site, t0, [record("x")])
    sweep(site, t2, scope_key=None, seen=set())

    observe(site, t1, [record("x")])

    snapshot = OfferSnapshot.objects.get(listing=listing(site, "x"), observed_at=t1)
    assert snapshot.expires_at is None
    assert snapshot.retention_class == RetentionClass.MERCHANT_FACT


def test_append_snapshot_default_copies_listing_retention() -> None:
    site = make_site()
    at = timezone.now()
    normalized = record("x")
    x, _ = upsert_listing(site, normalized, BOUNDED, expires_at=at + SIX_HOURS)

    snapshot = append_snapshot(x, normalized, observed_at=at)

    assert (snapshot.retention_class, snapshot.expires_at) == (x.retention_class, x.expires_at)
