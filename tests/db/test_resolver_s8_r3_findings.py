"""Codex s8 round-3 findings 6 (partial), 7 (partial), 8 and 9 on the live
resolver and eligibility paths, as observation SEQUENCES on one listing: each
finding is a stored decision (or stored review edge) surviving changed
evidence, or a store default re-asserting what the listing denies."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Category,
    Condition,
    Listing,
    ListingResolution,
    ProductAlias,
    ProductModel,
    ProductVariant,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.matching import MATCHER_VERSION
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(db: None) -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def market(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


@pytest.fixture
def wd_store(db: None) -> SourceSite:
    site, _ = SourceSite.objects.get_or_create(
        normalized_name="wd-recertified", defaults={"name": "WD Recertified"}
    )
    return site


class _Seq:
    """One listing observed repeatedly: each edit changes title/label and
    appends a later snapshot, like a re-poll that saw the edit."""

    def __init__(
        self,
        site: SourceSite,
        key: str,
        title: str,
        *,
        label: str = "",
        attrs: dict[str, object] | None = None,
    ) -> None:
        self.step = 0
        self.attrs = attrs or {}
        self.listing = Listing.objects.create(
            source_site=site,
            source_listing_key=key,
            canonical_url=f"https://example.test/{key}",
            url_hash=key,
            title_raw=title,
            condition_label_raw=label,
            retention_class=RetentionClass.MERCHANT_FACT,
        )
        self._observe()

    def _observe(self) -> None:
        persist.append_snapshot(
            self.listing,
            NormalizedListing(
                source_listing_key=self.listing.source_listing_key,
                url=self.listing.canonical_url,
                title=self.listing.title_raw,
                price=Decimal("199.00"),
                shipping_price=Decimal(0),
                attrs=self.attrs,
                fx_rate=Decimal(1),
                fx_pair="USD/USD",
                fx_rate_date=date(2026, 9, 26),
                fx_source="identity",
                is_international=False,
                category_hint=None,
            ),
            observed_at=_OBSERVED_AT + timedelta(hours=self.step),
        )

    def edit(self, title: str, *, label: str = "") -> None:
        self.step += 1
        Listing.objects.filter(pk=self.listing.pk).update(
            title_raw=title, condition_label_raw=label
        )
        self.listing.refresh_from_db()
        self._observe()

    def resolve(self, *, reconsider: bool = False) -> ListingResolution:
        CatalogResolver().resolve_listing(self.listing.pk, reconsider=reconsider)
        self.listing.refresh_from_db()
        return ListingResolution.objects.get(listing=self.listing, is_current=True)

    def edges(self) -> int:
        return ListingResolution.objects.filter(listing=self.listing).count()

    def variant_tuple(self) -> tuple[str, str, str, str] | None:
        v = self.listing.product_variant
        if v is None:
            return None
        return (v.condition, v.packaging, v.recert_channel, v.warranty_channel)


def _model(mpn: str) -> ProductModel:
    return ProductAlias.objects.get(
        normalized_alias_text=mpn, product_model__isnull=False
    ).product_model  # pyright: ignore[reportReturnType] - filtered non-null above


def _condition_outcome(listing: Listing, allowed: tuple[Condition, ...]) -> object:
    watch = Watch.objects.create(
        name="drive watch", category=Category.objects.get(slug="drive"), enabled=True
    )
    save_requirement(watch, DriveRequirementSpec(allowed_conditions=allowed))
    evaluate.evaluate_listing(listing.pk)
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return {str(r["clause"]): r for r in row.reasons}["offer.condition"]["outcome"]


# ── Finding 6 (partial): a negated compound condition stays negated ─────────


def test_store_not_factory_recertified_used_is_used_without_a_conflict(
    seeded: None, wd_store: SourceSite
) -> None:
    seq = _Seq(
        wd_store,
        "r3-6",
        "WD Red Plus WD20EFPX 2TB NOT FACTORY RECERTIFIED Used",
        attrs={"mpn": "WD20EFPX"},
    )
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert "offer_condition_conflict" not in edge.evidence
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")


def test_store_recertified_not_new_pull_keeps_the_factory_variant(
    seeded: None, wd_store: SourceSite
) -> None:
    seq = _Seq(
        wd_store,
        "r3-6b",
        "WD Red Plus WD20EFPX 2TB Recertified NOT New Pull",
        attrs={"mpn": "WD20EFPX"},
    )
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert "offer_condition_conflict" not in edge.evidence
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")


# ── Finding 8: an explicit denial is evidence, not omission ─────────────────


def test_store_title_denying_recertified_withdraws_the_factory_variant(
    seeded: None, wd_store: SourceSite
) -> None:
    seq = _Seq(wd_store, "r3-8a", "WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    first = seq.resolve()
    assert first.evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")

    seq.edit("WD Red Plus WD20EFPX 2TB NOT RECERTIFIED")
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": "recertified"},
    }
    assert edge.evidence["outcome"] == "accept"
    assert seq.listing.resolution_grain == ResolutionGrain.MODEL
    assert seq.variant_tuple() is None
    # Eligibility reads the same unfolded condition: a recertified-only watch
    # can no longer match a listing that says it is not recertified.
    assert _condition_outcome(seq.listing, (Condition.RECERTIFIED,)) == "unknown"
    # Settled: identical polls and a catalog-refresh re-run add nothing.
    assert seq.resolve().pk == edge.pk
    assert seq.resolve(reconsider=True).pk == edge.pk
    assert seq.edges() == 2


@pytest.mark.parametrize(
    ("before", "after", "condition"),
    [
        (
            "Used Seagate ST12000NE0008 12TB HDD",
            "Seagate ST12000NE0008 12TB HDD never used",
            "used",
        ),
        ("New Seagate ST12000NE0008 12TB HDD", "NOT NEW Seagate ST12000NE0008 12TB HDD", "new"),
    ],
)
def test_denied_condition_redecides_an_automated_variant_prior(
    seeded: None, market: SourceSite, before: str, after: str, condition: str
) -> None:
    seq = _Seq(market, f"r3-8b-{condition}", before)
    seq.resolve()
    assert seq.variant_tuple() == (condition, "unknown", "unknown", "unknown")

    seq.edit(after)
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": condition},
    }
    assert edge.evidence["outcome"] == "accept"
    assert seq.variant_tuple() is None
    assert seq.listing.product_model == _model("st12000ne0008")
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


def test_omitted_condition_still_inherits_the_variant(seeded: None, market: SourceSite) -> None:
    # The omission policy is unchanged: a title that stops naming its
    # condition says nothing against the stored variant.
    seq = _Seq(market, "r3-8c", "Used Seagate ST12000NE0008 12TB HDD")
    first = seq.resolve()
    seq.edit("Seagate ST12000NE0008 12TB HDD")
    assert seq.resolve().pk == first.pk
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")


# ── Finding 9: a newly contradictory brand is re-decided like a fresh title ─


def test_automated_prior_does_not_survive_a_contradictory_brand(
    seeded: None, market: SourceSite
) -> None:
    seq = _Seq(market, "r3-9", "New Seagate ST12000NE0008 12TB HDD")
    assert seq.resolve().evidence["outcome"] == "accept"

    seq.edit("New Toshiba ST12000NE0008 12TB HDD")
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["brand_contradicts_exact_alias"] == {
        "brand": "toshiba",
        "alias_brands": ["seagate"],
    }
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "brand_contradicted",
        "brand": "toshiba",
        "prior_brand": "seagate",
    }
    assert seq.listing.resolution_grain == ResolutionGrain.NONE
    assert seq.listing.product_model is None
    assert seq.listing.product_variant is None

    # Same decision as a fresh resolution of the edited title.
    fresh = _Seq(market, "r3-9-fresh", "New Toshiba ST12000NE0008 12TB HDD").resolve()
    for key in ("outcome", "rung", "brand_contradicts_exact_alias", "mpn_hypothesis"):
        assert edge.evidence[key] == fresh.evidence[key]
    # Independent of invocation mode, and stable.
    assert seq.resolve(reconsider=True).pk == edge.pk
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


def test_absent_brand_still_inherits(seeded: None, market: SourceSite) -> None:
    seq = _Seq(market, "r3-9b", "New Seagate ST12000NE0008 12TB HDD")
    first = seq.resolve()
    seq.edit("New ST12000NE0008 12TB HDD")
    assert seq.resolve().pk == first.pk
    assert seq.variant_tuple() == ("new", "unknown", "unknown", "unknown")


def test_manual_prior_inherits_despite_a_contradictory_brand(
    seeded: None, market: SourceSite
) -> None:
    # The owner's decision stands (resolver._automated_origin: a manual accept
    # has no automated origin, so no reconsideration of any kind applies).
    model = _model("st12000ne0008")
    seq = _Seq(market, "r3-9c", "New Toshiba ST12000NE0008 12TB HDD")
    ListingResolution.objects.create(
        listing=seq.listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "category": "drive"},
    )
    Listing.objects.filter(pk=seq.listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model, resolution_confidence=1.0
    )
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert "reconsidered_prior" not in edge.evidence
    assert seq.listing.product_model == model


# ── Finding 7 (partial): the review edge follows the product it is about ────


def test_review_moving_to_another_mpn_supersedes_the_stale_hypothesis(
    seeded: None, market: SourceSite
) -> None:
    seq = _Seq(market, "r3-7a", "2x New Seagate ST12000NE0008 12TB HDD")
    first = seq.resolve()
    assert first.evidence["outcome"] == "review"
    assert first.evidence["mpn_hypothesis"] == "st12000ne0008"

    seq.edit("2x New Seagate ST16000NM001G 16TB HDD")
    edge = seq.resolve()
    assert edge.pk != first.pk
    assert edge.evidence["outcome"] == "review"
    assert "lot" in edge.evidence
    assert edge.evidence["mpn_hypothesis"] == "st16000nm001g"

    # Quantity-only and cosmetic edits, re-runs and re-polls stay put.
    assert seq.resolve(reconsider=True).pk == edge.pk
    seq.edit("3x New Seagate ST16000NM001G 16TB HDD")
    assert seq.resolve().pk == edge.pk
    seq.edit("3 x NEW Seagate ST16000NM001G 16TB HDD!")
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


def test_changed_contradicted_variant_field_writes_one_new_edge(
    seeded: None, market: SourceSite
) -> None:
    variant = ProductVariant.objects.create(
        product_model=_model("st12000ne0008"), condition="new", packaging="bulk"
    )
    alias = ProductAlias.objects.filter(
        normalized_alias_text="st12000ne0008", product_model__isnull=False
    )
    assert alias.update(product_model=None, product_variant=variant) == 1

    seq = _Seq(market, "r3-7b", "Used Seagate ST12000NE0008 12TB bulk")
    first = seq.resolve()
    assert first.evidence["variant_contradicted"] == {"condition": "new"}

    seq.edit("New Seagate ST12000NE0008 12TB retail")
    edge = seq.resolve()
    assert edge.pk != first.pk
    assert edge.evidence["variant_contradicted"] == {"packaging": "bulk"}
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2
