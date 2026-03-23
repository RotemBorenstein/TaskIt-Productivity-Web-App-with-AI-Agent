from datetime import time
from zoneinfo import ZoneInfo

from django.db import migrations, models


PROJECT_TZ = ZoneInfo("Asia/Jerusalem")


def backfill_auto_sync_time_and_weekday(apps, schema_editor):
    EmailIntegration = apps.get_model("main", "EmailIntegration")

    for integration in EmailIntegration.objects.all().iterator():
        next_run = integration.next_auto_sync_at
        update_fields = []

        if next_run:
            local_next_run = next_run.astimezone(PROJECT_TZ)
            integration.auto_sync_time = time(hour=local_next_run.hour, minute=0)
            update_fields.append("auto_sync_time")
            if integration.auto_sync_frequency_hours == 168:
                integration.auto_sync_weekday = local_next_run.weekday()
                update_fields.append("auto_sync_weekday")
            else:
                integration.auto_sync_weekday = None
                update_fields.append("auto_sync_weekday")
        else:
            integration.auto_sync_time = time(hour=20, minute=0)
            update_fields.append("auto_sync_time")
            if integration.auto_sync_frequency_hours == 168:
                integration.auto_sync_weekday = 6
                update_fields.append("auto_sync_weekday")

        if update_fields:
            integration.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0014_emailautosync_assistantinboxitem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailintegration",
            name="auto_sync_time",
            field=models.TimeField(default=time(hour=20, minute=0)),
        ),
        migrations.AddField(
            model_name="emailintegration",
            name="auto_sync_weekday",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
                    (0, "Monday"),
                    (1, "Tuesday"),
                    (2, "Wednesday"),
                    (3, "Thursday"),
                    (4, "Friday"),
                    (5, "Saturday"),
                    (6, "Sunday"),
                ],
                null=True,
            ),
        ),
        migrations.RunPython(backfill_auto_sync_time_and_weekday, migrations.RunPython.noop),
    ]
