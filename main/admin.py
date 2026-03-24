# main/admin.py
from django.contrib import admin
from django.utils import timezone
from datetime import datetime, time, timedelta
from .models import (
    AssistantInboxItem,
    EmailIntegration,
    EmailSyncRun,
    Event,
    Reminder,
    Task,
    UserNotificationSettings,
)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "start_datetime", "end_datetime", "all_day")
    list_filter = ("all_day", "start_datetime")
    search_fields = ("title", "description")
    date_hierarchy = "start_datetime"
    ordering = ("-start_datetime",)
    exclude = ("user",)  # set automatically

    def save_model(self, request, obj, form, change):
        # Auto-assign creator if empty
        if not obj.user_id:
            obj.user = request.user

        # Normalize all-day events to midnight boundaries (end is exclusive)
        if obj.all_day:
            tz = timezone.get_current_timezone()

            # If start is missing (shouldn't be, but just in case), use today
            if obj.start_datetime:
                start_date = obj.start_datetime.astimezone(tz).date()
            else:
                start_date = timezone.localdate()

            # If end provided, use its date; otherwise default to same start day
            if obj.end_datetime:
                end_date = obj.end_datetime.astimezone(tz).date()
                # Guard: don’t allow end before start
                if end_date < start_date:
                    end_date = start_date
            else:
                end_date = start_date

            # Build aware datetimes at local midnight
            start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
            # FullCalendar expects end to be exclusive midnight *after* the last day
            end_dt = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)

            obj.start_datetime = start_dt
            obj.end_datetime = end_dt

        super().save_model(request, obj, form, change)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "task_type", "due_date", "due_time", "is_active")
    list_filter = ("task_type", "is_active", "is_completed")
    search_fields = ("title", "description")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "kind",
        "task",
        "event",
        "next_run_at",
        "channel_email",
        "channel_telegram",
        "is_enabled",
    )
    list_filter = ("kind", "channel_email", "channel_telegram", "is_enabled")
    search_fields = ("task__title", "event__title", "user__username")


@admin.register(UserNotificationSettings)
class UserNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "email_enabled",
        "telegram_enabled",
        "telegram_chat_id",
        "telegram_connected_at",
    )
    search_fields = ("user__username", "user__email", "telegram_chat_id")


@admin.register(EmailIntegration)
class EmailIntegrationAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "provider",
        "email_address",
        "is_active",
        "auto_sync_enabled",
        "auto_sync_frequency_hours",
        "auto_sync_time",
        "auto_sync_weekday",
        "next_auto_sync_at",
    )
    list_filter = ("provider", "is_active", "auto_sync_enabled")
    search_fields = ("user__username", "email_address")


@admin.register(EmailSyncRun)
class EmailSyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "integration",
        "trigger_type",
        "date_preset",
        "status",
        "emails_scanned_count",
        "suggestions_count",
        "finished_at",
    )
    list_filter = ("trigger_type", "status", "date_preset")
    search_fields = ("user__username", "integration__email_address")


@admin.register(AssistantInboxItem)
class AssistantInboxItemAdmin(admin.ModelAdmin):
    list_display = ("user", "item_type", "is_read", "created_at", "sync_run")
    list_filter = ("item_type", "is_read")
    search_fields = ("user__username", "title", "body")
