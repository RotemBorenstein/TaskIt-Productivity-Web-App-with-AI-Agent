"""Model signals that keep reminder schedules synchronized."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from main.models import Event, Task
from main.services.reminder_service import refresh_item_reminder


@receiver(post_save, sender=Task)
def sync_task_reminder_schedule(sender, instance, **kwargs):
    """Refresh task reminder timing after task fields change anywhere in the app."""
    refresh_item_reminder(instance)


@receiver(post_save, sender=Event)
def sync_event_reminder_schedule(sender, instance, **kwargs):
    """Refresh event reminder timing after event fields change anywhere in the app."""
    refresh_item_reminder(instance)
