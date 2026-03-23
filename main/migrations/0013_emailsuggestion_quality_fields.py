from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0012_reminder_usernotificationsettings_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailsuggestion",
            name="digest_eligible",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="emailsuggestion",
            name="model_confidence",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=4,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="emailsuggestion",
            name="rejection_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("not_actionable", "Not Actionable"),
                    ("newsletter_or_automated", "Newsletter Or Automated"),
                    ("wrong_task", "Wrong Task"),
                    ("wrong_event", "Wrong Event"),
                    ("quoted_old_thread", "Quoted Old Thread"),
                    ("other", "Other"),
                ],
                max_length=40,
            ),
        ),
    ]
