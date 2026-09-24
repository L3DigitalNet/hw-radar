"""Resolver category dispatch (MS2-D-02/-03): the snapshot's category hint picks the
rules the resolver runs. No hint is the legacy drive default and must decide
exactly as before; a registered-but-unsupported hint writes an
`unsupported_category` none-edge without ever running drive rules.

Fixtures mirror `test_resolver.py` locally so that frozen file stays untouched."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import categories, ladder, resolver
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.matching.types import ExtractedAttributes, Grain

_EXOS_TITLE = "Seagate Exos X16 16TB ST16000NM001G Factory Recertified SATA"
_DECODE_TITLE = "Seagate 20TB ST20000NM007D Recertified Enterprise"
_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Recert", normalized_name="demorecert")


@pytest.fixture
def seagate(db: None) -> Manufacturer:
    return Manufacturer.objects.create(name="Seagate", normalized_name="seagate")


@pytest.fixture
def exos_16tb(seagate: Manufacturer) -> ProductModel:
    model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model,
        media_type=MediaType.HDD,
        capacity_tb="16.000",
        interface="SATA 6Gb/s",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text("ST16000NM-001G "),
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return model


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _snapshot(listing: Listing, *, category_hint: str | None) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("199.99"),
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
            category_hint=category_hint,
        ),
        observed_at=_OBSERVED_AT,
    )


# Typed return boundary for the runtime-only `resolutions` reverse manager; see the
# identical precedent in test_resolver.py.
def _current(listing: Listing) -> ListingResolution:
    return listing.resolutions.get(is_current=True)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]


def _edge_count(listing: Listing) -> int:
    return listing.resolutions.count()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]


def _decision(edge: ListingResolution) -> tuple[object, ...]:
    return (  # pyright: ignore[reportUnknownVariableType] - the <field>_id reads below are Unknown under django-types
        edge.evidence["outcome"],
        edge.grain,
        edge.evidence.get("rung"),
        edge.method,
        edge.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        edge.product_model_id,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        edge.product_variant_id,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    )


def test_no_hint_resolves_as_drive_and_records_provenance(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    listing = _listing(site, "d1", _EXOS_TITLE)
    CatalogResolver().resolve_listing(listing.pk)
    edge = _current(listing)
    # Same decision as the frozen test_rung1_parity_* case.
    assert edge.grain == ResolutionGrain.VARIANT
    assert edge.method == ResolutionMethod.EXACT_ALIAS
    assert edge.evidence["rung"] == 1
    assert edge.evidence["category"] == "drive"
    assert edge.evidence["category_source"] == "legacy_default"


def test_explicit_drive_hint_is_decision_identical(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    unhinted = _listing(site, "d2", _EXOS_TITLE)
    _snapshot(unhinted, category_hint=None)
    hinted = _listing(site, "d3", _EXOS_TITLE)
    _snapshot(hinted, category_hint="drive")
    resolver_ = CatalogResolver()
    resolver_.resolve_listing(unhinted.pk)
    resolver_.resolve_listing(hinted.pk)
    unhinted_edge, hinted_edge = _current(unhinted), _current(hinted)
    assert _decision(hinted_edge) == _decision(unhinted_edge)
    assert unhinted_edge.evidence["category_source"] == "legacy_default"
    assert hinted_edge.evidence["category"] == "drive"
    assert hinted_edge.evidence["category_source"] == "hint"


def test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    listing = _listing(site, "g1", _EXOS_TITLE)
    _snapshot(listing, category_hint="zz-unregistered")
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    edge = _current(listing)
    assert edge.grain == ResolutionGrain.NONE
    assert edge.evidence["unsupported_category"] is True
    assert edge.evidence["category"] == "zz-unregistered"
    assert edge.evidence["category_source"] == "hint"
    assert "error" not in edge.evidence
    # ladder.decide() always writes mpn_hypothesis, so its absence proves the
    # drive ladder never ran on this listing.
    assert "mpn_hypothesis" not in edge.evidence
    assert listing.product_model is None
    assert listing.product_variant is None
    assert listing.resolution_grain == ResolutionGrain.NONE


def test_rung2_provisional_family_uses_dispatch_category(
    site: SourceSite, seagate: Manufacturer
) -> None:
    listing = _listing(site, "r2", _DECODE_TITLE)
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    assert listing.product_family is not None
    assert listing.product_family.category.slug == "drive"
    assert _current(listing).evidence["category"] == "drive"


def test_materialize_refuses_family_key_without_category() -> None:
    # A provisional family with no dispatch category must fail loudly (and so
    # land on the resolver's error-edge fallback), never default to drive.
    verdict = ladder.Verdict(
        ladder.Outcome.ACCEPT,
        Grain.FAMILY,
        rung=2,
        method="mpn_decode",
        target=ladder.TargetRef(grain=Grain.FAMILY, family_key=("seagate", "exos")),
    )
    with pytest.raises(ValueError, match="category"):
        resolver._materialize(ExtractedAttributes(), verdict)  # pyright: ignore[reportPrivateUsage]


def test_spec_readers_match_registry() -> None:
    assert set(resolver._SPEC_READERS) == categories.registered_categories()  # pyright: ignore[reportPrivateUsage]


def test_unsupported_repoll_does_not_spam_edges(site: SourceSite) -> None:
    listing = _listing(site, "g2", _EXOS_TITLE)
    _snapshot(listing, category_hint="gpu")
    resolver_ = CatalogResolver()
    resolver_.resolve_listing(listing.pk)
    resolver_.resolve_listing(listing.pk)
    assert _edge_count(listing) == 1
