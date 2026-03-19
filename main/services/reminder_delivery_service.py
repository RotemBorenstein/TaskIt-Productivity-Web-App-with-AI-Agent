"""Compose and dispatch reminder notifications across configured channels."""

from __future__ import annotations

from django.utils import timezone

from main.models import Reminder
from main.services.notification_email_service import NotificationEmailService
from main.services.reminder_service import get_user_notification_settings
from main.services.telegram_notification_service import TelegramNotificationService


def _task_title(task):
    if task.task_type == "daily":
        return f"Daily task reminder: {task.title}"
    return f"Task reminder: {task.title}"


def _event_title(event):
    return f"Event reminder: {event.title}"


def _build_payload(reminder: Reminder):
    tz = timezone.get_current_timezone()
    if reminder.task_id:
        task = reminder.task
        if task.task_type == "daily":
            subject = _task_title(task)
            text = f"Reminder: {task.title}\nThis is your daily task reminder."
            html = f"<p><strong>{task.title}</strong></p><p>This is your daily task reminder.</p>"
        else:
            due_bits = [task.due_date.isoformat()]
            if task.due_time:
                due_bits.append(task.due_time.strftime("%H:%M"))
            subject = _task_title(task)
            text = f"Reminder: {task.title}\nDue: {' '.join(due_bits)}"
            html = f"<p><strong>{task.title}</strong></p><p>Due: {' '.join(due_bits)}</p>"
        return subject, text, html

    event = reminder.event
    start_local = timezone.localtime(event.start_datetime, tz).strftime("%Y-%m-%d %H:%M")
    subject = _event_title(event)
    text = f"Reminder: {event.title}\nStarts at: {start_local}"
    html = f"<p><strong>{event.title}</strong></p><p>Starts at: {start_local}</p>"
    return subject, text, html


def dispatch_reminder(reminder: Reminder) -> list[str]:
    """
    Deliver a reminder across configured channels. Returns a list of error strings.
    """
    notification_settings = get_user_notification_settings(reminder.user)
    email_service = NotificationEmailService()
    telegram_service = TelegramNotificationService()
    subject, text, html = _build_payload(reminder)
    errors = []

    if reminder.channel_email and notification_settings.email_enabled:
        if reminder.user.email:
            result = email_service.send(
                to_email=reminder.user.email,
                subject=subject,
                html=html,
                text=text,
            )
            if not result.success:
                errors.append(result.error or "Email send failed.")
        else:
            errors.append("User does not have an email address.")

    if reminder.channel_telegram and notification_settings.telegram_enabled:
        if notification_settings.telegram_chat_id:
            result = telegram_service.send_message(
                chat_id=notification_settings.telegram_chat_id,
                text=text,
            )
            if not result.success:
                errors.append(result.error or "Telegram send failed.")
        else:
            errors.append("Telegram is enabled but not connected.")

    if reminder.channel_email and not notification_settings.email_enabled:
        errors.append("Email notifications are disabled in settings.")
    if reminder.channel_telegram and not notification_settings.telegram_enabled:
        errors.append("Telegram notifications are disabled in settings.")

    return errors
