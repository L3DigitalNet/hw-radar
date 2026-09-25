"""MS2-D-30 / MS2-D-35 ordering guards: a delayed observation never rewinds current state.

A durable import can land after a newer run or a newer delist. Each test replays
one such interleaving at explicit event times through the extracted stages
(ordering_support), which both the local pipeline and the importer compose.
"""

from __future__ import annotations

import pytest
from ordering_support import HOUR, T0, listing, make_site, observe, record, sweep

from hw_radar.catalog.models import DelistReason, OfferSnapshot, RetentionClass

pytestmark = pytest.mark.django_db

S = "d10order:gpu:q1"
T1, T2 = T0 + HOUR, T0 + 2 * HOUR


def test_reverse_completion_older_import_does_not_overwrite_newer_listing_state() -> None:
    site = make_site()
    newer = record("x", title="R2 title", scope=S, condition="used", international=True)
    older = record("x", title="R1 title", scope=S, condition="new", international=False)

    observe(site, T2, [newer])
    observe(site, T1, [older])

    x = listing(site, "x")
    assert (x.title_raw, x.condition_label_raw, x.is_international) == ("R2 title", "used", True)
    assert x.last_observed_at == T2
    history = list(OfferSnapshot.objects.filter(listing=x).order_by("observed_at"))
    assert [s.observed_at for s in history] == [T1, T2]


def test_older_import_does_not_revive_listing_delisted_by_newer_evidence() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    sweep(site, T2, scope_key=S, seen=set())
    assert listing(site, "x").delisted_at == T2

    observe(site, T1, [record("x", title="late", scope=S)])

    x = listing(site, "x")
    assert x.delisted_at == T2
    assert x.title_raw == "Drive"


def test_older_complete_import_does_not_delist_listing_observed_by_newer_run() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    # "y" is first seen by the newer run and is unknown to the older sweep.
    observe(site, T2, [record("x", scope=S), record("y", scope=S)])

    delisted = sweep(site, T1, scope_key=S, seen=set())

    assert delisted == 0
    assert listing(site, "x").delisted_at is None
    assert listing(site, "y").delisted_at is None


def test_legacy_listing_without_watermark_accepts_first_observation() -> None:
    site = make_site()
    observe(site, T2, [record("x", scope=S)])
    # A row written before migration 0021 carries no watermark.
    type(listing(site, "x")).objects.filter(source_listing_key="x").update(last_observed_at=None)

    observe(site, T1, [record("x", title="first guarded", scope=S)])

    x = listing(site, "x")
    assert x.title_raw == "first guarded"
    assert x.last_observed_at == T1


@pytest.mark.parametrize(
    "retention_class",
    [RetentionClass.EBAY_LISTING_OBSERVATION, RetentionClass.MERCHANT_FACT],
)
@pytest.mark.parametrize("via", ["complete_sweep", "stale_absence"])
def test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry(
    retention_class: RetentionClass, via: str
) -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)], retention_class)
    if via == "complete_sweep":
        sweep(site, T2, scope_key=S, seen=set())
    else:
        x = listing(site, "x")
        assert x.mark_delisted(DelistReason.ABSENT_STALE, when=T2)
        type(x).objects.filter(pk=x.pk).update(last_absence_at=T2)
    before = listing(site, "x")

    observe(site, T1, [record("x", title="late", scope=S)], retention_class)

    x = listing(site, "x")
    assert x.delisted_at == T2
    assert x.title_raw == before.title_raw
    assert x.expires_at == before.expires_at
    assert x.last_observed_at == T0
    if retention_class is RetentionClass.EBAY_LISTING_OBSERVATION:
        assert x.is_content_redacted()


@pytest.mark.parametrize("scope_key", [S, None])
@pytest.mark.parametrize(
    "retention_class",
    [RetentionClass.MERCHANT_FACT, RetentionClass.AMAZON_EPHEMERAL],
)
def test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing(
    scope_key: str | None, retention_class: RetentionClass
) -> None:
    site = make_site()
    sweep(site, T2, scope_key=scope_key, seen=set())

    observe(site, T1, [record("k", scope=scope_key)], retention_class)

    k = listing(site, "k")
    assert k.delisted_at == T2
    assert k.last_absence_at == T2
    assert k.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    snapshots = OfferSnapshot.objects.filter(listing=k)
    # Merchant facts keep the t1 snapshot as history; a bounded class's t1
    # snapshot would be born already expired by the newer absence.
    assert snapshots.count() == (1 if retention_class is RetentionClass.MERCHANT_FACT else 0)

    later = T2 + HOUR
    observe(site, later, [record("k", scope=scope_key)], retention_class)
    relisted = listing(site, "k")
    assert relisted.pk == k.pk
    assert relisted.delisted_at is None


def test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    x = listing(site, "x")
    assert x.mark_delisted(DelistReason.ABSENT_STALE, when=T0 + HOUR / 2)
    sweep(site, T2, scope_key=S, seen=set())

    observe(site, T1, [record("x", scope=S)])

    assert listing(site, "x").delisted_at == T0 + HOUR / 2


def test_delayed_import_bumps_last_seen_only_forward_and_only_when_current_eligible() -> None:
    site = make_site()
    observe(site, T2, [record("x", scope=S)])
    seen_after_newer = listing(site, "x").last_seen

    observe(site, T1, [record("x", scope=S)])
    assert listing(site, "x").last_seen == seen_after_newer

    observe(site, T2 + HOUR, [record("x", scope=S)])
    assert listing(site, "x").last_seen > seen_after_newer
