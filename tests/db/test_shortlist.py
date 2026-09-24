"""MS-2 Slice C4 read model: `shortlist()` / `review_queue()`, the shared batched
currency predicate, and the `evaluate_watches` / `show_shortlist` commands
(MS2-D-01, MS2-D-09, MS2-D-20, MS2-D-29; AC-3's code half; FR-014).

The GPU fixtures reuse the A100 shape of test_evaluation_binding.py: the seed
records the A100 as passive-cooled, so a `coolings=[passive]` watch matches
through catalog-tier evidence and a same-target spec correction flips it.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pytest_django import DjangoAssertNumQueries

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Category,
    DelistReason,
    EligibilityVerdict,
    GpuSpec,
    Listing,
    ListingResolution,
    OfferSnapshot,
    ProductFamily,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceConfig,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate, service
from hw_radar.eligibility import shortlist as shortlist_module
from hw_radar.eligibility.evaluate import evaluate_listing, live_binding
from hw_radar.eligibility.requirements import (
    DriveRequirementSpec,
    GpuRequirementSpec,
    RequirementSpec,
    save_requirement,
)
from hw_radar.eligibility.shortlist import (
    Freshness,
    ReviewRow,
    ReviewState,
    ShortlistRow,
    review_queue,
    shortlist,
)
from hw_radar.matching import MATCHER_VERSION
from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db

M, N, U = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH, EligibilityVerdict.UNKNOWN
_A100 = "A100 80GB PCIe"
_ELIGIBILITY_SRC = Path(__file__).resolve().parents[2] / "src" / "hw_radar" / "eligibility"
_COMMANDS_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "hw_radar" / "catalog" / "management" / "commands"
)


@pytest.fixture
def seeded(db: None) -> None:
    import_documents(load_seed_documents())


@pytest.fixture
def site(db: None) -> SourceSite:
    # The migration-seeded demo source, so the freshness check has a real
    # SourceConfig cadence to read.
    return SourceSite.objects.get(normalized_name="demo")


def _now() -> datetime:
    return timezone.now().replace(microsecond=0)


def _snapshot(
    listing: Listing,
    *,
    price: Decimal,
    observed_at: datetime,
    category: str | None = "gpu",
    shipping: Decimal | None = Decimal(0),
) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=price,
            shipping_price=shipping,
            stock_status="in_stock",
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 24),
            fx_source="identity",
            is_international=False,
            category_hint=category,
        ),
        observed_at=observed_at,
    )


def _listing(
    site: SourceSite,
    key: str,
    *,
    title: str = "NVIDIA A100 80GB PCIe",
    price: Decimal = Decimal("900.00"),
    category: str | None = "gpu",
    observed_at: datetime | None = None,
    shipping: Decimal | None = Decimal(0),
) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _snapshot(
        listing,
        price=price,
        observed_at=observed_at or _now(),
        category=category,
        shipping=shipping,
    )
    return listing


def _edge(
    listing: Listing, *, model: ProductModel | None = None, family: ProductFamily | None = None
) -> ListingResolution:
    ListingResolution.objects.filter(listing=listing, is_current=True).update(is_current=False)
    return ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL if model is not None else ResolutionGrain.FAMILY,
        product_model=model,
        product_family=family,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept"},
    )


def _model(number: str) -> ProductModel:
    return ProductModel.objects.get(model_number=number)


def _watch(category: str, spec: RequirementSpec) -> Watch:
    watch = Watch.objects.create(
        name=f"{category} watch", category=Category.objects.get(slug=category)
    )
    save_requirement(watch, spec)
    return watch


_PASSIVE_UNDER_1000 = GpuRequirementSpec(
    coolings=("passive",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
    max_unit_price_usd=Decimal("1000.00"),
)


@pytest.fixture
def matched(seeded: None, site: SourceSite) -> tuple[Watch, Listing]:
    """An A100 listing accepted at model grain and evaluated `match`."""
    listing = _listing(site, "a100")
    _edge(listing, model=_model(_A100))
    watch = _gpu_watch()
    evaluate_listing(listing.pk)
    assert [r.listing_id for r in shortlist(watch.pk)] == [listing.pk]
    return watch, listing


def _gpu_watch(spec: GpuRequirementSpec = _PASSIVE_UNDER_1000) -> Watch:
    return _watch("gpu", spec)


def _pending(watch: Watch) -> list[int]:
    return [r.listing_id for r in review_queue(watch.pk) if r.state is ReviewState.PENDING]


def _corrected_seed(model_number: str, **spec: object) -> SeedDocument:
    for doc in load_seed_documents():
        data = doc.model_dump(mode="json")
        for model in data["models"]:
            if model["model_number"] == model_number:
                model["spec"].update(spec)
                return SeedDocument.model_validate(data)
    raise AssertionError(f"{model_number} not in the seeds")


# ── Ordering, shape, freshness ───────────────────────────────────────────────


def test_shortlist_orders_by_landed_usd_ascending(seeded: None, site: SourceSite) -> None:
    # A drive watch with no product constraint matches on offer evidence alone,
    # so ordering can be exercised without resolutions.
    watch = _watch("drive", DriveRequirementSpec(max_unit_price_usd=Decimal("500.00")))
    prices = {"mid": "200.00", "high": "300.00", "low": "100.00"}
    ids = {
        key: _listing(site, key, title=f"Drive {key}", price=Decimal(p), category=None).pk
        for key, p in prices.items()
    }
    for listing_id in ids.values():
        evaluate_listing(listing_id)

    rows = shortlist(watch.pk)

    assert [r.listing_id for r in rows] == [ids["low"], ids["mid"], ids["high"]]
    assert [r.landed_usd for r in rows] == [Decimal("100.00"), Decimal("200.00"), Decimal("300.00")]


def test_landed_usd_includes_stated_shipping(seeded: None, site: SourceSite) -> None:
    watch = _watch("drive", DriveRequirementSpec(max_unit_price_usd=Decimal("500.00")))
    cheap_item = _listing(
        site,
        "ship",
        title="Drive ship",
        price=Decimal("90.00"),
        shipping=Decimal("40.00"),
        category=None,
    )
    flat = _listing(site, "flat", title="Drive flat", price=Decimal("120.00"), category=None)
    evaluate_listing(cheap_item.pk)
    evaluate_listing(flat.pk)

    assert [(r.listing_id, r.landed_usd) for r in shortlist(watch.pk)] == [
        (flat.pk, Decimal("120.00")),
        (cheap_item.pk, Decimal("130.00")),
    ]


def test_result_has_no_score_field() -> None:
    # R-MS2-10 / ADR 0022: no universal or cross-category score in MS-2.
    for row_type in (ShortlistRow, ReviewRow):
        names = [f.name for f in dataclasses.fields(row_type)]
        assert not [n for n in names if "score" in n.lower() or "rank" in n.lower()], names


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def test_no_scoring_module_is_imported_by_the_read_model() -> None:
    # AC-3 code half: the shortlist runs with no ADR-0011 artifact. Scans import
    # statements (not prose), so a docstring that mentions scoring is fine.
    files = [
        *_ELIGIBILITY_SRC.glob("*.py"),
        _COMMANDS_SRC / "show_shortlist.py",
        _COMMANDS_SRC / "evaluate_watches.py",
    ]
    assert len(files) >= 6  # the four package modules plus the two commands
    offenders = {
        f"{path.name}: {module}"
        for path in files
        for module in _imported_modules(path)
        if "scor" in module.lower()
    }
    assert not offenders


def test_freshness_is_stale_beyond_two_baseline_cadences(seeded: None, site: SourceSite) -> None:
    SourceConfig.objects.filter(source_site=site).update(
        cadence_baseline_s=3600, cadence_ceiling_s=300
    )
    watch = _watch("drive", DriveRequirementSpec())
    now = _now()
    fresh = _listing(
        site, "fresh", title="Drive fresh", category=None, observed_at=now - timedelta(hours=1)
    )
    stale = _listing(
        site, "stale", title="Drive stale", category=None, observed_at=now - timedelta(hours=3)
    )
    evaluate_listing(fresh.pk)
    evaluate_listing(stale.pk)

    by_id = {r.listing_id: r for r in shortlist(watch.pk)}

    assert by_id[fresh.pk].freshness is Freshness.FRESH
    assert by_id[stale.pk].freshness is Freshness.STALE
    assert by_id[stale.pk].observed_at == now - timedelta(hours=3)
    assert shortlist_module.STALE_CADENCE_MULTIPLE == 2


def test_source_without_config_reads_as_stale(seeded: None) -> None:
    orphan = SourceSite.objects.create(name="No Config", normalized_name="noconfig")
    watch = _watch("drive", DriveRequirementSpec())
    listing = _listing(orphan, "orphan", title="Drive orphan", category=None)
    evaluate_listing(listing.pk)

    assert [r.freshness for r in shortlist(watch.pk)] == [Freshness.STALE]


@pytest.mark.parametrize(
    ("target", "shipping", "expected"),
    [
        (None, Decimal(0), None),
        (Decimal("950.00"), Decimal(0), True),
        (Decimal("850.00"), Decimal(0), False),
        # Unstated shipping: 900 is only a lower bound, so under a 950 target
        # the soft annotation cannot be proven either way.
        (Decimal("950.00"), None, None),
    ],
)
def test_meets_target_annotates_without_changing_membership(
    seeded: None,
    site: SourceSite,
    target: Decimal | None,
    shipping: Decimal | None,
    expected: bool | None,
) -> None:
    # No hard max, so unstated shipping cannot make the verdict unknown and the
    # row stays a match whatever the soft target says (MS2-D-07).
    watch = _gpu_watch(
        GpuRequirementSpec(
            coolings=("passive",),  # pyright: ignore[reportArgumentType] - str coerces to the enum
            target_unit_price_usd=target,
        )
    )
    listing = _listing(site, "a100", shipping=shipping)
    _edge(listing, model=_model(_A100))
    evaluate_listing(listing.pk)

    rows = shortlist(watch.pk)

    assert [(r.listing_id, r.meets_target) for r in rows] == [(listing.pk, expected)]
    assert rows[0].shipping_known is (shipping is not None)


# ── Currency: only current rows qualify ──────────────────────────────────────


def _new_snapshot(listing: Listing) -> str:
    _snapshot(listing, price=Decimal("900.00"), observed_at=_now() + timedelta(minutes=5))
    return "snapshot_observed_at"


def _new_resolution(listing: Listing) -> str:
    _edge(listing, model=_model(_A100))  # same target, new edge id
    return "resolution_id"


def _requirement_edit(listing: Listing) -> str:
    watch = WatchEvaluation.objects.get(listing=listing).watch
    save_requirement(watch, _PASSIVE_UNDER_1000.model_copy(update={"min_vram_gb": 40}))
    return "requirement_version"


def _catalog_edit(_listing_: Listing) -> str:
    GpuSpec.objects.filter(product_model=_model(_A100)).update(tdp_w=301)
    return "catalog_fingerprint"


@pytest.mark.parametrize(
    "change", [_new_snapshot, _new_resolution, _requirement_edit, _catalog_edit]
)
def test_each_stale_binding_input_moves_the_row_to_pending(
    matched: tuple[Watch, Listing], change: Callable[[Listing], str]
) -> None:
    watch, listing = matched
    field_name = change(listing)

    assert shortlist(watch.pk) == []
    (row,) = review_queue(watch.pk)
    assert row.state is ReviewState.PENDING
    assert row.verdict is M  # the stored verdict is shown, but never qualifies
    assert row.stale_fields == (field_name,)


def test_evaluator_version_change_moves_the_row_to_pending(
    matched: tuple[Watch, Listing], monkeypatch: pytest.MonkeyPatch
) -> None:
    watch, listing = matched
    monkeypatch.setattr(evaluate, "EVALUATOR_VERSION", "next")

    assert shortlist(watch.pk) == []
    assert [(r.listing_id, r.stale_fields) for r in review_queue(watch.pk)] == [
        (listing.pk, ("evaluator_version",))
    ]


def test_prior_match_then_contradictory_evidence_leaves_shortlist(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    _snapshot(listing, price=Decimal("1500.00"), observed_at=_now() + timedelta(minutes=5))
    # Before re-evaluation the old `match` is pending, not shortlisted.
    assert shortlist(watch.pk) == []
    assert _pending(watch) == [listing.pk]

    evaluate_listing(listing.pk)

    assert shortlist(watch.pk) == []
    assert review_queue(watch.pk) == []  # a current no_match is in neither list
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == N


def test_evaluator_failure_after_prior_match_leaves_shortlist(
    matched: tuple[Watch, Listing], monkeypatch: pytest.MonkeyPatch
) -> None:
    watch, listing = matched
    _snapshot(listing, price=Decimal("1500.00"), observed_at=_now() + timedelta(minutes=5))

    def boom(_results: object) -> EligibilityVerdict:
        raise RuntimeError("evaluator crashed")

    monkeypatch.setattr(evaluate, "aggregate", boom)
    with pytest.raises(RuntimeError):
        evaluate_listing(listing.pk)

    assert shortlist(watch.pk) == []
    (row,) = review_queue(watch.pk)
    assert (row.state, row.verdict, row.stale_fields) == (
        ReviewState.PENDING,
        M,
        ("snapshot_observed_at",),
    )


def test_current_unknown_row_is_in_review_queue_not_shortlist(
    seeded: None, site: SourceSite
) -> None:
    # No resolution: the cooling clause has no catalog evidence, so `unknown`.
    watch = _gpu_watch()
    listing = _listing(site, "unresolved")
    evaluate_listing(listing.pk)

    assert shortlist(watch.pk) == []
    (row,) = review_queue(watch.pk)
    assert (row.state, row.verdict, row.stale_fields) == (ReviewState.UNKNOWN, U, ())


def test_same_target_catalog_correction_excludes_match_from_shortlist_immediately(
    matched: tuple[Watch, Listing],
) -> None:
    watch, listing = matched
    edge = ListingResolution.objects.get(listing=listing, is_current=True)

    # Same model, alias and target; only the spec value changes. No new
    # observation and no resolver run.
    import_documents([_corrected_seed(_A100, cooling="active")])

    assert ListingResolution.objects.get(listing=listing, is_current=True).pk == edge.pk
    assert shortlist(watch.pk) == []
    assert [(r.state, r.stale_fields) for r in review_queue(watch.pk)] == [
        (ReviewState.PENDING, ("catalog_fingerprint",))
    ]

    call_command("evaluate_watches", "--pending", stdout=StringIO())

    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == N
    assert review_queue(watch.pk) == []


# ── Live-offer filter ────────────────────────────────────────────────────────


def test_delisted_and_expired_listings_are_excluded(seeded: None, site: SourceSite) -> None:
    watch = _watch("drive", DriveRequirementSpec())
    live = _listing(site, "live", title="Drive live", category=None)
    gone = _listing(site, "gone", title="Drive gone", category=None)
    unknown_gone = _listing(site, "gone-u", title="Drive gone u", category=None)
    expiring = Listing.objects.create(
        source_site=site,
        source_listing_key="exp",
        canonical_url="https://example.test/exp",
        url_hash="exp",
        title_raw="Drive exp",
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=timezone.now() + timedelta(hours=6),
    )
    _snapshot(expiring, price=Decimal("50.00"), observed_at=_now(), category=None)
    for listing in (live, gone, unknown_gone, expiring):
        evaluate_listing(listing.pk)
    assert {r.listing_id for r in shortlist(watch.pk)} == {
        live.pk,
        gone.pk,
        unknown_gone.pk,
        expiring.pk,
    }
    # Make one row non-current first, so exclusion is proven for the review
    # queue too, not just for current matches.
    _snapshot(
        unknown_gone,
        price=Decimal("75.00"),
        observed_at=_now() + timedelta(minutes=5),
        category=None,
    )

    gone.mark_delisted(DelistReason.ABSENT_FROM_SWEEP)
    unknown_gone.mark_delisted(DelistReason.ABSENT_FROM_SWEEP)
    Listing.objects.filter(pk=expiring.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert [r.listing_id for r in shortlist(watch.pk)] == [live.pk]
    assert review_queue(watch.pk) == []


# ── The shared batched predicate ─────────────────────────────────────────────


def test_batched_binding_equals_live_binding(seeded: None, site: SourceSite) -> None:
    """service.live_inputs restates evaluate._gather in batch; this pins them
    equal across every grain shape the fingerprint distinguishes."""
    gpu = _gpu_watch()
    drive = _watch("drive", DriveRequirementSpec())
    model_grain = _listing(site, "model")
    _edge(model_grain, model=_model(_A100))
    family = _model("GeForce RTX 4090").product_family
    assert family is not None
    family_grain = _listing(site, "family", title="NVIDIA GeForce RTX")
    _edge(family_grain, family=family)
    no_edge = _listing(site, "none")
    missing_spec = _listing(site, "nospec", title="NVIDIA H100 PCIe")
    _edge(missing_spec, model=_model("H100 PCIe"))
    GpuSpec.objects.filter(product_model=_model("H100 PCIe")).delete()
    drive_row = _listing(site, "drive", title="Drive 8TB", category=None)
    listings = [model_grain, family_grain, no_edge, missing_spec, drive_row]
    for listing in listings:
        evaluate_listing(listing.pk)

    rows = list(WatchEvaluation.objects.select_related("watch", "listing"))
    assert len(rows) == len(listings)
    lives = service.live_inputs(r.listing for r in rows)
    for row in rows:
        assert service.binding_for(row.watch, lives[row.listing.pk]) == live_binding(
            row.listing, row.watch
        ), row.listing.source_listing_key
    assert {r.watch.pk for r in rows} == {gpu.pk, drive.pk}
    assert all(s.current for s in service.row_currency(rows))


def test_batched_predicate_query_count_does_not_grow_with_rows(
    seeded: None, site: SourceSite, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    watch = _gpu_watch()
    for i in range(12):
        listing = _listing(site, f"a100-{i}")
        _edge(listing, model=_model(_A100))
        evaluate_listing(listing.pk)
    rows = service.candidate_rows(watch.pk)
    assert len(rows) == 12

    # snapshots, edges, family members, one spec read per category.
    with django_assert_max_num_queries(4):
        states = service.row_currency(rows)
    assert all(s.current for s in states)


def test_malformed_hint_makes_rows_pending_not_an_error(matched: tuple[Watch, Listing]) -> None:
    watch, listing = matched
    OfferSnapshot.objects.filter(listing=listing).update(attrs_json={"category_hint": 7})

    assert shortlist(watch.pk) == []
    (row,) = review_queue(watch.pk)
    assert row.state is ReviewState.PENDING
    assert row.stale_fields == service.BINDING_FIELDS


# ── Commands ─────────────────────────────────────────────────────────────────


def test_evaluate_watches_default_mode_evaluates_a_new_watch(
    seeded: None, site: SourceSite
) -> None:
    listing = _listing(site, "a100")
    _edge(listing, model=_model(_A100))
    watch = _gpu_watch()  # created after the listing: no rows yet
    assert not WatchEvaluation.objects.filter(watch=watch).exists()

    out = StringIO()
    call_command("evaluate_watches", stdout=out)

    assert [r.listing_id for r in shortlist(watch.pk)] == [listing.pk]
    assert "evaluated: 1" in out.getvalue()


def test_evaluate_watches_reports_failures_and_continues(
    matched: tuple[Watch, Listing], site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch, listing = matched
    other = _listing(site, "a100-b")
    _edge(other, model=_model(_A100))
    real = evaluate.evaluate_listing

    def flaky(listing_id: int) -> evaluate.ListingEvaluationResult:
        if listing_id == listing.pk:
            raise RuntimeError("boom")
        return real(listing_id)

    monkeypatch.setattr(service, "evaluate_listing", flaky)
    with pytest.raises(CommandError, match=str(listing.pk)):
        call_command("evaluate_watches", stdout=StringIO())
    assert WatchEvaluation.objects.filter(watch=watch, listing=other).exists()


def test_evaluate_watches_rejects_watch_filter_without_pending(db: None) -> None:
    with pytest.raises(CommandError, match="--pending"):
        call_command("evaluate_watches", "--watch", "1", stdout=StringIO())


def test_show_shortlist_prints_matches_and_review_summary(
    matched: tuple[Watch, Listing], site: SourceSite
) -> None:
    watch, listing = matched
    unresolved = _listing(site, "unresolved")
    evaluate_listing(unresolved.pk)

    out = StringIO()
    call_command("show_shortlist", str(watch.pk), "--review", stdout=out)
    text = out.getvalue()

    assert "1 shortlisted" in text
    assert "$900.00" in text
    assert f"#{listing.pk}]" in text
    assert "review queue: 1 unknown, 0 pending" in text
    assert "score" not in text.lower()


def test_show_shortlist_unknown_watch_is_an_error(db: None) -> None:
    with pytest.raises(CommandError, match="no watch"):
        call_command("show_shortlist", "999999", stdout=StringIO())
