"""Registry-level invariants the retention sweep depends on but cannot check itself.

Two properties, both derived from the app registry rather than a hand-written
list: the sweep must cover every RetentionGoverned table, now and after new
models land; and any model that claims expired rows against deletion must also
declare what gets scrubbed from the rows it keeps.
"""

from django.db.models import Field, Model

from hw_radar.catalog.management.commands.purge_expired import retention_governed_models
from hw_radar.catalog.models.base import RetentionGoverned


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
