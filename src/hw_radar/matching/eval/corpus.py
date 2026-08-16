"""MS-1e corpus schema + loaders (design §3): the on-disk contract for the
hand-labeled validation corpus that ratifies ADR-0019.

Pure validation and file I/O. The eval package deliberately holds NO matching
logic (design §2): a second implementation of any normalization or matching rule
here would be exactly the drift the Approach-A harness exists to prevent.

Three conventions the schema enforces rather than trusts:

- **Closed models (E-4).** Every level rejects unknown fields. The corpus is
  committed to a PUBLIC repository, so an accidentally harvested `raw_payload`,
  `seller`, or similar key must fail loading rather than ride along invisibly —
  and an unknown key is equally likely to be a silently ignored label field.

- **Source keys (SA-002).** `source` must be one of the five real adapter-registry
  keys; a parallel short-name namespace ("spd", "wd") would silently split the
  per-source floor in `report.py` and let a missing source look covered.
- **One normalizer, both sides (SA-003).** Labels hold human display values
  ("Exos X18", "ST18000NM000J"); the normalized comparison keys are derived here
  through the PRODUCTION `canonicalize_title` / `normalize_alias_text`, so the
  corpus can never encode a hand-authored second normalization convention.
  `manufacturer_key` is the exception — it is already the stored
  `Manufacturer.normalized_name` controlled key and compares exactly, since
  canonicalizing it would turn "western_digital" into "western digital" and never
  match.

`CorpusEntry.label` is required: this module models the LABELED corpus. The
harvest command's staging file (design §4) is a deliberately unlabeled shape and
does not round-trip through these models.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hw_radar.catalog.models import Condition, Packaging, RecertChannel, WarrantyChannel
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.types import Grain

# The five harvestable sources. Counterpart: `acquisition.sources.ADAPTERS`, which
# additionally registers the `demo` fixture adapter — demo listings are synthetic
# and must never enter a precision corpus. tests/unit/test_corpus_schema.py pins
# this set against the registry so a new connector fails loudly here rather than
# being silently unharvestable.
CORPUS_SOURCE_KEYS: frozenset[str] = frozenset(
    {"serverpartdeals", "goharddrive", "wd-recertified", "seagate-recertified", "ebay"}
)

SourceKey = Literal[
    "serverpartdeals", "goharddrive", "wd-recertified", "seagate-recertified", "ebay"
]

# Corpus v1 is US-priced. Admitting a non-USD entry would make the persisted
# snapshot price depend on a cached FX rate — i.e. on when the harness ran — and a
# ratification verdict that moves with the FX cache is not a gate. Widening this
# is a schema change with an FX-pinning design, never a quiet relaxation.
CORPUS_CURRENCY = "USD"

# Design E-5 / owner audit: the fraction of the corpus that must be owner-audited,
# selected reproducibly (see select_audit_sample).
AUDIT_SAMPLE_FRACTION = 0.20


class AuditStatus(StrEnum):
    """Design E-5 label provenance: every entry starts as a Claude draft and only
    owner review moves it, so the audit rollup in the manifest is auditable."""

    CLAUDE_DRAFT = "claude_draft"
    OWNER_CONFIRMED = "owner_confirmed"
    OWNER_CORRECTED = "owner_corrected"


class CorpusFormatError(ValueError):
    """A corpus or manifest file could not be read as the design §3 schema."""


class VariantKey(BaseModel):
    """The `ProductVariant` sellable identity (E-3), as exact enum choice values.

    A "variant string" cannot represent this identity: it is the 4-tuple below
    hanging off a `ProductModel`, and two listings of one model differing only by
    `condition` are two distinct sellable products.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    condition: Condition
    packaging: Packaging = Packaging.UNKNOWN
    recert_channel: RecertChannel = RecertChannel.UNKNOWN
    warranty_channel: WarrantyChannel = WarrantyChannel.UNKNOWN

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            str(self.condition),
            str(self.packaging),
            str(self.recert_channel),
            str(self.warranty_channel),
        )


