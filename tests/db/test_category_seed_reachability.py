"""The shipped first-party GPU/RAM/CPU seed aliases reach the B3 category rules
through the real resolver (owner §29: exact-authoritative-alias acceptance).

The B3 candidate patterns were written before the seeds landed, so a seeded
alias the rules can never produce as a candidate would make its model silently
unmatchable. Every case here imports ALL shipped seeds (drive included, so the
alias table is the production one) and resolves a merchant-style title through
`CatalogResolver`, the entry point `test_resolver_categories.py` uses.

Auto-accept is OFF for gpu/ram/cpu at merge; the accept assertions enable it
through the same test-only registration that file uses (`auto_accept_on`).
Titles carry no condition word, so an accept stays at model grain."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    Listing,
    ListingResolution,
    ProductAlias,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import categories
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

# (category, seeded model_number, merchant-style title). At least one row per
# seeded non-drive model (test_every_seeded_model_has_a_reach_case enforces it);
# extra rows cover the ordering codes a title may carry instead of the name.
_REACH: tuple[tuple[str, str, str], ...] = (
    ("cpu", "Xeon Gold 6448Y", "Intel Xeon Gold 6448Y 32-Core 2.1GHz LGA4677 CPU Processor"),
    ("cpu", "Xeon Platinum 8480+", "Intel Xeon Platinum 8480+ 56-Core 2.0GHz LGA4677 Server CPU"),
    ("cpu", "Xeon Platinum 8480+", "Intel PK8071305074801 56-Core LGA4677 Tray Processor"),
    ("cpu", "Xeon Gold 6548Y+", "Intel Xeon Gold 6548Y+ 32-Core 2.5GHz FCLGA4677 CPU"),
    ("cpu", "Xeon Gold 6338", "Intel Xeon Gold 6338 32-Core 2.0GHz LGA4189 CPU Processor"),
    ("cpu", "Xeon Platinum 8358", "Intel Xeon Platinum 8358 32-Core 2.6GHz LGA 4189 CPU"),
    ("cpu", "EPYC 9354", "AMD EPYC 9354 32-Core 3.25GHz SP5 Server Processor"),
    ("cpu", "EPYC 9354", "AMD 100-000000798 32-Core SP5 Tray CPU"),
    ("cpu", "EPYC 9654", "AMD EPYC 9654 96-Core 2.4GHz SP5 100-000000789 CPU"),
    ("cpu", "EPYC 7763", "AMD EPYC 7763 64-Core 2.45GHz SP3 Server CPU"),
    ("cpu", "EPYC 7763", "AMD 100-000000312 64-Core SP3 Tray Processor"),
    ("cpu", "EPYC 7763", "AMD 100-100000312WOF 64-Core SP3 Boxed Processor"),
    ("cpu", "EPYC 7742", "AMD EPYC 7742 64-Core 2.25GHz SP3 Server CPU"),
    ("gpu", "Tesla V100 PCIe (32GB)", "NVIDIA Tesla V100 PCIe 32GB GPU Accelerator"),
    ("gpu", "Tesla V100 PCIe (32GB)", "NVIDIA Tesla V100 32GB PCIe GPU Accelerator"),
    ("gpu", "A100 80GB PCIe", "NVIDIA A100 80GB PCIe Tensor Core GPU Passive"),
    ("gpu", "H100 PCIe", "NVIDIA H100 PCIe 80GB HBM2e GPU Accelerator"),
    ("gpu", "RTX A6000", "NVIDIA RTX A6000 48GB GDDR6 Workstation Graphics Card"),
    ("gpu", "GeForce RTX 3090", "NVIDIA GeForce RTX 3090 24GB GDDR6X Founders Edition"),
    ("gpu", "GeForce RTX 4090", "NVIDIA GeForce RTX 4090 24GB GDDR6X Founders Edition"),
    ("gpu", "Instinct MI100", "AMD Instinct MI100 32GB HBM2 PCIe Accelerator"),
    ("gpu", "Instinct MI210", "AMD Instinct MI210 64GB HBM2e PCIe Accelerator"),
    ("ram", "MTA36ASF8G72PZ-3G2F2", "Micron 64GB DDR4-3200 ECC RDIMM MTA36ASF8G72PZ-3G2F2"),
    ("ram", "MTC20F2085S1RC52BA1", "Micron 32GB DDR5 ECC RDIMM 2Rx8 MTC20F2085S1RC52BA1"),
)

# Seeded aliases no _REACH title produces as a candidate, with why that is
# acceptable: both are datasheet descriptors carrying an NVIDIA board-SKU label
# that listings do not print, and each model is reached by its primary alias.
# The rules do not synthesize text a title does not contain, so these stay
# unreachable by design. A new unreachable seed alias fails
# test_unreached_seed_aliases_are_exactly_the_known_descriptors until it is
# either reachable or justified here.
_KNOWN_UNREACHED = frozenset(
    normalize_alias_text(text)
    for text in ("NVIDIA A100 80GB (SKU 230)", "NVIDIA H100 PCIe (SKU GH100-200)")
)


def _non_drive_seed_models() -> dict[str, set[str]]:
    by_category: dict[str, set[str]] = {}
    for doc in load_seed_documents():
        if doc.category != "drive":
            by_category.setdefault(doc.category, set()).update(m.model_number for m in doc.models)
    return by_category


def _seeded_model(model_number: str) -> ProductModel:
    return ProductModel.objects.get(normalized_model_number=normalize_alias_text(model_number))


@pytest.fixture
def seeded(db: None) -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _enable_auto_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test-only registration `test_resolver_categories.auto_accept_on`
    uses: gpu/ram/cpu with auto_accept=True, everything else as registered."""
    for slug in ("gpu", "ram", "cpu"):
        rules = categories.rules_for(slug)
        assert rules is not None and rules.acceptance is not None
        monkeypatch.setitem(
            categories._REGISTRY,  # pyright: ignore[reportPrivateUsage] - the test-only registration the plan prescribes
            slug,
            lambda rules=rules: replace(rules, auto_accept=True),
        )


