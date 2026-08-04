from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ghlauthcredentials",
            name="templates_parent_id",
            field=models.CharField(
                blank=True,
                help_text="GHL media folder id for Needs List Templates (separate from borrower uploads)",
                max_length=100,
            ),
        ),
    ]
