"""Codex s8 round-3 findings 6, 7 and 8 at the pure layers: the shared
negation window over condition phrases, denied conditions as negative
evidence against a source's declared condition, and the review fingerprint's
identity context."""

from __future__ import annotations

import json

import pytest

from hw_radar.matching import resolver, vocab
from hw_radar.matching.normalize import canonicalize_title


def _read(title: str) -> tuple[str | None, str | None, tuple[str, ...], tuple[str, ...]]:
    e = vocab.extract(canonicalize_title(title))
    return (
        None if e.condition is None else e.condition.value,
        None if e.recert_channel is None else e.recert_channel.value,
        () if e.condition_conflict is None else e.condition_conflict.value,
        () if e.denied_conditions is None else e.denied_conditions.value,
    )


# (title, condition, channel, conflict, denied)
_NEGATION_TABLE = [
    # Codex r3 finding 6: a negated compound phrase negates its sub-matches,
    # and the negator scopes over that phrase only.
    ("WD Red Plus WD20EFPX 2TB NOT FACTORY RECERTIFIED Used", "used", None, (), ("recertified",)),
    ("WD Red Plus WD20EFPX 2TB Recertified NOT New Pull", "recertified", None, (), ("used",)),
    ("Seagate 12TB not a factory-recertified drive, used", "used", None, (), ("recertified",)),
    # Negators and window width.
    ("WD Red Plus WD20EFPX 2TB NOT RECERTIFIED", None, None, (), ("recertified",)),
    ("Seagate ST12000NE0008 12TB never used", None, None, (), ("used",)),
    ("NOT NEW Seagate ST12000NE0008 12TB", None, None, (), ("new",)),
    ("Seagate 12TB isn't new", None, None, (), ("new",)),
    ("Seagate 12TB no longer new", None, None, (), ("new",)),
    ("Seagate 12TB non-refurbished used", "used", None, (), ("refurbished",)),
    ("Seagate 12TB not - used", None, None, (), ("used",)),
    ("not tested seagate hdd 12tb used", "used", None, (), ()),
    # Positive phrases owning their negator stay positive and negate nothing.
    ("Seagate ST12000NE0008 12TB not working", "for_parts", None, (), ()),
    ("Seagate ST12000NE0008 12TB no warranty used", "used", None, (), ()),
    # Unnegated behaviour is unchanged.
    ("Seagate ST12000NE0008 12TB Recertified", "recertified", None, (), ()),
    ("Seagate ST12000NE0008 12TB factory recertified", "recertified", "factory", (), ()),
    ("New Pull Seagate ST12000NE0008 12TB", "used", None, (), ()),
    (
        "Seagate ST12000NE0008 12TB Recertified Used",
        "recertified",
        None,
        ("recertified", "used"),
        (),
    ),
    # A condition asserted anywhere is never also denied.
    ("Seagate 12TB not new - new", "new", None, (), ()),
]


@pytest.mark.parametrize(("title", "condition", "channel", "conflict", "denied"), _NEGATION_TABLE)
def test_negation_window(
    title: str,
    condition: str | None,
    channel: str | None,
    conflict: tuple[str, ...],
    denied: tuple[str, ...],
) -> None:
    assert _read(title) == (condition, channel, conflict, denied)


# ── Finding 8: a denial blocks the store's declared condition ───────────────


@pytest.mark.parametrize(
    "title",
    [
        "WD Red Plus WD20EFPX 2TB NOT RECERTIFIED",
        "WD Red Plus WD20EFPX 2TB not recertified 90%NEW",
        "WD Red Plus WD20EFPX 2TB NOT FACTORY RECERTIFIED",
    ],
)
def test_store_fold_never_asserts_a_denied_condition(title: str) -> None:
    folded = vocab.with_source_offer_terms(
        vocab.extract(canonicalize_title(title)), "wd-recertified"
    )
    assert folded.condition is None
    assert folded.recert_channel is None


@pytest.mark.parametrize(
    "title",
    [
        "WD Red Plus WD20EFPX 2TB",  # omission keeps the fold
        "WD Red Plus WD20EFPX 2TB Recertified NOT NEW",
        "WD Red Plus WD20EFPX 2TB Recertified NOT New Pull",
    ],
)
def test_store_fold_still_fills_an_omitted_or_agreeing_condition(title: str) -> None:
    folded = vocab.with_source_offer_terms(
        vocab.extract(canonicalize_title(title)), "wd-recertified"
    )
    assert folded.condition is not None and folded.condition.value == "recertified"
    assert folded.recert_channel is not None and folded.recert_channel.value == "factory"


# ── Finding 7: the fingerprint carries the product a miss is about ──────────


def _evidence(**extra: object) -> dict[str, object]:
    return {"outcome": "review", "rung": 1, "category": "drive", **extra}


def test_fingerprint_changes_with_the_mpn_hypothesis() -> None:
    old = _evidence(lot={"quantity": 2, "source_text": "2x"}, mpn_hypothesis="st12000ne0008")
    new = _evidence(lot={"quantity": 2, "source_text": "2x"}, mpn_hypothesis="st16000nm001g")
    assert resolver._review_reason(old) != resolver._review_reason(new)  # pyright: ignore[reportPrivateUsage] - the fingerprint under test is private


def test_fingerprint_changes_with_the_vendor_hint_of_a_none_miss() -> None:
    old = {"outcome": "none", "mpn_hypothesis": "abc123", "vendor_hint": "seagate"}
    new = {"outcome": "none", "mpn_hypothesis": "abc123", "vendor_hint": "toshiba"}
    assert resolver._review_reason(old) != resolver._review_reason(new)  # pyright: ignore[reportPrivateUsage] - see above


def test_fingerprint_changes_with_the_contradicted_variant_fields() -> None:
    old = _evidence(variant_contradicted={"condition": "new"}, mpn_hypothesis="x")
    new = _evidence(variant_contradicted={"packaging": "bulk"}, mpn_hypothesis="x")
    assert resolver._review_reason(old) != resolver._review_reason(new)  # pyright: ignore[reportPrivateUsage] - see above


def test_fingerprint_ignores_counts_ids_text_and_provenance() -> None:
    base = _evidence(
        lot={"quantity": 2, "source_text": "2x"},
        prior_model_not_named={"prior_model_id": 1, "alias_model_ids": [2], "identifiers": ["a"]},
        variant_contradicted={"condition": "new"},
        mpn_hypothesis="st12000ne0008",
    )
    moved = {
        **base,
        "lot": {"quantity": 3, "source_text": "3 x"},
        "prior_model_not_named": {
            "prior_model_id": 9,
            "alias_model_ids": [7],
            "identifiers": ["a"],
        },
        "variant_contradicted": {"condition": "refurbished"},
        "rung": 0,
        "reconsider": True,
        "reconsidered_prior": {"reason": "identifiers_changed"},
    }
    assert resolver._review_reason(base) == resolver._review_reason(moved)  # pyright: ignore[reportPrivateUsage] - see above
    # A stored edge is the JSON round trip of the verdict evidence.
    assert resolver._review_reason(json.loads(json.dumps(base))) == resolver._review_reason(base)  # pyright: ignore[reportPrivateUsage] - see above
