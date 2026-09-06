"""DR-001 CHECK pair on the three identity tables, plus the OQ22 retention class.

Adding `RetentionClass.LISTING_DERIVED_ALIAS` to the indefinite set rewrites the
predicate `retention_constraints()` bakes into every `*_retention_ttl_coherent`
CHECK, so the drop/recreate pairs below on the already-constrained evidence
tables are mechanical, not a policy change: the new class is simply admitted to
the "expires_at IS NULL" branch everywhere.

The new work is on `product_model`, `drive_spec` and `product_alias`, which until
now carried only the partial `expires_at` index (0016). Ordering is load-bearing:
`backfill_identity_retention_class` must run BEFORE the AddConstraint ops, since
any pre-existing row with an empty `retention_class` would otherwise abort the
migration when Postgres validates `*_retention_class_set` against the whole
table.
"""

from django.db import migrations, models

# Rows written before the DR-001 stamp existed on these tables. The classes are
# derived from provenance, not guessed: `refdata.persist` is the only writer of
# product_model/drive_spec rows and always seeds from a first-party datasheet
# (DR-009 → manufacturer_reference), while `matching.resolver.
# _emit_learned_aliases` is the only writer that ever left the class empty and
# always sets source_kind=listing_derived (OQ22 → listing_derived_alias). A
# product_alias row with an empty class and any other source_kind cannot be
# produced by either writer; it is mapped to manufacturer_reference so the
# migration cannot fail on an unclassifiable row, and both targets are
# indefinite, so no row acquires a TTL it did not have.
_LISTING_DERIVED = "listing_derived"
_LEARNED_ALIAS_CLASS = "listing_derived_alias"
_SEED_CLASS = "manufacturer_reference"


def backfill_identity_retention_class(apps, schema_editor):
    """Stamp a retention class on identity rows written before the CHECK existed.

    Idempotent: every UPDATE is filtered on `retention_class=""`, so a re-run
    matches nothing. `expires_at` is cleared in the same statement because both
    target classes are indefinite and `*_retention_ttl_coherent` rejects an
    indefinite row that carries an expiry.
    """
    ProductModel = apps.get_model("catalog", "ProductModel")
    DriveSpec = apps.get_model("catalog", "DriveSpec")
    ProductAlias = apps.get_model("catalog", "ProductAlias")
    for model in (ProductModel, DriveSpec):
        model.objects.filter(retention_class="").update(
            retention_class=_SEED_CLASS, expires_at=None
        )
    unclassed = ProductAlias.objects.filter(retention_class="")
    unclassed.filter(source_kind=_LISTING_DERIVED).update(
        retention_class=_LEARNED_ALIAS_CLASS, expires_at=None
    )
    unclassed.exclude(source_kind=_LISTING_DERIVED).update(
        retention_class=_SEED_CLASS, expires_at=None
    )


def unbackfill_identity_retention_class(apps, schema_editor):
    """Deliberate no-op reverse.

    Reversing the stamp would mean writing `retention_class=""` back onto rows
    whose provenance is now unrecoverable, re-creating the DR-001 violation this
    migration exists to close. Un-applying 0017 drops the CHECK constraints, so
    the stamped rows are legal under 0016; leaving them classified is strictly
    safer than clearing them.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0016_identity_retention_indexes"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="availabilityheartbeatevent",
            name="availability_heartbeat_event_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="availabilityheartbeatobservation",
            name="availability_heartbeat_observation_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="listing",
            name="listing_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="offersnapshot",
            name="offer_snapshot_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="rawpayload",
            name="raw_payload_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="searchobservation",
            name="search_observation_retention_ttl_coherent",
        ),
        migrations.RemoveConstraint(
            model_name="verificationevent",
            name="verification_event_retention_ttl_coherent",
        ),
        migrations.AlterField(
            model_name="availabilityheartbeatevent",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="availabilityheartbeatobservation",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="drivespec",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="listing",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="offersnapshot",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="productalias",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="productmodel",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="rawpayload",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="searchobservation",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="verificationevent",
            name="retention_class",
            field=models.CharField(
                blank=True,
                choices=[
                    ("merchant_fact", "Merchant fact (indefinite)"),
                    ("ebay_listing_observation", "eBay observation"),
                    ("amazon_ephemeral", "Amazon ephemeral"),
                    ("amazon_identifier", "Amazon identifier (indefinite)"),
                    ("transient_discovery", "Search-provider discovery"),
                    ("tavily_extract", "Tavily-extracted fact (indefinite)"),
                    ("manufacturer_reference", "Manufacturer reference"),
                    ("availability_heartbeat", "Availability heartbeat (30d)"),
                    ("availability_heartbeat_event", "Heartbeat event (365d)"),
                    ("listing_derived_alias", "Listing-derived alias (indefinite)"),
                ],
                max_length=40,
            ),
        ),
        migrations.RunPython(
            backfill_identity_retention_class, unbackfill_identity_retention_class
        ),
        migrations.AddConstraint(
            model_name="availabilityheartbeatevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="availability_heartbeat_event_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="availabilityheartbeatobservation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="availability_heartbeat_observation_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="drivespec",
            constraint=models.CheckConstraint(
                condition=models.Q(("retention_class", ""), _negated=True),
                name="drive_spec_retention_class_set",
            ),
        ),
        migrations.AddConstraint(
            model_name="drivespec",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="drive_spec_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="listing",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="listing_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="offersnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="offer_snapshot_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="productalias",
            constraint=models.CheckConstraint(
                condition=models.Q(("retention_class", ""), _negated=True),
                name="product_alias_retention_class_set",
            ),
        ),
        migrations.AddConstraint(
            model_name="productalias",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="product_alias_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="productmodel",
            constraint=models.CheckConstraint(
                condition=models.Q(("retention_class", ""), _negated=True),
                name="product_model_retention_class_set",
            ),
        ),
        migrations.AddConstraint(
            model_name="productmodel",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="product_model_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="rawpayload",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="raw_payload_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="searchobservation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="search_observation_retention_ttl_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="verificationevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        (
                            "retention_class__in",
                            [
                                "merchant_fact",
                                "amazon_identifier",
                                "tavily_extract",
                                "manufacturer_reference",
                                "listing_derived_alias",
                            ],
                        ),
                    ),
                    models.Q(
                        ("expires_at__isnull", False),
                        (
                            "retention_class__in",
                            [
                                "ebay_listing_observation",
                                "amazon_ephemeral",
                                "transient_discovery",
                                "availability_heartbeat",
                                "availability_heartbeat_event",
                            ],
                        ),
                    ),
                    _connector="OR",
                ),
                name="verification_event_retention_ttl_coherent",
            ),
        ),
    ]
