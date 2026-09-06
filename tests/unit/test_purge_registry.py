"""Registry-level invariants the retention sweep depends on but cannot check itself.

Four properties, all derived from the app registry rather than a hand-written
list: the sweep must cover every RetentionGoverned table, now and after new
models land; any model that claims expired rows against deletion must also
declare what gets scrubbed from the rows it keeps; every swept table must
declare the partial expires_at index the sweep's own query plan depends on; and
every swept table must declare the DR-001 CHECK pair that makes the sweep's
class/expires_at assumptions true at the database.
"""

from django.db.models import CheckConstraint, Field, Model

from hw_radar.catalog.management.commands.purge_expired import retention_governed_models
from hw_radar.catalog.models.base import (
    RetentionGoverned,
    retention_constraints,
    retention_indexes,
)


def _concrete_descendants(cls: type[Model]) -> set[type[Model]]:
    found: set[type[Model]] = set()
    for sub in cls.__subclasses__():
        if not sub._meta.abstract:  # pyright: ignore[reportPrivateUsage]
            found.add(sub)
        found |= _concrete_descendants(sub)
    return found


def test_registry_covers_every_retention_governed_model() -> None:
    # Two independent derivations of the same truth: the sweeper reads Django's
    # app registry, this walks the class hierarchy. They diverge exactly when a
    # RetentionGoverned model is declared in an app the sweeper never visits —
    # the silent-retention-gap failure the registry exists to prevent.
    assert set(retention_governed_models()) == _concrete_descendants(RetentionGoverned)


def test_registry_is_not_empty_and_includes_the_bounded_tables() -> None:
    # Guards the degenerate pass: an empty registry satisfies the equality above.
    labels = {m._meta.label for m in retention_governed_models()}  # pyright: ignore[reportPrivateUsage]
    assert {
        "catalog.Listing",
        "catalog.OfferSnapshot",
        "catalog.RawPayload",
        "catalog.SearchObservation",
        "catalog.VerificationEvent",
        "catalog.AvailabilityHeartbeatObservation",
        "catalog.AvailabilityHeartbeatEvent",
    } <= labels


def test_every_swept_model_carries_the_retention_fields() -> None:
    for model in retention_governed_models():
        names = {f.name for f in model._meta.get_fields()}  # pyright: ignore[reportPrivateUsage]
        assert {"retention_class", "expires_at"} <= names, model


def _exemption_claim(model: type[Model]) -> bool:
    """Whether this model claims any expired row against deletion.

    Mirrors purge_expired._deletion_exempt_q exactly, including its truthiness
    test: an absent hook and a hook returning an empty Q are the same thing to the
    sweeper — every expired row is deleted — so neither obliges the model to
    redact anything.
    """
    predicate = getattr(model, "deletion_exempt_q", None)
    return predicate is not None and bool(predicate())


def test_deletion_exemption_and_redaction_are_declared_together() -> None:
    # Cross-file contract with catalog.management.commands.purge_expired: the
    # sweeper looks up `deletion_exempt_q` (_deletion_exempt_q) and
    # `redact_expired` (_redact_expired) by name on each RetentionGoverned model
    # and holds no policy or field list of its own. It cannot detect a model that
    # declares one hook without the other — _redact_expired's own docstring says
    # so — and each half-declaration is a distinct DR-001 breach: an exemption
    # alone retains merchant content past its TTL, a redaction hook alone runs
    # over rows the sweeper already deleted and quietly does nothing. This test is
    # the only enforcement of the pairing, so a future anchor model cannot earn
    # survival past its TTL without also declaring what gets scrubbed.
    paired = 0
    for model in retention_governed_models():
        label = model._meta.label  # pyright: ignore[reportPrivateUsage]
        claims_exemption = _exemption_claim(model)
        redactor = getattr(model, "redact_expired", None)
        content_fields = getattr(model, "REDACTED_CONTENT_FIELDS", None)
        declares_redaction = redactor is not None or bool(content_fields)
        assert claims_exemption == declares_redaction, (
            f"{label}: deletion_exempt_q and redact_expired/REDACTED_CONTENT_FIELDS "
            "must be declared together"
        )
        if claims_exemption:
            assert redactor is not None, f"{label}: exemption without redact_expired"
            assert content_fields, f"{label}: exemption without REDACTED_CONTENT_FIELDS"
            paired += 1
    # Guards the degenerate pass: with no exempt model in the registry the loop
    # above asserts nothing, and the pairing rule would rot unnoticed.
    assert paired >= 1, "no retention-governed model exercises the exemption/redaction pairing"


