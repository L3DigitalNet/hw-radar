# OQ31 (owner decision 2026-09-26): ServerPartDeals and Seagate-recertified are
# retired from the enableable acquisition set. Pins both SourceConfig rows to
# enabled=False and lifecycle SKIP ("terminal pending human re-review"), so the
# row itself says what acquisition.admission.RETIRED_SOURCES says in code.
#
# The keys are literals, not imported from acquisition.admission: a migration
# must replay identically forever, and a later edit to the live retired set
# (a re-admission) must not rewrite what this historical step did.
#
# Data-only and idempotent: a missing row is skipped (fresh test DBs may lack
# the 0005 seed), and a re-run rewrites the same values. Reverse is a no-op —
# un-retiring is a fresh source-admission review, never a migration rollback.
from django.db import migrations

RETIRED = ("serverpartdeals", "seagate-recertified")


def retire(apps, schema_editor):
    SourceConfig = apps.get_model("catalog", "SourceConfig")
    SourceConfig.objects.filter(source_site__normalized_name__in=RETIRED).update(
        enabled=False, lifecycle_state="skip"
    )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0022_apify_spend_ledger")]
    operations = [migrations.RunPython(retire, migrations.RunPython.noop)]
