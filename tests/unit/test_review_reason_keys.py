"""The review-reason registry is complete for the resolver's own gates.

resolver._review_reason fingerprints a review by its registered reason keys, so
an unchanged re-poll writes no edge while a changed reason writes one (s8 Codex
r2 finding E). A `_review(...)` call whose reason key is not registered would
still review, but a change to or from that reason would keep an obsolete reason
on the current edge; this test turns that silent drift into a failure."""

from __future__ import annotations

import ast
import inspect

from hw_radar.matching import resolver

# Extra evidence a `_review` call carries beside its reason (not itself a reason).
_CONTEXT_KEYS = frozenset({"alias_source_kind"})


def _review_call_keywords() -> set[str]:
    tree = ast.parse(inspect.getsource(resolver))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_review"
        ):
            keys.update(kw.arg for kw in node.keywords if kw.arg is not None)
    return keys


def test_every_review_call_reason_is_registered() -> None:
    keys = _review_call_keywords()
    assert keys, "no _review(...) call sites found; the scan is broken"
    assert keys - _CONTEXT_KEYS <= resolver.GATE_REVIEW_REASON_KEYS


def test_registry_joins_ladder_and_gate_reasons() -> None:
    assert resolver.REVIEW_REASON_KEYS == (
        resolver.ladder.REVIEW_REASON_KEYS | resolver.GATE_REVIEW_REASON_KEYS
    )
