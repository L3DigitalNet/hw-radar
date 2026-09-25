"""MS2-D-27 (plan task B6): the collector's category hint survives the MS-1e corpus
tooling — harvest staging → JSONL → `load_corpus` → `evaluate_corpus` — so a
harvested non-drive entry replays through its own category rules instead of
silently taking the legacy drive default. Unhinted (drive-only) staging and the
committed drive corpus must be untouched by the new field.

Seeding is local rather than imported from the frozen `test_ratification_corpus.py`,
so that file stays unedited.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from hw_radar.acquisition.contracts import ParsedListing
from hw_radar.catalog.management.commands.harvest_corpus import (
    _staging_entry,  # pyright: ignore[reportPrivateUsage]
)
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    OfferSnapshot,
    ProductAlias,
    ProductModel,
    RetentionClass,
)
from hw_radar.matching.eval.corpus import CorpusMeta, load_corpus, load_meta
from hw_radar.matching.eval.evaluate import evaluate_corpus
from hw_radar.matching.normalize import normalize_alias_text

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"

# Drive-shaped on purpose: under the drive rules this title is a rung-1 exact hit
# on the seeded ST16000NM001G alias (the control assertion below proves it), so a
# hint dropped anywhere in the pipeline shows up as a drive model accept.
_DRIVE_SHAPED_TITLE = "Seagate Exos X16 16TB ST16000NM001G SATA Factory Recertified Hard Drive"

_META = CorpusMeta(
    corpus_version="b6-test",
    harvested_from=date(2026, 9, 24),
    harvested_to=date(2026, 9, 24),
    observed_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
    source_counts={"serverpartdeals": 2},
    matcher_version="test",
    audit_rollup={},
)


def _seed_exos() -> ProductModel:
    seagate, _ = Manufacturer.objects.get_or_create(
        normalized_name="seagate", defaults={"name": "Seagate"}
    )
    model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST16000NM001G",
        normalized_model_number=normalize_alias_text("ST16000NM001G"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model,
        media_type=MediaType.HDD,
        capacity_tb=Decimal(16),
        interface="SATA 6Gb/s",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("ST16000NM001G"),
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


def _parsed(key: str, *, category_hint: str | None) -> ParsedListing:
    return ParsedListing(
        source_listing_key=key,
        url=f"https://example.test/spd/{key}",
        title=_DRIVE_SHAPED_TITLE,
        price=Decimal("179.00"),
        condition_label="Recertified",
        attrs={"sku": f"SPD-{key}"},
        category_hint=category_hint,
    )


def _labeled(staging: dict[str, object]) -> dict[str, object]:
    """The owner labeling step (design §6) in miniature: staging carries a bare
    `oem_dual_label` pre-fill where the corpus carries a `label` block."""
    entry = {k: v for k, v in staging.items() if k != "oem_dual_label"}
    entry["label"] = {"expected_grain": "none"}
    return entry


def _current_edge(listing: Listing) -> ListingResolution:
    return ListingResolution.objects.get(listing=listing, is_current=True)


def test_harvested_non_drive_entry_reaches_category_rules(db: None, tmp_path: Path) -> None:
    drive_model = _seed_exos()
    staged = [
        _staging_entry("serverpartdeals", _parsed("gpu-1", category_hint="gpu")),
        _staging_entry("serverpartdeals", _parsed("control-1", category_hint=None)),
    ]
    listing_block = staged[0]["listing"]
    assert isinstance(listing_block, dict)
    assert listing_block["category_hint"] == "gpu"

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(json.dumps(_labeled(entry)) + "\n" for entry in staged), encoding="utf-8"
    )
    entries = load_corpus(corpus)
    assert [e.listing.category_hint for e in entries] == ["gpu", None]

    hinted, control = evaluate_corpus(entries, _META)

    # Control: the identical title unhinted is a drive model accept, so the hinted
    # result below is the hint's doing and not a title that never matched.
    assert control.model_norm == drive_model.normalized_model_number
    assert control.rung == 1

    listing = Listing.objects.get(source_listing_key="gpu-1")
    edge = _current_edge(listing)
    assert edge.evidence["category"] == "gpu"
    # Holds both before `gpu` is registered (the unsupported-category none-edge)
    # and after (gpu rules cannot reach a drive-spec model): no edge in this
    # listing's history ever points at a drive model.
    assert not ListingResolution.objects.filter(
        listing=listing, product_model__drive_spec__isnull=False
    ).exists()
    assert not ListingResolution.objects.filter(
        listing=listing, product_variant__product_model__drive_spec__isnull=False
    ).exists()
    assert hinted.model_norm != drive_model.normalized_model_number


def test_unhinted_staging_entry_bytes_unchanged() -> None:
    # The exact pre-MS2-D-27 serialization, key order included: a drive-only
    # harvest must stage byte-identically, so the hint key may appear only when set.
    entry = _staging_entry("serverpartdeals", _parsed("drive-1", category_hint=None))
    assert json.dumps(entry) == (
        '{"id": "serverpartdeals:drive-1", "source": "serverpartdeals", '
        f'"title": "{_DRIVE_SHAPED_TITLE}", '
        '"listing": {"source_listing_key": "drive-1", '
        '"url": "https://example.test/spd/drive-1", "price": "179.00", '
        '"currency": "USD", "condition_label": "Recertified", '
        '"attrs": {"sku": "SPD-drive-1"}}, '
        '"oem_dual_label": false}'
    )


def test_existing_drive_corpus_loads_unchanged(db: None) -> None:
    raw_lines = [
        json.loads(line)
        for line in SYNTHETIC_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    entries = load_corpus(SYNTHETIC_JSONL)
    assert len(entries) == len(raw_lines)
    for raw, entry in zip(raw_lines, entries, strict=True):
        assert "category_hint" not in raw["listing"]
        assert entry.listing.category_hint is None
        # The loaded listing re-serializes to exactly the committed line's block.
        assert entry.listing.model_dump(mode="json", exclude={"category_hint"}) == raw["listing"]

    # Replay one entry: an unhinted corpus line persists no hint key and still
    # dispatches to the legacy drive default.
    _seed_exos()
    first = next(e for e in entries if e.id == "spd-0001")
    evaluate_corpus([first], load_meta(SYNTHETIC_META))
    listing = Listing.objects.get(source_listing_key=first.listing.source_listing_key)
    snapshot = OfferSnapshot.objects.get(listing=listing)
    assert snapshot.attrs_json == dict(first.listing.attrs)
    assert _current_edge(listing).evidence["category"] == "drive"
