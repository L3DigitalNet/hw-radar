"""Create the synthetic proof site's SourceSite and SourceConfig (MS2-D-42, F5a).

    apify_synthetic_setup [--provider local|apify]

`apify_synthetic_setup` creates both rows once and is a no-op afterwards. The
site is a management-command row, not a migration, so it exists only in the
non-production environment that runs the proof, and synthetic listings never
enter the production catalog (MS2-D-42 *Environment*). The command refuses to
run when settings.IS_PRODUCTION.

The SourceConfig is created `collection_provider=apify` with `enabled=False`
and both cheap lanes off. That keeps it out of every scheduled job: the poller
schedules and recovery-probes only `enabled=True` configs, so the only runs for
this site are explicit ones: apify_smoke (start_provider_run does not read
`enabled`) or, once switched to `local`, synthetic_collect_local.

An existing row is never rewritten implicitly. When it differs from this
command's rows in a field that keeps it unscheduled or on its provider (see
_GUARDED), the command exits non-zero and names the drift: an operator change
is either deliberate, and silently reverting it would be wrong, or a mistake
that must be seen.

--provider is the one explicit rewrite: it sets collection_provider, and only
that field, to the named provider and prints old -> new (or that nothing
changed), for the AC-4 live provider switch (F5a). The other guarded fields are
still checked first, and any drift in them refuses the switch with nothing
changed: a switch must never put an enabled or lane-carrying config on a new
provider. Without the flag, a config left on `local` reports as drift.
"""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from hw_radar.acquisition.apify import synthetic
from hw_radar.catalog.models import (
    ProviderKind,
    SourceConfig,
    SourceSite,
    SourceTier,
    SourceType,
)

_SITE_DEFAULTS: Final[dict[str, Any]] = {
    "name": "Synthetic proof source",
    "source_type": SourceType.OTHER,
    "region": "US",
    "notes": "MS2-D-42 synthetic proof site; created by apify_synthetic_setup.",
}
# tier is display-only (SourceConfig.__str__). The domain is a reserved .invalid
# name because neither provider fetches by it: both the Actor and the local
# adapter (acquisition.sources.synthetic) pin their fetch target in code. The
# cadence values only satisfy the CHECK constraints; nothing schedules a
# disabled config.
_CONFIG_DEFAULTS: Final[dict[str, Any]] = {
    "enabled": False,
    "tier": SourceTier.T2_SPECIALIST,
    "domain": "synthetic.invalid",
    "cadence_baseline_s": 86_400,
    "cadence_ceiling_s": 86_400,
    "heartbeat_enabled": False,
    "fast_lane": False,
    "collection_provider": ProviderKind.APIFY,
    "notes": "Disabled on purpose: apify_smoke or synthetic_collect_local runs it explicitly.",
}
# The fields whose drift would put the site on a schedule or on another provider.
_GUARDED: Final = ("enabled", "heartbeat_enabled", "fast_lane", "collection_provider")
_PROVIDERS: Final = (ProviderKind.LOCAL.value, ProviderKind.APIFY.value)


class Command(BaseCommand):
    help = (
        "Create the synthetic proof site (disabled, Actor-backed); a no-op when it exists. "
        "--provider switches its collection_provider explicitly."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--provider",
            choices=_PROVIDERS,
            help="set collection_provider to this provider (the AC-4 switch); nothing else",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_PRODUCTION:
            raise CommandError(
                "refused: the synthetic proof site must never exist in production (MS2-D-42)"
            )
        provider: str | None = options["provider"]
        # The drift check and the switch share one transaction and a row lock,
        # so a concurrent edit cannot slip between the check and the write.
        with transaction.atomic():
            site, site_created = SourceSite.objects.get_or_create(
                normalized_name=synthetic.SITE_KEY, defaults=_SITE_DEFAULTS
            )
            config, config_created = SourceConfig.objects.get_or_create(
                source_site=site, defaults=_CONFIG_DEFAULTS
            )
            config = SourceConfig.objects.select_for_update().get(pk=config.pk)
            checked = [
                name for name in _GUARDED if provider is None or name != "collection_provider"
            ]
            drift = [
                f"{name}={getattr(config, name)!r} (expected {_CONFIG_DEFAULTS[name]!r})"
                for name in checked
                if getattr(config, name) != _CONFIG_DEFAULTS[name]
            ]
            if drift:
                raise CommandError(
                    f"synthetic SourceConfig {config.pk} exists with drift, left unchanged: "
                    + "; ".join(drift)
                )
            switched = self._switch(config, provider)
        verb = (
            "created"
            if site_created or config_created
            else "provider switched"
            if switched
            else "already present (no change)"
        )
        self.stdout.write(
            f"synthetic site {verb}: source_site {site.pk}, source_config {config.pk}, "
            f"collection_provider={config.collection_provider}, enabled={config.enabled}"
        )

    def _switch(self, config: SourceConfig, provider: str | None) -> bool:
        """Set collection_provider to `provider` and report it; True when it changed."""
        if provider is None:
            return False
        old = config.collection_provider
        if old == provider:
            self.stdout.write(f"collection_provider unchanged: {old}")
            return False
        config.collection_provider = provider
        config.save(update_fields=["collection_provider", "updated_at"])
        self.stdout.write(f"collection_provider: {old} -> {provider}")
        return True
