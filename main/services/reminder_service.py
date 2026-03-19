"""
Helpers for creating and synchronizing reminders and notification settings.
"""

from __future__ import annotations

import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from main.models import Event, Reminder, Task, UserNotificationSettings

def get_user_notification_settings(user) -> UserNotificationSettings:
    """Lazily create notification settings for the given user."""
    settings_obj, _ = UserNotificationSettings.objects.get_or_create(user=user)
    return settings_obj


def ensure_telegram_connect_token(settings_obj: UserNotificationSettings) -> UserNotificationSettings:
    """Ensure the settings row has a stable token used for Telegram linking."""
    if settings_obj.telegram_connect_token:
        return settings_obj

    settings_obj.telegram_connect_token = secrets.token_urlsafe(24)
    settings_obj.save(update_fields=["telegram_connect_token", "updated_at"])
    return settings_obj


@transaction.atomic
def sync_task_reminder(
    task: Task,
    *,
    reminder_enabled: bool,
    reminder_time,
    channel_email: bool,
    channel_telegram: bool,
):
    """Create, update, or remove the single reminder for a task."""
    existing = getattr(task, "reminder", None)

    if not reminder_enabled:
        if existing:
            existing.delete()
        return None

    if task.task_type == "long_term" and not task.due_date:
        raise ValidationError("Long-term task reminders require a due date.")

    if not reminder_time:
        raise ValidationError("Reminder time is required when reminders are enabled.")

    if not channel_email and not channel_telegram:
        raise ValidationError("Choose at least one reminder channel.")

    reminder = existing or Reminder(user=task.user, task=task)
    reminder.user = task.user
    reminder.task = task
    reminder.event = None
    reminder.kind = (
        Reminder.KIND_DAILY_TASK if task.task_type == "daily" else Reminder.KIND_TASK_DUE
    )
    reminder.remind_at_time = reminder_time
    reminder.offset_minutes = None
    reminder.channel_email = channel_email
    reminder.channel_telegram = channel_telegram
    reminder.is_enabled = True
    reminder.next_run_at = reminder.compute_next_run_at()
    reminder.full_clean()
    reminder.save()
    return reminder


@transaction.atomic
def sync_event_reminder(
    event: Event,
    *,
    offset_minutes,
    channel_email: bool,
    channel_telegram: bool,
):
    """Create, update, or remove the single reminder for an event."""
    existing = getattr(event, "reminder", None)

    if offset_minutes in (None, "", "none"):
        if existing:
            existing.delete()
        return None

    if not channel_email and not channel_telegram:
        raise ValidationError("Choose at least one reminder channel.")

    offset_value = int(offset_minutes)
    reminder = existing or Reminder(user=event.user, event=event)
    reminder.user = event.user
    reminder.event = event
    reminder.task = None
    reminder.kind = Reminder.KIND_EVENT_OFFSET
    reminder.remind_at_time = None
    reminder.offset_minutes = offset_value
    reminder.channel_email = channel_email
    reminder.channel_telegram = channel_telegram
    reminder.is_enabled = True
    reminder.next_run_at = reminder.compute_next_run_at()
    reminder.full_clean()
    reminder.save()
    return reminder


def refresh_item_reminder(instance):
    """Recompute an existing reminder schedule after task/event changes."""
    reminder = getattr(instance, "reminder", None)
    if not reminder:
        return None

    next_run_at = reminder.compute_next_run_at()
    Reminder.objects.filter(pk=reminder.pk).update(
        next_run_at=next_run_at,
        last_error="",
        updated_at=timezone.now(),
    )
    reminder.next_run_at = next_run_at
    reminder.last_error = ""
    return reminder
