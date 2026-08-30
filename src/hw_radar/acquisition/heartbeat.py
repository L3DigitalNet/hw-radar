"""ADR-0015 heartbeat: cheap variant/SKU-grain availability fingerprinting.

Pure logic here (fingerprint + decide) is table-tested with no DB. The DB-facing
run_heartbeat (writes observations, dual-writes events, fires the full pipeline
on transition) lands in the poller task (D1)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from asgiref.sync import sync_to_async
from django.utils import timezone

from hw_radar.acquisition.classify import classify_exception
from hw_radar.acquisition.contracts import ListingResolver, SourceAdapter, adapter_retention

# _EVENT_BY_CLASS is the single source of truth for the failure-class -> lifecycle
# event mapping; a heartbeat probe that raises must back the source off exactly as
# a full run does, so we reuse pipeline's map rather than duplicate it.
from hw_radar.acquisition.pipeline import (
    _EVENT_BY_CLASS,  # pyright: ignore[reportPrivateUsage]
    run_source,
)
from hw_radar.acquisition.scheduling.apply import RunOutcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import (
    AvailabilityHeartbeatEvent,
    AvailabilityHeartbeatObservation,
    CheapSignal,
    HeartbeatDecision,
    RetentionClass,
    RunKind,
    SourceConfig,
    SourceSite,
)

logger = logging.getLogger(__name__)

_UNKNOWN_STOCK = {"", "unknown"}


@dataclass(frozen=True)
class HeartbeatReading:
    source_sku: str
    price: Decimal | None
    currency: str
    stock_status: str
    shipping_price: Decimal | None
    http_status: int
    latency_ms: int | None
    endpoint: str


def fingerprint(
    *, price: Decimal | None, currency: str, stock_status: str, shipping: Decimal | None
) -> str:
    basis = f"{price}|{currency}|{stock_status}|{shipping}"
    return hashlib.sha256(basis.encode()).hexdigest()


def _fp(r: HeartbeatReading) -> str:
    return fingerprint(
        price=r.price, currency=r.currency, stock_status=r.stock_status, shipping=r.shipping_price
    )


def decide(
    prev: HeartbeatReading | None, new: HeartbeatReading | None, *, price_drop_pct: float = 0.0
) -> HeartbeatDecision:
    if new is None:
        return HeartbeatDecision.FAILED
    if new.stock_status in _UNKNOWN_STOCK:
        return HeartbeatDecision.AMBIGUOUS
    if prev is None:
        return HeartbeatDecision.TRANSITION_DETECTED  # baseline sighting fires the pipeline once
    if _fp(prev) == _fp(new):
        return HeartbeatDecision.UNCHANGED
    if prev.stock_status != new.stock_status:
        return HeartbeatDecision.TRANSITION_DETECTED
    if prev.price is not None and new.price is not None and new.price < prev.price:
        drop = float((prev.price - new.price) / prev.price)
        if drop >= price_drop_pct:  # default 0.0 ⇒ any drop is material
            return HeartbeatDecision.TRANSITION_DETECTED
    return HeartbeatDecision.UNCHANGED  # price rose / non-material change ⇒ no snapshot


class HeartbeatProbe(SourceAdapter, Protocol):
    """A full SourceAdapter that ALSO exposes a cheap availability probe.
    heartbeat_enabled sources implement this: the probe is the fast lane, and the
    inherited fetch/parse are what run_heartbeat replays as a FULL run on a hit."""

    async def probe(self) -> list[HeartbeatReading]: ...


@dataclass(frozen=True)
class HeartbeatRetention:
    """DR-001 retention for the two heartbeat tables. eBay uses one bounded class
    for both; every other source splits observation (30d) from event (365d)."""

    observation_class: RetentionClass
    observation_ttl: timedelta
    event_class: RetentionClass
    event_ttl: timedelta


def heartbeat_retention_for(config: SourceConfig) -> HeartbeatRetention:
    """CR-NEW-001: the ADR-0015 rule-6 eBay carve-out caps BOTH heartbeat tables
    at the 6h source-restricted class, so no eBay heartbeat row ever lands on the
    365-day path. Detected by cheap_signal, not a hardcoded site name."""
    # .value, not the member: django-types stubs a TextChoices member as its
    # (value, label) tuple, so a bare `== CheapSignal.EBAY_BROWSE` is a type error.
    if config.cheap_signal == CheapSignal.EBAY_BROWSE.value:
        return HeartbeatRetention(
            RetentionClass.EBAY_LISTING_OBSERVATION,
            timedelta(hours=6),
            RetentionClass.EBAY_LISTING_OBSERVATION,
            timedelta(hours=6),
        )
    return HeartbeatRetention(
        RetentionClass.AVAILABILITY_HEARTBEAT,
        timedelta(days=30),
        RetentionClass.AVAILABILITY_HEARTBEAT_EVENT,
        timedelta(days=365),
    )


def _reading_from_observation(obs: AvailabilityHeartbeatObservation) -> HeartbeatReading:
    return HeartbeatReading(
        source_sku=obs.source_sku,
        price=obs.price,
        currency=obs.currency,
        stock_status=obs.stock_status,
        shipping_price=obs.shipping_price,
        http_status=obs.http_status,
        latency_ms=obs.latency_ms,
        endpoint=obs.endpoint,
    )


def _record_heartbeat(
    site_key: str, config: SourceConfig, readings: list[HeartbeatReading]
) -> bool:
    """Sync ORM body (wrapped by run_heartbeat's sync_to_async). Writes one
    observation per reading, dual-writes an event for every non-`unchanged`
    decision, and returns whether ANY reading warrants firing the full pipeline
    (transition_detected or ambiguous). Runs on a worker thread, so plain ORM."""
    site = SourceSite.objects.get(normalized_name=site_key)
    retention = heartbeat_retention_for(config)
    should_fire = False
    for reading in readings:
        prev_obs = (
            AvailabilityHeartbeatObservation.objects.filter(
                source_site=site, source_sku=reading.source_sku
            )
            .order_by("-observed_at")
            .first()
        )
        prev = _reading_from_observation(prev_obs) if prev_obs is not None else None
        decision = decide(prev, reading)
        observed_at = timezone.now()
        AvailabilityHeartbeatObservation.objects.create(
            source_site=site,
            source_sku=reading.source_sku,
            observed_at=observed_at,
            decision=decision,
            price=reading.price,
            currency=reading.currency,
            stock_status=reading.stock_status,
            shipping_price=reading.shipping_price,
            fingerprint=_fp(reading),
            http_status=reading.http_status,
            latency_ms=reading.latency_ms,
            endpoint=reading.endpoint,
            retention_class=retention.observation_class,
            expires_at=observed_at + retention.observation_ttl,
        )
        if decision is not HeartbeatDecision.UNCHANGED:
            AvailabilityHeartbeatEvent.objects.create(
                source_site=site,
                source_sku=reading.source_sku,
                observed_at=observed_at,
                decision=decision,
                prev_fingerprint=_fp(prev) if prev is not None else "",
                fingerprint=_fp(reading),
                detail_json={
                    "prev_stock": prev.stock_status if prev is not None else None,
                    "new_stock": reading.stock_status,
                    "prev_price": str(prev.price)
                    if prev is not None and prev.price is not None
                    else None,
                    "new_price": str(reading.price) if reading.price is not None else None,
                },
                retention_class=retention.event_class,
                expires_at=observed_at + retention.event_ttl,
            )
        if decision in (HeartbeatDecision.TRANSITION_DETECTED, HeartbeatDecision.AMBIGUOUS):
            should_fire = True
    return should_fire


async def run_heartbeat(
    adapter: HeartbeatProbe, config: SourceConfig, resolver: ListingResolver
) -> RunOutcome:
    """ADR-0015 DB-facing heartbeat: probe -> per-SKU decide + observation/event
    writes -> fire the FULL pipeline ONCE iff any SKU transitioned/was ambiguous.

    The cheap probe IS the heartbeat; the fired run is RunKind.FULL so it persists
    offer_snapshots (the confirmation criterion: no fire, no snapshot). The fired
    run inherits the adapter's own retention (eBay -> ebay_listing_observation/6h;
    everyone else -> merchant_fact/indefinite). Returns a RunOutcome so the poller
    applies lifecycle/auto-ramp exactly as it does for poll_source."""
    try:
        readings = await adapter.probe()
    except Exception as exc:  # a failed probe backs the source off like a full run
        logger.exception("heartbeat probe failed for %s", adapter.site_key)
        return RunOutcome(_EVENT_BY_CLASS[classify_exception(exc)])
    should_fire = await sync_to_async(_record_heartbeat)(adapter.site_key, config, readings)
    if not should_fire:
        # A clean probe with no transition is still a successful poll: it feeds
        # auto-ramp so a stable source widens its cadence over time.
        return RunOutcome(LifecycleEvent.SUCCESS)
    retention = adapter_retention(adapter)
    _run, outcome = await run_source(
        adapter,
        resolver,
        retention_class=retention.retention_class,
        expires_policy=retention.expires_policy,
        run_kind=RunKind.FULL,
    )
    return outcome
