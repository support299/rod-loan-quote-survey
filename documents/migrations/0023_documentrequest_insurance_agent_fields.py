# Generated manually for insurance agent fields on DocumentRequest

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0022_documentrequest_title_company_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentrequest",
            name="insurance_agent_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Insurance Agent Name (synced to GHL opportunity).",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="documentrequest",
            name="insurance_agent_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="Insurance Agent Email (synced to GHL opportunity).",
                max_length=254,
            ),
        ),
        migrations.AddField(
            model_name="documentrequest",
            name="insurance_agent_phone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Insurance Agent Phone (synced to GHL opportunity).",
                max_length=64,
            ),
        ),
    ]
