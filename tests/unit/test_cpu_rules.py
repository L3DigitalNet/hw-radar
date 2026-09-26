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


# --- engineering / qualification samples (owner F6: different part, not retail) -


def _vetoed(title: str) -> list[str]:
    extracted = cpu.extract(canonicalize_title(title))
    return cpu.veto(extracted, ladder.HardAttrs(category=cpu.CpuHard(socket="sp3", cores=64)))


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 QS 100-000000314-04 64-Core SP3",
        "AMD EPYC 7763 64-Core SP3 QS",
        "AMD EPYC 7763 ES 64-Core SP3",
        "AMD EPYC 7763-ES 64-Core SP3",
        "AMD EPYC 7763 ES1 64-Core SP3",
        "AMD EPYC 7763 ES/QS 64-Core SP3",
        "AMD EPYC 7763 64-Core SP3 Engineering Sample",
        "AMD EPYC 7763 64-Core SP3 Qualification Sample",
        "AMD EPYC 7763 64-Core SP3 Sample",
        "AMD EPYC 7763 64-Core SP3 Pre-Production",
        "AMD EPYC 7763 64-Core SP3 preproduction",
        # A suffixed OPN with no sample word is the sample marking itself.
        "AMD EPYC 7763 64-Core SP3 100-000000312-04",
    ],
)
def test_sample_marker_vetoes_any_target(title: str) -> None:
    assert _vetoed(title) == ["sample"]


def test_sample_marker_vetoes_even_without_a_catalog_spec() -> None:
    # A seeded model with no cpu_spec row must not let a sample reach accept.
    extracted = cpu.extract(canonicalize_title("AMD EPYC 9654 ES 96-Core SP5"))
    assert cpu.veto(extracted, ladder.HardAttrs()) == ["sample"]


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 64-Core SP3",
        # 'es' inside words, Xeon E-series names, sockets and memory types.
        "AMD EPYC 7763 64-Core SP3 for 7002/7003 Series Boards",
        "AMD EPYC 7763 64-Core SP3 ESXi Tested",
        "AMD EPYC 7763 64-Core SP3 Processes Tested Working",
        "AMD EPYC 7763 64-Core SP3 DDR4 Server CPU",
        "AMD EPYC 7763 64-Core SP3 100-000000312",
    ],
)
def test_sample_marker_false_triggers_do_not_veto(title: str) -> None:
    assert _vetoed(title) == []


def test_xeon_e5_is_not_a_sample_marker() -> None:
    attrs = _extract("Intel Xeon E5-2690 v4 14-Core LGA2011-3")
    assert attrs.sample is None
    attrs = _extract("AMD EPYC 9654 96-Core SP5 DDR5")
    assert attrs.sample is None


def test_sample_marker_reads_the_masked_title() -> None:
    # The cited object of a reference phrase is not the listed part.
    assert _extract("AMD EPYC 7763 64-Core SP3 replacement for engineering sample").sample is None


def test_sample_title_still_names_the_retail_model() -> None:
    # The candidate is kept (the veto, not candidate filtering, blocks accept),
    # so the resolver reaches the seeded model and records a reviewable
    # contradiction rather than a silent miss.
    assert "epyc7763" in _keys("AMD EPYC 7763 QS 100-000000314-04 64-Core SP3")


# --- boards, systems and bundles (s7 audit: cpu-0207/-0248/-0269/-0279) -------

# The four false merges of the first EPYC measurement share this title.
_H12DSI_BUNDLE = "Supermicro H12DSi-N6 Motherboard With 2x AMD EPYC 7763 64 Core 2.45GHz CPU"


@pytest.mark.parametrize(
    "title",
    [
        _H12DSI_BUNDLE,
        "AMD EPYC 7763 64-Core SP3 + H12SSL-i Mainboard",
        "Gigabyte MZ32-AR0 Mobo AMD EPYC 7763 64-Core SP3",
        "AMD EPYC 7763 SP3 Combo Supermicro H12SSL-NT",
        "AMD EPYC 7763 CPU Bundle SP3",
        "Dell AMD EPYC 7763 64-Core Processor Upgrade Kit",
        "Barebone 1U AMD EPYC 7763 64-Core SP3",
        "Supermicro H12DSi board w/ 2x AMD EPYC 7763",
        "2x AMD EPYC 7763 64-Core SP3 Server CPU",
        "AMD EPYC 7763 64-Core SP3 2x CPUs",
    ],
)
def test_board_system_or_bundle_listing_vetoes(title: str) -> None:
    assert _vetoed(title) == ["bundle"]


