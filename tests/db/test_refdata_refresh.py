"""RefdataConfig settings row, ReferenceFetchRequest queue, and (later tasks)
the reconsider/discovery/refresh loop."""

from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from hw_radar.catalog.models import (
    DelistReason,
    FetchRequestStatus,
    Listing,
    ListingResolution,
    Manufacturer,
    ProductAlias,
    ProductModel,
    RefdataConfig,
    ReferenceFetchRequest,
    ResolutionGrain,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata.discovery import scan_backfill_queue
from hw_radar.refdata.refresh import run_refresh

pytestmark = pytest.mark.django_db


def test_refdata_config_current_is_a_get_or_create_singleton() -> None:
    first = RefdataConfig.current()
    second = RefdataConfig.current()
    assert first.pk == second.pk == 1
    assert first.enabled is True
    assert first.discovery_occurrence_threshold == 3  # ADR-0016 tunable default


def test_reference_fetch_request_dedupes_on_hypothesis_key() -> None:
    ReferenceFetchRequest.objects.create(
        hypothesis_key="seagate:st99000nm999",
        mpn_hypothesis="st99000nm999",
        vendor_hint="seagate",
        occurrences_at_enqueue=3,
    )
    row, created = ReferenceFetchRequest.objects.get_or_create(
        hypothesis_key="seagate:st99000nm999",
        defaults={
            "mpn_hypothesis": "st99000nm999",
            "vendor_hint": "seagate",
            "occurrences_at_enqueue": 5,
        },
    )
    assert created is False
    assert row.status == FetchRequestStatus.PENDING


@pytest.fixture
def site(db: None) -> SourceSite:
    # normalized_name is unique and migration 0005 already seeds one row with
    # normalized_name="demo" (the walking-skeleton source) — a distinct value
    # avoids colliding with that pre-existing row (not present in the brief).
    return SourceSite.objects.create(name="Demo", normalized_name="rr-demo")


def _listing(site: SourceSite, key: str, title: str) -> Listing:
    return Listing.objects.create(
        source_site=site,
        source_listing_key=key,
        canonical_url=f"https://example.test/{key}",
        url_hash=key,
        title_raw=title,
        retention_class=RetentionClass.MERCHANT_FACT,
    )


def _unknown_decoded_listings(site: SourceSite, count: int) -> None:
    """count listings decoding to the same unknown Seagate MPN hypothesis."""
    resolver = CatalogResolver()
    for i in range(count):
        listing = _listing(site, f"unk-{i}", "seagate st99000nm999 99tb sata hdd")
        resolver.resolve_listing(listing.pk)


def test_scan_enqueues_hypotheses_at_or_above_threshold(site: SourceSite) -> None:
    _unknown_decoded_listings(site, 3)  # default threshold = 3
    assert scan_backfill_queue() == 1
    request = ReferenceFetchRequest.objects.get()
    assert request.mpn_hypothesis == "st99000nm999"
    assert request.vendor_hint == "seagate"
    assert request.occurrences_at_enqueue >= 3


def test_scan_below_threshold_enqueues_nothing(site: SourceSite) -> None:
    _unknown_decoded_listings(site, 2)
    assert scan_backfill_queue() == 0


def test_scan_is_idempotent_and_skips_synthetic_keys(site: SourceSite) -> None:
    _unknown_decoded_listings(site, 3)
    _listing(site, "no-tokens", "mystery drive nothing decodable")
    CatalogResolver().resolve_listing(Listing.objects.get(source_listing_key="no-tokens").pk)
    assert scan_backfill_queue() == 1
    assert scan_backfill_queue() == 0  # dedup on hypothesis_key
    assert ReferenceFetchRequest.objects.count() == 1


def test_run_refresh_imports_reconsiders_and_scans(site: SourceSite) -> None:
    listing = _listing(site, "rr-1", "seagate exos st16000nm002c 16tb sata")
    CatalogResolver().resolve_listing(listing.pk)  # family grain before the seed
    report = run_refresh()
    assert report.ran is True
    assert report.conflicts == []
    assert report.import_report is not None
    assert report.reconsidered >= 1
    assert report.upgraded >= 1  # family → model via the seeded Exos alias
    listing.refresh_from_db()
    assert listing.resolution_grain == ResolutionGrain.MODEL
    config = RefdataConfig.current()
    assert config.last_refresh_at is not None
    assert config.last_report_json["upgraded"] >= 1  # pyright: ignore[reportOperatorIssue] - JSONField value type is object; runtime value is the int written by as_json()


def test_run_refresh_skips_delisted_listings(site: SourceSite) -> None:
    # `gone` keeps its full title (MERCHANT_FACT is never redacted), so
    # without the not_delisted() filter it would be reconsidered and
    # upgraded exactly like `live`; the assertion below therefore isolates
    # the filter, not redaction.
    live = _listing(site, "rr-live", "seagate exos st16000nm002c 16tb sata")
    gone = _listing(site, "rr-gone", "seagate exos st16000nm002c 16tb sata")
    gone.mark_delisted(DelistReason.ABSENT_FROM_SWEEP)
    report = run_refresh()
    assert report.ran is True
    assert report.reconsidered >= 1
    live.refresh_from_db()
    gone.refresh_from_db()
    assert live.resolution_grain != ResolutionGrain.NONE
    assert gone.resolution_grain == ResolutionGrain.NONE


def test_run_refresh_disabled_is_a_noop(site: SourceSite) -> None:
    config = RefdataConfig.current()
    config.enabled = False
    config.save(update_fields=["enabled"])
    report = run_refresh()
    assert report.ran is False
    assert ProductModel.objects.count() == 0


def test_run_refresh_conflicted_import_still_reconsiders(site: SourceSite) -> None:
    # Enrich-never-gate: a conflicted (rolled-back) import must not stop the
    # queue re-run against previously seeded aliases. The listing is created
    # UNRESOLVED (grain none) and never resolved before the conflicted refresh,
    # so its upgrade can ONLY come from that refresh's reconsider pass — an
    # implementation that returns early after ImportConflictError leaves it at
    # none and fails this test (Codex CR-NEW-001).
    run_refresh()  # first refresh seeds the catalog
    samsung = Manufacturer.objects.create(name="Samsung", normalized_name="samsung")
    stranger = ProductModel.objects.create(
        manufacturer=samsung,
        model_number="XYZ-1",
        normalized_model_number="xyz1",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    alias = ProductAlias.objects.get(normalized_alias_text="st16000nm002c")
    alias.pk = None
    alias.product_model = stranger
    alias.alias_type = "retail_pn"
    alias.save()  # forged collision → next import conflicts
    # Brand gate note: the forged alias targets a SAMSUNG model, so the WD
    # listing's rung-1 lookup filters it via brands_consistent — the reconsider
    # pass still sees a single viable target and can accept.
    listing = _listing(site, "rr-2", "wd ultrastar wuh721818ale6l4 18tb sata")
    assert listing.resolution_grain == ResolutionGrain.NONE  # pre-refresh: unresolved
    report = run_refresh()
    assert report.ran is True
    assert report.conflicts  # import failed into review...
    assert report.reconsidered >= 1  # ...but the queue re-run still happened
    assert report.upgraded >= 1
    listing.refresh_from_db()
    assert listing.resolution_grain in (ResolutionGrain.MODEL, ResolutionGrain.VARIANT)


def test_import_refdata_command_imports_the_seeds(db: None) -> None:
    call_command("import_refdata")
    assert ProductModel.objects.count() == 15


def test_scan_skips_overlength_hypotheses(site: SourceSite) -> None:
    _unknown_decoded_listings(site, 3)  # normal decode, at threshold
    for edge in ListingResolution.objects.filter(is_current=True):
        evidence = dict(edge.evidence)
        evidence["mpn_hypothesis"] = "x" * 250  # exceeds the 200-char column cap
        edge.evidence = evidence
        edge.save(update_fields=["evidence"])
    assert scan_backfill_queue() == 0
    assert ReferenceFetchRequest.objects.count() == 0


def test_run_refresh_survives_discovery_failure(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> int:
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr("hw_radar.refdata.refresh.scan_backfill_queue", _boom)
    report = run_refresh()
    assert report.ran is True
    assert report.errors >= 1
    assert report.discovery_enqueued == 0
    config = RefdataConfig.current()
    assert config.last_refresh_at is not None


def test_import_refdata_command_no_seed_dir_raises_command_error(tmp_path: Path) -> None:
    with pytest.raises(CommandError):
        call_command("import_refdata", "--seed-dir", str(tmp_path))


def test_import_refdata_command_fails_loudly_on_conflicts(db: None) -> None:
    samsung = Manufacturer.objects.create(name="Samsung", normalized_name="samsung")
    stranger = ProductModel.objects.create(
        manufacturer=samsung,
        model_number="XYZ-2",
        normalized_model_number="xyz2",
        retention_class=RetentionClass.MANUFACTURER_REFERENCE,
    )
    ProductAlias.objects.create(
        alias_type="mpn",
        normalized_alias_text="st16000nm002c",
        product_model=stranger,
        source_kind="listing_derived",
    )
    with pytest.raises(CommandError):
        call_command("import_refdata")
