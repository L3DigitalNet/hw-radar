"""The repo's own seed documents must always parse and stay conflict-free.
Corpus-size-independent by design (Codex CR-002): these invariants hold at
every task boundary; the exact corpus-count test lands with the full corpus
in Task 2, so no commit ever carries a known-red test."""

from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.refdata.contracts import detect_conflicts
from hw_radar.refdata.loader import load_seed_documents


def test_repo_seed_documents_parse() -> None:
    docs = load_seed_documents()
    assert docs, "seed corpus must never be empty"
    assert all(doc.models for doc in docs)


def test_repo_seed_documents_have_no_conflicts() -> None:
    assert detect_conflicts(load_seed_documents()) == ()


def test_every_alias_is_normalize_alias_text_of_its_raw_form() -> None:
    for doc in load_seed_documents():
        for model in doc.models:
            for alias in model.aliases:
                assert alias.normalized == normalize_alias_text(alias.text)


def test_repo_seed_corpus_totals() -> None:
    docs = load_seed_documents()
    # Drive-scoped assertions are unchanged from before the MS-2 multi-category
    # corpus landed (B4c) — kept exact so a drive-seed regression still fails
    # here, independent of how the non-drive corpus grows.
    drive = [d for d in docs if d.category == "drive"]
    assert {d.manufacturer_key for d in drive} == {"seagate", "western_digital"}
    assert sum(len(d.models) for d in drive) == 15
    assert sum(len(m.aliases) for d in drive for m in d.models) == 17

    # Whole-corpus snapshot (B4c): exact per this task's committed seed set.
    # A future seed addition updates these four numbers in the same commit.
    assert {d.manufacturer_key for d in docs} == {
        "seagate",
        "western_digital",
        "intel",
        "amd",
        "nvidia",
        "micron",
    }
    assert sum(len(d.models) for d in docs) == 34
    assert sum(len(m.aliases) for d in docs for m in d.models) == 52
    by_category: dict[str, int] = {}
    for doc in docs:
        by_category[doc.category] = by_category.get(doc.category, 0) + len(doc.models)
    assert by_category == {"drive": 15, "cpu": 9, "gpu": 8, "ram": 2}
