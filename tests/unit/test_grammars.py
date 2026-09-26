"""N4 grammar decoder vectors (spec C.3.1; references per the warranty-verification
and SSD part-number research reports). The decoder contract is WEAK by design:
family/capacity/generation only, provenance-tiered."""

import pytest

from hw_radar.matching.grammars import decode
from hw_radar.matching.types import Provenance

_TB = 1_000_000_000_000


@pytest.mark.parametrize(
    ("token", "capacity_gb", "generation"),
    [
        ("st16000nm001g", 16000, "g"),  # Exos X16
        ("st1000nm0001", 1000, "1"),  # Constellation ES — MS-1e ghd-0006
        ("st8000nm0055", 8000, "5"),  # Enterprise Capacity v5, rebranded Exos 7E8
    ],
)
def test_seagate_nm_segment_decodes_without_family(
    token: str, capacity_gb: int, generation: str
) -> None:
    # `nm` spans Exos, Constellation and Enterprise Capacity with no structural
    # boundary (grammars/seagate.py): the token is still a Seagate MPN with a
    # capacity, but it must never name a family for rung 2 to attach.
    r = decode(token)
    assert r is not None
    assert r.vendor == "seagate"
    assert r.family_name is None
    assert r.capacity_bytes == capacity_gb * 1_000_000_000
    assert r.generation == generation
    assert r.provenance is Provenance.VENDOR_OFFICIAL  # only official structure used


def test_seagate_ironwolf_and_unknown_segment() -> None:
    r = decode("st4000vn008")
    assert r is not None and r.family_name == "ironwolf"
    unknown = decode("st8000zz123")  # valid shape, unmapped segment
    assert unknown is not None
    assert unknown.family_name is None  # structure decodes; family unasserted
    assert unknown.provenance is Provenance.VENDOR_OFFICIAL  # only official structure used


@pytest.mark.parametrize(
    ("token", "family", "capacity_tb"),
    [
        ("wd120efbx", "red plus", 12),
        ("wd20efpx", "red plus", 2),
        ("wd30efzx", "red plus", 3),
        ("wd121kryz", "gold", 12),
        ("wd102kfbx", "red pro", 10),
    ],
)
def test_wd_modern(token: str, family: str, capacity_tb: int) -> None:
    r = decode(token)
    assert r is not None
    assert r.vendor == "western_digital"
    assert r.family_name == family
    assert r.capacity_bytes == capacity_tb * _TB
    assert r.provenance is Provenance.CORROBORATED_COMMUNITY


@pytest.mark.parametrize("token", ["wd40efax", "wd80efax", "wd60efrx", "wd20efrx"])
def test_wd_red_line_suffix_without_first_party_family_asserts_none(token: str) -> None:
    # EFAX spans WD Red (SMR, 2-6TB) and CMR capacities WD sells as Red Plus;
    # EFRX has no first-party table. Asserting "red" here filed Red Plus
    # drives under a family WD does not sell them as (MS-1e ebay-0424/0434).
    r = decode(token)
    assert r is not None
    assert r.vendor == "western_digital"
    assert r.family_name is None
    assert r.capacity_bytes is not None


def test_wd_four_digit_capacity_is_never_guessed() -> None:
    r = decode("wd4004fryz")  # 4-digit block: capacity encoding is ambiguous
    assert r is not None
    assert r.family_name == "gold"
    assert r.capacity_bytes is None
    assert r.provenance is Provenance.INFERRED


def test_hgst_lineage_family_only() -> None:
    wd = decode("wuh721816ale6l4")
    assert wd is not None
    assert wd.vendor == "western_digital"
    assert wd.family_name == "ultrastar"
    assert wd.capacity_bytes is None  # HGST capacity digits are ambiguous — vocab carries it
    hgst = decode("hus724040als640")
    assert hgst is not None and hgst.vendor == "hgst" and hgst.family_name == "ultrastar"


@pytest.mark.parametrize(
    ("token", "family", "capacity_tb", "generation"),
    [
        ("mg08aca16te", "mg enterprise capacity", 16, "08"),
        ("mg04aca400e", "mg enterprise capacity", 4, "04"),
        ("mn08aca14t", "n300", 14, "08"),
    ],
)
def test_toshiba_official_grammar(
    token: str, family: str, capacity_tb: int, generation: str
) -> None:
    r = decode(token)
    assert r is not None
    assert r.vendor == "toshiba"
    assert r.family_name == family
    assert r.capacity_bytes == capacity_tb * _TB
    assert r.generation == generation
    assert r.provenance is Provenance.VENDOR_OFFICIAL


def test_non_mpn_tokens_do_not_decode() -> None:
    for token in ("abc123xyz99", "x477ar6", "005049070", "16tb", ""):
        assert decode(token) is None
