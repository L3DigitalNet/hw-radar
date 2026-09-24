"""Category rules registry (MS2-D-02/-03): slice A registers drive only, bound to the
existing ADR-0019 functions by identity, with `None` hints dispatching to drive."""

import pytest

from hw_radar.matching import categories, grammars, ladder, mpn, vocab


def test_drive_rules_bind_existing_functions_by_identity() -> None:
    rules = categories.rules_for("drive")
    assert rules is not None
    assert rules.slug == "drive"
    assert rules.extract is vocab.extract
    assert rules.extract_candidates is mpn.extract_candidates
    assert rules.decode is grammars.decode
    assert rules.veto is ladder.contradictions


def test_registered_categories_matches_ms2_registry() -> None:
    assert categories.registered_categories() == frozenset(
        {"drive", "gpu", "ram", "cpu", "nic", "hba", "motherboard", "server"}
    )


def test_dispatch_category_none_is_legacy_drive() -> None:
    assert categories.dispatch_category(None) == categories.LEGACY_DEFAULT_CATEGORY == "drive"


def test_dispatch_category_passes_hint_through() -> None:
    assert categories.dispatch_category("drive") == "drive"
    # An unregistered but well-formed slug passes through unchanged: dispatch
    # names the category; whether rules exist for it is rules_for's question.
    assert categories.dispatch_category("gpu") == "gpu"
    assert categories.dispatch_category("basic-watch") == "basic-watch"


def test_rules_for_unregistered_is_none() -> None:
    assert categories.rules_for("zz-unregistered") is None


@pytest.mark.parametrize("hint", ["GPU!", "GPU", "gpu card", "", "-gpu", "gpu-", "a" * 51])
def test_invalid_slug_rejected(hint: str) -> None:
    with pytest.raises(ValueError, match="category"):
        categories.dispatch_category(hint)
