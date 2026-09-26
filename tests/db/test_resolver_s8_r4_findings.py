"""Codex s8 round-4 findings 4 and 6 on the live resolver and eligibility
paths, as observation SEQUENCES on one listing: a negation-suppressed
condition must not become the store's declared condition, and an explicitly
denied recertification channel must withdraw a stored factory variant exactly
as fresh resolution would."""

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
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_WD_ATTRS: dict[str, object] = {"mpn": "WD20EFPX"}


@pytest.fixture
def seeded(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])


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
        name=f"drive watch {Watch.objects.count()}",
        category=Category.objects.get(slug="drive"),
        enabled=True,
    )
    save_requirement(watch, DriveRequirementSpec(allowed_conditions=allowed))
    evaluate.evaluate_listing(listing.pk)
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return {str(r["clause"]): r for r in row.reasons}["offer.condition"]["outcome"]


# ── Finding 4: a suppressed condition never becomes the store's ─────────────


@pytest.mark.parametrize(
    ("title", "label"),
    [
        # "screws" is not a registered negator-owning phrase, so the window
        # reads "no screws used" as a denial: the structural rule decides.
        ("WD Red Plus WD20EFPX 2TB No Screws Used", ""),
        ("WD Red Plus WD20EFPX 2TB No Screws", "Used"),
    ],
)
def test_store_used_listing_edited_to_a_suppressed_used_never_turns_factory(
    seeded: None, wd_store: SourceSite, title: str, label: str
) -> None:
    seq = _Seq(wd_store, f"r4-4-{label}", "WD Red Plus WD20EFPX 2TB Used", attrs=_WD_ATTRS)
    assert seq.resolve().evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")

    seq.edit(title, label=label)
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": "used"},
    }
    assert edge.evidence["outcome"] == "accept"
    assert seq.listing.resolution_grain == ResolutionGrain.MODEL
    assert seq.variant_tuple() is None
    assert _condition_outcome(seq.listing, (Condition.RECERTIFIED,)) == "unknown"
    # Settled, and equal to a fresh resolution of the same text.
    assert seq.resolve().pk == edge.pk
    assert seq.resolve(reconsider=True).pk == edge.pk
    assert seq.edges() == 2
    fresh = _Seq(wd_store, f"r4-4-fresh-{label}", title, label=label, attrs=_WD_ATTRS)
    fresh.resolve()
    assert fresh.listing.resolution_grain == ResolutionGrain.MODEL
    assert fresh.variant_tuple() is None


@pytest.mark.parametrize(
    ("title", "label"),
    [("WD Red Plus WD20EFPX 2TB No tray Used", ""), ("WD Red Plus WD20EFPX 2TB No tray", "Used")],
)
def test_store_used_listing_with_no_tray_boilerplate_stays_used(
    seeded: None, wd_store: SourceSite, title: str, label: str
) -> None:
    seq = _Seq(wd_store, f"r4-4b-{label}", "WD Red Plus WD20EFPX 2TB Used", attrs=_WD_ATTRS)
    first = seq.resolve()
    seq.edit(title, label=label)
    assert seq.resolve().pk == first.pk
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")
    assert _condition_outcome(seq.listing, (Condition.RECERTIFIED,)) == "no_match"


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("WD Red Plus WD20EFPX 2TB No Screws Used", ""),
        ("WD Red Plus WD20EFPX 2TB No Screws", "Used"),
    ],
)
def test_store_folded_factory_variant_withdraws_on_a_suppressed_condition(
    seeded: None, wd_store: SourceSite, title: str, label: str
) -> None:
    """Codex s8 r5: the prior here comes from source provenance alone (the
    title names no condition), so the denial of 'used' names nothing in its
    tuple; it still withdraws the fold, and the stored variant must follow."""
    seq = _Seq(wd_store, f"r5-8-{label}", "WD Red Plus WD20EFPX 2TB", attrs=_WD_ATTRS)
    assert seq.resolve().evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")

    seq.edit(title, label=label)
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"condition": "recertified"},
    }
    assert seq.listing.resolution_grain == ResolutionGrain.MODEL
    assert seq.variant_tuple() is None
    assert _condition_outcome(seq.listing, (Condition.RECERTIFIED,)) == "unknown"
    assert seq.resolve().pk == edge.pk
    assert seq.resolve(reconsider=True).pk == edge.pk
    assert seq.edges() == 2
    fresh = _Seq(wd_store, f"r5-8-fresh-{label}", title, label=label, attrs=_WD_ATTRS)
    fresh.resolve()
    assert fresh.listing.resolution_grain == ResolutionGrain.MODEL
    assert fresh.variant_tuple() is None


# ── Finding 6: a denied channel withdraws the stored factory variant ────────

_FACTORY = ("recertified", "unknown", "factory", "unknown")
_GENERIC = ("recertified", "unknown", "unknown", "unknown")


@pytest.mark.parametrize(
    ("site_name", "before", "after", "attrs"),
    [
        (
            "market",
            "Seagate ST12000NE0008 12TB Factory Recertified",
            "Seagate ST12000NE0008 12TB Recertified NOT Factory Recertified",
            None,
        ),
        (
            "wd_store",
            "WD Red Plus WD20EFPX 2TB Recertified",
            "WD Red Plus WD20EFPX 2TB Recertified NOT Factory Recertified",
            _WD_ATTRS,
        ),
    ],
)
def test_denied_factory_channel_redecides_to_the_fresh_variant(
    seeded: None,
    request: pytest.FixtureRequest,
    site_name: str,
    before: str,
    after: str,
    attrs: dict[str, object] | None,
) -> None:
    site: SourceSite = request.getfixturevalue(site_name)
    seq = _Seq(site, f"r4-6-{site_name}", before, attrs=attrs)
    seq.resolve()
    assert seq.variant_tuple() == _FACTORY

    seq.edit(after)
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"recert_channel": "factory"},
    }
    assert edge.evidence["outcome"] == "accept"
    assert seq.variant_tuple() == _GENERIC
    # Normal resolution equals fresh resolution, then stays put.
    fresh = _Seq(site, f"r4-6-fresh-{site_name}", after, attrs=attrs)
    fresh.resolve()
    assert fresh.variant_tuple() == _GENERIC
    assert fresh.listing.product_variant == seq.listing.product_variant
    assert seq.resolve().pk == edge.pk
    assert seq.resolve(reconsider=True).pk == edge.pk
    assert seq.edges() == 2
    assert seq.variant_tuple() == _GENERIC


def test_factory_variant_alias_is_contradicted_by_a_denied_channel(
    seeded: None, market: SourceSite
) -> None:
    variant = ProductVariant.objects.create(
        product_model=_model("st12000ne0008"), condition="recertified", recert_channel="factory"
    )
    alias = ProductAlias.objects.filter(
        normalized_alias_text="st12000ne0008", product_model__isnull=False
    )
    assert alias.update(product_model=None, product_variant=variant) == 1
    seq = _Seq(
        market, "r4-6-alias", "Seagate ST12000NE0008 12TB Recertified NOT Factory Recertified"
    )
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["variant_contradicted"] == {"recert_channel": "factory"}
    assert seq.resolve().pk == edge.pk
