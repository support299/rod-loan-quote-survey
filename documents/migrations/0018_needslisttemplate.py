import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_templates_parent_id"),
        ("documents", "0017_userdocumentupload_rejection_reason"),
    ]

    operations = [
        migrations.CreateModel(
            name="NeedsListTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Display name shown to admin and borrower", max_length=255)),
                ("ghl_file_id", models.CharField(help_text="GHL media fileId", max_length=100)),
                ("ghl_file_url", models.URLField(help_text="GHL media URL for view/download", max_length=500)),
                ("file_name", models.CharField(blank=True, help_text="Original uploaded file name", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.ForeignKey(
                        help_text="GHL subaccount that owns this template",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="needs_list_templates",
                        to="accounts.ghlauthcredentials",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="admindocumentselection",
            name="template",
            field=models.ForeignKey(
                blank=True,
                help_text="Template file the borrower should download for this requested document",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="admin_selections",
                to="documents.needslisttemplate",
            ),
        ),
        migrations.AddIndex(
            model_name="needslisttemplate",
            index=models.Index(fields=["account", "name"], name="documents_n_account_7c0f1a_idx"),
        ),
    ]
