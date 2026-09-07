# Generated manually for loan-program document flow

from django.db import migrations, models
import django.db.models.deletion


def delete_needs_list_selections(apps, schema_editor):
    AdminDocumentSelection = apps.get_model("documents", "AdminDocumentSelection")
    AdminDocumentSelection.objects.filter(section_type="needs_list").delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0019_rename_documents_n_account_7c0f1a_idx_documents_n_account_dd5b88_idx"),
    ]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="description",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional description of what the document is and its purpose",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="attachment_mode",
            field=models.CharField(
                choices=[
                    ("file", "File (request only)"),
                    ("template", "Template (download + upload)"),
                ],
                default="file",
                help_text="file = request upload only; template = borrower can download blank then upload",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="blank_template",
            field=models.ForeignKey(
                blank=True,
                help_text="Blank template file in GHL Media (when attachment_mode=template)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="documents.needslisttemplate",
            ),
        ),
        migrations.RunPython(delete_needs_list_selections, noop_reverse),
    ]
