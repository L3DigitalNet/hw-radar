"""MS-2 Slice B: seed the first-class and basic-watch category rows.

`drive` already exists (0001). The slugs here are a cross-file contract with the
matching.categories registry: the resolver materializes families with
`Category.objects.get(slug=...)`, which raises for a registered slug that has no
row here.

Forward is get_or_create, so a row an operator or test created earlier is adopted
with its existing name, never duplicated or renamed.

Reverse deletes only rows that no ProductFamily references. product_family.category
is PROTECT, so deleting a referenced row would abort the whole rollback; skipping it
instead keeps the rollback runnable on a DB that already holds catalog data for a
new category, and leaves that data intact. `drive` is never touched.
"""

from django.db import migrations

CATEGORIES = (
    ("gpu", "GPU / accelerator"),
    ("ram", "RAM"),
    ("cpu", "CPU"),
    ("nic", "Network adapter"),
    ("hba", "Host bus adapter"),
    ("motherboard", "Motherboard"),
    ("server", "Server"),
)


def seed_categories(apps, schema_editor):
    category = apps.get_model("catalog", "Category")
    for slug, name in CATEGORIES:
        category.objects.get_or_create(slug=slug, defaults={"name": name})


def unseed_categories(apps, schema_editor):
    category = apps.get_model("catalog", "Category")
    category.objects.filter(
        slug__in=[slug for slug, _ in CATEGORIES], families__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0018_category_spec_satellites"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
