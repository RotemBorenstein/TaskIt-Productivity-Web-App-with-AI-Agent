"""Helpers for assistant inbox items such as automatic email digests."""

from __future__ import annotations

from django.urls import reverse

from main.models import AssistantInboxItem, EmailSuggestion, EmailSyncRun


def create_email_digest_for_sync_run(sync_run: EmailSyncRun) -> AssistantInboxItem | None:
    """Create one unread inbox digest for a background sync run when eligible items exist."""
    if sync_run.trigger_type != EmailSyncRun.TRIGGER_BACKGROUND:
        return None

    existing = AssistantInboxItem.objects.filter(
        sync_run=sync_run,
        item_type=AssistantInboxItem.TYPE_EMAIL_DIGEST,
    ).first()
    if existing:
        return existing

    suggestions = list(
        EmailSuggestion.objects.filter(
            sync_run=sync_run,
            status=EmailSuggestion.STATUS_PENDING,
            digest_eligible=True,
        ).order_by("created_at")
    )
    if not suggestions:
        return None

    task_count = sum(1 for suggestion in suggestions if suggestion.suggestion_type == EmailSuggestion.TYPE_TASK)
    event_count = len(suggestions) - task_count
    total_count = len(suggestions)
    title = "New email suggestions"
    body = (
        f"I checked your email and found {total_count} possible item"
        f"{'' if total_count == 1 else 's'}: {task_count} task"
        f"{'' if task_count == 1 else 's'} and {event_count} event"
        f"{'' if event_count == 1 else 's'}."
    )

    return AssistantInboxItem.objects.create(
        user=sync_run.user,
        sync_run=sync_run,
        item_type=AssistantInboxItem.TYPE_EMAIL_DIGEST,
        title=title,
        body=body,
        payload={
            "sync_run_id": sync_run.id,
            "suggestion_ids": [suggestion.id for suggestion in suggestions],
            "task_count": task_count,
            "event_count": event_count,
            "total_count": total_count,
            "review_url": reverse("main:email_suggestions"),
        },
    )
