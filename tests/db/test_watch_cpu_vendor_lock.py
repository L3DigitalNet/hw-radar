"""`CpuRequirement.require_vendor_unlocked` end to end: requirement service
persistence and version bump, the persisted `cpu.vendor_lock` verdicts, and the
read model (an unstated lock is never shortlisted).

The listing is accepted as EPYC 7763 through a MANUAL model-grain edge, so
every catalog clause matches and the lock clause alone decides the verdict.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import persist
from hw_radar.acquisition.contracts import NormalizedListing
from hw_radar.catalog.models import (
    Category,
    CpuRequirement,
    EligibilityVerdict,
    Listing,
    ListingResolution,
    ProductModel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility.evaluate import evaluate_listing, is_current
from hw_radar.eligibility.requirements import CpuRequirementSpec, save_requirement
from hw_radar.eligibility.shortlist import ReviewState, review_queue, shortlist
from hw_radar.matching import MATCHER_VERSION
from hw_radar.refdata.loader import load_seed_documents
from hw_radar.refdata.persist import import_documents

pytestmark = pytest.mark.django_db

M, N, U = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH, EligibilityVerdict.UNKNOWN
_OBSERVED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_BASE = "AMD EPYC 7763 64-Core 2.45GHz 280W SP3 CPU 100-000000312"


@pytest.fixture
def seeded(db: None) -> None:
    import_documents([d for d in load_seed_documents() if d.category == "cpu"])


@pytest.fixture
def site(db: None) -> SourceSite:
    return SourceSite.objects.create(name="Demo Market", normalized_name="demomarket")


def _observe(listing: Listing, observed_at: datetime) -> None:
    persist.append_snapshot(
        listing,
        NormalizedListing(
            source_listing_key=listing.source_listing_key,
            url=listing.canonical_url,
            title=listing.title_raw,
            price=Decimal("1999.00"),
            shipping_price=Decimal(0),
            stock_status="in_stock",
            fx_rate=Decimal(1),
            fx_pair="USD/USD",
            fx_rate_date=date(2026, 9, 26),
            fx_source="identity",
            is_international=False,
            category_hint="cpu",
        ),
        observed_at=observed_at,
    )


def _accepted_listing(site: SourceSite, key: str, title: str) -> Listing:
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )
    _observe(listing, _OBSERVED_AT)
    model = ProductModel.objects.get(model_number="EPYC 7763")
    ListingResolution.objects.create(
        listing=listing,
        grain=ResolutionGrain.MODEL,
        product_model=model,
        method=ResolutionMethod.MANUAL,
        confidence=1.0,
        matcher_version=MATCHER_VERSION,
        evidence={"outcome": "accept", "category": "test"},
    )
    Listing.objects.filter(pk=listing.pk).update(
        resolution_grain=ResolutionGrain.MODEL, product_model=model
    )
    return listing


def _watch(spec: CpuRequirementSpec) -> Watch:
    watch = Watch.objects.create(name="epyc", category=Category.objects.get(slug="cpu"))
    save_requirement(watch, spec)
    return watch


def _lock_reason(watch: Watch, listing: Listing) -> dict[str, object]:
    row = WatchEvaluation.objects.get(watch=watch, listing=listing)
    return next(r for r in row.reasons if r["clause"] == "cpu.vendor_lock")


@pytest.mark.parametrize(
    ("suffix", "required", "expected"),
    [
        (" *UNLOCKED*", True, M),
        (" Dell Locked", True, N),
        ("", True, U),
        (" Pulled from Cisco UCS", True, U),
        (" *UNLOCKED*", False, M),
        (" Dell Locked", False, M),
        ("", False, M),
    ],
)
def test_vendor_lock_verdict_matrix(
    seeded: None, site: SourceSite, suffix: str, required: bool, expected: EligibilityVerdict
) -> None:
    listing = _accepted_listing(site, f"lock-{required}-{suffix.strip()}", _BASE + suffix)
    watch = _watch(CpuRequirementSpec(sockets=("SP3",), require_vendor_unlocked=required))

    result = evaluate_listing(listing.pk)

    assert result.verdicts == {watch.pk: expected}
    assert _lock_reason(watch, listing)["outcome"] == expected.value


def test_unknown_lock_is_never_shortlisted(seeded: None, site: SourceSite) -> None:
    unknown = _accepted_listing(site, "unknown", _BASE)
    unlocked = _accepted_listing(site, "unlocked", _BASE + " Unlocked")
    watch = _watch(CpuRequirementSpec(require_vendor_unlocked=True))
    evaluate_listing(unknown.pk)
    evaluate_listing(unlocked.pk)

    assert [r.listing_id for r in shortlist(watch.pk)] == [unlocked.pk]
    queue = review_queue(watch.pk)
    assert [(r.listing_id, r.state) for r in queue] == [(unknown.pk, ReviewState.UNKNOWN)]


def test_title_turning_locked_does_not_keep_the_match(seeded: None, site: SourceSite) -> None:
    """The stale-decision class: a stored `match` must not survive a later
    observation that states the unit is locked. The new snapshot moves the
    binding, so the old row reads non-current until re-evaluated to no_match."""
    listing = _accepted_listing(site, "turns-locked", _BASE + " Unlocked")
    watch = _watch(CpuRequirementSpec(require_vendor_unlocked=True))
    evaluate_listing(listing.pk)
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == M.value

    Listing.objects.filter(pk=listing.pk).update(title_raw=_BASE + " Dell Locked")
    listing.refresh_from_db()
    _observe(listing, _OBSERVED_AT + timedelta(hours=1))
    stale = WatchEvaluation.objects.get(watch=watch, listing=listing)
    assert not is_current(stale)
    assert shortlist(watch.pk) == []

    evaluate_listing(listing.pk)
    assert WatchEvaluation.objects.get(watch=watch, listing=listing).verdict == N.value


def test_requirement_is_persisted_and_bumps_the_version(seeded: None) -> None:
    watch = _watch(CpuRequirementSpec(min_cores=32))
    before = watch.requirement_version
    assert CpuRequirement.objects.get(watch=watch).require_vendor_unlocked is False

    flipped = save_requirement(
        watch, CpuRequirementSpec(min_cores=32, require_vendor_unlocked=True)
    )
    assert flipped.bumped and flipped.requirement_version == before + 1
    assert CpuRequirement.objects.get(watch=watch).require_vendor_unlocked is True

    again = save_requirement(watch, CpuRequirementSpec(min_cores=32, require_vendor_unlocked=True))
    assert not again.changed and not again.bumped


def test_satellite_default_is_no_constraint(db: None) -> None:
    # A row written without the column (as every pre-0024 row was) reads False.
    watch = Watch.objects.create(name="legacy", category=Category.objects.get(slug="cpu"))
    row = CpuRequirement.objects.create(watch=watch, sockets=["sp3"])
    row.refresh_from_db()
    assert row.require_vendor_unlocked is False
