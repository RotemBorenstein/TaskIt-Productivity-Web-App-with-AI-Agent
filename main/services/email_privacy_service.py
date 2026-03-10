from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone

from main.models import EmailSyncedMessage


def purge_expired_synced_messages(*, batch_size: int = 1000) -> int:
    now = timezone.now()
    expired_ids = list(
        EmailSyncedMessage.objects.filter(expires_at__lte=now)
        .values_list("id", flat=True)[:batch_size]
    )
    if not expired_ids:
        return 0
    deleted_count, _ = EmailSyncedMessage.objects.filter(id__in=expired_ids).delete()
    return deleted_count


def delete_user_synced_messages(user: get_user_model()) -> int:
    deleted_count, _ = EmailSyncedMessage.objects.filter(user=user).delete()
    return deleted_count

