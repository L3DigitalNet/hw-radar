"""CPU category rules (MS2-D-04/-05, plan B3): extraction and the veto table."""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import TokenKind

_XEON = "Intel Xeon Gold 6448Y 32-Core 225W FCLGA4677 SRMGD"


def _extract(title: str) -> cpu.CpuAttributes:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return payload


def _keys(title: str) -> list[str]:
    return [c.normalized for c in cpu.extract_candidates(canonicalize_title(title))]


def test_extracts_socket_cores_tdp() -> None:
    attrs = _extract(_XEON)
    assert attrs.socket is not None and attrs.socket.value == "lga4677"
    assert attrs.cores is not None and attrs.cores.value == 32
    assert attrs.tdp_w is not None and attrs.tdp_w.value == 225


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("FCLGA4677", "lga4677"),
        ("LGA 4677", "lga4677"),
        ("lga-1700", "lga1700"),
        ("Socket SP5", "sp5"),
        ("AM5", "am5"),
    ],
)
def test_socket_key(raw: str, key: str) -> None:
    assert cpu.socket_key(raw) == key


def test_two_sockets_are_unknown() -> None:
    assert _extract("Intel Xeon LGA3647 or LGA4189 board").socket is None


def test_name_candidates_whole_and_bare() -> None:
    keys = _keys(_XEON)
    assert {"xeongold6448y", "6448y", "srmgd"} <= set(keys)
    # Vocabulary tokens are not candidates.
    assert "32core" not in keys


def test_bare_number_only_from_a_product_line_phrase() -> None:
    # '6448Y' without 'Xeon Gold' never becomes a candidate: a free-standing
    # number is not identity evidence.
    assert _keys("6448Y processor 32 core") == []


def test_sspec_needs_intel_context() -> None:
    assert "srmgd" not in _keys("SRMGD 32 core")


@pytest.mark.parametrize(
    ("title", "key", "vendor"),
    [
        ("AMD EPYC 9654 96 Core SP5 100-000000789", "100000000789", "amd"),
        ("AMD EPYC 9654 96 Core SP5", "epyc9654", "amd"),
        ("Intel Core i9-13900K BX8071513900K", "bx8071513900k", "intel"),
        ("Intel Core i9-13900K", "corei913900k", "intel"),
        ("AMD Ryzen 9 7950X AM5", "ryzen97950x", "amd"),
        ("Intel Xeon E5-2690 v4", "xeone52690v4", "intel"),
        ("AMD Ryzen Threadripper PRO 7995WX", "7995wx", "amd"),
    ],
)
def test_identifier_shapes(title: str, key: str, vendor: str) -> None:
    by_key = {c.normalized: c for c in cpu.extract_candidates(canonicalize_title(title))}
    assert by_key[key].kind is TokenKind.MANUFACTURER_MPN
    assert by_key[key].vendor_hint == vendor


def test_brand_from_product_line() -> None:
    assert cpu.extract(canonicalize_title("Xeon Gold 6448Y")).brand is not None
    assert cpu.extract(canonicalize_title("Intel vs AMD EPYC")).brand is None


_SPEC = cpu.CpuHard(socket="lga4677", cores=32)


@pytest.mark.parametrize(
    ("title", "vetoed"),
    [
        (_XEON, []),
        ("Intel Xeon Gold 6448Y 32-Core LGA4189", ["socket"]),
        ("Intel Xeon Gold 6448Y 24-Core LGA4677", ["cores"]),
        # TDP differs but is never vetoed (configurable TDP).
        ("Intel Xeon Gold 6448Y 32-Core 185W", []),
        ("Intel Xeon Gold 6448Y", []),
    ],
)
def test_veto_table(title: str, vetoed: list[str]) -> None:
    extracted = cpu.extract(canonicalize_title(title))
    assert cpu.veto(extracted, ladder.HardAttrs(category=_SPEC)) == vetoed


def test_unknown_catalog_field_cannot_veto() -> None:
    extracted = cpu.extract(canonicalize_title("Intel Xeon 24-Core LGA4189"))
    assert cpu.veto(extracted, ladder.HardAttrs(category=cpu.CpuHard())) == []
