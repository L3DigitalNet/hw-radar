from django.db import models


class RetentionClass(models.TextChoices):
    """DR-001 retention classes."""

    MERCHANT_FACT = "merchant_fact", "Merchant fact (indefinite)"
    EBAY_LISTING_OBSERVATION = "ebay_listing_observation", "eBay observation"
    AMAZON_EPHEMERAL = "amazon_ephemeral", "Amazon ephemeral"
    AMAZON_IDENTIFIER = "amazon_identifier", "Amazon identifier (indefinite)"
    TRANSIENT_DISCOVERY = "transient_discovery", "Search-provider discovery"
    TAVILY_EXTRACT = "tavily_extract", "Tavily-extracted fact (indefinite)"
    MANUFACTURER_REFERENCE = "manufacturer_reference", "Manufacturer reference"
    AVAILABILITY_HEARTBEAT = "availability_heartbeat", "Availability heartbeat (30d)"
    AVAILABILITY_HEARTBEAT_EVENT = "availability_heartbeat_event", "Heartbeat event (365d)"
    # Provenance semantics (owner-ratified 2026-09-06, OQ22 option (a)) — this
    # class exists to keep learned aliases distinguishable from facts:
    #   * The alias token was extracted from a merchant listing (often an eBay
    #     title). It is an INFERENCE by matching.resolver._emit_learned_aliases,
    #     not a manufacturer or merchant assertion, so it must never be promoted
    #     to MANUFACTURER_REFERENCE without independent corroboration — the
    #     refdata seed path (refdata/persist._import_alias) is the only writer
    #     allowed to make that promotion, and it does so from a first-party
    #     datasheet.
    #   * Indefinite (expires_at NULL) because ADR-0019 rule 7's review queue
    #     shrinks only if learned aliases persist: a bounded class would let the
    #     hourly purge_expired sweep delete them and re-open every resolution the
    #     alias had already settled.
    #   * NOT subject to DR-008's six-hour eBay deletion duty. The row stores no
    #     listing content — only a normalized identifier token and the model it
    #     points at — so it is not an eBay observation record. That reading is
    #     the owner's ratified position, recorded in docs/resolved-questions.md
    #     under OQ22; do not narrow it silently.
    LISTING_DERIVED_ALIAS = "listing_derived_alias", "Listing-derived alias (indefinite)"


INDEFINITE_RETENTION_CLASSES: tuple[RetentionClass, ...] = (
    RetentionClass.MERCHANT_FACT,
    RetentionClass.AMAZON_IDENTIFIER,
    RetentionClass.TAVILY_EXTRACT,
    RetentionClass.MANUFACTURER_REFERENCE,
    RetentionClass.LISTING_DERIVED_ALIAS,
)
BOUNDED_RETENTION_CLASSES: tuple[RetentionClass, ...] = (
    RetentionClass.EBAY_LISTING_OBSERVATION,
    RetentionClass.AMAZON_EPHEMERAL,
    RetentionClass.TRANSIENT_DISCOVERY,
    RetentionClass.AVAILABILITY_HEARTBEAT,
    RetentionClass.AVAILABILITY_HEARTBEAT_EVENT,
)


def retention_constraints(prefix: str) -> list[models.CheckConstraint]:
    """Return DR-001 constraints for concrete evidence models."""

    return [
        models.CheckConstraint(
            condition=~models.Q(retention_class=""),
            name=f"{prefix}_retention_class_set",
        ),
        models.CheckConstraint(
            condition=(
                models.Q(
                    retention_class__in=[c.value for c in INDEFINITE_RETENTION_CLASSES],
                    expires_at__isnull=True,
                )
                | models.Q(
                    retention_class__in=[c.value for c in BOUNDED_RETENTION_CLASSES],
                    expires_at__isnull=False,
                )
            ),
            name=f"{prefix}_retention_ttl_coherent",
        ),
    ]


def retention_indexes(name: str) -> list[models.Index]:
    """Return the partial index that keeps the retention sweep an index scan.

    The predicate mirrors purge_expired._expired()'s WHERE shape: only rows
    with a non-NULL expires_at are eligible for deletion. Bounded retention
    classes always set expires_at (enforced by _retention_ttl_coherent above);
    indefinite classes leave it NULL and are excluded from the index, so the
    index stays small relative to the mostly-NULL evidence tables it covers.
    The name is passed explicitly because Django truncates/hashes index names
    past 30 characters, and the DR-001 constraint prefixes used above already
    exceed that budget.
    """

    return [
        models.Index(
            fields=["expires_at"],
            condition=models.Q(expires_at__isnull=False),
            name=name,
        ),
    ]


class RetentionGoverned(models.Model):
    """DR-001 retention governance fields."""

    retention_class = models.CharField(max_length=40, choices=RetentionClass.choices, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ResolutionGrain(models.TextChoices):
    """C.3.3 resolution grains. String values are shared verbatim with
    matching.types.Grain — the resolver maps between them by value."""

    NONE = "none", "Unresolved"
    FAMILY = "family", "Product family"
    MODEL = "model", "Product model"
    VARIANT = "variant", "Product variant"
