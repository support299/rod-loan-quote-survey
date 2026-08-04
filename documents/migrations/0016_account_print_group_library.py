from django.db import migrations, models
import django.db.models.deletion


def seed_account_print_group_libraries(apps, schema_editor):
    AccountPrintGroupLibrary = apps.get_model("documents", "AccountPrintGroupLibrary")
    PrintGroup = apps.get_model("documents", "PrintGroup")
    GHLAuthCredentials = apps.get_model("accounts", "GHLAuthCredentials")
    master_ids = list(
        PrintGroup.objects.filter(
            request__isnull=True, owner_account__isnull=True
        ).values_list("id", flat=True)
    )
    acc_ids = list(GHLAuthCredentials.objects.values_list("id", flat=True))
    rows = [
        AccountPrintGroupLibrary(account_id=aid, print_group_id=gid)
        for aid in acc_ids
        for gid in master_ids
    ]
    if rows:
        AccountPrintGroupLibrary.objects.bulk_create(
            rows, ignore_conflicts=True, batch_size=2000
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("documents", "0015_account_document_library"),
    ]

    operations = [
        migrations.AddField(
            model_name="printgroup",
            name="owner_account",
            field=models.ForeignKey(
                blank=True,
                help_text="If set, this print group is only available for this GHL subaccount (master catalog otherwise).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_print_groups",
                to="accounts.ghlauthcredentials",
            ),
        ),
        migrations.CreateModel(
            name="AccountPrintGroupLibrary",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="print_group_library_entries",
                        to="accounts.ghlauthcredentials",
                    ),
                ),
                (
                    "print_group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="account_library_entries",
                        to="documents.printgroup",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "Account print group library entries",
                "unique_together": {("account", "print_group")},
            },
        ),
        migrations.RunPython(seed_account_print_group_libraries, noop_reverse),
    ]
