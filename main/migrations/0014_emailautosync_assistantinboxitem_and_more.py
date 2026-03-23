from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0013_emailsuggestion_quality_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailintegration",
            name="auto_sync_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="emailintegration",
            name="auto_sync_frequency_hours",
            field=models.PositiveSmallIntegerField(
                choices=[(24, "24 Hours"), (48, "48 Hours"), (168, "7 Days")],
                default=24,
            ),
        ),
        migrations.AddField(
            model_name="emailintegration",
            name="next_auto_sync_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailsyncrun",
            name="trigger_type",
            field=models.CharField(
                choices=[("manual", "Manual"), ("background", "Background")],
                default="manual",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="emailsyncrun",
            name="date_preset",
            field=models.CharField(
                choices=[("day", "Day"), ("48h", "48 Hours"), ("week", "Week")],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="AssistantInboxItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item_type", models.CharField(choices=[("email_digest", "Email Digest")], max_length=30)),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("is_read", models.BooleanField(default=False)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "sync_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assistant_inbox_items",
                        to="main.emailsyncrun",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assistant_inbox_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="emailintegration",
            index=models.Index(fields=["is_active", "auto_sync_enabled", "next_auto_sync_at"], name="main_emaili_is_acti_984a4d_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantinboxitem",
            index=models.Index(fields=["user", "is_read", "created_at"], name="main_assist_user_id_67ae0c_idx"),
        ),
        migrations.AddIndex(
            model_name="assistantinboxitem",
            index=models.Index(fields=["sync_run", "item_type"], name="main_assist_sync_ru_a5614d_idx"),
        ),
    ]
