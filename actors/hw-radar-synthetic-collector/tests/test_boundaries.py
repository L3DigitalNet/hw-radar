"""Source-scan boundaries (MS2-D-38): no proxy, no Django, no hw_radar, no DB access.

A scan, not a runtime check, because the rule is about what the shipped source
could ever do: a proxy constructed on a rare branch would never show up in a
behavioral test, yet it would bill residential transfer (MS2-D-26 disallows it).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from tests.support import ACTOR_ROOT

SOURCE_FILES = sorted((ACTOR_ROOT / "src").rglob("*.py"))
FORBIDDEN_IMPORT_ROOTS = {"django", "hw_radar", "psycopg", "psycopg2", "sqlite3", "sqlalchemy"}
# Apify's proxy entry points plus the httpx parameters that route traffic via a proxy.
FORBIDDEN_NAMES = {"ProxyConfiguration", "create_proxy_configuration", "proxy_configuration"}
FORBIDDEN_KEYWORDS = {"proxy", "proxies", "mounts"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_proxy_configuration_or_django_import() -> None:
    assert SOURCE_FILES, "the scan found no source files"
    violations: list[str] = []
    for path in SOURCE_FILES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
                roots |= {alias.name for alias in node.names} & FORBIDDEN_NAMES
            elif isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                roots = {name} & FORBIDDEN_NAMES
            elif isinstance(node, ast.keyword):
                roots = {node.arg or ""} & FORBIDDEN_KEYWORDS
            else:
                continue
            hits = roots & (FORBIDDEN_IMPORT_ROOTS | FORBIDDEN_NAMES | FORBIDDEN_KEYWORDS)
            violations += [
                f"{path.name}:{getattr(node, 'lineno', '?')}: {hit}" for hit in sorted(hits)
            ]
    assert violations == []


def test_every_http_client_ignores_proxy_environment() -> None:
    constructions: list[ast.Call] = [
        node
        for path in SOURCE_FILES
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"AsyncClient", "Client"}
    ]
    assert constructions, "the Actor builds its HTTP client somewhere"
    for call in constructions:
        trust_env = [kw.value for kw in call.keywords if kw.arg == "trust_env"]
        assert len(trust_env) == 1
        assert isinstance(trust_env[0], ast.Constant)
        assert trust_env[0].value is False


def test_input_schemas_carry_no_proxy_browser_or_unblocker_field() -> None:
    schemas = [
        ACTOR_ROOT / ".actor" / "input_schema.json",
        ACTOR_ROOT / "contract" / "hw-radar-input-v1.schema.json",
    ]
    for path in schemas:
        properties = json.loads(path.read_text(encoding="utf-8"))["properties"]
        for name in properties:
            lowered = name.lower()
            assert not any(
                word in lowered for word in ("proxy", "browser", "captcha", "unblock")
            ), name
