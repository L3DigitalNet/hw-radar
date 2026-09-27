"""Codex s8 round-1 findings 1-7 on the live resolver and eligibility paths,
as observation SEQUENCES on one listing where the finding is about a stored
decision surviving changed evidence.

Drive cases import the shipped drive seeds; CPU cases the shipped CPU seeds
under the production CPU rules (AMD EPYC ratified, OQ34), so an ACCEPT there
is what production writes."""

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
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.matching import resolver, vocab
from hw_radar.matching.normalize import canonicalize_listing_text
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def drives(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "drive"])


@pytest.fixture
def cpus(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])


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
    """One listing observed repeatedly: each step edits title/label/attrs and
    appends a later snapshot, like a re-poll that saw the edit."""

    def __init__(
        self,
        site: SourceSite,
        key: str,
        title: str,
        *,
        attrs: dict[str, object] | None = None,
        hint: str | None = None,
        label: str = "",
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

    def edit(
        self,
        title: str,
        *,
        label: str = "",
        attrs: dict[str, object] | None = None,
        price: str = "199.00",
    ) -> None:
        self.step += 1
        Listing.objects.filter(pk=self.listing.pk).update(
            title_raw=title, condition_label_raw=label
        )
        self.listing.refresh_from_db()
        self._observe(attrs or {}, Decimal(price))

    def resolve(self) -> ListingResolution:
        CatalogResolver().resolve_listing(self.listing.pk)
        self.listing.refresh_from_db()
        return ListingResolution.objects.get(listing=self.listing, is_current=True)

    def edges(self) -> int:
        return ListingResolution.objects.filter(listing=self.listing).count()

    def variant_tuple(self) -> tuple[str, str, str, str] | None:
        v = self.listing.product_variant
        if v is None:
            return None
        return (v.condition, v.packaging, v.recert_channel, v.warranty_channel)


def _drive_model(mpn: str) -> ProductModel:
    return ProductModel.objects.get(normalized_model_number=mpn)


def _watch(*, conditions: tuple[Condition, ...] = (), max_unit: str | None = None) -> Watch:
    watch = Watch.objects.create(
        name="drive watch", category=Category.objects.get(slug="drive"), enabled=True
    )
    # save_requirement owns the watch's offer fields (price, conditions).
    save_requirement(
        watch,
        DriveRequirementSpec(
            allowed_conditions=conditions,
            max_unit_price_usd=None if max_unit is None else Decimal(max_unit),
        ),
    )
    return watch


def _clauses(watch: Watch, listing: Listing) -> dict[str, dict[str, object]]:
    evaluate.evaluate_listing(listing.pk)
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return {str(r["clause"]): r for r in row.reasons}


# ── Finding 1: a model prior upgrades when an explicit condition appears ────


def test_drive_model_prior_becomes_the_newly_asserted_variant(
    drives: None, market: SourceSite
) -> None:
    seq = _Seq(market, "f1-drive", "90%NEW Seagate Exos 7E10 ST10000NM017B 10TB")
    first = seq.resolve()
    assert first.grain == "model"
    # Model-grain accepts now record their (empty) assertions too.
    assert first.evidence["variant_attributes"] == {}

    seq.edit("Seagate Exos 7E10 ST10000NM017B 10TB New Pull")
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_asserted",
        "prior_variant_attributes": {},
    }
    assert edge.grain == "variant"
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")
    assert seq.listing.product_variant is not None
    assert seq.listing.product_variant.product_model == _drive_model("st10000nm017b")

    # Settled: the next poll inherits without a new edge.
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2
    # A title that STOPS naming the condition still inherits the variant.
    seq.edit("Seagate Exos 7E10 ST10000NM017B 10TB")
    assert seq.resolve().pk == edge.pk
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")


def test_drive_model_prior_without_a_condition_never_redecides(
    drives: None, market: SourceSite
) -> None:
    seq = _Seq(market, "f1-quiet", "Seagate Exos 7E10 ST10000NM017B 10TB")
    first = seq.resolve()
    assert first.grain == "model"
    seq.edit("Seagate Exos 7E10 ST10000NM017B 10TB Retail Box 3.5in")
    assert seq.resolve().pk == first.pk
    assert seq.edges() == 1