class TargetKey(BaseModel):
    """Grain-shaped natural key (E-3) — never a catalog PK, so the committed corpus
    outlives any DB seed or re-import."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manufacturer_key: str | None = None
    family: str | None = None
    model_number: str | None = None
    variant: VariantKey | None = None

    @property
    def family_norm(self) -> str | None:
        """Label family as `ProductFamily.normalized_name` sees it."""
        return canonicalize_title(self.family) if self.family else None

    @property
    def model_norm(self) -> str | None:
        """Label model number as `ProductModel.normalized_model_number` sees it."""
        return normalize_alias_text(self.model_number) if self.model_number else None


# Grain → (required, forbidden) target fields. `family` is optional at model and
# variant grain on purpose: a seeded ProductModel may legitimately carry no family
# FK, and the label author decides whether to assert one (an asserted family IS
# compared, strictly — see evaluate.prediction_matches).
_REQUIRED_TARGET_FIELDS: dict[Grain, frozenset[str]] = {
    Grain.NONE: frozenset(),
    Grain.FAMILY: frozenset({"manufacturer_key", "family"}),
    Grain.MODEL: frozenset({"manufacturer_key", "model_number"}),
    Grain.VARIANT: frozenset({"manufacturer_key", "model_number", "variant"}),
}
_FORBIDDEN_TARGET_FIELDS: dict[Grain, frozenset[str]] = {
    # A `none` label means "must not auto-accept" (unresolvable, or a hard-attribute
    # contradiction). Any populated target field would make that unfalsifiable.
    Grain.NONE: frozenset({"manufacturer_key", "family", "model_number", "variant"}),
    Grain.FAMILY: frozenset({"model_number", "variant"}),
    Grain.MODEL: frozenset({"variant"}),
    Grain.VARIANT: frozenset(),
}


class GroundTruthLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_grain: Grain
    expected_target: TargetKey = Field(default_factory=TargetKey)
    # ADR-0019 rule 7 spot-check flag: the listing prints both an OEM token and an
    # MPN. Measured over ServerPartDeals + eBay only (design §5).
    oem_dual_label: bool = False
    audit_status: AuditStatus = AuditStatus.CLAUDE_DRAFT
    notes: str = ""

    @model_validator(mode="after")
    def _grain_and_target_agree(self) -> GroundTruthLabel:
        grain = self.expected_grain
        target = self.expected_target
        missing = sorted(
            name for name in _REQUIRED_TARGET_FIELDS[grain] if getattr(target, name) is None
        )
        if missing:
            raise ValueError(f"{grain} grain requires expected_target {', '.join(missing)}")
        present = sorted(
            name for name in _FORBIDDEN_TARGET_FIELDS[grain] if getattr(target, name) is not None
        )
        if present:
            raise ValueError(f"{grain} grain must not carry expected_target {', '.join(present)}")
        return self


class ListingFields(BaseModel):
    """Exactly the `ParsedListing` fields the production pipeline persists (E-4).

    Deliberately no invented keys: `evaluate.py` rebuilds a real `ParsedListing`
    from these, and `attrs` is written verbatim into `OfferSnapshot.attrs_json` so
    the resolver's structured-MPN hook sees production-identical input (SA-004).
    Note the split from the label side: `condition_label` is RAW marketplace text
    (persisted as `condition_label_raw`), while a variant label's `condition` is
    the normalized enum (SA-NEW-001).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_listing_key: str = Field(min_length=1)
    url: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    currency: Literal["USD"] = CORPUS_CURRENCY
    condition_label: str = ""
    attrs: dict[str, object] = Field(default_factory=dict)


class CorpusEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    source: SourceKey
    title: str = Field(min_length=1)
    listing: ListingFields
    label: GroundTruthLabel


