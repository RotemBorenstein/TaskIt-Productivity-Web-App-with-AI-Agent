"""Forms for task and event management, including reminder configuration."""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Event, Reminder, Task


class TaskForm(forms.ModelForm):
    """
    Task form with a single optional reminder configuration.
    """

    reminder_enabled = forms.BooleanField(required=False, label="Enable reminder")
    reminder_time = forms.TimeField(
        required=False,
        label="Reminder time",
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
    )

    class Meta:
        model = Task
        fields = ["title", "description", "due_date", "due_time"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter task title",
                    "required": True,
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Optional description",
                }
            ),
            "due_date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "due_time": forms.TimeInput(
                attrs={"type": "time", "class": "form-control"}
            ),
        }
        labels = {
            "title": "Title",
            "description": "Description",
            "due_date": "Due date",
            "due_time": "Due time",
        }

    def __init__(self, *args, **kwargs):
        self.task_type = kwargs.pop("task_type", None)
        self.notification_settings = kwargs.pop("notification_settings", None)
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and not self.task_type:
            self.task_type = self.instance.task_type

        reminder = getattr(self.instance, "reminder", None)
        if reminder:
            self.initial.setdefault("reminder_enabled", True)
            self.initial.setdefault("reminder_time", reminder.remind_at_time)

        if self.task_type == "daily":
            self.fields["due_date"].widget = forms.HiddenInput()
            self.fields["due_time"].widget = forms.HiddenInput()
            self.fields["reminder_time"].label = "Daily reminder time"
        else:
            self.fields["reminder_time"].label = "Reminder time"

    def clean(self):
        cleaned = super().clean()
        due_date = cleaned.get("due_date")
        reminder_enabled = cleaned.get("reminder_enabled")
        reminder_time = cleaned.get("reminder_time")
        reminder = getattr(self.instance, "reminder", None)
        telegram_connected = bool(
            self.notification_settings
            and self.notification_settings.telegram_is_connected
        )

        if self.task_type == "daily":
            cleaned["due_date"] = None
            cleaned["due_time"] = None
        elif reminder_enabled and not due_date:
            raise ValidationError("Long-term task reminders require a due date.")

        if reminder_enabled:
            if not reminder_time:
                raise ValidationError("Choose a reminder time.")
            if not telegram_connected:
                preserving_existing = (
                    reminder is not None
                    and reminder.channel_telegram
                    and reminder.remind_at_time == reminder_time
                )
                if not preserving_existing:
                    raise ValidationError(
                        "Connect Telegram in Settings before saving reminders."
                    )

        return cleaned


class EventForm(forms.ModelForm):
    reminder_offset_minutes = forms.TypedChoiceField(
        required=False,
        choices=[("", "No reminder")] + list(Reminder.OFFSET_PRESET_CHOICES),
        coerce=int,
        empty_value=None,
        label="Reminder",
    )
    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "start_datetime",
            "end_datetime",
            "all_day",
            "task",
        ]
        widgets = {
            "start_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.notification_settings = kwargs.pop("notification_settings", None)
        super().__init__(*args, **kwargs)
        reminder = getattr(self.instance, "reminder", None)
        if reminder:
            self.initial.setdefault("reminder_offset_minutes", reminder.offset_minutes)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_datetime")
        end = cleaned.get("end_datetime")
        all_day = cleaned.get("all_day")
        reminder_offset = cleaned.get("reminder_offset_minutes")
        reminder = getattr(self.instance, "reminder", None)
        telegram_connected = bool(
            self.notification_settings
            and self.notification_settings.telegram_is_connected
        )

        if start and timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
            cleaned["start_datetime"] = start
        if end and timezone.is_naive(end):
            end = timezone.make_aware(end, timezone.get_current_timezone())
            cleaned["end_datetime"] = end

        if start and end and end <= start:
            raise ValidationError("End must be after start.")

        # FullCalendar end is exclusive for all-day. If all_day and same-day,
        # bump end to next midnight to follow that convention.
        if all_day and start and end and start.date() == end.date():
            cleaned["end_datetime"] = timezone.make_aware(
                timezone.datetime.combine(end.date(), timezone.datetime.min.time())
            ) + timezone.timedelta(days=1)

        if reminder_offset is not None and not telegram_connected:
            preserving_existing = (
                reminder is not None and reminder.offset_minutes == reminder_offset
            )
            if not preserving_existing:
                raise ValidationError(
                    "Connect Telegram in Settings before saving reminders."
                )

        return cleaned