def test_legacy_model_edge_without_the_record_upgrades_once(
    drives: None, market: SourceSite
) -> None:
    seq = _Seq(market, "f1-legacy", "Seagate Exos 7E10 ST10000NM017B 10TB")
    first = seq.resolve()
    evidence = dict(first.evidence)
    del evidence["variant_attributes"]
    ListingResolution.objects.filter(pk=first.pk).update(evidence=evidence)
    seq.edit("Used Seagate Exos 7E10 ST10000NM017B 10TB")
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_asserted",
        "prior_variant_attributes": None,
    }
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")
    assert seq.resolve().pk == edge.pk


def test_cpu_model_prior_becomes_the_newly_asserted_variant(cpus: None, market: SourceSite) -> None:
    seq = _Seq(market, "f1-cpu", "AMD EPYC 7763", hint="cpu")
    first = seq.resolve()
    assert first.evidence["outcome"] == "accept"
    assert first.grain == "model"
    seq.edit("AMD EPYC 7763 Used")
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert edge.grain == "variant"
    assert seq.variant_tuple() == ("used", "unknown", "unknown", "unknown")
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


# ── Finding 2: a variant alias contradicting the assertions is never settled ─


def _variant_alias(mpn: str, **tuple_: str) -> ProductVariant:
    """Re-point the seeded MPN alias at a variant, so the MPN reaches the
    variant-target alias path (not a model alias plus on-demand creation)."""
    variant = ProductVariant.objects.create(product_model=_drive_model(mpn), **tuple_)
    updated = ProductAlias.objects.filter(normalized_alias_text=mpn, product_model__isnull=False)
    assert updated.update(product_model=None, product_variant=variant) == 1
    return variant