class CorpusMeta(BaseModel):
    """`*.meta.json` sidecar (design §3): provenance for a corpus revision.

    `matcher_version` is the version the labels were audited against — a later
    matcher bump invalidates nothing automatically, but a re-run under a different
    version is a different experiment (master-spec C.3.5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_version: str = Field(min_length=1)
    harvested_from: date
    harvested_to: date
    # The single evaluation instant, strictly UTC: every reconstructed snapshot is
    # stamped with it, so a re-run months later produces byte-identical predictions
    # instead of silently drifting with the wall clock (and, through fx.stamp's
    # rate lookup, with the FX cache). It is corpus provenance, not a runtime
    # option. A non-UTC offset is rejected rather than converted, because
    # fx.stamp's observed_DATE is derived from it: a +02:00 stamp near midnight
    # would pick a different rate date than the same instant written in UTC.
    observed_at: datetime
    source_counts: dict[SourceKey, int]
    matcher_version: str = Field(min_length=1)
    audit_rollup: dict[AuditStatus, int]

    @model_validator(mode="after")
    def _range_is_ordered(self) -> CorpusMeta:
        if self.harvested_to < self.harvested_from:
            raise ValueError("harvested_to precedes harvested_from")
        if self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at must be a timezone-aware UTC instant")
        return self


def select_audit_sample(entry_ids: Iterable[str], corpus_version: str) -> tuple[str, ...]:
    """Return the ids of the audit sample: ceil(20%) of the corpus, reproducibly.

    Reproducibility is the whole point — the owner audits a sample and the gate
    must later verify THAT sample, so the selection is a pure function of the
    entry ids and `corpus_version`: sort by id (file order must not matter), then
    shuffle with a `corpus_version`-seeded Mersenne Twister. Re-labeling entries
    under an unchanged `corpus_version` therefore keeps the same sample; issuing a
    new `corpus_version` deliberately draws a new one.

    Not a security primitive: `random`, not `secrets`, precisely because the draw
    must be replayable by anyone holding the corpus.
    """
    ordered = sorted(set(entry_ids))
    random.Random(corpus_version).shuffle(ordered)
    return tuple(ordered[: math.ceil(AUDIT_SAMPLE_FRACTION * len(ordered))])


def _numbered_lines(path: Path) -> Iterator[tuple[int, str]]:
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if line:
                yield number, line


def load_corpus(jsonl_path: Path) -> list[CorpusEntry]:
    """Load and validate a corpus JSONL file, preserving file order.

    Raises CorpusFormatError — carrying the offending line number — for malformed
    JSON, schema violations, a duplicate entry id, or a duplicate
    (source, source_listing_key) pair. Never degrades: a corpus that partially
    loads would silently shrink the precision denominator, which is the one failure
    mode SA-005 forbids.

    Both uniqueness checks run BEFORE any evaluation, because both break the
    distinct-first-observation premise the rung-1/2 denominator rests on (SA-004):
    a repeated source-local key makes `upsert_listing` reuse the same Listing, so
    the second entry is scored as a rung-0 re-observation — and, under the corpus's
    single fixed `observed_at`, its snapshot collides on the observation key.
    A duplicate id breaks the prediction↔label join in `report.py`.
    """
    entries: list[CorpusEntry] = []
    seen: set[str] = set()
    seen_source_keys: set[tuple[str, str]] = set()
    for number, line in _numbered_lines(jsonl_path):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusFormatError(f"{jsonl_path}: line {number} is not valid JSON") from exc
        try:
            entry = CorpusEntry.model_validate(payload)
        except ValidationError as exc:
            raise CorpusFormatError(
                f"{jsonl_path}: line {number} is not a valid entry: {exc}"
            ) from exc
        if entry.id in seen:
            raise CorpusFormatError(f"{jsonl_path}: line {number} has duplicate id {entry.id!r}")
        source_key = (entry.source, entry.listing.source_listing_key)
        if source_key in seen_source_keys:
            raise CorpusFormatError(
                f"{jsonl_path}: line {number} has duplicate (source, source_listing_key) "
                f"{source_key!r}"
            )
        seen.add(entry.id)
        seen_source_keys.add(source_key)
        entries.append(entry)
    return entries


def load_meta(path: Path) -> CorpusMeta:
    """Load and validate a corpus manifest. Raises CorpusFormatError on any defect."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusFormatError(f"{path} is not valid JSON") from exc
    try:
        return CorpusMeta.model_validate(payload)
    except ValidationError as exc:
        raise CorpusFormatError(f"{path} is not a valid corpus manifest: {exc}") from exc
