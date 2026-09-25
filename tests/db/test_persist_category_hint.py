"""append_snapshot persists ParsedListing.category_hint under the reserved attrs_json
key ONLY when set (MS2-D-03), so every hint-less row stays byte-identical to what
MS-1 wrote."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR, NormalizedListing
from hw_radar.acquisition.persist import append_snapshot, upsert_listing
from hw_radar.catalog.models import RetentionClass, SourceSite

pytestmark = pytest.mark.django_db

_ATTRS: dict[str, object] = {"mpn": "ST16000NM001G", "capacity": "16TB"}


def _normalized(category_hint: str | None) -> NormalizedListing:
    return NormalizedListing(
        source_listing_key="hint-1",
        url="https://demo.invalid/hint-1",
        title="Demo 16TB",
        price=Decimal("199.99"),
        fx_rate=Decimal("1"),
        fx_pair="USD/USD",
        fx_rate_date=date(2026, 9, 24),
        fx_source="identity",
        is_international=False,
        attrs=dict(_ATTRS),
        category_hint=category_hint,
    )


def _site() -> SourceSite:
    return SourceSite.objects.get(normalized_name="demo")  # seeded by migration 0005


def test_snapshot_attrs_unchanged_without_hint() -> None:
    record = _normalized(None)
    listing, _ = upsert_listing(_site(), record, RetentionClass.MERCHANT_FACT)
    snapshot = append_snapshot(listing, record, observed_at=timezone.now())
    snapshot.refresh_from_db()
    assert snapshot.attrs_json == dict(record.attrs)
    assert CATEGORY_HINT_ATTR not in snapshot.attrs_json


def test_snapshot_attrs_carry_hint_when_set() -> None:
    record = _normalized("gpu")
    listing, _ = upsert_listing(_site(), record, RetentionClass.MERCHANT_FACT)
    snapshot = append_snapshot(listing, record, observed_at=timezone.now())
    snapshot.refresh_from_db()
    assert snapshot.attrs_json == {**_ATTRS, CATEGORY_HINT_ATTR: "gpu"}
    # The merge copies: the record's own attrs never gain the reserved key.
    assert CATEGORY_HINT_ATTR not in record.attrs
