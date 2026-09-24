"""Seed-document schema (ADR-0018 D1): shape validation + pure conflict detection."""

import pydantic
import pytest

from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.refdata.contracts import (
    SEED_SCHEMA,
    SeedAlias,
    SeedDocument,
    SeedDriveSpec,
    detect_conflicts,
)

VALID_DOC = {
    "schema": SEED_SCHEMA,
    "manufacturer_name": "Seagate",
    "manufacturer_key": "seagate",
    "family_name": "Exos",
    "provenance": {
        "source_kind": "first_party_datasheet",
        "sources": [
            {
                "url": "https://www.seagate.com/content/dam/seagate/en/content-fragments/products/datasheets/exos-recertified-drive/exos-recertified-drive-DS2045-2-2010US-October-2020-en_US.pdf",
                "title": "Seagate Exos Recertified Drive datasheet DS2045.2",
                "retrieved": "2026-07-05",
            }
        ],
    },
    "models": [
        {
            "model_number": "ST16000NM002C",
            "spec": {
                "media_type": "hdd",
                "interface": "SATA 6Gb/s",
                "form_factor": "3.5",
                "capacity_tb": "16",
                "sector_format": "512e",
                "recording_tech": "cmr",
                "market_tier": "enterprise",
                "model_family": "Exos",
            },
            "aliases": [{"alias_type": "mpn", "text": "ST16000NM002C", "is_primary": True}],
        }
    ],
}


def _doc(**overrides: object) -> dict[str, object]:
    return {**VALID_DOC, **overrides}


def test_valid_document_parses() -> None:
    doc = SeedDocument.model_validate(VALID_DOC)
    assert doc.manufacturer_key == "seagate"
    spec = doc.models[0].spec
    assert isinstance(spec, SeedDriveSpec)
    assert spec.capacity_tb is not None
    assert spec.rpm is None  # null = unknown, never guessed


def test_alias_normalized_is_the_join_key() -> None:
    alias = SeedAlias(alias_type="mpn", text="WUH721818ALE6L4", is_primary=True)
    assert alias.normalized == normalize_alias_text("WUH721818ALE6L4")


def test_wrong_schema_id_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(_doc(schema="hw-radar.refdata.seed/v0"))


def test_manufacturer_key_must_be_normalized() -> None:
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(_doc(manufacturer_key="Western Digital"))


def test_model_requires_alias_covering_its_own_model_number() -> None:
    broken = _doc(
        models=[
            {
                "model_number": "ST16000NM002C",
                "spec": {"media_type": "hdd"},
                "aliases": [{"alias_type": "retail_pn", "text": "0F00000"}],
            }
        ]
    )
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(broken)


def test_model_requires_exactly_one_primary_alias() -> None:
    broken = _doc(
        models=[
            {
                "model_number": "ST16000NM002C",
                "spec": {"media_type": "hdd"},
                "aliases": [{"alias_type": "mpn", "text": "ST16000NM002C"}],
            }
        ]
    )
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(broken)


def test_extra_fields_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        SeedDocument.model_validate(_doc(surprise="field"))


def test_detect_conflicts_same_key_different_targets() -> None:
    doc_a = SeedDocument.model_validate(VALID_DOC)
    doc_b = SeedDocument.model_validate(
        _doc(
            manufacturer_name="Western Digital",
            manufacturer_key="western_digital",
            family_name="Ultrastar DC HC550",
            models=[
                {
                    "model_number": "WUH721818ALE6L4",
                    "spec": {"media_type": "hdd"},
                    "aliases": [
                        {"alias_type": "mpn", "text": "WUH721818ALE6L4", "is_primary": True},
                        # deliberate collision with doc_a's ST16000NM002C:
                        {"alias_type": "retail_pn", "text": "st16000nm002c"},
                    ],
                }
            ],
        )
    )
    conflicts = detect_conflicts([doc_a, doc_b])
    assert len(conflicts) == 1
    assert conflicts[0].normalized_text == normalize_alias_text("ST16000NM002C")
    assert len(conflicts[0].targets) == 2


def test_detect_conflicts_same_key_same_target_is_not_a_conflict() -> None:
    doc = SeedDocument.model_validate(VALID_DOC)
    assert detect_conflicts([doc, doc]) == ()
