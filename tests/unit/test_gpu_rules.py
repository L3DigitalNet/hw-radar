"""GPU category rules (MS2-D-04/-05, plan B3): extraction and the veto table.

Titles go through canonicalize_title first, exactly as the resolver feeds them."""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder, vocab
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import gpu, no_decode
from hw_radar.matching.types import ExtractedAttributes, TokenKind


def _extract(title: str) -> gpu.GpuAttributes:
    payload = gpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, gpu.GpuAttributes)
    return payload


def _keys(title: str) -> list[str]:
    return [c.normalized for c in gpu.extract_candidates(canonicalize_title(title))]


def test_extracts_chip_level_fields() -> None:
    attrs = _extract("PNY NVIDIA A100 80GB PCIe Passive GPU")
    assert attrs.chip_vendor is not None and attrs.chip_vendor.value == "nvidia"
    assert attrs.vram_gb is not None and attrs.vram_gb.value == 80
    assert attrs.interface is not None and attrs.interface.value == "pcie"
    assert attrs.cooling is not None and attrs.cooling.value == "passive"


def test_brand_is_chip_vendor_not_board_partner() -> None:
    extracted = gpu.extract(canonicalize_title("ASUS TUF GeForce RTX 4090 24GB"))
    assert extracted.brand is not None and extracted.brand.value == "nvidia"


@pytest.mark.parametrize(
    ("title", "field"),
    [
        # Two sizes: a comparison or a bundle; guessing either could veto wrongly.
        ("NVIDIA RTX A6000 48GB vs 24GB", "vram_gb"),
        ("NVIDIA A100 SXM4 to PCIe adapter", "interface"),
        ("AMD Radeon card, two vendors: NVIDIA", "chip_vendor"),
    ],
)
def test_ambiguous_values_are_unknown(title: str, field: str) -> None:
    assert getattr(_extract(title), field) is None


def test_link_speed_is_not_vram() -> None:
    assert _extract("NVIDIA Tesla T4 PCIe 16GB 12Gb/s").vram_gb is not None
    assert _extract("NVIDIA Tesla T4 PCIe 12Gb/s").vram_gb is None


def test_offer_terms_come_from_vocab() -> None:
    extracted = gpu.extract(canonicalize_title("NVIDIA Tesla V100 32GB Used Server Pull"))
    assert extracted.condition is not None and extracted.condition.value == "used"
    # Drive fields never populate for a GPU title.
    assert extracted.capacity_bytes is None


_OFFER_FIELDS = ("condition", "recert_channel", "packaging", "warranty_months", "warranty_channel")


@pytest.mark.parametrize(
    "title",
    [
        "NVIDIA A100 Factory Recertified 3 Year Warranty Manufacturer Warranty",
        "RTX 4090 Seller Refurbished Retail Box",
        "Tesla T4 for parts OEM bulk no warranty",
        "Quadro like new",
    ],
)
def test_offer_terms_match_vocab_extract(title: str) -> None:
    # vocab.offer_terms is the shared helper every category reads offer terms
    # through; it must agree field for field with the drive extractor.
    canonical = canonicalize_title(title)
    terms, drive = vocab.offer_terms(canonical), vocab.extract(canonical)
    for name in _OFFER_FIELDS:
        assert getattr(terms, name) == getattr(drive, name)
    assert terms.capacity_bytes is None
    assert terms.brand is None


def test_name_candidates_need_the_chip_vendor() -> None:
    assert "a100" in _keys("NVIDIA A100 80GB PCIe")
    # A bare 'A100' with no vendor or product-line word is an ordinary token.
    assert _keys("A100 80GB card") == []


def test_name_candidates_are_vendor_hinted_mpns() -> None:
    candidates = gpu.extract_candidates(canonicalize_title("NVIDIA GeForce RTX 4090 24GB"))
    by_key = {c.normalized: c for c in candidates}
    assert {"geforcertx4090", "rtx4090"} <= set(by_key)
    assert by_key["rtx4090"].kind is TokenKind.MANUFACTURER_MPN
    assert by_key["rtx4090"].vendor_hint == "nvidia"


