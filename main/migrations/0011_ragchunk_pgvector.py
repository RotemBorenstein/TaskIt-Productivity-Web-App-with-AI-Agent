from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from pgvector.django import VectorField


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0010_emailsuggestion_task_type_hint"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="RagChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("doc_type", models.CharField(default="note", max_length=20)),
                ("doc_key", models.CharField(max_length=100)),
                ("chunk_index", models.PositiveIntegerField(default=0)),
                ("subject_title", models.CharField(blank=True, max_length=100)),
                ("note_title", models.CharField(blank=True, max_length=255)),
                ("content", models.TextField()),
                ("embedding", VectorField(dimensions=1536)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "note",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rag_chunks",
                        to="main.note",
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rag_chunks",
                        to="main.subject",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rag_chunks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="ragchunk",
            index=models.Index(fields=["user", "doc_key"], name="ragchunk_user_key"),
        ),
        migrations.AddIndex(
            model_name="ragchunk",
            index=models.Index(fields=["note"], name="ragchunk_note_idx"),
        ),
        migrations.AddIndex(
            model_name="ragchunk",
            index=models.Index(fields=["subject"], name="ragchunk_subject_idx"),
        ),
    ]