def test_contradicted_variant_alias_reviews_instead_of_freezing(
    drives: None, wd_store: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = _variant_alias("wd20efpx", condition="recertified")
    title = 'WD Red Plus Internal NAS HDD 3.5" - Recertified'
    # Decided before the store's provenance existed: the alias variant's
    # unknown channel agreed with everything the title asserted then.
    with monkeypatch.context() as patch:
        patch.delitem(vocab.SOURCE_OFFER_PROVENANCE, "wd-recertified")
        seq = _Seq(wd_store, "f2", title, attrs={"mpn": "WD20EFPX"})
        first = seq.resolve()
    assert first.evidence["outcome"] == "accept"
    assert seq.listing.product_variant == variant

    # Provenance now asserts a factory channel the alias variant does not have.
    seq.edit(title, attrs={"mpn": "WD20EFPX"})
    edge = seq.resolve()
    assert edge.evidence["reconsidered_prior"] == {
        "reason": "variant_attributes_changed",
        "prior_variant_attributes": {"recert_channel": "unknown"},
    }
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["variant_contradicted"] == {"recert_channel": "unknown"}
    assert seq.listing.product_variant is None
    # Stable: the next poll reviews again without appending an edge.
    assert seq.resolve().pk == edge.pk
    assert seq.edges() == 2


def test_fresh_variant_alias_contradicting_the_title_reviews(
    drives: None, market: SourceSite
) -> None:
    _variant_alias("st12000ne0008", condition="new")
    seq = _Seq(market, "f2-fresh", "Used Seagate ST12000NE0008 12TB HDD")
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["variant_contradicted"] == {"condition": "new"}


def test_agreeing_variant_alias_still_accepts(drives: None, market: SourceSite) -> None:
    variant = _variant_alias("st12000ne0008", condition="new")
    seq = _Seq(market, "f2-agree", "New Seagate ST12000NE0008 12TB HDD")
    assert seq.resolve().evidence["outcome"] == "accept"
    assert seq.listing.product_variant == variant


# ── Finding 3: an "Nx" count tied to the item is a lot ──────────────────────


def test_single_unit_prior_gaining_2x_goes_to_review(drives: None, market: SourceSite) -> None:
    seq = _Seq(market, "f3", "New Seagate ST12000NE0008 12TB HDD")
    assert seq.resolve().evidence["outcome"] == "accept"
    seq.edit("2x New Seagate ST12000NE0008 12TB HDD")
    edge = seq.resolve()
    assert edge.evidence["rung"] == 0
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["lot"]["quantity"] == 2  # pyright: ignore[reportIndexIssue] - evidence is a JSON object
    assert seq.listing.product_variant is None


def test_fresh_cpu_multi_unit_offer_reviews(cpus: None, market: SourceSite) -> None:
    seq = _Seq(market, "f3-cpu", "10x AMD EPYC 7763 64-Core SP3 CPUs", hint="cpu")
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["lot"]["quantity"] == 10  # pyright: ignore[reportIndexIssue] - evidence is a JSON object


# ── Finding 4: auction identifiers and conflicting counts ───────────────────


def test_auction_lot_is_one_unit_for_identity_and_price(drives: None, market: SourceSite) -> None:
    seq = _Seq(
        market,
        "f4-auction",
        "Auction Lot 42: Seagate ST12000NE0008 12TB HDD (1pc)",
        price="420.00",
    )
    assert seq.resolve().evidence["outcome"] == "accept"
    watch = _watch(max_unit="20.00")
    price = _clauses(watch, seq.listing)["offer.max_unit_price_usd"]
    assert price["outcome"] == "no_match"


def test_conflicting_counts_review_and_never_divide_the_price(
    drives: None, market: SourceSite
) -> None:
    seq = _Seq(
        market, "f4-conflict", "Lot of 2 Seagate ST12000NE0008 12TB HDD (1pc)", price="30.00"
    )
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert "lot" in edge.evidence
    watch = _watch(max_unit="20.00")
    price = _clauses(watch, seq.listing)["offer.max_unit_price_usd"]
    # $30 / 2 = $15 would "match" the $20 maximum; the count is unproven.
    assert price["outcome"] == "unknown"


# ── Finding 5: a store listing that also asserts another condition ──────────


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("WD Red Plus WD20EFPX 2TB Recertified", "Used"),
        ("WD Red Plus WD20EFPX 2TB Recertified New Pull", ""),
    ],
)
def test_store_factory_prior_is_not_kept_under_a_condition_conflict(
    drives: None, wd_store: SourceSite, title: str, label: str
) -> None:
    seq = _Seq(wd_store, "f5", "WD Red Plus WD20EFPX 2TB Recertified", attrs={"mpn": "WD20EFPX"})
    assert seq.resolve().evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")
    watch = _watch(conditions=(Condition.RECERTIFIED,))
    assert _clauses(watch, seq.listing)["offer.condition"]["outcome"] == "match"

    seq.edit(title, label=label, attrs={"mpn": "WD20EFPX"})
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "review"
    assert edge.evidence["rung"] == 0
    assert "recertified" in edge.evidence["offer_condition_conflict"]  # pyright: ignore[reportOperatorIssue] - evidence is a JSON object
    assert seq.listing.product_variant is None
    assert _clauses(watch, seq.listing)["offer.condition"]["outcome"] == "unknown"
    assert seq.resolve().pk == edge.pk


def test_marketplace_conflicting_title_keeps_first_match(drives: None, market: SourceSite) -> None:
    seq = _Seq(market, "f5-mkt", "WD Red Plus WD20EFPX 2TB Recertified", label="Used")
    assert seq.resolve().evidence["outcome"] == "accept"
    assert seq.variant_tuple() == ("recertified", "unknown", "unknown", "unknown")


# ── Finding 6: resolver and eligibility read one offer extraction ───────────