def test_board_part_number_and_amd_names() -> None:
    assert "900210010020100" in _keys("NVIDIA A100 900-21001-0020-100")
    assert "mi250x" in _keys("AMD Instinct MI250X 128GB OAM")


def test_structured_mpn_is_a_structured_candidate() -> None:
    candidates = gpu.extract_candidates("nvidia card", structured_mpn="900-21001-0020-100")
    assert candidates[0].from_structured_field
    assert candidates[0].vendor_hint == "nvidia"


def test_no_grammar_decode() -> None:
    assert no_decode("a100") is None


_SPEC = gpu.GpuHard(chip_vendor="nvidia", vram_gb=80, interface="pcie", cooling="passive")


@pytest.mark.parametrize(
    ("title", "vetoed"),
    [
        ("NVIDIA A100 80GB PCIe Passive", []),
        ("NVIDIA A100 40GB PCIe Passive", ["vram_gb"]),
        ("NVIDIA A100 80GB SXM4", ["interface"]),
        ("NVIDIA A100 80GB PCIe blower", ["cooling"]),
        ("AMD Instinct 80GB PCIe", ["chip_vendor"]),
        # Unknown on the listing side never vetoes.
        ("NVIDIA A100", []),
    ],
)
def test_veto_table(title: str, vetoed: list[str]) -> None:
    extracted = gpu.extract(canonicalize_title(title))
    assert gpu.veto(extracted, ladder.HardAttrs(category=_SPEC)) == vetoed


def test_unknown_catalog_field_cannot_veto() -> None:
    extracted = gpu.extract(canonicalize_title("NVIDIA A100 40GB SXM4"))
    assert gpu.veto(extracted, ladder.HardAttrs(category=gpu.GpuHard())) == []


def test_foreign_payloads_never_veto() -> None:
    # A drive-shaped HardAttrs (no category payload) or drive-extracted
    # attributes read as all-unknown, never as a contradiction.
    extracted = gpu.extract(canonicalize_title("NVIDIA A100 40GB"))
    assert gpu.veto(extracted, ladder.HardAttrs(capacity_bytes=1)) == []
    assert gpu.veto(ExtractedAttributes(), ladder.HardAttrs(category=_SPEC)) == []


@pytest.mark.parametrize(
    ("title", "key"),
    [
        # The seeded data-center aliases are name + VRAM/interface qualifiers.
        ("NVIDIA A100 80GB PCIe Tensor Core GPU", "a10080gbpcie"),
        ("NVIDIA H100 PCIe 80GB HBM2e", "h100pcie"),
        ("NVIDIA Tesla V100 PCIe (32GB) Accelerator", "teslav100pcie32gb"),
        # The vendor word written directly before the name joins the seeds'
        # vendor-prefixed datasheet spellings.
        ("NVIDIA Tesla V100 32GB PCIe", "nvidiateslav10032gbpcie"),
        ("NVIDIA RTX A6000 48GB", "nvidiartxa6000"),
        ("AMD Instinct MI210 64GB", "amdinstinctmi210"),
    ],
)
def test_name_extends_with_contiguous_qualifiers_and_vendor(title: str, key: str) -> None:
    assert key in _keys(title)


def test_qualifier_extension_is_contiguous_and_vendor_gated() -> None:
    # Title order only: 'V100 32GB PCIe' never yields the 'V100 PCIe 32GB' key.
    assert "teslav100pcie32gb" not in _keys("NVIDIA Tesla V100 32GB PCIe")
    # A word between the name and a qualifier ends the run.
    assert "a10080gb" not in _keys("NVIDIA A100 Tensor 80GB")
    # A link speed is not a VRAM qualifier.
    assert "a10080gb" not in _keys("NVIDIA A100 80GB/s")
    # The vendor gate still applies to the extended spans.
    assert _keys("A100 80GB PCIe accelerator") == []
