from django.db import migrations, models
import django.db.models.deletion


def seed_account_document_libraries(apps, schema_editor):
    AccountDocumentLibrary = apps.get_model("documents", "AccountDocumentLibrary")
    Document = apps.get_model("documents", "Document")
    GHLAuthCredentials = apps.get_model("accounts", "GHLAuthCredentials")
    master_ids = list(
        Document.objects.filter(
            request__isnull=True, owner_account__isnull=True
        ).values_list("id", flat=True)
    )
    acc_ids = list(GHLAuthCredentials.objects.values_list("id", flat=True))
    rows = [
        AccountDocumentLibrary(account_id=aid, document_id=did)
        for aid in acc_ids
        for did in master_ids
    ]
    if rows:
        AccountDocumentLibrary.objects.bulk_create(
            rows, ignore_conflicts=True, batch_size=2000
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("documents", "0014_documentrequest_ghl_account"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountDocumentLibrary",
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
                        related_name="document_library_entries",
                        to="accounts.ghlauthcredentials",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="account_library_entries",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "Account document library entries",
                "unique_together": {("account", "document")},
            },
        ),
        migrations.AddField(
            model_name="document",
            name="owner_account",
            field=models.ForeignKey(
                blank=True,
                help_text="If set, this catalog-style document belongs only to this subaccount (not in shared master list).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_catalog_documents",
                to="accounts.ghlauthcredentials",
            ),
        ),
        migrations.RunPython(seed_account_document_libraries, noop_reverse),
    ]
