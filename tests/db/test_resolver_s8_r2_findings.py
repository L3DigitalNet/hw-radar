"""Codex s8 round-2 findings 3 (partial), B, D and E on the live resolver and
eligibility paths, as observation SEQUENCES on one listing: each finding is a
stored decision (or stored review reason) surviving changed evidence."""

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
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.requirements import (
    DriveRequirementSpec,
    RamRequirementSpec,
    save_requirement,
)
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
        hint: str | None = None,
        price: str = "199.00",
    ) -> None:
        self.hint = hint
        self.step = 0
        self.listing = Listing.objects.create(
            source_site=site,
            source_listing_key=key,
            canonical_url=f"https://example.test/{key}",
            url_hash=key,
            title_raw=title,
            condition_label_raw=label,
            retention_class=RetentionClass.MERCHANT_FACT,
        )
        self._observe(attrs or {}, Decimal(price))

    def _observe(self, attrs: dict[str, object], price: Decimal) -> None:
        persist.append_snapshot(
            self.listing,
            NormalizedListing(
                source_listing_key=self.listing.source_listing_key,
                url=self.listing.canonical_url,
                title=self.listing.title_raw,
                price=price,
                shipping_price=Decimal(0),
                attrs=attrs,
                fx_rate=Decimal(1),
                fx_pair="USD/USD",
                fx_rate_date=date(2026, 9, 26),
                fx_source="identity",
                is_international=False,
                category_hint=self.hint,
            ),
            observed_at=_OBSERVED_AT + timedelta(hours=self.step),
        )

    def edit(self, title: str, *, label: str = "", attrs: dict[str, object] | None = None) -> None:
        self.step += 1
        Listing.objects.filter(pk=self.listing.pk).update(
            title_raw=title, condition_label_raw=label
        )
        self.listing.refresh_from_db()
        self._observe(attrs or {}, Decimal("199.00"))

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


def _clauses(watch: Watch, listing: Listing) -> dict[str, dict[str, object]]:
    evaluate.evaluate_listing(listing.pk)
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return {str(r["clause"]): r for r in row.reasons}


# ── Finding 3 (partial): compact "2x12TB" reaches the lot review ────────────


def test_single_unit_prior_gaining_compact_2x12tb_goes_to_review(
    seeded: None, market: SourceSite
) -> None:
    seq = _Seq(market, "r2-3", "New Seagate ST12000NE0008 12TB HDD")
    first = seq.resolve()
    assert first.evidence["outcome"] == "accept"
    assert seq.listing.product_variant is not None

    seq.edit("New Seagate ST12000NE0008 2x12TB HDD")
    edge = seq.resolve()
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["lot"]["quantity"] == 2  # pyright: ignore[reportIndexIssue] - evidence is a JSON object
    assert seq.listing.product_variant is None
    # Settled: further identical polls add nothing.
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


# ── Finding B: a memory marker before the brand keeps the kit price whole ───


def test_ram_kit_price_is_not_divided_by_a_module_count(seeded: None, market: SourceSite) -> None:
    watch = Watch.objects.create(
        name="ram watch", category=Category.objects.get(slug="ram"), enabled=True
    )
    save_requirement(watch, RamRequirementSpec(max_unit_price_usd=Decimal("50.00")))
    seq = _Seq(market, "r2-b", "DDR4 RAM kit 2x Kingston 16GB 2666 ECC", hint="ram", price="80.00")
    seq.resolve()
    price = _clauses(watch, seq.listing)["offer.max_unit_price_usd"]
    # $80 / 2 = $40 would "match" the $50 maximum. The bare "2x" is still read,
    # below the divisor bar, so the undivided total only bounds the unit price
    # (quantity_uncertain), exactly as the after-the-brand kit form does.
    assert price["outcome"] == "unknown"
    assert price["observed"]["quantity"] == 1  # pyright: ignore[reportIndexIssue] - reasons are JSON objects
    assert price["observed"]["reason_code"] == "quantity_uncertain"  # pyright: ignore[reportIndexIssue] - reasons are JSON objects


# ── Finding D: an agreeing negated clarification keeps the store variant ────


def test_store_factory_prior_survives_a_not_new_clarification(
    seeded: None, wd_store: SourceSite
) -> None:
    seq = _Seq(wd_store, "r2-d", "WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    first = seq.resolve()
    assert first.evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")

    seq.edit("WD Red Plus WD20EFPX 2TB Recertified NOT NEW", attrs={"mpn": "WD20EFPX"})
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert "offer_condition_conflict" not in edge.evidence
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")
    assert seq.edges() == 1

    watch = Watch.objects.create(
        name="drive watch", category=Category.objects.get(slug="drive"), enabled=True
    )
    save_requirement(watch, DriveRequirementSpec(allowed_conditions=(Condition.RECERTIFIED,)))
    assert _clauses(watch, seq.listing)["offer.condition"]["outcome"] == "match"


def test_store_prior_negating_its_own_condition_is_not_kept(
    seeded: None, wd_store: SourceSite
) -> None:
    seq = _Seq(wd_store, "r2-d2", "WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    assert seq.resolve().evidence["outcome"] == "accept"
    seq.edit("WD Red Plus WD20EFPX 2TB NOT RECERTIFIED Used", attrs={"mpn": "WD20EFPX"})
    seq.resolve()
    assert seq.variant_tuple() != ("recertified", "unknown", "factory", "unknown")
    if seq.listing.product_variant is not None:
        assert seq.listing.product_variant.recert_channel != "factory"


# ── Finding E: a changed review reason supersedes the obsolete one once ─────


def test_changed_review_reason_writes_one_new_edge(seeded: None, wd_store: SourceSite) -> None:
    seq = _Seq(
        wd_store,
        "r2-e",
        "WD Red Plus WD20EFPX 2TB Recertified",
        label="Used",
        attrs={"mpn": "WD20EFPX"},
    )
    first = seq.resolve()
    assert first.evidence["outcome"] == "review"
    assert "offer_condition_conflict" in first.evidence

    seq.edit("2x WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    edge = seq.resolve()
    assert edge.pk != first.pk
    assert edge.evidence["outcome"] == "review"
    assert "lot" in edge.evidence
    assert "offer_condition_conflict" not in edge.evidence
    assert seq.edges() == 2

    # No flapping: identical polls, a catalog-refresh re-run (which adds a
    # `reconsider` provenance key) and a title edit that keeps the same reason
    # all stay on the one current edge.
    assert seq.resolve().pk == edge.pk
    assert seq.resolve(reconsider=True).pk == edge.pk
    seq.edit("2 x WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


def test_unchanged_review_reason_never_appends(seeded: None, market: SourceSite) -> None:
    seq = _Seq(market, "r2-e2", "2x New Seagate ST12000NE0008 12TB HDD")
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    for _ in range(3):
        assert seq.resolve().pk == edge.pk
    assert seq.edges() == 1
