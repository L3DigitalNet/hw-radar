"""The Actor's end of the hw-radar Actor contract (MS2-D-14): schema files and identity.

The committed JSON Schemas in `<actor>/contract/` are the single contract
artifact; hw-radar's Pydantic models are tested equal to them, and this Actor
validates against them with a standard JSON Schema engine. This module never
imports hw-radar (MS2-D-38): the files are the only shared surface.

Requirement: the `contract/` directory must sit two levels above this package
(`<actor>/contract`, next to `<actor>/src`). That holds in the repository and in
the image, whose Dockerfile copies `contract/` beside `src/` and installs the
project editable; a non-editable install into site-packages would lose it, so
load_schema fails loudly instead of degrading.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

CONTRACT_DIR: Final = Path(__file__).resolve().parents[2] / "contract"

INPUT_SCHEMA_FILE: Final = "hw-radar-input-v1.schema.json"
LISTING_SCHEMA_FILE: Final = "hw-radar-listing-v1.schema.json"
RUN_SCHEMA_FILE: Final = "hw-radar-run-v1.schema.json"

LISTING_SCHEMA_VERSION: Final = "hw-radar-listing/v1"
RUN_SCHEMA_VERSION: Final = "hw-radar-run/v1"
# Stamped by faultMode=unknown_schema only: a contract major hw-radar does not know.
UNKNOWN_LISTING_SCHEMA_VERSION: Final = "hw-radar-listing/v99"
UNKNOWN_RUN_SCHEMA_VERSION: Final = "hw-radar-run/v99"

# Must equal .actor/actor.json "name" and "version" (a test pins both). The
# version's major equals the contract major (MS2-D-38): v1 contract => 1.x.
ACTOR_NAME: Final = "hw-radar-synthetic-collector"
ACTOR_VERSION: Final = "1.0"
SOURCE_KIND: Final = "synthetic"

# The one default-KV record this Actor writes (MS2-D-25).
OUTPUT_KEY: Final = "OUTPUT"


@cache
def load_schema(file_name: str) -> dict[str, Any]:
    path = CONTRACT_DIR / file_name
    if not path.is_file():
        raise FileNotFoundError(f"contract schema missing: {path}")
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def validator(file_name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(file_name))


def schema_errors(file_name: str, instance: Any) -> list[str]:
    """Return every violation of the named contract schema, sorted; [] when valid."""
    errors = validator(file_name).iter_errors(instance)  # pyright: ignore[reportUnknownMemberType]
    return sorted(error.message for error in errors)
