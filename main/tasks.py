"""Celery tasks for reminder delivery and background email sync."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from main.agent.rag_utils import index_note
from main.models import EmailIntegration, EmailSyncRun, Note, Reminder
from main.services.assistant_inbox_service import create_email_digest_for_sync_run
from main.services.email_suggestion_service import EmailSuggestionService
from main.services.email_sync_service import EmailSyncService
from main.services.reminder_delivery_service import dispatch_reminder

logger = logging.getLogger(__name__)


def _compute_next_regular_email_sync_at(integration: EmailIntegration, scheduled_slot, now):
    """Advance the saved schedule from the previous planned slot without drift."""
    next_auto_sync_at = integration.compute_next_auto_sync_at(
        reference_time=scheduled_slot,
        from_scheduled_slot=True,
    )
    while next_auto_sync_at <= now:
        next_auto_sync_at = integration.compute_next_auto_sync_at(
            reference_time=next_auto_sync_at,
            from_scheduled_slot=True,
        )
    return next_auto_sync_at


@shared_task
def send_due_reminder(reminder_id: int):
    """Send a single due reminder and recompute its next run."""
    with transaction.atomic():
        try:
            reminder = Reminder.objects.select_for_update().get(pk=reminder_id)
        except Reminder.DoesNotExist:
            return {"status": "missing"}

        reminder = Reminder.objects.select_related("task", "event", "user").get(
            pk=reminder.pk
        )

        now = timezone.now()
        if not reminder.is_enabled or not reminder.next_run_at or reminder.next_run_at > now:
            return {"status": "skipped"}

        errors = dispatch_reminder(reminder)
        reminder.last_error = "\n".join(errors)

        if errors:
            retry_at = now + timedelta(minutes=5)
            if reminder.kind == Reminder.KIND_TASK_DUE:
                original_due_run = reminder.compute_next_run_at(
                    reference_time=now - timedelta(seconds=1)
                )
                reminder.next_run_at = (
                    min(retry_at, original_due_run) if original_due_run else retry_at
                )
            elif reminder.kind == Reminder.KIND_EVENT_OFFSET:
                reminder.next_run_at = retry_at
            else:
                reminder.next_run_at = retry_at

            reminder.save(update_fields=["last_error", "next_run_at", "updated_at"])
            return {
                "status": "retry_scheduled",
                "errors": errors,
            }

        reminder.last_sent_at = now
        reminder.next_run_at = reminder.compute_next_run_at(
            reference_time=now + timedelta(seconds=1)
        )
        reminder.save(update_fields=["last_sent_at", "last_error", "next_run_at", "updated_at"])

        return {
            "status": "sent",
            "errors": [],
        }


@shared_task
def scan_due_reminders():
    """Queue reminder deliveries that are due."""
    now = timezone.now()
    due_ids = list(
        Reminder.objects.filter(
            is_enabled=True,
            next_run_at__isnull=False,
            next_run_at__lte=now,
        )
        .order_by("next_run_at")
        .values_list("id", flat=True)[:100]
    )

    for reminder_id in due_ids:
        send_due_reminder.delay(reminder_id)

    return {"queued": len(due_ids)}


@shared_task(bind=True, max_retries=3)
def queue_note_index(self, note_id: int, expected_updated_at: str | None = None):
    """Index the latest saved note state without blocking the request cycle."""
    try:
        note = Note.objects.select_related("subject").get(pk=note_id)
    except Note.DoesNotExist:
        logger.info("Skipped note indexing for missing note: note_id=%s", note_id)
        return {"status": "missing", "note_id": note_id}

    expected_updated_at_dt = parse_datetime(expected_updated_at) if expected_updated_at else None
    if expected_updated_at_dt is not None and note.updated_at != expected_updated_at_dt:
        logger.info(
            "Skipped stale note indexing before embed: note_id=%s user_id=%s expected_updated_at=%s current_updated_at=%s",
            note.id,
            note.subject.user_id,
            expected_updated_at,
            note.updated_at.isoformat(),
        )
        return {"status": "stale", "note_id": note.id, "user_id": note.subject.user_id}

    logger.info(
        "Starting note indexing: note_id=%s user_id=%s attempt=%s",
        note.id,
        note.subject.user_id,
        self.request.retries + 1,
    )

    try:
        indexed = index_note(note, expected_updated_at=expected_updated_at_dt)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(
                "Note indexing permanently failed: note_id=%s user_id=%s",
                note.id,
                note.subject.user_id,
            )
            raise

        countdown = min(2 ** (self.request.retries + 1), 60)
        logger.warning(
            "Note indexing failed; retrying in %ss: note_id=%s user_id=%s retry=%s/%s",
            countdown,
            note.id,
            note.subject.user_id,
            self.request.retries + 1,
            self.max_retries,
        )
        raise self.retry(exc=exc, countdown=countdown)

    if not indexed:
        logger.info(
            "Skipped stale note indexing after embed: note_id=%s user_id=%s expected_updated_at=%s",
            note.id,
            note.subject.user_id,
            expected_updated_at,
        )
        return {"status": "stale", "note_id": note.id, "user_id": note.subject.user_id}

    logger.info("Note indexing succeeded: note_id=%s user_id=%s", note.id, note.subject.user_id)
    return {"status": "indexed", "note_id": note.id, "user_id": note.subject.user_id}


@shared_task
def run_background_email_sync(integration_id: int):
    """Run one due background email sync and create an assistant digest if needed."""
    sync_service = EmailSyncService()
    suggestion_service = EmailSuggestionService()
    now = timezone.now()
    scheduled_slot = None

    with transaction.atomic():
        try:
            integration = (
                EmailIntegration.objects.select_for_update()
                .select_related("user")
                .get(pk=integration_id)
            )
        except EmailIntegration.DoesNotExist:
            return {"status": "missing"}

        if (
            not integration.is_active
            or not integration.auto_sync_enabled
            or not integration.next_auto_sync_at
            or integration.next_auto_sync_at > now
        ):
            return {"status": "skipped"}
        scheduled_slot = integration.next_auto_sync_at

        latest_success = (
            EmailSyncRun.objects.filter(
                integration=integration,
                status=EmailSyncRun.STATUS_COMPLETED,
            )
            .order_by("-to_datetime")
            .first()
        )
        from_dt = latest_success.to_datetime if latest_success else now - integration.get_auto_sync_delta()
        to_dt = now
        date_preset = integration.get_auto_sync_date_preset()
        next_auto_sync_at = _compute_next_regular_email_sync_at(
            integration,
            scheduled_slot,
            now,
        )
        integration.next_auto_sync_at = next_auto_sync_at
        integration.save(update_fields=["next_auto_sync_at", "updated_at"])

    try:
        sync_run, messages = sync_service.run_sync_window(
            user=integration.user,
            integration=integration,
            from_dt=from_dt,
            to_dt=to_dt,
            date_preset=date_preset,
            trigger_type=EmailSyncRun.TRIGGER_BACKGROUND,
        )
        suggestion_service.generate_suggestions(sync_run=sync_run, messages=messages)
        sync_run.refresh_from_db(fields=["suggestions_count"])
        digest = create_email_digest_for_sync_run(sync_run)
        return {
            "status": "completed",
            "sync_run_id": sync_run.id,
            "digest_created": bool(digest),
        }
    except Exception as exc:
        with transaction.atomic():
            try:
                integration = EmailIntegration.objects.select_for_update().get(pk=integration_id)
            except EmailIntegration.DoesNotExist:
                return {"status": "failed", "error": str(exc)[:200]}
            if scheduled_slot:
                next_auto_sync_at = _compute_next_regular_email_sync_at(
                    integration,
                    scheduled_slot,
                    timezone.now(),
                )
                integration.next_auto_sync_at = next_auto_sync_at
                integration.save(update_fields=["next_auto_sync_at", "updated_at"])
        raise


@shared_task
def queue_due_email_syncs():
    """Queue background email sync tasks for integrations whose next run is due."""
    now = timezone.now()
    due_ids = list(
        EmailIntegration.objects.filter(
            is_active=True,
            auto_sync_enabled=True,
            next_auto_sync_at__isnull=False,
            next_auto_sync_at__lte=now,
        )
        .order_by("next_auto_sync_at")
        .values_list("id", flat=True)
    )

    for integration_id in due_ids:
        run_background_email_sync.delay(integration_id)

    return {"queued": len(due_ids)}
