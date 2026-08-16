"""The sweep must cover every RetentionGoverned table, now and after new models land."""

from django.db.models import Model

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