def test_store_title_edited_into_a_drive_reference_phrase_keeps_one_reading(
    drives: None, wd_store: SourceSite
) -> None:
    seq = _Seq(wd_store, "f6", "WD Red Plus WD20EFPX 2TB", attrs={"mpn": "WD20EFPX"})
    seq.resolve()
    watch = _watch(conditions=(Condition.RECERTIFIED,))
    assert _clauses(watch, seq.listing)["offer.condition"]["outcome"] == "match"

    seq.edit("WD Red Plus WD20EFPX 2TB fit for used servers", attrs={"mpn": "WD20EFPX"})
    seq.resolve()
    assert seq.variant_tuple() == ("recertified", "unknown", "factory", "unknown")
    # Before the fix eligibility read "used" out of the masked reference span.
    assert _clauses(watch, seq.listing)["offer.condition"]["outcome"] == "match"


_PARITY_CASES: list[tuple[str, str, str | None, str]] = [
    ("WD Red Plus WD20EFPX 2TB fit for used servers", "", None, "wd-recertified"),
    ("WD Red Plus WD20EFPX 2TB fit for used servers", "", None, "demomarket"),
    ("WD Red Plus WD20EFPX 2TB Recertified", "Used", None, "wd-recertified"),
    ("WD Red Plus WD20EFPX 2TB Recertified New Pull", "", None, "wd-recertified"),
    ("WD Red Plus WD20EFPX 2TB", "", None, "wd-recertified"),
    ("FOR Seagate ST12000NE0008 12TB NEW", "", None, "demomarket"),
    ("Seagate ST12000NE0008 12TB Factory Recertified", "", None, "demomarket"),
    ("Seagate ST12000NE0008 12TB NEW 90%", "", None, "demomarket"),
    ("AMD EPYC 7763 Used", "", "cpu", "demomarket"),
    ("AMD EPYC 7763 replacement for used server CPU New", "", "cpu", "demomarket"),
    ("AMD EPYC 7763 Recertified", "Used", "cpu", "wd-recertified"),
    ("AMD EPYC 7763", "New", "cpu", "demomarket"),
]


@pytest.mark.parametrize(("title", "label", "hint", "source"), _PARITY_CASES)
def test_resolver_and_evaluator_extract_the_same_offer_terms(
    drives: None, cpus: None, title: str, label: str, hint: str | None, source: str
) -> None:
    site, _ = SourceSite.objects.get_or_create(normalized_name=source, defaults={"name": source})
    seq = _Seq(site, f"parity-{hash((title, label, hint, source))}", title, label=label, hint=hint)
    listing = Listing.objects.select_related("source_site").get(pk=seq.listing.pk)
    _canonical, resolved, _candidates, _verdict = resolver._run_ladder(listing)  # pyright: ignore[reportPrivateUsage] - the parity under test is between the two private readers
    snapshot = evaluate._latest_snapshot(listing)  # pyright: ignore[reportPrivateUsage] - see above
    category = evaluate._dispatch_category(listing, snapshot)  # pyright: ignore[reportPrivateUsage] - see above
    evaluated = evaluate._listing_attributes(  # pyright: ignore[reportPrivateUsage] - see above
        category, canonicalize_listing_text(title, label), source
    )
    facts = evaluate._offer_facts(  # pyright: ignore[reportPrivateUsage] - see above
        listing, snapshot, canonicalize_listing_text(title, label), category
    )

    def value(attr: object) -> object:
        return getattr(attr, "value", None)

    assert value(resolved.condition) == value(evaluated.condition) == value(facts.condition)
    assert value(resolved.recert_channel) == value(evaluated.recert_channel)


# ── Finding 7: percentage and cosmetic "new" on the live path ───────────────


@pytest.mark.parametrize(
    "title",
    [
        "Seagate Exos 7E10 ST10000NM017B 10TB NEW 90%",
        "Seagate Exos 7E10 ST10000NM017B 10TB 90 % NEW",
        "Seagate Exos 7E10 ST10000NM017B 10TB Like-New",
    ],
)
def test_percentage_or_cosmetic_new_stays_at_model_grain(
    drives: None, market: SourceSite, title: str
) -> None:
    seq = _Seq(market, f"f7-{hash(title)}", title)
    edge = seq.resolve()
    assert edge.evidence["outcome"] == "accept"
    assert edge.grain == "model"
    assert seq.listing.product_model == _drive_model("st10000nm017b")