def test_redacted_content_fields_name_real_columns_that_accept_their_blank() -> None:
    # Redaction writes these blanks through .update() and save(update_fields=…),
    # which fail at runtime — inside the hourly sweep — on a misspelled name or a
    # blank of the wrong type. The check is static so the failure lands here first.
    for model in retention_governed_models():
        label = model._meta.label  # pyright: ignore[reportPrivateUsage]
        for name, blank in getattr(model, "REDACTED_CONTENT_FIELDS", {}).items():
            field = model._meta.get_field(name)  # pyright: ignore[reportPrivateUsage]
            # A reverse relation resolves to ForeignObjectRel, not Field, and has
            # no column to blank; a forward FK has one but redacting it would cut
            # the row out of the audit graph rather than scrub content.
            assert isinstance(field, Field), f"{label}.{name} is not a concrete field"
            assert not field.is_relation, f"{label}.{name} is a relation"
            # Redacting an identity column would destroy the audit trail the kept
            # row exists to carry (see Listing.deletion_exempt_q clause 2).
            assert not field.primary_key, f"{label}.{name} is the primary key"
            assert blank is not None or field.null, f"{label}.{name} rejects NULL"
            # to_python round-trips only a value of the field's own Python type:
            # a dict handed to a CharField comes back as its repr, not itself.
            assert field.to_python(blank) == blank, f"{label}.{name} does not accept {blank!r}"


def test_every_swept_model_declares_the_expires_at_partial_index() -> None:
    # Without this index the hourly sweep sequential-scans the table: its WHERE
    # is `expires_at < now()`, and the partial predicate keeps the index to the
    # bounded rows only. A missing index is invisible in test output — it costs
    # a full scan per table per hour in production — so it is pinned here.
    for model in retention_governed_models():
        label = model._meta.label  # pyright: ignore[reportPrivateUsage]
        partial = [
            index
            for index in model._meta.indexes  # pyright: ignore[reportPrivateUsage]
            if list(index.fields) == ["expires_at"] and index.condition is not None
        ]
        assert partial, f"{label}: no partial expires_at index from retention_indexes()"
        assert partial == retention_indexes(partial[0].name), (
            f"{label}: expires_at index predicate differs from retention_indexes()"
        )


def test_every_swept_model_declares_the_dr001_check_pair() -> None:
    # The sweep's correctness rests on two facts it never verifies at runtime:
    # every row carries a class, and an indefinite-class row has a NULL
    # expires_at (see purge_expired._expired, which treats the class filter and
    # the timestamp filter as independent guards). Only the CHECK pair makes
    # those true — a table without it can hold a classless row that no sweep
    # will ever retire, and the loss is silent. The prefix is read back off the
    # constraint name rather than hardcoded per model, so this compares each
    # model's pair against retention_constraints() itself: a change to the
    # predicate that a model's migration never picked up fails here.
    for model in retention_governed_models():
        label = model._meta.label  # pyright: ignore[reportPrivateUsage]
        checks = {
            c.name: c
            for c in model._meta.constraints  # pyright: ignore[reportPrivateUsage]
            if isinstance(c, CheckConstraint)
        }
        class_set = [n for n in checks if n.endswith("_retention_class_set")]
        assert class_set, f"{label}: no *_retention_class_set CHECK from retention_constraints()"
        prefix = class_set[0].removesuffix("_retention_class_set")
        expected = retention_constraints(prefix)
        assert [checks.get(c.name) for c in expected] == expected, (
            f"{label}: DR-001 CHECK pair differs from retention_constraints({prefix!r})"
        )