@pytest.fixture
def auto_accept_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _enable_auto_accept(monkeypatch)
    yield


def _hinted(site: SourceSite, key: str, title: str, category: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=key,
            url=listing.canonical_url,
            title=title,
            price=Decimal("999.00"),
            attrs={},
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
            category_hint=category,
        ),
        observed_at=_OBSERVED_AT,
    )
    return listing


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _candidate_alias_models(category: str, title: str) -> set[int | None]:
    """Models whose seeded aliases the category's candidates hit — the rung-1
    lookup, without the ladder, so a review edge (which records no target) can
    still be pinned to the right model."""
    rules = categories.rules_for(category)
    assert rules is not None
    keys = [c.normalized for c in rules.extract_candidates(canonicalize_title(title))]
    rows = ProductAlias.objects.filter(normalized_alias_text__in=keys)
    assert all(r.source_kind == AliasSourceKind.CATALOG_AUTHORITATIVE for r in rows)
    return {r.product_model_id for r in rows}  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs


# --- coverage of the seed corpus -------------------------------------------------


def test_every_seeded_model_has_a_reach_case() -> None:
    covered: dict[str, set[str]] = {}
    for category, model_number, _ in _REACH:
        covered.setdefault(category, set()).add(model_number)
    assert covered == _non_drive_seed_models()


def test_unreached_seed_aliases_are_exactly_the_known_descriptors() -> None:
    reached: set[str] = set()
    for category, _, title in _REACH:
        rules = categories.rules_for(category)
        assert rules is not None
        reached |= {c.normalized for c in rules.extract_candidates(canonicalize_title(title))}
    seeded = {
        alias.normalized
        for doc in load_seed_documents()
        if doc.category != "drive"
        for model in doc.models
        for alias in model.aliases
    }
    assert seeded - reached == _KNOWN_UNREACHED


# --- exact authoritative alias: review while off, accept when on -----------------


