"""Celery tasks for reminder delivery."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from main.models import Reminder
from main.services.reminder_delivery_service import dispatch_reminder


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
