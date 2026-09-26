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


# --- EPYC identity safety (F6 pilot: exact authoritative identity only) -------
#
# Seeded EPYC alias keys (refdata/seeds/amd-epyc.json) a near-model title must
# never produce as a candidate: each one is an exact-alias hit on a seeded model.
_SEEDED_EPYC_KEYS = frozenset(
    {
        "epyc9354",
        "100000000798",
        "epyc9654",
        "100000000789",
        "epyc7763",
        "100000000312",
        "100100000312wof",
        "epyc7742",
    }
)


def _seeded_hits(title: str) -> set[str]:
    return set(_keys(title)) & _SEEDED_EPYC_KEYS


@pytest.mark.parametrize(
    "title",
    [
        # The cited model is the OBJECT of a reference phrase: an OEM/cloud SKU
        # sold as "the OEM version of" a retail model is a different part.
        "AMD 7J13 64-Core SP3 CPU OEM version of AMD EPYC 7763 2.45GHz",
        "AMD 7B13 64-Core SP3 Processor compatible with EPYC 7763",
        "AMD 7R13 48-Core SP3 replacement for EPYC 7763",
    ],
)
def test_reference_phrase_object_is_not_the_listed_cpu(title: str) -> None:
    assert _seeded_hits(title) == set()


def test_reference_mask_keeps_the_brand_before_the_phrase() -> None:
    title = canonicalize_title("AMD 7J13 64-Core SP3 OEM version of EPYC 7763")
    brand = cpu.extract(title).brand
    assert brand is not None and brand.value == "amd"
    # Physical attributes are never masked: the listed part's own cores stay.
    attrs = cpu.extract(title).category_attrs
    assert isinstance(attrs, cpu.CpuAttributes)
    assert attrs.cores is not None and attrs.cores.value == 64


def test_oem_version_phrase_is_cpu_local() -> None:
    # Drive titles keep their meaning: the shared list is unchanged, so a drive
    # MPN after "OEM version of" still reaches the drive layers.
    from hw_radar.matching.normalize import mask_reference_spans

    title = canonicalize_title("Dell OEM version of Seagate ST16000NM002G")
    assert mask_reference_spans(title) == title


@pytest.mark.parametrize(
    ("title", "own_key"),
    [
        # 1P SKUs: a different OPN and a different model from the seeded 2P part.
        ("AMD EPYC 9354P 32-Core 3.25GHz SP5 Processor", "epyc9354p"),
        ("AMD EPYC 9654P 96-Core SP5 CPU", "epyc9654p"),
        ("AMD EPYC 7302P 16-Core SP3 CPU", "epyc7302p"),
    ],
)
def test_p_suffix_never_reads_as_the_seeded_model(title: str, own_key: str) -> None:
    assert own_key in _keys(title)
    assert _seeded_hits(title) == set()


def test_hyphenated_exact_model_reaches_the_seeded_alias() -> None:
    # 'EPYC-9654' is the seeded model with separator styling. The name pattern
    # needs whitespace, but the code-token fallback claims 'epyc-9654', and
    # normalize_alias_text strips separators on both sides of the alias join,
    # so it lands on the same key as 'EPYC 9654' (resolved end to end in
    # tests/db/test_category_seed_reachability.py).
    found = {
        c.normalized: c
        for c in cpu.extract_candidates(canonicalize_title("AMD EPYC-9654 96-Core SP5"))
    }
    assert "epyc9654" in found
    assert found["epyc9654"].kind is TokenKind.UNKNOWN_CODE


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7742/7702 64-Core SP3 Server CPU",
        "AMD EPYC 7742 7702 7642 64-Core SP3 No Lock",
        "AMD EPYC 7763 or EPYC 7713 64-Core SP3",
        # Two seeded models: still no identity, not a pick between them.
        "AMD EPYC 9654 / EPYC 9354 SP5 Processor",
        # An OEM SKU named beside the retail model without a reference phrase.
        "AMD 7B13 64-Core epyc 7763 OEM version SP3",
        # The multi-model rule reaches OPNs and hyphenated names too.
        "AMD EPYC-9654 9554 SP5 100-000000789",
    ],
)
def test_title_naming_two_epyc_models_yields_no_amd_identity(title: str) -> None:
    assert _keys(title) == []


@pytest.mark.parametrize(
    "title",
    [
        # Generation/series names are not models (no x00y EPYC model exists).
        "AMD EPYC 7763 64-Core SP3 for 7002/7003 Series Boards",
        # Memory speed and socket numbers are not models.
        "AMD EPYC 9654 96-Core SP5 DDR5 4800 12-Channel",
        "AMD EPYC 7742 64-Core Socket LGA 4094",
    ],
)
def test_non_model_numbers_do_not_trip_the_multi_model_rule(title: str) -> None:
    assert len(_seeded_hits(title)) == 1


@pytest.mark.parametrize(
    "title",
    [
        # 7763 QS sample: OPN 100-000000314 with the -04 sample suffix.
        "AMD EPYC 7763 QS 100-000000314-04 64-Core SP3",
        # A suffixed OPN is a different marking even when its stem is seeded.
        "AMD 100-000000312-04 64-Core SP3 Engineering Sample",
    ],
)
def test_suffixed_opn_is_not_the_seeded_opn(title: str) -> None:
    keys = set(_keys(title))
    assert "100000000312" not in keys
    assert "100000000314" not in keys
