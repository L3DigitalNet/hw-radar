"""WD recertified store SKU → structured manufacturer MPN (wd._recert_mpn).

Keys and titles are the ones observed in the 2026-09-25 harvest
(docs/evidence/2026-09-25-ms1e-draft-corpus.jsonl, source "wd-recertified").
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hw_radar.acquisition.contracts import RawBatch, RawItem
from hw_radar.acquisition.sources.wd import WdAdapter
from hw_radar.matching.mpn import extract_candidates
from hw_radar.matching.types import TokenKind

_PRODUCT_URL = "https://api.westerndigital.com/wdwebservices/v2/us/products/p"

INTERNAL = [
    ("RWD20EFPX", 'WD Red Plus Internal NAS HDD 3.5" - Recertified', "WD20EFPX"),
    ("RWD1004FBYZ", "WD Gold Enterprise Class SATA HDD - Recertified", "WD1004FBYZ"),
    ("RWD102KFBX", "WD Red Pro NAS Hard Drive - Recertified", "WD102KFBX"),
    ("RWD10EARZ", "WD Blue PC Desktop Hard Drive - Recertified", "WD10EARZ"),
    ("RWD10SPZX", "WD Blue PC Mobile Hard Drive - Recertified", "WD10SPZX"),
    ("RWD10SPSX", "WD_BLACK Performance Mobile Hard Drive - Recertified", "WD10SPSX"),
    ("RWD20EFAX", "WD Red NAS Hard Drive - Recertified", "WD20EFAX"),
]

NOT_PROMOTED = [
    # wd-0074: the one internal key observed without the R prefix. Not the
    # observed contract, so nothing is synthesized from it.
    ("WD240KFGX", "WD Red Pro NAS Hard Drive - Recertified"),
    # Ultrastar keys are "R" + a WD part number, not an MPN.
    ("R0F62802", "Ultrastar DC HC580 Data Center HDD - Recertified"),
    ("R0B42266", "Ultrastar DC HC330 Data Center HDD - Recertified"),
    # Consumer enclosures: WDB... retail codes.
    ("RWDBBGB0040HBK-NESN", "My Book (Recertified)"),
    ("RWDBA2D0020BBL-WESN", "My Passport for Mac (Recertified)"),
    ("RWDBPKJ0040BBK-WESN", "My Passport (Recertified)"),
    ("RWDBHJS0060BBK-WESN", "WD Elements Portable (Recertified)"),
    ("RWDBEPK0020BBK-WESN", "WD Elements SE (Recertified)"),
    ("RWDBC3C0020BBL-WESN", "My Passport Ultra (Recertified)"),
    ("RWDBWLG0040HBK-NESN", "WD Elements Desktop Hard Drive (Recertified)"),
]


def _parse_one(code: str, title: str) -> tuple[str, dict[str, object]]:
    batch = RawBatch(
        source="wd-recertified",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url=_PRODUCT_URL,
                payload_json={
                    "name": title,
                    "variantOptions": [
                        {"code": code, "priceData": {"value": 49.99}, "saleable": True}
                    ],
                },
            )
        ],
    )
    (listing,) = WdAdapter().parse(batch)
    return listing.source_listing_key, listing.attrs


@pytest.mark.parametrize(("code", "title", "mpn"), INTERNAL)
def test_internal_recert_key_promotes_mpn_and_keeps_key(code: str, title: str, mpn: str) -> None:
    key, attrs = _parse_one(code, title)
    assert attrs["mpn"] == mpn
    assert key == code  # listing identity is the raw store code, never the MPN
    assert attrs["saleable"] is True


@pytest.mark.parametrize(("code", "title"), NOT_PROMOTED)
def test_nonconforming_and_enclosure_keys_promote_nothing(code: str, title: str) -> None:
    key, attrs = _parse_one(code, title)
    assert "mpn" not in attrs
    assert key == code


def test_enclosure_title_refuses_even_an_internal_shaped_key() -> None:
    # The title guard must hold on its own: if WD ever keys an enclosure with an
    # internal-drive-shaped code, it still never resolves onto a bare drive.
    _, attrs = _parse_one("RWD40EFPX", "WD Elements Desktop Hard Drive (Recertified)")
    assert "mpn" not in attrs


@pytest.mark.parametrize(("code", "title", "mpn"), INTERNAL)
def test_promoted_mpn_is_a_wd_manufacturer_mpn_to_the_matcher(
    code: str, title: str, mpn: str
) -> None:
    # Cross-file contract with matching.mpn._MFR_SHAPES: every value the
    # connector promotes must classify as a western_digital MANUFACTURER_MPN,
    # otherwise the resolver would carry it as a 0.98-confidence UNKNOWN_CODE.
    _, attrs = _parse_one(code, title)
    structured = next(
        c
        for c in extract_candidates(title.casefold(), structured_mpn=mpn)
        if c.from_structured_field
    )
    assert attrs["mpn"] == mpn
    assert structured.kind is TokenKind.MANUFACTURER_MPN
    assert structured.vendor_hint == "western_digital"
