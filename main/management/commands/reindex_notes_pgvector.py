from django.core.management.base import BaseCommand
from django.db import transaction

from main.agent.rag_utils import index_note
from main.models import Note, RagChunk


class Command(BaseCommand):
    help = "Rebuild pgvector note index for all users or for a specific user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="Optional user id. If omitted, all users are reindexed.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Iterator batch size when reading notes (default: 200).",
        )

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        batch_size = options.get("batch_size") or 200

        notes_qs = Note.objects.select_related("subject").order_by("id")
        if user_id:
            notes_qs = notes_qs.filter(subject__user_id=user_id)
            RagChunk.objects.filter(user_id=user_id).delete()
        else:
            RagChunk.objects.all().delete()

        total = notes_qs.count()
        self.stdout.write(f"Reindexing {total} notes into pgvector...")

        processed = 0
        for note in notes_qs.iterator(chunk_size=batch_size):
            # Keep per-note replacement behavior consistent with runtime writes.
            with transaction.atomic():
                index_note(note)
            processed += 1
            if processed % 50 == 0:
                self.stdout.write(f"Processed {processed}/{total} notes...")

        self.stdout.write(self.style.SUCCESS(f"Done. Reindexed {processed} notes."))
