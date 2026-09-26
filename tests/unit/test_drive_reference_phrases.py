"""Drive-local compatibility phrasing masks the cited drive (s7 D4).

eBay resellers title compatible or look-alike stock after the drive it
substitutes for: "FOR Seagate Exos X14 ... ST12000NM0008 NEW" (MS-1e
ebay-0190/0224), "FIT FOR ..." (0263), "适用于 ..." ("suitable for", 0294),
"suitable for Western Digital ..." (0463). Each auto-accepted the cited drive.
The drive layers now mask "fit for" and "suitable for" anywhere and a bare
"for" only as the title's first token; other categories keep their masks."""

import pytest

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import (
    DRIVE_REFERENCE_PHRASE,
    canonicalize_listing_text,
    canonicalize_title,
    mask_reference_spans,
)
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import Grain


def _verdict(title: str) -> ladder.Verdict:
    canonical = canonicalize_title(title)
    candidates = mpn.extract_candidates(canonical)
    decoded = next(
        (d for c in candidates if (d := grammars.decode(c.normalized)) is not None), None
    )
    return ladder.decide(
        vocab.extract(canonical), candidates, None, [], decoded, distinct_mpn_guard=True
    )


def _mpns(title: str) -> set[str]:
    return {c.normalized for c in mpn.extract_candidates(canonicalize_title(title))}


@pytest.mark.parametrize(
    ("title", "cited"),
    [
        (
            'FOR Seagate Exos X14 12TB 7200 RPM SATA 6Gb/s 256MB 3.5" HDD ST12000NM0008 NEW',
            "st12000nm0008",
        ),
        (
            'for Seagate Exos 7E10 ST4000NM024B 4TB 6Gb/s 256MB SATA 3.5" Enterprise HDD',
            "st4000nm024b",
        ),
        (
            'FIT FOR Seagate Exos 7E10 ST10000NM017B 10TB 7200 RPM 256MB Cache SATA 3.5"',
            "st10000nm017b",
        ),
        (
            "全新 适用于 Seagate ST2000NX0253 Exos 7E2000 2TB SATA 6Gb/s 7200 RPM 2.5” 硬盘-",
            "st2000nx0253",
        ),
        (
            'For WD Ultrastar DC HC560 WUH722020BLE6L4 20TB 512MB 7200RPM 3.5" HDD Hard Drive',
            "wuh722020ble6l4",
        ),
        (
            'suitable for Western Digital Red Plus WD40EFPX/WD40EFZX 4TB 3.5" SATA Hard Drive',
            "wd40efpx",
        ),
    ],
)
def test_compatibility_phrased_drive_is_not_identity_evidence(title: str, cited: str) -> None:
    assert cited not in _mpns(title)
    extracted = vocab.extract(canonicalize_title(title))
    assert extracted.brand is None
    assert extracted.family_mentions is None
    verdict = _verdict(title)
    assert verdict.outcome is ladder.Outcome.NONE
    assert verdict.grain is Grain.NONE


@pytest.mark.parametrize(
    ("title", "kept"),
    [
        # Mid-title bare "for" describes the drive's own use.
        ("Seagate ST4000NM000A 4TB for Dell server", "st4000nm000a"),
        ("NAS drive for Synology Seagate IronWolf ST4000VN008", "st4000vn008"),
        # "for parts" is a condition, not a reference.
        ("For Parts Seagate IronWolf 4TB ST4000VN008", "st4000vn008"),
        # "for" inside a word is not the token.
        ("Forward Seagate IronWolf 4TB ST4000VN008", "st4000vn008"),
    ],
)
def test_non_leading_or_condition_for_keeps_identity(title: str, kept: str) -> None:
    assert kept in _mpns(title)


def test_for_parts_title_keeps_its_condition() -> None:
    extracted = vocab.extract(canonicalize_title("For Parts Seagate IronWolf 4TB ST4000VN008"))
    assert extracted.condition is not None
    assert extracted.condition.value == "for_parts"


def test_leading_for_span_stops_at_the_clause_boundary() -> None:
    canonical = canonicalize_title("For Dell R740, Seagate IronWolf 4TB ST4000VN008")
    masked = mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE)
    assert "st4000vn008" in masked
    assert "dell" not in masked


def test_leading_for_never_masks_the_seller_condition_label() -> None:
    canonical = canonicalize_listing_text("FOR Seagate Exos ST12000NM0008", "New")
    assert mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE).endswith(" - new")


def test_cjk_phrase_folds_idempotently() -> None:
    once = canonicalize_title("全新 适用于 Seagate ST2000NX0253")
    assert once == "suitable for seagate st2000nx0253"
    assert canonicalize_title(once) == once


def test_cpu_masking_does_not_take_the_drive_phrases() -> None:
    # CPU identity keeps its own phrase set: the drive-local phrases and the
    # leading "for" are not in it.
    for title in ("for AMD EPYC 7763 64-core", "AMD EPYC 7763 suitable for SP3 boards"):
        canonical = canonicalize_title(title)
        assert cpu._identity_text(canonical) == canonical  # pyright: ignore[reportPrivateUsage]
