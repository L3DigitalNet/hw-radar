"""Owner rulings of 2026-09-26 on the MS-1e drive audit, at the vocab and ladder
layers: Q7 ("90%NEW" asserts nothing; "new pull" is used), Q4 (a lot never
auto-accepts) and Q6 (the WD recertified store proves a factory recert)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hw_radar.acquisition.sources import ADAPTERS, RETIRED_ADAPTERS
from hw_radar.acquisition.sources.wd import WdAdapter
from hw_radar.matching import ladder, vocab
from hw_radar.matching.normalize import canonicalize_listing_text, canonicalize_title
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import (
    Attribute,
    DecodeResult,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    Provenance,
    TokenKind,
)

_REPO = Path(__file__).resolve().parents[2]
# The ratified MS-1e corpus is the ADR-0019 gate fixture; the CPU corpus stays evidence.
_DRIVE_CORPUS = _REPO / "tests" / "fixtures" / "matching_corpus" / "corpus.jsonl"
_CPU_CORPUS = _REPO / "docs" / "evidence" / "2026-09-26-cpu-epyc-draft-corpus.jsonl"


def _x(title: str) -> ExtractedAttributes:
    return vocab.extract(canonicalize_title(title))


def _condition(title: str) -> str | None:
    attr = _x(title).condition
    return None if attr is None else attr.value


# ── Q7: percentage-qualified "new" and "new pull" ────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        # MS-1e ebay-0261, verbatim.
        '90%NEW Seagate Exos 7E10 ST10000NM017B 10TB 7200 RPM 256MB Cache SATA 3.5"',
        "95% new Seagate 12TB",
        "99%new WD Red Plus 4TB",
        "Seagate 16TB 90 % NEW",
    ],
)
def test_percentage_new_asserts_no_condition(title: str) -> None:
    assert canonicalize_title(title).startswith(("90%new", "95% new", "99%new", "seagate"))
    assert _condition(title) is None


@pytest.mark.parametrize(
    ("title", "condition"),
    [
        ("Brand New Seagate 12TB", "new"),
        ("NEW Seagate Exos 16TB", "new"),
        ("Seagate 16TB factory sealed", "new"),
        # A percentage elsewhere does not hide a bare "new".
        ("100% health NEW Seagate 12TB", "new"),
        ("like new Seagate 12TB", None),
    ],
)
def test_unqualified_new_keeps_its_behavior(title: str, condition: str | None) -> None:
    assert _condition(title) == condition


@pytest.mark.parametrize(
    ("title", "condition"),
    [
        ('ST16000NM007H Seagate Exos X24 16TB SAS 3.5" HDD New Pull', "used"),
        ("Seagate 16TB NEW PULL", "used"),
        ("Seagate 16TB new pulled from server", "used"),
        ("Seagate 16TB new-pull", "used"),
        ("Seagate 16TB pulled", "used"),
        ("Seagate 16TB server pull", "used"),
        ("For parts Seagate 16TB new pull", "for_parts"),
    ],
)
def test_new_pull_is_used(title: str, condition: str) -> None:
    assert _condition(title) == condition


@pytest.mark.parametrize("phrase", ["new pull", "new pulled", "new-pull"])
def test_new_rule_never_claims_a_pull(phrase: str) -> None:
    # Ordering-proof: the new rule itself rejects the phrase, so moving it above
    # the used rule cannot turn a pull into a new drive.
    new_rules = [pattern for pattern, value, _c, _conf in vocab._CONDITIONS if value == "new"]  # pyright: ignore[reportPrivateUsage] - the table's own contract is under test
    assert new_rules
    assert all(pattern.search(phrase) is None for pattern in new_rules)


# ── Q4: quantity forms and the lot review ────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "quantity", "is_lot"),
    [
        ("Lot of 2 Seagate Exos X14 ST10000NM0478 10TB", 2, True),
        ("Lot 10 Supermicro Seagate EXOS AF 7E2000 ST2000NX0253 2TB", 10, True),
        ("Lot 40 Seagate Exos AF 7E2000 ST2000NX0253", 40, True),
        ("(Lot of 4x) Western Digital 6TB WD6002FFWX", 4, True),
        ("Seagate Exos X24 24TB 4-pack", 4, True),
        ("qty 3 Seagate 16TB", 3, True),
        ("2pcs AMD EPYC Milan 7T83 CPU", 2, True),
        ("Lot of 1 Seagate 16TB", 1, False),
        ("qty 1 Seagate 16TB", 1, False),
        ("1pcs new Seagate Exos 7E2000 1TB ST1000NX0313", 1, False),
        # The "Nx" form is below the lot bar: a CPU thread count, not a lot.
        ("AMD EPYC 9354 32C server processor 32x 3.25GHz", 32, False),
        ("Seagate Exos X16 16TB", None, False),
    ],
)
def test_quantity_forms_and_lot_bar(title: str, quantity: int | None, is_lot: bool) -> None:
    attr = vocab.extract_quantity(canonicalize_title(title))
    assert (None if attr is None else attr.value) == quantity
    assert (ladder.lot_quantity(attr) is not None) is is_lot


def _corpus_lots(path: Path) -> dict[str, int]:
    lots: dict[str, int] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        canonical = canonicalize_listing_text(row["title"], row["listing"]["condition_label"])
        lot = ladder.lot_quantity(vocab.extract_quantity(canonical))
        if lot is not None:
            lots[row["id"]] = int(str(lot["quantity"]))
    return lots


def test_lot_forms_fire_only_on_the_corpus_multi_unit_titles() -> None:
    # The veto-safety audit, pinned: across all 718 drive and 284 CPU corpus
    # titles, every lot-grade quantity above 1 is a real multi-unit offer.
    # Growing this set needs the same check of each new title. The contextual
    # "Nx" form (s8 Codex r1 finding 3) added ebay-0029 ("4x Hard Disk ...",
    # labelled a multipack) and the four "Motherboard With 2x AMD EPYC"
    # board bundles, which the CPU bundle veto already reviews.
    assert _corpus_lots(_DRIVE_CORPUS) == {
        "ebay-0011": 4,
        "ebay-0029": 4,
        "ebay-0053": 4,
        "ebay-0199": 2,
        "ebay-0282": 10,
        "ebay-0288": 40,
        "ebay-0467": 4,
        "ebay-0490": 2,
    }
    assert _corpus_lots(_CPU_CORPUS) == {
        "cpu-0043": 4,
        "cpu-0140": 4,
        "cpu-0207": 2,
        "cpu-0248": 2,
        "cpu-0260": 2,
        "cpu-0269": 2,
        "cpu-0270": 2,
        "cpu-0277": 2,
        "cpu-0279": 2,
    }


def test_cpu_extract_reads_the_quantity() -> None:
    extracted = cpu.extract(canonicalize_title("Lot of 4 AMD EPYC 7763 64-Core SP3"))
    assert extracted.quantity is not None and extracted.quantity.value == 4


_HIT_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=7)


def _hit() -> ladder.AliasHit:
    return ladder.AliasHit(
        target=_HIT_TARGET,
        source_kind="catalog_authoritative",
        alias_type="mpn",
        brand="seagate",
        hard_attrs=ladder.HardAttrs(),
        candidate_kind=TokenKind.MANUFACTURER_MPN,
        candidate_vendor="seagate",
        candidate_normalized="st10000nm0478",
    )


def _lot(n: int) -> ExtractedAttributes:
    return ExtractedAttributes(
        brand=Attribute("seagate", 0.9, "vocab"),
        quantity=Attribute(n, 0.95, "vocab", f"lot of {n}"),
    )


_CANDIDATE = MpnCandidate(
    raw="st10000nm0478",
    normalized="st10000nm0478",
    kind=TokenKind.MANUFACTURER_MPN,
    vendor_hint="seagate",
    confidence=0.9,
)


def test_lot_reviews_a_rung_one_hit() -> None:
    verdict = ladder.decide(_lot(2), [_CANDIDATE], None, [_hit()], None)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 1
    assert verdict.evidence["lot"] == {"quantity": 2, "source_text": "lot of 2"}


def test_lot_reviews_an_inherited_prior() -> None:
    prior = ladder.PriorResolution(
        target=_HIT_TARGET, confidence=0.98, hard_attrs=ladder.HardAttrs()
    )
    verdict = ladder.decide(_lot(3), [], prior, [], None)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 0
    assert verdict.evidence["lot"] == {"quantity": 3, "source_text": "lot of 3"}


def test_lot_reviews_a_rung_two_decode() -> None:
    decoded = DecodeResult(
        vendor="seagate",
        family_name="exos",
        capacity_bytes=None,
        generation=None,
        provenance=Provenance.CORROBORATED_COMMUNITY,
        rule="test",
    )
    verdict = ladder.decide(_lot(2), [_CANDIDATE], None, [], decoded)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 2
    assert "lot" in verdict.evidence


def test_single_unit_still_accepts() -> None:
    verdict = ladder.decide(_lot(1), [_CANDIDATE], None, [_hit()], None)
    assert verdict.outcome is ladder.Outcome.ACCEPT


# ── Q6: source-proven offer terms ────────────────────────────────────────────


def _offer(title: str, source: str) -> ExtractedAttributes:
    return vocab.with_source_offer_terms(vocab.offer_terms(canonicalize_title(title)), source)


def _terms(extracted: ExtractedAttributes) -> tuple[str | None, str | None]:
    return (
        None if extracted.condition is None else extracted.condition.value,
        None if extracted.recert_channel is None else extracted.recert_channel.value,
    )


@pytest.mark.parametrize(
    ("title", "source", "terms"),
    [
        # MS-1e wd-0057's store title: the store proves the channel.
        (
            'WD Red Plus Internal NAS HDD 3.5" - Recertified',
            "wd-recertified",
            ("recertified", "factory"),
        ),
        # A store title stating no condition still gets the declared one.
        ('WD Red Plus Internal NAS HDD 3.5"', "wd-recertified", ("recertified", "factory")),
        ("WD Gold 8TB Factory Recertified", "wd-recertified", ("recertified", "factory")),
        # A contradicting store title keeps its own condition, without a channel.
        ("WD Red Plus 4TB - Used", "wd-recertified", ("used", None)),
        ("WD Red Plus 4TB - For parts", "wd-recertified", ("for_parts", None)),
        # The generic text rule is unchanged everywhere else.
        ("WD Red Plus 4TB Recertified", "ebay", ("recertified", None)),
        ("WD Red Plus 4TB Factory Recertified", "ebay", ("recertified", "factory")),
        ("WD Red Plus 4TB", "ebay", (None, None)),
    ],
)
def test_source_offer_terms(title: str, source: str, terms: tuple[str | None, str | None]) -> None:
    assert _terms(_offer(title, source)) == terms


def test_declared_sources_are_registered_adapters() -> None:
    # The declaration keys on SourceSite.normalized_name, which each adapter's
    # site_key mirrors; a renamed key would silently drop the provenance.
    assert set(vocab.SOURCE_OFFER_PROVENANCE) <= set(ADAPTERS) | set(RETIRED_ADAPTERS)
    assert WdAdapter.site_key in vocab.SOURCE_OFFER_PROVENANCE
