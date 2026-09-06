"""CatalogResolver end-to-end: rung flows, veto, no-spam re-observation, variant
on demand, provisional families, lazy aliases, error path, and the ADR-0019
rule-1 normalizer PARITY test (the CI guard for the single-normalizer invariant)."""

from decimal import Decimal

import pytest
from django.db.models.deletion import ProtectedError

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Condition,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching import vocab
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.resolver import CatalogResolver


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
    # Catalog alias seeded THROUGH the shared normalizer, from a deliberately
    # messy catalog-side rendering — this is the parity contract in action.
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


def _seed_alias_for(model_number: str) -> ProductModel:
    """Catalog-authoritative alias + model + spec, as the Task-4 importer writes them."""
    seagate, _ = Manufacturer.objects.get_or_create(
        normalized_name="seagate", defaults={"name": "Seagate"}
    )
    model, _ = ProductModel.objects.get_or_create(
        manufacturer=seagate,
        normalized_model_number=normalize_alias_text(model_number),
        defaults={
            "model_number": model_number,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    DriveSpec.objects.update_or_create(
        product_model=model,
        defaults={
            "media_type": MediaType.HDD,
            "capacity_tb": Decimal("16"),
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    ProductAlias.objects.get_or_create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text(model_number),
        source_site=None,
        defaults={
            "product_model": model,
            "source_kind": AliasSourceKind.CATALOG_AUTHORITATIVE,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    return model


# django-types has no stub for the `resolutions` reverse-FK related manager
# (Listing carries no such attribute in its stubs — related_name is runtime-only
# Django metaclass magic; see the identical precedent in test_resolution_models.py).
# A bare inline ignore on the `.get(...)`/`.count()` call still leaves the
# RETURNED value's own type Unknown, so every downstream `.grain`/`.evidence`
# read would need its own ignore too — these two typed helpers are the function
# return-type boundary that actually stops that propagation (verified empirically:
# a declared variable annotation alone does not).
def _edge(listing: Listing, **filters: object) -> ListingResolution:
    return listing.resolutions.get(**filters)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]


def _edge_count(listing: Listing, **filters: object) -> int:
    return listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
        **filters
    ).count()


def test_rung1_parity_exact_alias_resolves_to_variant_on_demand(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    listing = _listing(site, "l1", "Seagate Exos X16 16TB ST16000NM001G Factory Recertified SATA")
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    # Model grain + known condition → variant created on demand (C.3.3).
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert listing.product_variant is not None
    assert listing.product_variant.product_model == exos_16tb
    assert listing.product_variant.condition == Condition.RECERTIFIED
    assert listing.product_family is None and listing.product_model is None  # lower grains NULL
    edge = _edge(listing, superseded_by__isnull=True)
    assert edge.method == ResolutionMethod.EXACT_ALIAS
    assert edge.grain == ResolutionGrain.VARIANT
    assert listing.title_normalized  # N1 output persisted


def test_recert_and_new_listings_make_one_model_two_variants(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    # FR-003 MS-1 acceptance, resolver-driven (also upgraded in test_identity.py).
    resolver = CatalogResolver()
    a = _listing(site, "a", "Seagate Exos 16TB ST16000NM001G Factory Recertified")
    b = _listing(site, "b", "Seagate Exos 16TB ST16000NM001G Brand New Sealed")
    resolver.resolve_listing(a.pk)
    resolver.resolve_listing(b.pk)
    assert ProductModel.objects.count() == 1
    assert ProductVariant.objects.filter(product_model=exos_16tb).count() == 2


def test_rung0_reobservation_appends_no_duplicate_edge(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    listing = _listing(site, "l2", "Seagate Exos 16TB ST16000NM001G Recertified")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)
    resolver.resolve_listing(listing.pk)  # routine re-poll: veto re-runs, NO new edge
    assert _edge_count(listing) == 1


def test_capacity_contradiction_goes_to_review_not_price_history(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    listing = _listing(site, "l3", "Seagate 14TB ST16000NM001G Recertified")
    CatalogResolver().resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_variant is None
    edge = _edge(listing)
    assert edge.grain == ResolutionGrain.NONE
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["veto"] == ["capacity"]


def test_rung2_decode_materializes_provisional_family_once(
    site: SourceSite, seagate: Manufacturer
) -> None:
    resolver = CatalogResolver()
    a = _listing(site, "l4", "Seagate 20TB ST20000NM007D Recertified Enterprise")
    b = _listing(site, "l5", "Seagate 20TB ST20000NM007D Renewed")
    resolver.resolve_listing(a.pk)
    resolver.resolve_listing(b.pk)
    a.refresh_from_db()
    assert a.resolution_grain == ResolutionGrain.FAMILY
    assert a.product_family is not None and a.product_family.normalized_name == "exos"
    assert ProductFamily.objects.filter(normalized_name="exos").count() == 1  # reused
    edge = _edge(a, superseded_by__isnull=True)
    assert edge.method == ResolutionMethod.MPN_DECODE
    assert edge.evidence["provisional"] is True


def test_dual_labeled_listing_emits_learned_oem_alias(
    site: SourceSite, seagate: Manufacturer
) -> None:
    hgst = Manufacturer.objects.create(name="HGST", normalized_name="hgst")
    model = ProductModel.objects.create(
        manufacturer=hgst,
        model_number="HUS724040ALS640",
        normalized_model_number="hus724040als640",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type=AliasType.MPN,
        normalized_alias_text="hus724040als640",
        product_model=model,
        source_kind=AliasSourceKind.CATALOG_AUTHORITATIVE,
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    listing = _listing(site, "l6", "NetApp X477A-R6 4TB 7.2K SAS HDD HUS724040ALS640")
    CatalogResolver().resolve_listing(listing.pk)
    learned = ProductAlias.objects.get(alias_type=AliasType.OEM_PN, normalized_alias_text="x477ar6")
    assert learned.product_model == model  # model grain max — never variant (rule 7)
    assert learned.source_kind == AliasSourceKind.LISTING_DERIVED
    # OQ22: the row is stamped indefinite so the hourly purge_expired sweep can
    # never retire it, and it is NOT the authoritative manufacturer class — the
    # token came out of a merchant title.
    assert learned.retention_class == RetentionClass.LISTING_DERIVED_ALIAS
    assert learned.expires_at is None


def test_matcher_crash_writes_error_edge_and_never_raises(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = _listing(site, "l7", "whatever 16TB")

    def boom(title: str) -> object:
        raise RuntimeError("vocab exploded")

    monkeypatch.setattr(vocab, "extract", boom)
    CatalogResolver().resolve_listing(listing.pk)  # must not raise (C.3)
    edge = _edge(listing)
    assert edge.grain == ResolutionGrain.NONE
    assert "error" in edge.evidence


def test_unresolved_repoll_appends_no_duplicate_none_edge(site: SourceSite) -> None:
    listing = _listing(site, "l8", "mystery enterprise drive")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)
    resolver.resolve_listing(listing.pk)
    assert _edge_count(listing) == 1  # unchanged NONE outcome: no spam


def test_crash_after_accepted_resolution_records_error_without_demotion(
    site: SourceSite, exos_16tb: ProductModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = _listing(site, "l9", "Seagate Exos 16TB ST16000NM001G Recertified")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.VARIANT

    def boom(title: str) -> object:
        raise RuntimeError("transient matcher bug")

    monkeypatch.setattr(vocab, "extract", boom)
    resolver.resolve_listing(listing.pk)
    listing.refresh_from_db()
    # CR-001: the crash is IN the ledger, the accepted state is NOT demoted.
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert listing.product_variant is not None
    current = _edge(listing, is_current=True)
    assert "error" in current.evidence
    assert current.evidence["denorm_preserved"] is True
    assert _edge_count(listing) == 2


def test_repeated_identical_crash_does_not_spam_error_edges(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = _listing(site, "l10", "whatever 16TB")

    def boom(title: str) -> object:
        raise RuntimeError("same bug every poll")

    monkeypatch.setattr(vocab, "extract", boom)
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)
    resolver.resolve_listing(listing.pk)
    assert _edge_count(listing) == 1  # identical error: no spam (CR-001)


def test_distinct_consecutive_crashes_each_append_an_error_edge(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the no-spam contract (resolver `unchanged_error`): the
    skip is keyed on `repr(exc)`, so a DIFFERENT failure on the next poll is a
    real state change and MUST append a new edge — a regressed matcher must not
    hide behind a stale error the way an identical crash is deduped."""
    listing = _listing(site, "l10b", "whatever 16TB")
    resolver = CatalogResolver()

    def boom_a(title: str) -> object:
        raise RuntimeError("first distinct failure")

    def boom_b(title: str) -> object:
        raise ValueError("second, different failure")

    monkeypatch.setattr(vocab, "extract", boom_a)
    resolver.resolve_listing(listing.pk)
    monkeypatch.setattr(vocab, "extract", boom_b)
    resolver.resolve_listing(listing.pk)

    assert _edge_count(listing) == 2  # distinct errors are NOT deduped
    errors = {edge.evidence["error"] for edge in ListingResolution.objects.filter(listing=listing)}
    assert len(errors) == 2  # two different repr(exc) payloads recorded


def test_clean_poll_recovers_a_listing_stuck_on_an_error_edge(
    site: SourceSite, exos_16tb: ProductModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `unchanged_accept` carve-out (resolver): when the current edge is an
    error edge, a clean re-resolution to the SAME target the denorm still holds
    must NOT be treated as an unchanged-accept no-op — it must append a real
    recovery edge that supersedes the error, or a transient crash would pin the
    listing on the error edge forever."""
    listing = _listing(site, "l10c", "Seagate Exos 16TB ST16000NM001G Recertified")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)  # rung-1 accept → VARIANT (edge 1)

    def boom(title: str) -> object:
        raise RuntimeError("transient matcher bug")

    with monkeypatch.context() as m:
        m.setattr(vocab, "extract", boom)
        resolver.resolve_listing(listing.pk)  # error edge, denorm preserved (edge 2)
    assert _edge(listing, is_current=True).evidence.get("error") is not None

    resolver.resolve_listing(listing.pk)  # vocab restored: clean recovery (edge 3)
    listing.refresh_from_db()
    recovered = _edge(listing, is_current=True)
    assert "error" not in recovered.evidence  # back to a clean accept edge
    assert recovered.grain == ResolutionGrain.VARIANT
    assert listing.resolution_grain == ResolutionGrain.VARIANT
    assert _edge_count(listing) == 3  # recovery appended, did not skip


def test_apply_failure_falls_back_to_error_edge(
    site: SourceSite, exos_16tb: ProductModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CR-001: a failure INSIDE the write path still leaves a ledger trace via
    # the fallback error-edge write (which skips _materialize by design).
    from hw_radar.matching import resolver as resolver_module

    listing = _listing(site, "l11", "Seagate Exos 16TB ST16000NM001G Recertified")

    def broken_materialize(extracted: object, verdict: object) -> object:
        raise RuntimeError("materialize exploded")

    monkeypatch.setattr(resolver_module, "_materialize", broken_materialize)
    CatalogResolver().resolve_listing(listing.pk)  # must not raise
    edge = _edge(listing, is_current=True)
    assert edge.grain == ResolutionGrain.NONE
    assert "error" in edge.evidence


def test_veto_on_reobservation_demotes_and_keeps_one_current(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    # CR-002 + C.3.2 relist/edit abuse: the transition appends exactly one new
    # current edge and the evidence-based demotion clears the denorm state.
    listing = _listing(site, "l12", "Seagate Exos 16TB ST16000NM001G Recertified")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="Seagate Exos 14TB ST16000NM001G Recertified"
    )
    resolver.resolve_listing(listing.pk)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.NONE
    assert listing.product_variant is None
    assert _edge_count(listing) == 2
    assert _edge_count(listing, is_current=True) == 1
    current = _edge(listing, is_current=True)
    assert current.evidence["outcome"] == "review"


def test_rung0_prior_blocks_upgrade_without_reconsider(site: SourceSite) -> None:
    """The trap this task exists for: family-grain prior short-circuits rung 1."""
    listing = _listing(site, "up-1", "seagate exos st16000nm002c 16tb sata")
    CatalogResolver().resolve_listing(listing.pk)  # rung 2 → provisional family
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.FAMILY
    _seed_alias_for("ST16000NM002C")  # helper below: catalog alias + model + spec
    CatalogResolver().resolve_listing(listing.pk)  # normal poll: rung 0 sticks
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.FAMILY


def test_reconsider_upgrades_family_grain_via_seeded_alias(site: SourceSite) -> None:
    listing = _listing(site, "up-2", "seagate exos st16000nm002c 16tb sata")
    CatalogResolver().resolve_listing(listing.pk)
    _seed_alias_for("ST16000NM002C")
    CatalogResolver().resolve_listing(listing.pk, reconsider=True)
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.MODEL
    edge = _edge(listing, is_current=True)
    assert edge.method == "exact_alias"
    assert edge.evidence.get("reconsider") is True


def test_reconsider_same_outcome_stamps_freshness_without_new_edge(
    site: SourceSite,
) -> None:
    listing = _listing(site, "fresh-1", "mystery drive with no tokens at all")
    CatalogResolver().resolve_listing(listing.pk)  # grain none edge
    edge = _edge(listing, is_current=True)
    stamp_before = edge.last_evaluated_at
    edges_before = _edge_count(listing)
    CatalogResolver().resolve_listing(listing.pk, reconsider=True)
    edge.refresh_from_db()
    assert _edge_count(listing) == edges_before  # no edge spam
    assert edge.last_evaluated_at > stamp_before  # but freshness recorded


def test_unchanged_rung0_accept_stamps_freshness(site: SourceSite) -> None:
    listing = _listing(site, "fresh-2", "seagate exos st16000nm002c 16tb sata")
    CatalogResolver().resolve_listing(listing.pk)
    edge = _edge(listing, is_current=True)
    stamp_before = edge.last_evaluated_at
    CatalogResolver().resolve_listing(listing.pk)  # rung-0 unchanged re-poll
    edge.refresh_from_db()
    assert edge.last_evaluated_at > stamp_before


def test_reconsider_accept_rehit_same_target_stamps_without_new_edge(
    site: SourceSite,
) -> None:
    """The (d) unchanged-target skip: a reconsider that re-accepts the SAME
    grain+target writes no new edge (no edge spam per monthly refresh) but
    advances the freshness stamp."""
    _seed_alias_for("ST16000NM002C")
    listing = _listing(site, "rehit-1", "seagate exos st16000nm002c 16tb sata")
    CatalogResolver().resolve_listing(listing.pk)  # rung 1 -> MODEL grain
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.MODEL
    edge = _edge(listing, is_current=True)
    stamp_before = edge.last_evaluated_at
    edges_before = _edge_count(listing)
    CatalogResolver().resolve_listing(listing.pk, reconsider=True)  # same target re-hit
    edge.refresh_from_db()
    assert _edge_count(listing) == edges_before  # (d) branch: no new edge
    assert edge.last_evaluated_at > stamp_before  # freshness recorded
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.MODEL  # denorm untouched


def test_rung2_decode_capacity_contradiction_vetoes_to_review(site: SourceSite) -> None:
    """Rung-2 family branch: WD20EFRX decodes to family 'red' at 2 TB
    (community-corroborated). A title asserting a different capacity is a
    decoder-vs-extracted contradiction, so the decode must NOT be adopted as a
    provisional family — it vetoes to REVIEW (ADR-0019 rule 3: never guess
    against contradicting evidence). This is the only rung where the veto
    compares the DECODER's capacity, not a catalog spec's."""
    listing = _listing(site, "cap-veto-1", "WD Red 8TB WD20EFRX SATA NAS Hard Drive")
    CatalogResolver().resolve_listing(listing.pk)
    edge = _edge(listing, is_current=True)
    assert edge.grain == ResolutionGrain.NONE  # not accepted as a provisional family
    assert edge.evidence.get("veto") == ["capacity"]
    assert "decoder_capacity" in edge.evidence  # the rung-2-specific veto payload


def test_listing_delete_is_blocked_by_supersede_chain(
    site: SourceSite, exos_16tb: ProductModel
) -> None:
    """Characterization of an INTENTIONAL posture (owner decision 2026-07-05): a
    Listing whose resolution ledger contains a superseded edge cannot be
    hard-deleted — the append-only edge table's `superseded_by` PROTECT (DR-010,
    ADR-0019 rule 6) makes the cascade raise ProtectedError. The append-only
    audit trail is deliberate; removing a listing is a future soft-delete /
    terminal-state concern (eBay delete-on-delist, spec IR-002), never a hard
    delete that would shred the trail. This test pins that guarantee so a future
    on_delete relaxation is caught."""
    listing = _listing(site, "del-1", "Seagate Exos 16TB ST16000NM001G Recertified")
    resolver = CatalogResolver()
    resolver.resolve_listing(listing.pk)  # accept → VARIANT (edge 1)
    Listing.objects.filter(pk=listing.pk).update(
        title_raw="Seagate Exos 14TB ST16000NM001G Recertified"
    )
    resolver.resolve_listing(listing.pk)  # veto-demote → old edge.superseded_by set
    assert _edge_count(listing, superseded_by__isnull=False) == 1  # chain exists

    with pytest.raises(ProtectedError):
        listing.delete()  # cascade to a protected edge → blocked, by design