@pytest.mark.parametrize(
    "title",
    [
        # Bare-CPU titles from the EPYC corpus that say "Server CPU"/"Processor".
        "AMD EPYC 9354 Server CPU 32C 64T 3.25GHz Base 3.8GHz Boost 256MB L3 SP5 DDR5",
        "AMD EPYC 9354 32C Server Processor 32x 3.25GHz 256MB Cache 6096 SP5 CPU",
        "AMD EPYC 9654 96C Server Processor 96x 2.40GHz 384MB Cache 6096 SP5 CPU",
        "AMD EPYC Milan 7763 CPU 64 Cores SP3 Server Processor NO VENDOR LOCKED",
        "AMD EPYC 7763 Processor 64-Core 2.45GHz 256MB 280W CPU 100-000000312",
        "100-000000798 AMD EPYC Genoa 9354 3.25GHz 32-Core SP5 256MB Processor *UNLOCKED*",
        # Compatibility notes and generation boards are not bundles.
        "AMD EPYC 7763 64-Core SP3 for 7002/7003 Series Boards",
        "AMD EPYC 9354 32-Core SP5 support Supermicro H13SSL-N",
        "AMD EPYC 9354 SP5 socket board compatible",
        "AMD EPYC 7763 64-Core SP3 compatible with H12SSL-i motherboard",
        "AMD EPYC 7763 64-Core SP3 for server Workstation",
    ],
)
def test_bare_cpu_titles_are_not_bundles(title: str) -> None:
    assert _extract(title).bundle is None
    assert _extract(title).multi_model is None


def test_bundle_title_still_names_the_cpu() -> None:
    # The veto, not candidate filtering, blocks the accept: the hit stays
    # visible as a reviewable contradiction.
    assert "epyc7763" in _keys(_H12DSI_BUNDLE)


# --- EPYC codenames (s7 audit: 17 recall misses) -----------------------------


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("AMD EPYC Genoa 9354 280W 3.25GHz 32-Core 256MB DDR5 socket SP5", "epyc9354"),
        ("AMD EPYC GENOA 9654 CPU SP5 ZEN4 2.4GHz DDR5 96 Cores", "epyc9654"),
        ("AMD EPYC Milan 7763 CPU 64 Cores SP3 Server Processor", "epyc7763"),
        ("AMD EPYC Rome 7742 64-Core SP3", "epyc7742"),
        ("AMD EPYC Milan-X 7773X 64-Core SP3", "epyc7773x"),
        ("AMD EPYC Genoa-X 9684X 96-Core SP5", "epyc9684x"),
    ],
)
def test_codename_between_epyc_and_number_reaches_the_name(title: str, key: str) -> None:
    assert key in _keys(title)


@pytest.mark.parametrize(
    "title",
    [
        # The guards see the number exactly as without the codename.
        "AMD EPYC Genoa 9354P 32-Core SP5",
        "AMD EPYC Milan 7763 / 7713 64-Core SP3",
        "AMD EPYC Genoa 9654 9554 SP5",
    ],
)
def test_codename_keeps_the_p_variant_and_multi_model_guards(title: str) -> None:
    assert _seeded_hits(title) == set()


def test_unknown_word_between_epyc_and_number_is_not_skipped() -> None:
    # Only first-party codenames are skipped; anything else still blocks the name.
    assert "epyc9354" not in _keys("AMD EPYC Special 9354 SP5")


# --- ambiguity and structured-MPN samples (Codex N3/N4) -----------------------


def test_multi_model_title_records_the_ambiguity_and_vetoes() -> None:
    attrs = _extract("AMD EPYC 7763 / 7742 64-Core SP3")
    assert attrs.multi_model is not None and attrs.multi_model.value == "7742 7763"
    assert _vetoed("AMD EPYC 7763 / 7742 64-Core SP3") == ["multi_model"]


def test_structured_mpn_sample_marking_sets_sample() -> None:
    extracted = cpu.extract(canonicalize_title("AMD EPYC 7763 64-Core SP3"))
    folded = cpu.with_structured_mpn(extracted, "100-000000314-04")
    spec = ladder.HardAttrs(category=cpu.CpuHard(socket="sp3", cores=64))
    assert cpu.veto(folded, spec) == ["sample"]


def test_retail_structured_mpn_leaves_attributes_unchanged() -> None:
    extracted = cpu.extract(canonicalize_title("AMD EPYC 7763 64-Core SP3"))
    assert cpu.with_structured_mpn(extracted, "100-000000312") == extracted
