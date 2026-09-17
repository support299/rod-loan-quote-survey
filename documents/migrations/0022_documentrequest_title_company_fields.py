# Generated manually for title company contact fields on DocumentRequest

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0021_alter_document_category_alter_document_file_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentrequest",
            name="title_company_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last submitted Title Company Name (synced to GHL opportunity).",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="documentrequest",
            name="title_company_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="Last submitted Title Company Email (synced to GHL opportunity).",
                max_length=254,
            ),
        ),
        migrations.AddField(
            model_name="documentrequest",
            name="title_company_phone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last submitted Title Company Phone (synced to GHL opportunity).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="documentrequest",
            name="title_company_by_scope",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Title company info keyed by upload scope (loan program name or 'individual').",
            ),
        ),
    ]
