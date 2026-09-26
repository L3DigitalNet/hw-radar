"""Rung 0 does not inherit automated accepts decided under an older matcher_version
(review F2, spec C.3.5: a version bump is a diffable re-resolution experiment).

The pinned false merge: 2026.09.1 decoded Seagate `ST…NM…` tokens as Exos and
attached them at rung 2 to a provisional Exos family; 2026.09.2 decodes them
without a family. Without the version check, rung 0 would re-accept the old
attachment on every re-observation and the rule fix could never undo it.
Manual (owner) accepts and same-version accepts keep inheriting."""

from decimal import Decimal

import pytest

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import MATCHER_VERSION
from hw_radar.matching.resolver import CatalogResolver

# The last released version whose rung-2 rules asserted Exos for ST…NM… tokens.
_OLD_VERSION = "2026.09.1"


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Recert", normalized_name="demorecert")


@pytest.fixture
def seagate(db: None) -> Manufacturer:
    return Manufacturer.objects.get_or_create(
        normalized_name="seagate", defaults={"name": "Seagate"}
    )[0]


@pytest.fixture
def provisional_exos(seagate: Manufacturer) -> ProductFamily:
    # Shaped as _materialize writes a rung-2 provisional family: no models, no specs.
    return ProductFamily.objects.create(
        manufacturer=seagate,
        category=Category.objects.get(slug="drive"),
        name="Exos",
        normalized_name="exos",
    )


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _family_accept(
    listing: Listing, family: ProductFamily, *, method: str, version: str
) -> ListingResolution:
    """An accepted family-grain edge plus the denorm it would have left behind."""
    edge = ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=family,
        method=method,
        confidence=0.92,
        matcher_version=version,
        # Every fixture title carries this one MPN. Recorded so the same-version
        # case pins version handling alone; an automated edge without it is
        # re-decided for its identifiers (tests/db/test_resolver_prior_identifiers.py).
        evidence={
            "outcome": "accept",
            "rung": 2,
            "category": "drive",
            "identity_identifiers": ["st1000nm0001"],
        },
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.FAMILY,
        product_family=family,
        resolution_confidence=0.92,
    )
    return edge


def _resolve(listing: Listing) -> ListingResolution:
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return ListingResolution.objects.get(listing=listing, is_current=True)


def _edge_count(listing: Listing) -> int:
    return ListingResolution.objects.filter(listing=listing).count()


def test_old_version_rung2_exos_attachment_is_redecided(
    site: SourceSite, provisional_exos: ProductFamily
) -> None:
    listing = _listing(site, "nm-1", "Seagate ST1000NM0001 1TB SAS 3.5in")
    old = _family_accept(
        listing, provisional_exos, method=ResolutionMethod.MPN_DECODE, version=_OLD_VERSION
    )
    edge = _resolve(listing)
    assert edge.pk != old.pk
    assert edge.matcher_version == MATCHER_VERSION
    assert edge.evidence["reconsidered_from_matcher_version"] == _OLD_VERSION
    # Current rules decode ST…NM… without a family and there is no alias: no rung
    # may accept, so the false merge is cleared from the trusted read path.
    assert edge.evidence["outcome"] != "accept"
    assert edge.grain == ResolutionGrain.NONE
    assert edge.evidence.get("rung") != 0
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_family is None
    old.refresh_from_db()
    assert old.is_current is False
    assert old.superseded_by_id == edge.pk  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs


def test_old_version_origin_is_found_behind_a_rung0_edge(
    site: SourceSite, provisional_exos: ProductFamily
) -> None:
    # A rung-0 edge only re-stamps an inherited target (here: superseding an
    # error edge), so its own version must not launder the older decision.
    listing = _listing(site, "nm-2", "Seagate ST1000NM0001 1TB SAS 3.5in")
    _family_accept(
        listing, provisional_exos, method=ResolutionMethod.MPN_DECODE, version=_OLD_VERSION
    )
    ListingResolution.objects.filter(listing=listing).update(is_current=False)
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.FAMILY,
        product_family=provisional_exos,
        method=ResolutionMethod.SOURCE_ALIAS,
        confidence=0.92,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "rung": 0, "category": "drive"},
    )
    edge = _resolve(listing)
    assert edge.evidence["reconsidered_from_matcher_version"] == _OLD_VERSION
    assert listing.product_family is None


def test_same_version_automated_prior_is_inherited_at_rung0(
    site: SourceSite, provisional_exos: ProductFamily
) -> None:
    listing = _listing(site, "nm-3", "Seagate ST1000NM0001 1TB SAS 3.5in")
    edge = _family_accept(
        listing, provisional_exos, method=ResolutionMethod.MPN_DECODE, version=MATCHER_VERSION
    )
    assert _resolve(listing).pk == edge.pk
    assert _edge_count(listing) == 1  # unchanged rung-0 accept: no edge spam
    assert listing.product_family == provisional_exos


def test_old_version_manual_prior_is_never_overturned(
    site: SourceSite, provisional_exos: ProductFamily
) -> None:
    listing = _listing(site, "nm-4", "Seagate ST1000NM0001 1TB SAS 3.5in")
    edge = _family_accept(
        listing, provisional_exos, method=ResolutionMethod.MANUAL, version=_OLD_VERSION
    )
    assert _resolve(listing).pk == edge.pk
    assert _edge_count(listing) == 1
    assert listing.product_family == provisional_exos


def test_redecision_to_same_target_records_edge_then_inherits(
    site: SourceSite, seagate: Manufacturer
) -> None:
    model = ProductModel.objects.create(
        manufacturer=seagate,
        model_number="ST16000NM001G",
        normalized_model_number="st16000nm001g",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model,
        media_type=MediaType.HDD,
        capacity_tb=Decimal(16),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text="st16000nm001g",
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(site, "nm-5", "Seagate ST16000NM001G 16TB")
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.EXACT_ALIAS,
        confidence=0.98,
        matcher_version=_OLD_VERSION,
        evidence={"outcome": "accept", "rung": 1, "category": "drive"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=0.98
    )
    edge = _resolve(listing)
    # The current rules confirm the old decision; the confirmation is still
    # recorded under the current version so later polls can inherit it.
    assert edge.evidence["rung"] == 1
    assert edge.evidence["reconsidered_from_matcher_version"] == _OLD_VERSION
    assert edge.matcher_version == MATCHER_VERSION
    assert listing.product_model == model
    assert _edge_count(listing) == 2
    again = _resolve(listing)
    assert again.pk == edge.pk
    assert _edge_count(listing) == 2
