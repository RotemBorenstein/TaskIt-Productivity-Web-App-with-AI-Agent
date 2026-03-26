from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0015_emailintegration_auto_sync_time_and_weekday"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentchatmessage",
            name="include_in_memory",
            field=models.BooleanField(default=True),
        ),
    ]
