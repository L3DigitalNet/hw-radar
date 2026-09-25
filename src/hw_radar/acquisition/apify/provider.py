"""ApifyImportProvider: a terminal self-owned Actor run as a CollectionProvider (plan D4).

The provider turns one `ProviderRun` whose remote run has terminated into the
pipeline's inputs, so an Actor sweep enters ingestion through the same
CollectionProvider seam as a local adapter (ADR 0021, MS2-D-10):

- fetch reads the run's default dataset into a RawBatch of dataset rows only,
  with `fetched_at` = the run's `startedAt` (MS2-D-13: deterministic, and never
  overstates freshness). The default-KV `OUTPUT` record is read alongside and
  stored on the provider_run (`run_output`, MS2-D-14), never as a RawItem, so a
  proven-empty sweep is an empty batch that passes the zero-record guard. The
  run is classified here, once, by classify_run, and the classification is
  written to the provider_run.
- parse admits each row through import_row: contract-valid, the run's own site
  and admitted scope, and within the serialized-row byte cap.
- delist_scope returns a complete scope, keyed by the run's admitted
  `scope_key`, only for a `complete` classification (complete-empty included).
- run_evidence is the classification as ProviderRunEvidence, and is never
  stale-absence eligible: a remote truncation says nothing about absence
  (MS2-D-11).
- retention is source_retention(site_key) (MS2-D-25), resolved at construction,
  so an unregistered site fails before any paid read.

Usable count and parse share one admission function. classify_run's
`usable_count` is the number of rows parse will actually return, so a row the
importer drops (another site, another scope, over the cap, not ingestible)
makes the run a partial failure. Counting contract validity alone would let a
run that dropped a row still classify `complete` and delist the very listing
that row described.

SCOPE: the provider performs reads and one provider_run update (the OUTPUT and
the classification). It does not create the ScraperRun link, count reads
against the MS2-D-32 caps, advance `import_state`, reject a failed run before
persistence, or clean up storage: those belong to the durable staged importer
(D10), the jobs (D5), and cleanup (D11), which wrap this provider.

Requirements: a Django context with the catalog app loaded (the constructor
reads `provider_run.source_site`, so construct it outside the event loop), and
`settings.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES`, from which the page size is
derived.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, cast

from asgiref.sync import sync_to_async
from django.conf import settings
from pydantic import ValidationError

from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.contract import (
    MAX_LISTING_ROW_BYTES,
    TERMINAL_RUN_STATUSES,
    AdmittedRun,
    ContractViolation,
    ListingRow,
    QueryScope,
    RunClassification,
    RunOutput,
    classify_run,
    page_limit,
)
from hw_radar.acquisition.contracts import (
    AdapterRetention,
    DelistScope,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    RawItem,
)
from hw_radar.acquisition.retention_policy import source_retention
from hw_radar.catalog.models import ProviderKind, ProviderRun, RunCompleteness, RunKind

OUTPUT_RECORD_KEY: Final = "OUTPUT"
IMPORT_PROVIDER_KEY: Final = "apify"

# A complete remote sweep delists directly (ABSENT_FROM_SWEEP), which never
# reads the grace; and a remote run is never stale-absence eligible, so no path
# downgrades this scope to the grace-reading stale branch (MS2-D-11 gate).
_NO_ABSENCE_GRACE: Final = timedelta(0)


class ApifyImportError(RuntimeError):
    """The provider_run is not in a state an import can start from (a caller bug)."""


class RowRejection(StrEnum):
    """Why import_row refused a dataset row; each refusal counts as unusable."""

    OVER_BYTE_CAP = "over_byte_cap"
    CONTRACT_INVALID = "contract_invalid"
    SITE_KEY_MISMATCH = "site_key_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    NOT_INGESTIBLE = "not_ingestible"


def import_row(
    raw: object, *, site_key: str, scope_key: str, max_item_bytes: int
) -> ParsedListing | RowRejection:
    """Admit one raw dataset row as a ParsedListing, or say why it is refused.

    Never raises for remote content: every refusal is a RowRejection, so one
    bad row cannot fail the whole run as PARSER_ROT. Checks run cheapest and
    most defensive first: the byte cap on the compact ASCII serialization
    (which a contract-valid row can never exceed, MS2-D-32), then the
    hw-radar-listing/v1 contract, then the run's own site and admitted scope,
    then the ParsedListing invariants the contract does not carry (price > 0).
    """
    if not isinstance(raw, Mapping):
        return RowRejection.CONTRACT_INVALID
    try:
        size = len(json.dumps(raw, ensure_ascii=True, separators=(",", ":")))
    except TypeError, ValueError:
        return RowRejection.CONTRACT_INVALID
    if size > max_item_bytes:
        return RowRejection.OVER_BYTE_CAP
    try:
        row = ListingRow.model_validate(raw)
    except ValidationError:
        return RowRejection.CONTRACT_INVALID
    # A row for another site would write listings under the wrong SourceSite
    # identity; a row for another scope would move a listing into a scope this
    # run was never admitted for, where that scope's complete sweeps could then
    # delist it (MS2-D-12, -31: delist reads the admitted scope, not the Actor's).
    if row.site_key != site_key:
        return RowRejection.SITE_KEY_MISMATCH
    if row.collection_scope != scope_key:
        return RowRejection.SCOPE_MISMATCH
    fields: dict[str, object] = {
        "source_listing_key": row.source_listing_key,
        "url": row.url,
        "title": row.title,
        "price": Decimal(row.price),
        "currency": row.currency,
        "shipping_price": None if row.shipping_price is None else Decimal(row.shipping_price),
        "stock_status": row.stock_status,
        "quantity_available": row.quantity_available,
        "seller_name": row.seller_name or "",
        "condition_label": row.condition_label or "",
        "attrs": {} if row.mpn is None else {"mpn": row.mpn},
        "category_hint": row.category_hint,
        "collection_scope": row.collection_scope,
    }
    # An absent shipsFromCountry keeps ParsedListing's own default, exactly as a
    # local adapter that does not report the country does.
    if row.ships_from_country is not None:
        fields["ships_from_country"] = row.ships_from_country
    try:
        return ParsedListing.model_validate(fields)
    except ValidationError:
        return RowRejection.NOT_INGESTIBLE


def _raw_item(dataset_id: str, index: int, row: object) -> RawItem:
    # The provenance URL names the dataset item, not the merchant page: the row
    # is Actor output, and RawPayload.endpoint must say where the bytes came from.
    url = f"apify-dataset:{dataset_id}/{index}"
    if isinstance(row, Mapping):
        return RawItem(url=url, payload_json=dict(cast(Mapping[str, object], row)))
    return RawItem(url=url, payload_json=None, payload_text=json.dumps(row))


def _row_of(item: RawItem) -> object:
    if item.payload_json is not None:
        return item.payload_json
    return json.loads(item.payload_text or "null")


def _decode_output(body: bytes | None) -> tuple[object, dict[str, object] | None]:
    """Return (value to classify, record to store) for the OUTPUT record body.

    An absent record classifies as missing (None). An undecodable body is
    classified as a non-object, so classify_run reports invalid_output rather
    than missing_output. Only a contract-valid record is returned for storage.
    """
    if body is None:
        return None, None
    try:
        value: object = json.loads(body)
    except UnicodeDecodeError, ValueError:
        return body, None
    try:
        stored = RunOutput.model_validate(value).model_dump(mode="json")
    except ValidationError:
        stored = None
    return value, stored


class ApifyImportProvider:
    """CollectionProvider over one terminal Actor run (plan D4; module docstring)."""

    provider_key = IMPORT_PROVIDER_KEY
    # Declared for the CollectionProvider protocol only: dataset rows are never
    # run through the HTTP soft-block classifier (MS2-D-28).
    expects_json = True

    def __init__(self, provider_run: ProviderRun, *, client: ApifyClient, actor_name: str) -> None:
        """Bind the provider to one admitted run.

        actor_name is the Actor the run was admitted for; classify_run fails the
        run as scope_mismatch when OUTPUT names another. Raises
        UnknownSourceRetention for an unregistered site and ContractViolation
        when the stored admission (query_scope) is not a valid QueryScope for
        this row's own site and scope_key.
        """
        self.provider_kind = ProviderKind.APIFY
        self.site_key: str = provider_run.source_site.normalized_name
        self.run_kind = RunKind(provider_run.run_kind)
        self.retention: AdapterRetention = source_retention(self.site_key)
        try:
            scope = QueryScope.model_validate(provider_run.query_scope)
        except ValidationError as exc:
            raise ContractViolation(f"provider_run.query_scope is not a QueryScope: {exc}") from exc
        if scope.site_key != self.site_key or scope.collection_scope != provider_run.scope_key:
            raise ContractViolation(
                "provider_run.query_scope disagrees with the row's site or scope_key"
            )
        self._run = provider_run
        self._client = client
        self._admitted = AdmittedRun(query_scope=scope, actor_name=actor_name)
        self.page_limit = page_limit(
            settings.HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES, MAX_LISTING_ROW_BYTES
        )
        self._classification: RunClassification | None = None

    def _admit(self, raw: object) -> ParsedListing | RowRejection:
        return import_row(
            raw,
            site_key=self.site_key,
            scope_key=self._run.scope_key,
            max_item_bytes=MAX_LISTING_ROW_BYTES,
        )

    async def fetch(self) -> RawBatch:
        """Read the dataset and OUTPUT, classify the run, and store both on provider_run.

        Raises ApifyImportError when the run has not terminated or lacks the
        ids or startedAt an import needs; client errors propagate unchanged.
        """
        run = self._run
        status = run.remote_status
        if status not in TERMINAL_RUN_STATUSES:
            raise ApifyImportError(f"provider_run {run.pk} is not terminal: {status!r}")
        if run.started_at is None or not run.dataset_id or not run.kv_store_id:
            raise ApifyImportError(f"provider_run {run.pk} lacks startedAt or storage ids")
        started_at: datetime = run.started_at
        rows = [
            row
            async for row in self._client.iter_dataset_items(
                run.dataset_id, page_size=self.page_limit
            )
        ]
        record = await self._client.get_record(run.kv_store_id, OUTPUT_RECORD_KEY)
        output, stored = _decode_output(None if record is None else record.body)
        usable = sum(isinstance(self._admit(row), ParsedListing) for row in rows)
        classification = classify_run(status, output, len(rows), usable, admitted=self._admitted)
        run.run_output = stored
        run.dataset_item_count = len(rows)
        run.completeness = classification.completeness
        run.completeness_reason = classification.reason
        run.truncation_reason = classification.truncation_reason or ""
        await sync_to_async(run.save)(
            update_fields=[
                "run_output",
                "dataset_item_count",
                "completeness",
                "completeness_reason",
                "truncation_reason",
            ]
        )
        self._classification = classification
        return RawBatch(
            source=f"{IMPORT_PROVIDER_KEY}:{self.site_key}",
            fetched_at=started_at,
            items=[_raw_item(run.dataset_id, index, row) for index, row in enumerate(rows)],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        parsed: list[ParsedListing] = []
        for item in batch.items:
            verdict = self._admit(_row_of(item))
            if isinstance(verdict, ParsedListing):
                # Links the snapshot to the RawPayload stored for this row.
                parsed.append(verdict.model_copy(update={"raw_url": item.url}))
        return parsed

    def _classified(self) -> RunClassification:
        if self._classification is None:
            raise ApifyImportError("fetch() must run before the run can be judged")
        return self._classification

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        if self._classified().completeness is not RunCompleteness.COMPLETE:
            return None
        return DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=True,
            absence_grace=_NO_ABSENCE_GRACE,
            scope_key=self._run.scope_key,
        )

    def run_evidence(
        self,
        batch: RawBatch,
        parsed: list[ParsedListing],
        scope: DelistScope | None,
        *,
        run_kind: RunKind,
    ) -> ProviderRunEvidence:
        classification = self._classified()
        return ProviderRunEvidence(
            provider_kind=self.provider_kind,
            provider_key=self.provider_key,
            completeness=classification.completeness,
            completeness_reason=classification.reason,
            stale_absence_eligible=False,
            truncation_reason=classification.truncation_reason,
        )
