"""Guard: every `django_db(transaction=True, ...)` marker must also set
`serialized_rollback=True`. Without it, the transactional flush re-runs
Django's post_migrate signal, which re-inserts (re-keys) `django_content_type`
rows; any later `serialized_rollback=True` test in the same run then fails to
deserialize its snapshot because the content-type primary keys no longer
match (root cause identified 2026-09-24, fixed in a5881ee).

This scans source text, not import behavior, so it catches the mistake
before a flaky, order-dependent failure ever reaches CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"


def _is_django_db_call(node: ast.expr) -> ast.Call | None:
    """Return the Call node if `node` is `pytest.mark.django_db(...)` or
    `django_db(...)`, else None. Handles both decorator and pytestmark forms,
    since either can carry the transaction/serialized_rollback kwargs.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "django_db":
        return node
    if isinstance(func, ast.Name) and func.id == "django_db":
        return node
    return None


def _iter_django_db_calls(tree: ast.Module) -> list[ast.Call]:
    """Collect django_db(...) calls from both decorator sites and bare
    `pytestmark = ...` / `pytestmark = [...]` assignments.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                call = _is_django_db_call(dec)
                if call is not None:
                    calls.append(call)
        elif isinstance(node, ast.Assign):
            targets = node.targets
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                values = node.value.elts if isinstance(node.value, ast.List) else [node.value]
                for value in values:
                    call = _is_django_db_call(value)
                    if call is not None:
                        calls.append(call)
    return calls


def _kwarg_bool(call: ast.Call, name: str) -> bool | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            if isinstance(value, bool):
                return value
    return None


def find_violations(source: str, path: Path) -> list[str]:
    """Return "path:line" strings for each django_db(transaction=True, ...)
    call in `source` that omits serialized_rollback=True.
    """
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []
    for call in _iter_django_db_calls(tree):
        if (
            _kwarg_bool(call, "transaction") is True
            and _kwarg_bool(call, "serialized_rollback") is not True
        ):
            violations.append(f"{path}:{call.lineno}")
    return violations


def test_no_transaction_true_without_serialized_rollback() -> None:
    all_violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        all_violations.extend(find_violations(path.read_text(), path))
    assert not all_violations, (
        "django_db(transaction=True, ...) without serialized_rollback=True "
        "re-keys django_content_type on flush and breaks later "
        "serialized_rollback tests (see a5881ee):\n" + "\n".join(all_violations)
    )


def test_detector_flags_offending_source() -> None:
    offending = "pytestmark = pytest.mark.django_db(transaction=True)\n"
    assert find_violations(offending, Path("synthetic.py")) == ["synthetic.py:1"]


def test_detector_accepts_compliant_source() -> None:
    compliant = (
        "pytestmark = pytest.mark.django_db(\n    transaction=True, serialized_rollback=True\n)\n"
    )
    assert find_violations(compliant, Path("synthetic.py")) == []
