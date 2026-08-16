"""DR-001 sweeper behavior against the real schema: hypertables, composite PKs,
cascades, and the anchor rows the sweep must leave alone."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from hw_radar.catalog.management.commands import purge_expired
from hw_radar.catalog.management.commands.purge_expired import sweep_expired
from hw_radar.catalog.models import (
    AvailabilityHeartbeatObservation,
    HeartbeatDecision,
    Listing,
    OfferSnapshot,
    RawPayload,
    RetentionClass,
    SourceSite,
    SourceType,
    StockStatus,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=6)


@pytest.fixture
def site() -> SourceSite:
    # Distinct normalized_name so this fixture never collides with the
    # migration-0005 seed sites (see tests/db/test_market.py).
    return SourceSite.objects.create(
        name="Purge Test Site",
        normalized_name="purge-test-site",
        source_type=SourceType.MARKETPLACE,
    )


def _payload(key: str, retention_class: RetentionClass, expires_at: datetime | None) -> RawPayload:
    return RawPayload.objects.create(
        provider="ebay",
        endpoint=f"/buy/browse/v1/{key}",
        fetched_at=PAST,
        content_hash=key.ljust(64, "0"),
        http_status=200,
        retention_class=retention_class,
        expires_at=expires_at,
    )


def _listing(site: SourceSite, key: str, expires_at: datetime | None) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.invalid/{key}",
        url_hash=key.ljust(64, "b"),
        title_raw=f"Drive {key}",
        retention_class=(
            RetentionClass.EBAY_LISTING_OBSERVATION if expires_at else RetentionClass.MERCHANT_FACT
        ),
        expires_at=expires_at,
    )


def _snapshot(
    listing: Listing,
    observed_at: datetime,
    *,
    retention_class: RetentionClass = RetentionClass.MERCHANT_FACT,
    expires_at: datetime | None = None,
) -> OfferSnapshot:
    return OfferSnapshot.objects.create(
        listing=listing,
        observed_at=observed_at,
        item_price=Decimal("100.00"),
        stock_status=StockStatus.IN_STOCK,
        retention_class=retention_class,
        expires_at=expires_at,
    )


def _observation(site: SourceSite, sku: str, expires_at: datetime) -> None:
    AvailabilityHeartbeatObservation.objects.create(
        source_site=site,
        source_sku=sku,
        observed_at=PAST,
        decision=HeartbeatDecision.UNCHANGED,
        fingerprint="c" * 64,
        retention_class=RetentionClass.AVAILABILITY_HEARTBEAT,
        expires_at=expires_at,
    )


def test_expired_bounded_rows_are_deleted(site: SourceSite) -> None:
    _payload("expired", RetentionClass.EBAY_LISTING_OBSERVATION, PAST)
    # Composite-PK hypertable row: pins that the pk-batching path works for a
    # model whose primary key is a tuple, not an integer.
    _observation(site, "SKU-EXPIRED", PAST)

    report = sweep_expired(now=NOW)

    assert report.counts["catalog.RawPayload"] == 1
    assert report.counts["catalog.AvailabilityHeartbeatObservation"] == 1
    assert not RawPayload.objects.filter(provider="ebay").exists()
    assert not AvailabilityHeartbeatObservation.objects.filter(source_site=site).exists()


def test_unexpired_and_indefinite_rows_survive(site: SourceSite) -> None:
    _payload("future", RetentionClass.EBAY_LISTING_OBSERVATION, FUTURE)
    _payload("forever", RetentionClass.MERCHANT_FACT, None)
    _observation(site, "SKU-FUTURE", FUTURE)
    keeper = _listing(site, "keeper", None)
    _snapshot(keeper, PAST)

    report = sweep_expired(now=NOW)

    assert report.total == 0
    assert RawPayload.objects.count() == 2
    assert AvailabilityHeartbeatObservation.objects.filter(source_site=site).count() == 1
    assert OfferSnapshot.objects.filter(listing=keeper).count() == 1


def test_expired_listing_survives_as_an_audit_anchor(site: SourceSite) -> None:
    # Listing is a retention ANCHOR: deleting it would cascade into
    # listing_resolution and destroy the DR-010 trail, so an expired listing row
    # stays and DR-008 is met by deleting its observation rows instead. The
    # snapshots here carry the eBay bounded class, exactly as append_snapshot
    # copies it from the listing in production.
    expired = _listing(site, "expired-anchor", PAST)
    _snapshot(
        expired, PAST, retention_class=RetentionClass.EBAY_LISTING_OBSERVATION, expires_at=PAST
    )
    _snapshot(
        expired,
        PAST - timedelta(hours=1),
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=PAST,
    )
    keeper = _listing(site, "keeper", None)
    _snapshot(keeper, PAST)

    report = sweep_expired(now=NOW)

    assert "catalog.Listing" not in report.counts  # never promised, never deleted
    assert report.counts["catalog.OfferSnapshot"] == 2
    assert sorted(Listing.objects.values_list("source_listing_key", flat=True)) == [
        "expired-anchor",
        "keeper",
    ]
    assert not OfferSnapshot.objects.filter(listing=expired).exists()  # evidence is gone
    assert OfferSnapshot.objects.filter(listing=keeper).count() == 1


def test_dry_run_never_promises_to_delete_an_anchor(site: SourceSite) -> None:
    _listing(site, "expired-anchor", PAST)

    report = sweep_expired(now=NOW, dry_run=True)

    assert "catalog.Listing" not in report.counts


def test_row_refreshed_between_select_and_delete_survives(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The poller re-observing a source extends expires_at. If the DELETE trusted
    # the pk batch collected by the SELECT, that refreshed row would be destroyed
    # despite no longer being expired. Simulate the interleaving by refreshing the
    # row inside the batch generator, i.e. after selection and before deletion.
    payload = _payload("racy", RetentionClass.EBAY_LISTING_OBSERVATION, PAST)
    real_batches = purge_expired._batches  # pyright: ignore[reportPrivateUsage]

    def racing_batches(queryset: object, batch_size: int) -> object:
        for pks in real_batches(queryset, batch_size):  # pyright: ignore[reportArgumentType]
            RawPayload.objects.filter(pk__in=pks).update(expires_at=FUTURE)
            yield pks

    monkeypatch.setattr(purge_expired, "_batches", racing_batches)

    report = sweep_expired(now=NOW)  # must terminate, not spin on the same pks

    assert RawPayload.objects.filter(pk=payload.pk).exists()
    assert report.counts["catalog.RawPayload"] == 0


def test_dry_run_reports_without_deleting(site: SourceSite) -> None:
    _payload("expired", RetentionClass.EBAY_LISTING_OBSERVATION, PAST)
    _observation(site, "SKU-EXPIRED", PAST)

    report = sweep_expired(now=NOW, dry_run=True)

    assert report.dry_run
    assert report.counts["catalog.RawPayload"] == 1
    assert report.counts["catalog.AvailabilityHeartbeatObservation"] == 1
    assert RawPayload.objects.count() == 1
    assert AvailabilityHeartbeatObservation.objects.filter(source_site=site).count() == 1


def test_batching_drains_a_backlog_larger_than_one_batch(site: SourceSite) -> None:
    # batch_size=2 with 5 rows exercises the re-query loop and its terminating
    # short batch; a loop that reused the first LIMIT window would hang here.
    for index in range(5):
        _payload(f"batch{index}", RetentionClass.EBAY_LISTING_OBSERVATION, PAST)

    report = sweep_expired(now=NOW, batch_size=2)

    assert report.counts["catalog.RawPayload"] == 5
    assert RawPayload.objects.count() == 0


def test_command_reports_counts(site: SourceSite) -> None:
    # The command takes no --now, so the fixture row must be expired against the
    # real clock rather than this module's frozen NOW.
    _payload(
        "expired", RetentionClass.EBAY_LISTING_OBSERVATION, timezone.now() - timedelta(hours=1)
    )
    out = StringIO()

    call_command("purge_expired", "--dry-run", stdout=out)
    assert "catalog.RawPayload" in out.getvalue()
    assert RawPayload.objects.count() == 1

    call_command("purge_expired", stdout=out)
    assert RawPayload.objects.count() == 0
