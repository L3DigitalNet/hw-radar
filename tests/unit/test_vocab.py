"""N2 controlled vocabularies (spec C.3.1) — table-driven over golden titles."""

from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.types import Attribute, ExtractedAttributes
from hw_radar.matching.vocab import extract


def _x(title: str) -> ExtractedAttributes:
    # Extraction over the canonical form, exactly as the resolver does.
    return extract(canonicalize_title(title))


def _value[T](attribute: Attribute[T] | None) -> T:
    # Strict-typing helper: asserts presence, returns the narrowed value.
    assert attribute is not None
    return attribute.value


def test_capacity_tb_and_gb() -> None:
    assert _value(_x("Seagate 16TB Exos").capacity_bytes) == 16_000_000_000_000
    assert _value(_x("Samsung 960GB SSD").capacity_bytes) == 960_000_000_000
    assert _value(_x("WD 1.92TB SSD").capacity_bytes) == 1_920_000_000_000


def test_capacity_never_fires_inside_mpn_tokens() -> None:
    # 'st16000nm001g' has no standalone '<n> tb|gb' token
    assert _x("ST16000NM001G enterprise drive").capacity_bytes is None


def test_conflicting_capacities_drop_confidence() -> None:
    attrs = _x("16TB (2x 8TB bundle)")
    assert attrs.capacity_bytes is not None
    assert attrs.capacity_bytes.value == 16_000_000_000_000  # first mention wins
    assert attrs.capacity_bytes.confidence < 0.7


def test_link_speed_preceding_capacity_never_wins() -> None:
    # Final-review I-1: '12gb' inside '12Gb/s' must not become the capacity.
    attrs = _x("SAS 12Gb/s 3.5in Seagate ST4000NM0023 4TB")
    assert _value(attrs.capacity_bytes) == 4_000_000_000_000
    assert attrs.capacity_bytes is not None and attrs.capacity_bytes.confidence == 0.9


def test_interface_and_link_speed() -> None:
    attrs = _x("4TB 7.2K SAS 12Gb/s HDD")
    assert _value(attrs.interface) == "sas"
    assert _value(attrs.link_speed_gbps) == 12.0
    assert _value(_x("SATA III drive").interface) == "sata"
    assert _value(_x("NVMe PCIe 4.0 SSD").interface) == "nvme"


def test_form_factor() -> None:
    assert _value(_x('3.5" enterprise HDD').form_factor) == "3.5"
    assert _value(_x("2.5 inch SSD").form_factor) == "2.5"
    assert _value(_x("M.2 2280 NVMe").form_factor) == "m.2"
    assert _x("13.5 lbs box").form_factor is None  # no digit-prefixed false hit


def test_rpm_plain_and_k_forms() -> None:
    assert _value(_x("7200RPM SATA").rpm) == 7200
    assert _value(_x("7.2K RPM SAS").rpm) == 7200
    assert _value(_x("15K RPM SAS").rpm) == 15000


def test_cache_sector_recording_security() -> None:
    attrs = _x("256MB Cache 512e CMR SED drive")
    assert _value(attrs.cache_mb) == 256
    assert _value(attrs.sector_format) == "512e"
    assert _value(attrs.recording_tech) == "cmr"
    assert _value(attrs.security) == "sed"
    assert _value(_x("SMR archive drive").recording_tech) == "smr"


def test_condition_ladder_precedence() -> None:
    assert _value(_x("Seagate 8TB used - for parts").condition) == "for_parts"
    assert _value(_x("For spares or repair: Seagate 12TB HDD").condition) == "for_parts"
    assert _value(_x("Seagate 12TB spares/repair").condition) == "for_parts"
    assert _value(_x("WD 4TB for repair").condition) == "for_parts"
    attrs = _x("16TB Factory Recertified drive")
    assert _value(attrs.condition) == "recertified"
    assert _value(attrs.recert_channel) == "factory"
    assert _x("Recertified enterprise HDD").recert_channel is None
    assert _value(_x("Seller refurbished 10TB").condition) == "refurbished"
    assert _value(_x("Renewed 4TB drive").condition) == "refurbished"  # Amazon-speak
    assert _value(_x("Open box WD Gold").condition) == "open_box"
    assert _value(_x("server pull 4TB SAS").condition) == "used"
    assert _value(_x("Brand New sealed 12TB").condition) == "new"
    assert _x("like new 12TB").condition is None  # 'like new' asserts nothing


def test_packaging_and_warranty() -> None:
    assert _value(_x("retail box 4TB").packaging) == "retail"
    assert _value(_x("OEM bare drive 8TB").packaging) == "bulk"
    assert _value(_x("12TB with 5 year warranty").warranty_months) == 60
    assert _value(_x("no warranty as-is").warranty_channel) == "none"
    assert _value(_x("manufacturer warranty included").warranty_channel) == "manufacturer"


def test_quantity_lot_forms_but_never_xn() -> None:
    assert _value(_x("lot of 4 Seagate 16TB").quantity) == 4
    assert _value(_x("2-pack WD Red").quantity) == 2
    assert _value(_x("4x 16TB drives").quantity) == 4
    assert _value(_x("qty 3 enterprise drives").quantity) == 3
    # 'xN' form is unsupported by design: it collides with Seagate family names.
    assert _x("Seagate Exos X16 16TB").quantity is None


def test_brand_normalization_including_optimus_rebrand() -> None:
    assert _value(_x("Seagate Exos 16TB").brand) == "seagate"
    assert _value(_x("Western Digital Gold 12TB").brand) == "western_digital"
    assert _value(_x("WD Red Plus 12TB").brand) == "western_digital"
    assert _value(_x("SanDisk Ultrastar DC HC550 16TB").brand) == "western_digital"
    assert _value(_x("HGST Ultrastar 4TB").brand) == "hgst"
    assert _value(_x("Hitachi Deskstar 4TB").brand) == "hgst"
    assert _value(_x("Toshiba MG08 16TB").brand) == "toshiba"


def test_unknowns_stay_none() -> None:
    attrs = _x("enterprise hard drive")
    for extracted in (
        attrs.capacity_bytes,
        attrs.interface,
        attrs.form_factor,
        attrs.rpm,
        attrs.condition,
        attrs.brand,
        attrs.quantity,
        attrs.security,
    ):
        assert extracted is None
