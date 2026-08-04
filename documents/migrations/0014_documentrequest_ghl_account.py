from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("documents", "0013_documentrequest_ghl_needs_list_note_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentrequest",
            name="ghl_account",
            field=models.ForeignKey(
                blank=True,
                help_text="Linked GHL account for this request (resolved by location_id).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="document_requests",
                to="accounts.ghlauthcredentials",
            ),
        ),
    ]
