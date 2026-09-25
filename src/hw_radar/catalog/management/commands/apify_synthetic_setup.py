"""Create the synthetic proof site's SourceSite and SourceConfig (MS2-D-42, F5a).

`apify_synthetic_setup` creates both rows once and is a no-op afterwards. The
site is a management-command row, not a migration, so it exists only in the
non-production environment that runs the proof, and synthetic listings never
enter the production catalog (MS2-D-42 *Environment*). The command refuses to
run when settings.IS_PRODUCTION.

The SourceConfig is `collection_provider=apify` with `enabled=False` and both
cheap lanes off. That keeps it out of every scheduled job: the poller schedules
and recovery-probes only `enabled=True` configs, so the one way to start a run
for this site is an explicit apify_smoke. start_provider_run does not read
`enabled`, so the smoke needs no flip.

An existing row is never rewritten. When it differs from this command's rows in
a field that keeps it unscheduled or Actor-backed (see _GUARDED), the command
exits non-zero and names the drift: an operator change is either deliberate,
and silently reverting it would be wrong, or a mistake that must be seen.
"""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
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
    "notes": "MS2-D-42 Actor-only synthetic proof site; created by apify_synthetic_setup.",
}
# tier is display-only (SourceConfig.__str__). The domain is a reserved .invalid
# name because hw-radar itself never fetches this site: the Actor's fetch target
# is pinned in its own code. The cadence values only satisfy the CHECK
# constraints; nothing schedules a disabled config.
_CONFIG_DEFAULTS: Final[dict[str, Any]] = {
    "enabled": False,
    "tier": SourceTier.T2_SPECIALIST,
    "domain": "synthetic.invalid",
    "cadence_baseline_s": 86_400,
    "cadence_ceiling_s": 86_400,
    "heartbeat_enabled": False,
    "fast_lane": False,
    "collection_provider": ProviderKind.APIFY,
    "notes": "Disabled on purpose: apify_smoke starts its runs explicitly.",
}
# The fields whose drift would put the site on a schedule or off the Actor path.
_GUARDED: Final = ("enabled", "heartbeat_enabled", "fast_lane", "collection_provider")


class Command(BaseCommand):
    help = "Create the synthetic proof site (disabled, Actor-backed); a no-op when it exists."

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_PRODUCTION:
            raise CommandError(
                "refused: the synthetic proof site must never exist in production (MS2-D-42)"
            )
        with transaction.atomic():
            site, site_created = SourceSite.objects.get_or_create(
                normalized_name=synthetic.SITE_KEY, defaults=_SITE_DEFAULTS
            )
            config, config_created = SourceConfig.objects.get_or_create(
                source_site=site, defaults=_CONFIG_DEFAULTS
            )
        if not config_created:
            drift = [
                f"{name}={getattr(config, name)!r} (expected {_CONFIG_DEFAULTS[name]!r})"
                for name in _GUARDED
                if getattr(config, name) != _CONFIG_DEFAULTS[name]
            ]
            if drift:
                raise CommandError(
                    f"synthetic SourceConfig {config.pk} exists with drift, left unchanged: "
                    + "; ".join(drift)
                )
        verb = "created" if site_created or config_created else "already present (no change)"
        self.stdout.write(
            f"synthetic site {verb}: source_site {site.pk}, source_config {config.pk}, "
            f"collection_provider={config.collection_provider}, enabled={config.enabled}"
        )