@pytest.mark.usefixtures("seeded")
@pytest.mark.parametrize(
    ("category", "model_number", "title"), _REACH, ids=[f"{c}:{t}" for c, _, t in _REACH]
)
def test_seed_alias_reaches_review_then_accepts_with_auto_accept(
    site: SourceSite,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    model_number: str,
    title: str,
) -> None:
    expected = _seeded_model(model_number)
    assert _candidate_alias_models(category, title) == {expected.pk}

    listing = _hinted(site, "reach", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 1
    assert edge.evidence["auto_accept_disabled"] is True
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    assert edge.evidence["category"] == category
    assert listing.product_model is None

    # Flipping only the flag turns that same review into an accept of the
    # expected model: auto_accept is the last gate, so nothing else moved.
    _enable_auto_accept(monkeypatch)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "accept"
    assert edge.method == ResolutionMethod.EXACT_ALIAS
    assert edge.evidence["alias_source_kind"] == "catalog_authoritative"
    assert edge.grain == ResolutionGrain.MODEL
    assert listing.product_model == expected


@pytest.mark.usefixtures("seeded")
def test_partner_liquid_cooled_variant_not_vetoed_on_chip_cooling(site: SourceSite) -> None:
    """SeedGpuSpec.cooling is chip-level, not board-level (Founders Edition is
    one NVIDIA reference design among many partner boards). A partner
    liquid-cooled RTX 4090 title must still reach the shared 'GeForce RTX
    4090' alias and land on review, not be vetoed for a cooling mismatch
    against a board attribute the chip seed no longer asserts."""
    title = "MSI GeForce RTX 4090 SUPRIM LIQUID X 24GB GDDR6X"
    expected = _seeded_model("GeForce RTX 4090")
    assert _candidate_alias_models("gpu", title) == {expected.pk}

    listing = _hinted(site, "reach", title, "gpu")
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["auto_accept_disabled"] is True
    assert edge.evidence.get("veto") is None
    assert listing.product_model is None


# --- no exact token, contradiction, missing identity evidence --------------------


@pytest.mark.usefixtures("seeded", "auto_accept_on")
@pytest.mark.parametrize(
    ("category", "title"),
    [
        ("gpu", "NVIDIA 80GB Ampere data center accelerator PCIe card"),
        # Names the chip but not the seeded 'A100 80GB PCIe' identity.
        ("gpu", "NVIDIA A100 Tensor Core GPU"),
        ("gpu", "NVIDIA A100 40GB PCIe"),
        ("cpu", "Intel 4th Gen Xeon Scalable 32 core server processor LGA4677"),
        # Near miss: the 6448Y without its suffix is a different part number.
        ("cpu", "Intel Xeon Gold 6448 32-Core LGA4677"),
        ("cpu", "AMD EPYC Genoa 96 core SP5 server CPU"),
        ("ram", "Micron 64GB DDR4 3200MHz ECC registered server memory"),
    ],
)
def test_merchant_prose_without_exact_alias_never_accepts(
    site: SourceSite, category: str, title: str
) -> None:
    listing = _hinted(site, "prose", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] in {"review", "none"}
    assert listing.product_model is None
    assert listing.product_variant is None


@pytest.mark.usefixtures("seeded", "auto_accept_on")
@pytest.mark.parametrize(
    ("category", "title", "vetoed"),
    [
        # The seeded A100 alias names 80GB itself, so its VRAM cannot contradict
        # within the alias token; the 4090 (seeded 24GB) carries the VRAM case
        # and the A100 carries a cooling contradiction (seeded passive).
        ("gpu", "NVIDIA GeForce RTX 4090 48GB", ["vram_gb"]),
        ("gpu", "NVIDIA A100 80GB PCIe Active Cooled Blower", ["cooling"]),
        ("cpu", "Intel Xeon Gold 6448Y 32-Core LGA4189", ["socket"]),
        ("cpu", "AMD EPYC 9654 64-Core SP5", ["cores"]),
        ("ram", "Micron 64GB DDR5 ECC RDIMM MTA36ASF8G72PZ-3G2F2", ["generation"]),
        ("ram", "Micron 32GB DDR4 ECC RDIMM MTA36ASF8G72PZ-3G2F2", ["module_capacity_gb"]),
    ],
)
def test_hard_contradiction_vetoes_seed_alias_hit(
    site: SourceSite, category: str, title: str, vetoed: list[str]
) -> None:
    listing = _hinted(site, "veto", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == vetoed
    assert listing.product_model is None


@pytest.mark.usefixtures("seeded", "auto_accept_on")
@pytest.mark.parametrize(
    ("category", "title"),
    [
        # The seeded alias text is present, but the chip vendor / product line
        # is not, so the vendor-gated name candidates are never emitted.
        ("gpu", "A100 80GB PCIe Tensor Core accelerator"),
        ("cpu", "Gold 6448Y 32-Core 2.1GHz LGA4677 processor"),
        # Attributes that fit the seeded module, but no part number at all.
        ("ram", "Micron 64GB DDR4-3200 ECC RDIMM server memory"),
    ],
)
def test_missing_identity_evidence_never_accepts_seed_model(
    site: SourceSite, category: str, title: str
) -> None:
    listing = _hinted(site, "missing", title, category)
    edge = _resolve(listing)
    assert edge.evidence["outcome"] == "none"
    assert listing.product_model is None
