"""RAM category rules (MS2-D-04/-05, plan B3): extraction and the veto table."""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import ram
from hw_radar.matching.types import TokenKind

_RDIMM = "Samsung 32GB DDR4-3200 ECC RDIMM 2Rx4 M393A4K40DB3-CWE"


def _extract(title: str) -> ram.RamAttributes:
    payload = ram.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, ram.RamAttributes)
    return payload


def _values(attrs: ram.RamAttributes) -> dict[str, object]:
    names = ("generation", "module_type", "ecc", "module_capacity_gb", "modules_per_kit")
    names += ("speed_mts", "ranks")
    return {
        name: (attr.value if (attr := getattr(attrs, name)) is not None else None) for name in names
    }


def test_extracts_server_rdimm() -> None:
    assert _values(_extract(_RDIMM)) == {
        "generation": "ddr4",
        "module_type": "rdimm",
        "ecc": True,
        "module_capacity_gb": 32,
        "modules_per_kit": None,
        "speed_mts": 3200,
        "ranks": 2,
    }


def test_kit_form_splits_module_size_and_count() -> None:
    attrs = _extract("Micron 64GB (2x32GB) DDR5 4800MHz ECC Registered")
    assert attrs.module_capacity_gb is not None and attrs.module_capacity_gb.value == 32
    assert attrs.modules_per_kit is not None and attrs.modules_per_kit.value == 2


def test_lone_total_size_leaves_kit_count_unknown() -> None:
    attrs = _extract("Kingston 64GB DDR4 3200 RDIMM")
    assert attrs.module_capacity_gb is not None and attrs.module_capacity_gb.value == 64
    assert attrs.modules_per_kit is None


@pytest.mark.parametrize(
    ("title", "speed"),
    [
        ("32GB PC4-25600 RDIMM", 3200),  # bandwidth form, MB/s / 8
        ("32GB PC4-2933Y RDIMM", 2933),  # speed-bin form, MT/s
        ("32GB DDR5 4800MT/s", 4800),
    ],
)
def test_speed_forms(title: str, speed: int) -> None:
    attrs = _extract(title)
    assert attrs.speed_mts is not None and attrs.speed_mts.value == speed


def test_non_ecc_is_false_not_true() -> None:
    attrs = _extract("Crucial 16GB DDR4 non-ECC SODIMM")
    assert attrs.ecc is not None and attrs.ecc.value is False
    assert attrs.module_type is not None and attrs.module_type.value == "sodimm"


def test_lrdimm_is_not_rdimm() -> None:
    attrs = _extract("64GB DDR4 LRDIMM 4Rx4")
    assert attrs.module_type is not None and attrs.module_type.value == "lrdimm"
    assert attrs.ranks is not None and attrs.ranks.value == 4


def test_dual_labeled_title_has_no_single_brand() -> None:
    # The OEM label and the module maker are both real; neither may brand-gate
    # away the other's exact hit, so brand evidence comes from each token instead.
    title = canonicalize_title("HPE 815100-B21 32GB 2Rx4 PC4-2666V-R Samsung M393A4K40CB2-CTD")
    assert ram.extract(title).brand is None
    by_key = {c.normalized: c for c in ram.extract_candidates(title)}
    assert by_key["m393a4k40cb2ctd"].kind is TokenKind.MANUFACTURER_MPN
    assert by_key["m393a4k40cb2ctd"].vendor_hint == "samsung"
    assert by_key["815100b21"].kind is TokenKind.OEM_PN
    assert by_key["815100b21"].vendor_hint == "hpe"
    # The PC module name is vocabulary, not a part-number candidate.
    assert "pc42666vr" not in by_key


def test_hpe_oem_shape_needs_the_vendor_word() -> None:
    keys = [c.normalized for c in ram.extract_candidates(canonicalize_title("815100-B21 32GB"))]
    assert "815100b21" not in keys


@pytest.mark.parametrize(
    ("title", "key", "vendor"),
    [
        ("Micron MTA36ASF4G72PZ-3G2E1", "mta36asf4g72pz3g2e1", "micron"),
        ("Micron MTC20F2085S1RC48BA1", "mtc20f2085s1rc48ba1", "micron"),
        ("Crucial CT16G4SFRA32A", "ct16g4sfra32a", "micron"),
        ("SK hynix HMA84GR7CJR4N-XN", "hma84gr7cjr4nxn", "sk_hynix"),
        ("Kingston KSM32RD4/32HDR", "ksm32rd432hdr", "kingston"),
    ],
)
def test_manufacturer_part_shapes(title: str, key: str, vendor: str) -> None:
    by_key = {c.normalized: c for c in ram.extract_candidates(canonicalize_title(title))}
    assert by_key[key].kind is TokenKind.MANUFACTURER_MPN
    assert by_key[key].vendor_hint == vendor


def test_dell_snp_is_an_oem_candidate() -> None:
    by_key = {
        c.normalized: c
        for c in ram.extract_candidates(canonicalize_title("Dell SNP8WKDYC/64G 64GB LRDIMM"))
    }
    assert by_key["snp8wkdyc64g"].kind is TokenKind.OEM_PN


_SPEC = ram.RamHard(
    generation="ddr4",
    module_type="rdimm",
    ecc=True,
    module_capacity_gb=32,
    speed_mts=3200,
    ranks=2,
)


@pytest.mark.parametrize(
    ("title", "vetoed"),
    [
        (_RDIMM, []),
        ("32GB DDR5 ECC RDIMM 2Rx4 4800MHz", ["generation", "speed_mts"]),
        ("32GB DDR4-3200 ECC LRDIMM 2Rx4", ["module_type"]),
        ("32GB DDR4-3200 non-ECC UDIMM", ["module_type", "ecc"]),
        ("64GB DDR4-3200 ECC RDIMM 2Rx4", ["module_capacity_gb"]),
        ("32GB DDR4-3200 ECC RDIMM 1Rx4", ["ranks"]),
        # Selling two of the single-module part is a quantity, not a new part.
        ("2x32GB DDR4-3200 ECC RDIMM 2Rx4", []),
        # Unknown on the listing side never vetoes.
        ("Samsung M393A4K40DB3-CWE", []),
    ],
)
def test_veto_table(title: str, vetoed: list[str]) -> None:
    extracted = ram.extract(canonicalize_title(title))
    assert ram.veto(extracted, ladder.HardAttrs(category=_SPEC)) == vetoed


def test_unknown_catalog_field_cannot_veto() -> None:
    extracted = ram.extract(canonicalize_title("64GB DDR5 non-ECC UDIMM"))
    assert ram.veto(extracted, ladder.HardAttrs(category=ram.RamHard())) == []
