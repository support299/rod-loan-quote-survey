from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0016_account_print_group_library"),
    ]

    operations = [
        migrations.AddField(
            model_name="userdocumentupload",
            name="rejection_reason",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Admin reason shown to the borrower when the upload is rejected",
            ),
        ),
        migrations.AddField(
            model_name="userdocumentupload",
            name="rejected_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the document was rejected by admin",
                null=True,
            ),
        ),
    ]
