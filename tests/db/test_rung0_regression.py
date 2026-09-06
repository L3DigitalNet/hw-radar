"""Rung-0 regression suite (MS-1e design E-2b) — the certification the corpus cannot give.

The MS-1e precision corpus is a set of DISTINCT first-observed listings, so every
corpus entry runs with `prior = None` and can only exercise rungs 1-2 (design
E-2b). Rung 0 is reached exclusively by RE-observing a listing that already
carries an accepted denorm state, and its contract is behavioral, not a precision
sample. This module is the third input to the MS-1 ratification gate
(`ms1_ratification_gate`'s `rung0_status` parameter): it drives the
PRODUCTION `CatalogResolver` over the real ingest chain and pins both halves of
that contract —

  (a) an unchanged re-observation inherits the prior accept and writes no new
      resolution edge (append-only is not append-always), and
  (b) a re-observation whose hard attribute now CONTRADICTS the accepted target
      is demoted to REVIEW rather than silently re-inherited — the relist/edit
      abuse path of spec C.3.2.

Re-observation is expressed the way production expresses it: `fx.stamp` ->
`persist.upsert_listing` -> `persist.append_snapshot`, which upserts on
(source_site, source_listing_key) so the second observation IS the same Listing
row and therefore carries the denorm prior that rung 0 reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import fx, persist
from hw_radar.acquisition.contracts import ParsedListing
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    OfferSnapshot,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.ladder import Outcome
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver

# Writes plus the migration-0005 SourceSite seed the ingest chain expects.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

_FIRST_SEEN = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_ACCEPTED_TITLE = "Seagate Exos X16 16TB ST16000NM001G SATA Factory Recertified"


@pytest.fixture
def site() -> SourceSite:
    return SourceSite.objects.get_or_create(
        normalized_name="serverpartdeals", defaults={"name": "ServerPartDeals"}
    )[0]


@pytest.fixture
def exos_16tb() -> ProductModel:
    """Seeded catalog target: 16 TB SATA, reachable at rung 1 by its MPN alias.

    The DriveSpec is what rung 0 later re-vetoes against (the prior's
    `hard_attrs` are rebuilt from this spec on every re-observation), so both
    capacity and interface must be populated for case (b) to have anything to
    contradict.
    """
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name="seagate", defaults={"name": "Seagate"}
    )
    model = ProductModel.objects.create(
        manufacturer=manufacturer,
        model_number="ST16000NM001G",
        normalized_model_number=normalize_alias_text("ST16000NM001G"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model,
        media_type=MediaType.HDD,
        capacity_tb=Decimal("16.000"),
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


@pytest.fixture
def exos_family_split() -> ProductFamily:
    """FAMILY-grain agreement fixture (spec 1216 / C.3.2 `_family_agreement_attrs`):
    two models under one family that AGREE on interface (both SATA) but
    DISAGREE on capacity (16TB vs 18TB). Neither model carries an MPN alias,
    so a listing can only reach this family via the rung-2 grammar decode
    (mirrors `test_rung2_decode_materializes_provisional_family_once` in
    test_resolver.py) — the resulting prior's hard_attrs are the agreement
    set, not either model's own spec, which is exactly what the single-model
    `exos_16tb` fixture above can never exercise: one model can't disagree
    with itself.
    """
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name="seagate", defaults={"name": "Seagate"}
    )
    family = ProductFamily.objects.create(
        manufacturer=manufacturer,
        normalized_name="exos",
        name="Exos",
        category=Category.objects.get(slug="drive"),
    )
    model_a = ProductModel.objects.create(
        manufacturer=manufacturer,
        product_family=family,
        model_number="ST16000NM002C",
        normalized_model_number=normalize_alias_text("ST16000NM002C"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model_a,
        media_type=MediaType.HDD,
        capacity_tb=Decimal("16.000"),
        interface="SATA 6Gb/s",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    model_b = ProductModel.objects.create(
        manufacturer=manufacturer,
        product_family=family,
        model_number="ST18000NM003D",
        normalized_model_number=normalize_alias_text("ST18000NM003D"),
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    DriveSpec.objects.create(
        product_model=model_b,
        media_type=MediaType.HDD,
        capacity_tb=Decimal("18.000"),
        interface="SATA 6Gb/s",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    return family


def _observe(site: SourceSite, *, title: str, observed_at: datetime, price: str) -> Listing:
    """Run one observation of the single listing under test through the real
    ingest chain, then resolve it. Re-calling with a changed title is exactly how
    a merchant relist/edit reaches the resolver in production."""

    parsed = ParsedListing(
        source_listing_key="rung0-1",
        url="https://example.test/rung0-1",
        title=title,
        price=Decimal(price),
        condition_label="Recertified",
        attrs={"sku": "rung0-1"},
    )
    normalized = fx.stamp(parsed, observed_at.date())
    listing, _ = persist.upsert_listing(site, normalized, RetentionClass.MERCHANT_FACT)
    _ = persist.append_snapshot(listing, normalized, observed_at=observed_at)
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    return listing


# django-types ships no stub for the `resolutions` reverse-FK related manager
# (related_name is runtime-only Django metaclass magic), and a bare inline ignore
# on the queryset call still leaves the RETURNED value Unknown, so every
# downstream `.grain`/`.evidence` read would need its own ignore. These typed
# helpers are the function return-type boundary that stops that propagation —
# same precedent as tests/db/test_resolver.py.
def _edge(listing: Listing, **filters: object) -> ListingResolution:
    return listing.resolutions.get(**filters)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]


def _edge_count(listing: Listing, **filters: object) -> int:
    return listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
        **filters
    ).count()


def test_unchanged_reobservation_inherits_accept_without_edge_spam(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    """E-2b half (a): a routine re-poll of an unchanged listing must inherit and
    stay silent in the ledger. Edge spam here would be per-listing-per-poll
    growth in an append-only table, and would also churn every downstream
    consumer of `is_current`."""
    listing = _observe(site, title=_ACCEPTED_TITLE, observed_at=_FIRST_SEEN, price="199.00")
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    accepted_variant_id = listing.product_variant_id  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType] - django-types has no <field>_id shadow-attribute stubs
    first_edge = _edge(listing, is_current=True)
    assert first_edge.evidence["rung"] == 1  # first observation: prior is absent
    stamp_before = first_edge.last_evaluated_at

    listing = _observe(
        site, title=_ACCEPTED_TITLE, observed_at=_FIRST_SEEN + timedelta(days=1), price="189.00"
    )

    # The re-observation really is a second observation of ONE listing row — the
    # premise rung 0 depends on (resolver invariant: persist upserts on
    # (source_site, source_listing_key)).
    assert Listing.objects.count() == 1
    assert OfferSnapshot.objects.filter(listing=listing).count() == 2
    assert _edge_count(listing) == 1  # inherited: no second edge
    current = _edge(listing, is_current=True)
    assert current.pk == first_edge.pk
    assert current.last_evaluated_at > stamp_before  # inherited, but re-evaluated
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert listing.product_variant_id == accepted_variant_id  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs


def test_reobservation_inherits_via_rung0_after_catalog_alias_withdrawn(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    """Proves the inheritance in (a) is rung 0's DENORM prior, not a repeat rung-1
    alias hit — the two are indistinguishable while the alias is present, because
    an unchanged accept writes no edge to read a rung from.

    Withdrawing the catalog alias between observations removes the rung-1 path
    entirely. If rung 0 stopped consulting the prior, this title would fall
    through to the rung-2 grammar decode and re-attach at FAMILY grain (a new
    edge, a demoted grain, and a lost variant), which is what the assertions
    below catch.
    """
    listing = _observe(site, title=_ACCEPTED_TITLE, observed_at=_FIRST_SEEN, price="199.00")
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    accepted_variant_id = listing.product_variant_id  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType] - django-types has no <field>_id shadow-attribute stubs

    _ = ProductAlias.objects.filter(product_model=exos_16tb).delete()

    listing = _observe(
        site, title=_ACCEPTED_TITLE, observed_at=_FIRST_SEEN + timedelta(days=1), price="199.00"
    )

    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert listing.product_variant_id == accepted_variant_id  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs
    assert _edge_count(listing) == 1


@pytest.mark.parametrize(
    ("edited_title", "expected_veto"),
    [
        # Capacity: the poison case ADR-0019 rule 1 exists to stop — a 14 TB body
        # inheriting a 16 TB target would corrupt that target's price history.
        ("Seagate Exos X16 14TB ST16000NM001G SATA Factory Recertified", ["capacity"]),
        # A non-capacity field, so the suite pins the veto's whole agreement set
        # rather than one hardcoded attribute.
        ("Seagate Exos X16 16TB ST16000NM001G NVMe Factory Recertified", ["interface"]),
    ],
    ids=["capacity", "interface"],
)
def test_contradicting_reobservation_is_demoted_to_review_not_inherited(
    site: SourceSite, exos_16tb: ProductModel, edited_title: str, expected_veto: list[str]
) -> None:
    """E-2b half (b): rung 0 re-runs the hard-attribute veto on EVERY
    re-observation, so an edited/relisted body that now contradicts the accepted
    target is demoted instead of inheriting."""
    listing = _observe(site, title=_ACCEPTED_TITLE, observed_at=_FIRST_SEEN, price="199.00")
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    first_edge = _edge(listing, is_current=True)

    listing = _observe(
        site, title=edited_title, observed_at=_FIRST_SEEN + timedelta(days=1), price="199.00"
    )

    current = _edge(listing, is_current=True)
    # rung 0 specifically: with the alias still seeded, a rung-1 veto would
    # produce the same REVIEW outcome, so the rung is what distinguishes
    # "re-observation was re-checked" from "the prior was ignored".
    assert current.evidence["rung"] == 0
    assert current.evidence["outcome"] == Outcome.REVIEW
    assert current.evidence["veto"] == expected_veto
    assert current.grain == ResolutionGrain.NONE
    # The demotion must leave the trusted read path: denorm cleared, exactly one
    # current edge, and the superseded accept still on the audit trail.
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_variant is None
    assert listing.product_model is None
    assert listing.product_family is None
    assert _edge_count(listing) == 2
    assert _edge_count(listing, is_current=True) == 1
    first_edge.refresh_from_db()
    assert first_edge.superseded_by_id == current.pk  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs

    # And it stays demoted: the next poll of the still-contradicting body must
    # not bounce back to the old target (the denorm is cleared, so rung 0 no
    # longer applies and rung 1's own veto holds the line) and must not spam.
    listing = _observe(
        site, title=edited_title, observed_at=_FIRST_SEEN + timedelta(days=2), price="199.00"
    )
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert _edge_count(listing) == 2
    assert _edge(listing, is_current=True).evidence["outcome"] == Outcome.REVIEW


def test_family_agreement_veto_fires_on_agreed_field(
    site: SourceSite, exos_family_split: ProductFamily
) -> None:
    """C.3.2 agreement-set veto, agree half: the split models agree on
    interface (both SATA), so `_family_agreement_attrs()` carries a real
    'sata' value for that field and a re-observation claiming NVMe must veto
    exactly like the single-model contradiction tests above."""
    listing = _observe(
        site,
        title="Seagate Exos ST16000NM002C 16TB SATA Factory Recertified",
        observed_at=_FIRST_SEEN,
        price="199.00",
    )
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    assert listing.product_family == exos_family_split
    first_edge = _edge(listing, is_current=True)

    listing = _observe(
        site,
        title="Seagate Exos ST16000NM002C 16TB NVMe Factory Recertified",
        observed_at=_FIRST_SEEN + timedelta(days=1),
        price="199.00",
    )

    current = _edge(listing, is_current=True)
    assert current.evidence["rung"] == 0
    assert current.evidence["outcome"] == Outcome.REVIEW
    assert current.evidence["veto"] == ["interface"]
    assert current.grain == ResolutionGrain.NONE
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_family is None
    assert _edge_count(listing) == 2
    assert _edge_count(listing, is_current=True) == 1
    first_edge.refresh_from_db()
    assert first_edge.superseded_by_id == current.pk  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <field>_id shadow-attribute stubs


def test_family_agreement_veto_silent_on_disagreed_field(
    site: SourceSite, exos_family_split: ProductFamily
) -> None:
    """C.3.2 agreement-set veto, disagree half: the split models disagree on
    capacity (16TB vs 18TB), so that field is unknown on the catalog side and
    MUST NOT veto (spec 1216: 'disagreeing fields stay unknown, never
    guessed') — a re-observation claiming the other model's capacity is
    treated as non-contradicting and inherits, same as an unchanged re-poll."""
    listing = _observe(
        site,
        title="Seagate Exos ST16000NM002C 16TB SATA Factory Recertified",
        observed_at=_FIRST_SEEN,
        price="199.00",
    )
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    first_edge = _edge(listing, is_current=True)
    stamp_before = first_edge.last_evaluated_at

    listing = _observe(
        site,
        title="Seagate Exos ST16000NM002C 18TB SATA Factory Recertified",
        observed_at=_FIRST_SEEN + timedelta(days=1),
        price="189.00",
    )

    assert _edge_count(listing) == 1  # no veto on the disagreed field: inherited
    current = _edge(listing, is_current=True)
    assert current.pk == first_edge.pk
    assert current.last_evaluated_at > stamp_before
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    assert listing.product_family == exos_family_split
