"""ParsedListing.category_hint (MS2-D-03): an optional, slug-validated category the
collector asserts from the query scope it ran. The hint rides its own field; the
free-form `attrs` bag may not smuggle a competing value under the reserved key."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR, NormalizedListing, ParsedListing

_BASE: dict[str, object] = {
    "source_listing_key": "k1",
    "url": "https://example.test/k1",
    "title": "Some listing",
    "price": Decimal("10.00"),
}


def test_category_hint_defaults_to_none() -> None:
    assert ParsedListing.model_validate(_BASE).category_hint is None


@pytest.mark.parametrize("hint", ["gpu", "basic-watch", "drive"])
def test_category_hint_accepts_slug(hint: str) -> None:
    assert ParsedListing.model_validate({**_BASE, "category_hint": hint}).category_hint == hint


@pytest.mark.parametrize("hint", ["GPU", "gpu card", "a" * 51, "", "-gpu"])
def test_category_hint_rejects_non_slug(hint: str) -> None:
    with pytest.raises(ValidationError):
        ParsedListing.model_validate({**_BASE, "category_hint": hint})


def test_category_hint_accepts_max_length_slug() -> None:
    hint = "a" * 50
    assert ParsedListing.model_validate({**_BASE, "category_hint": hint}).category_hint == hint


def test_attrs_may_not_carry_reserved_category_hint_key() -> None:
    assert CATEGORY_HINT_ATTR == "category_hint"
    with pytest.raises(ValidationError, match="category_hint"):
        ParsedListing.model_validate({**_BASE, "attrs": {"category_hint": "gpu"}})


def test_normalized_listing_inherits_hint() -> None:
    normalized = NormalizedListing.model_validate(
        {
            **_BASE,
            "category_hint": "gpu",
            "fx_rate": Decimal(1),
            "fx_pair": "USD/USD",
            "fx_rate_date": date(2026, 9, 24),
            "fx_source": "identity",
            "is_international": False,
        }
    )
    assert normalized.category_hint == "gpu"
    with pytest.raises(ValidationError):
        NormalizedListing.model_validate(
            {**normalized.model_dump(), "attrs": {CATEGORY_HINT_ATTR: "gpu"}}
        )
