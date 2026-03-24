from datetime import datetime, time, timedelta

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from pgvector.django import VectorField

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None

class Task(models.Model):
    TASK_TYPE_CHOICES = [
        ("daily", "Daily"),
        ("long_term", "Long Term"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="tasks"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(
        max_length=10, choices=TASK_TYPE_CHOICES, default="long_term"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_anchored = models.BooleanField(default=False)
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)


    def __str__(self):
        return f"{self.title} ({self.task_type})"

    @property
    def due_datetime(self):
        """Return the local due datetime when both due fields are present."""
        if not self.due_date or not self.due_time:
            return None
        dt = datetime.combine(self.due_date, self.due_time)
        return timezone.make_aware(dt, timezone.get_current_timezone())


class DailyTaskCompletion(models.Model):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="completions"
    )
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    completed = models.BooleanField(default = False)


    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'date'], name='unique_task_date')]

    def __str__(self):
        return f"{self.task.title} completed on {self.date}"


class Event(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="events"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    all_day = models.BooleanField(default=False)

    # Optional: link to an existing task
    task = models.ForeignKey(
        'Task',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_datetime']

    def __str__(self):
        return f"{self.title} ({self.start_datetime})"


class UserNotificationSettings(models.Model):
    """
    Stores user-level notification channel preferences and Telegram linkage.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_settings",
    )
    email_enabled = models.BooleanField(default=False)
    telegram_enabled = models.BooleanField(default=False)
    telegram_chat_id = models.CharField(max_length=64, blank=True)
    telegram_connect_token = models.CharField(max_length=64, blank=True)
    telegram_connected_at = models.DateTimeField(null=True, blank=True)
    last_test_email_at = models.DateTimeField(null=True, blank=True)
    last_test_telegram_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification settings for user {self.user_id}"

    @property
    def telegram_is_connected(self) -> bool:
        return bool(self.telegram_chat_id)


class Reminder(models.Model):
    """
    Stores the single reminder configuration attached to a task or event.
    """

    KIND_TASK_DUE = "task_due"
    KIND_DAILY_TASK = "daily_task"
    KIND_EVENT_OFFSET = "event_offset"

    KIND_CHOICES = [
        (KIND_TASK_DUE, "Long-term Task Due Date"),
        (KIND_DAILY_TASK, "Daily Task"),
        (KIND_EVENT_OFFSET, "Event Offset"),
    ]

    OFFSET_PRESET_CHOICES = [
        (0, "At time"),
        (5, "5 minutes before"),
        (15, "15 minutes before"),
        (60, "1 hour before"),
        (1440, "1 day before"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    task = models.OneToOneField(
        "Task",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reminder",
    )
    event = models.OneToOneField(
        "Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reminder",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    remind_at_time = models.TimeField(null=True, blank=True)
    offset_minutes = models.IntegerField(null=True, blank=True)
    channel_email = models.BooleanField(default=False)
    channel_telegram = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(task__isnull=False) & models.Q(event__isnull=True))
                    | (models.Q(task__isnull=True) & models.Q(event__isnull=False))
                ),
                name="reminder_exactly_one_target",
            ),
            models.CheckConstraint(
                check=models.Q(channel_email=True) | models.Q(channel_telegram=True),
                name="reminder_requires_channel",
            ),
        ]

    def __str__(self):
        target = self.task or self.event
        return f"Reminder {self.id} for {target}"

    def clean(self):
        super().clean()

        if bool(self.task_id) == bool(self.event_id):
            raise ValidationError("Reminder must target exactly one task or event.")

        if not self.channel_email and not self.channel_telegram:
            raise ValidationError("Choose at least one reminder channel.")

        if self.task_id:
            if self.user_id and self.task and self.task.user_id != self.user_id:
                raise ValidationError("Reminder task must belong to the same user.")
            if self.kind == self.KIND_TASK_DUE:
                if self.task.task_type != "long_term":
                    raise ValidationError("Due-date reminders are only valid for long-term tasks.")
                if not self.task.due_date:
                    raise ValidationError("Long-term task reminders require a due date.")
                if not self.remind_at_time:
                    raise ValidationError("Long-term task reminders require a reminder time.")
            elif self.kind == self.KIND_DAILY_TASK:
                if self.task.task_type != "daily":
                    raise ValidationError("Daily reminders are only valid for daily tasks.")
                if not self.remind_at_time:
                    raise ValidationError("Daily reminders require a reminder time.")
            else:
                raise ValidationError("Task reminders must use a task reminder kind.")

        if self.event_id:
            if self.user_id and self.event and self.event.user_id != self.user_id:
                raise ValidationError("Reminder event must belong to the same user.")
            if self.kind != self.KIND_EVENT_OFFSET:
                raise ValidationError("Event reminders must use an offset reminder kind.")
            if self.offset_minutes is None:
                raise ValidationError("Event reminders require an offset.")

    def is_target_active(self) -> bool:
        """Return whether the reminder target can still produce notifications."""
        if not self.is_enabled:
            return False

        if self.task_id:
            task = self.task
            if task.task_type == "long_term":
                return bool(task.is_active and not task.is_completed and task.due_date)
            return bool(task.is_active)

        if self.event_id:
            return self.event.start_datetime is not None

        return False

    def compute_next_run_at(self, reference_time=None):
        """
        Compute the next scheduled datetime for this reminder in the project timezone.
        """
        if reference_time is None:
            reference_time = timezone.now()

        if not self.is_target_active():
            return None

        current_tz = timezone.get_current_timezone()

        if self.kind == self.KIND_TASK_DUE:
            base_dt = datetime.combine(self.task.due_date, self.remind_at_time)
            scheduled = timezone.make_aware(base_dt, current_tz)
            return scheduled if scheduled > reference_time else None

        if self.kind == self.KIND_DAILY_TASK:
            today_local = timezone.localtime(reference_time, current_tz).date()
            completed_today = DailyTaskCompletion.objects.filter(
                task=self.task,
                date=today_local,
                completed=True,
            ).exists()
            scheduled = timezone.make_aware(
                datetime.combine(today_local, self.remind_at_time),
                current_tz,
            )
            if completed_today or scheduled <= reference_time:
                scheduled += timedelta(days=1)
            return scheduled

        if self.kind == self.KIND_EVENT_OFFSET:
            scheduled = self.event.start_datetime - timedelta(minutes=self.offset_minutes or 0)
            return scheduled if scheduled > reference_time else None

        return None

    def sync_schedule(self, save=True):
        """
        Recalculate the next run and clear stale delivery errors when inputs change.
        """
        self.next_run_at = self.compute_next_run_at()
        if self.next_run_at is None:
            self.last_error = ""
        if save:
            self.save(update_fields=["next_run_at", "last_error", "updated_at"])

class Subject(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.TextField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    color = models.CharField(max_length=20, blank=True)
    class Meta:
        unique_together = ('user', 'title')
class Note(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    title = models.TextField()
    content = models.TextField(max_length=1000)
    pinned = models.BooleanField(default=False)
    tags = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RagChunk(models.Model):
    """
    Stores note chunks and embeddings for semantic retrieval via pgvector.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rag_chunks",
    )
    doc_type = models.CharField(max_length=20, default="note")
    doc_key = models.CharField(max_length=100)
    chunk_index = models.PositiveIntegerField(default=0)
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="rag_chunks",
        null=True,
        blank=True,
    )
    note = models.ForeignKey(
        Note,
        on_delete=models.CASCADE,
        related_name="rag_chunks",
        null=True,
        blank=True,
    )
    subject_title = models.CharField(max_length=100, blank=True)
    note_title = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "doc_key"]),
            models.Index(fields=["note"]),
            models.Index(fields=["subject"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.doc_key} / chunk {self.chunk_index}"


class AgentChatMessage(models.Model):
    ROLE_HUMAN = "human"
    ROLE_AI = "ai"
    ROLE_SYSTEM = "system"

    ROLE_CHOICES = [
        (ROLE_HUMAN, "Human"),
        (ROLE_AI, "AI"),
        (ROLE_SYSTEM, "System"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_messages",
    )
    session_id = models.CharField(max_length=64)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["user", "session_id", "created_at"]),
        ]

    def __str__(self):
        return f"[{self.user_id} / {self.session_id}] {self.role}: {self.content[:50]}"


class EmailIntegration(models.Model):
    PROVIDER_GMAIL = "gmail"
    PROVIDER_OUTLOOK = "outlook"
    AUTO_SYNC_24_HOURS = 24
    AUTO_SYNC_48_HOURS = 48
    AUTO_SYNC_168_HOURS = 168

    PROVIDER_CHOICES = [
        (PROVIDER_GMAIL, "Gmail"),
        (PROVIDER_OUTLOOK, "Outlook"),
    ]
    AUTO_SYNC_FREQUENCY_CHOICES = [
        (AUTO_SYNC_24_HOURS, "24 Hours"),
        (AUTO_SYNC_48_HOURS, "48 Hours"),
        (AUTO_SYNC_168_HOURS, "7 Days"),
    ]
    AUTO_SYNC_WEEKDAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]
    DEFAULT_AUTO_SYNC_TIME = time(hour=20, minute=0)
    DEFAULT_AUTO_SYNC_WEEKDAY = 6

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_integrations",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    provider_account_id = models.CharField(max_length=255)
    email_address = models.EmailField()
    scopes = models.JSONField(default=list, blank=True)
    encrypted_refresh_token = models.TextField()
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    auto_sync_enabled = models.BooleanField(default=False)
    auto_sync_frequency_hours = models.PositiveSmallIntegerField(
        choices=AUTO_SYNC_FREQUENCY_CHOICES,
        default=AUTO_SYNC_24_HOURS,
    )
    auto_sync_time = models.TimeField(default=DEFAULT_AUTO_SYNC_TIME)
    auto_sync_weekday = models.PositiveSmallIntegerField(
        choices=AUTO_SYNC_WEEKDAY_CHOICES,
        null=True,
        blank=True,
    )
    next_auto_sync_at = models.DateTimeField(null=True, blank=True)
    token_version = models.PositiveIntegerField(default=1)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider"], name="unique_user_email_provider"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "provider", "is_active"]),
            models.Index(fields=["provider_account_id"]),
            models.Index(fields=["is_active", "auto_sync_enabled", "next_auto_sync_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.provider} / {self.email_address}"

    def get_auto_sync_delta(self) -> timedelta:
        """Return the configured automatic sync interval as a timedelta."""
        return timedelta(hours=self.auto_sync_frequency_hours)

    def get_auto_sync_weekday_value(self) -> int:
        """Return the configured weekday, falling back to Sunday for weekly schedules."""
        if self.auto_sync_frequency_hours == self.AUTO_SYNC_168_HOURS:
            return (
                self.auto_sync_weekday
                if self.auto_sync_weekday is not None
                else self.DEFAULT_AUTO_SYNC_WEEKDAY
            )
        return self.auto_sync_weekday if self.auto_sync_weekday is not None else self.DEFAULT_AUTO_SYNC_WEEKDAY

    def get_auto_sync_weekday_name(self) -> str | None:
        """Return the configured weekday name for weekly schedules."""
        if self.auto_sync_frequency_hours != self.AUTO_SYNC_168_HOURS:
            return None
        weekday_map = dict(self.AUTO_SYNC_WEEKDAY_CHOICES)
        return weekday_map.get(self.get_auto_sync_weekday_value(), "Sunday").lower()

    def get_auto_sync_date_preset(self) -> str:
        """Map the saved frequency to the sync-run preset stored in EmailSyncRun."""
        if self.auto_sync_frequency_hours == self.AUTO_SYNC_24_HOURS:
            return EmailSyncRun.PRESET_DAY
        if self.auto_sync_frequency_hours == self.AUTO_SYNC_48_HOURS:
            return EmailSyncRun.PRESET_48_HOURS
        return EmailSyncRun.PRESET_WEEK

    def compute_next_auto_sync_at(self, reference_time=None, from_scheduled_slot: bool = False):
        """Compute the next scheduled automatic sync in the project timezone."""
        tz = timezone.get_current_timezone()
        if reference_time is None:
            reference_time = timezone.now()
        if timezone.is_naive(reference_time):
            reference_time = timezone.make_aware(reference_time, tz)
        else:
            reference_time = timezone.localtime(reference_time, tz)

        scheduled_time = self.auto_sync_time or self.DEFAULT_AUTO_SYNC_TIME

        if self.auto_sync_frequency_hours == self.AUTO_SYNC_24_HOURS:
            if from_scheduled_slot:
                next_local = timezone.localtime(reference_time, tz) + timedelta(days=1)
                return timezone.make_aware(
                    datetime.combine(next_local.date(), scheduled_time),
                    tz,
                )
            candidate = timezone.make_aware(
                datetime.combine(reference_time.date(), scheduled_time),
                tz,
            )
            if candidate <= reference_time:
                candidate += timedelta(days=1)
            return candidate

        if self.auto_sync_frequency_hours == self.AUTO_SYNC_48_HOURS:
            if from_scheduled_slot:
                next_local = timezone.localtime(reference_time, tz) + timedelta(days=2)
                return timezone.make_aware(
                    datetime.combine(next_local.date(), scheduled_time),
                    tz,
                )
            candidate = timezone.make_aware(
                datetime.combine(reference_time.date(), scheduled_time),
                tz,
            )
            if candidate <= reference_time:
                candidate += timedelta(days=1)
            return candidate

        weekday = self.get_auto_sync_weekday_value()
        if from_scheduled_slot:
            next_local = timezone.localtime(reference_time, tz) + timedelta(days=7)
            return timezone.make_aware(
                datetime.combine(next_local.date(), scheduled_time),
                tz,
            )

        current_weekday = reference_time.weekday()
        days_ahead = (weekday - current_weekday) % 7
        candidate_date = reference_time.date() + timedelta(days=days_ahead)
        candidate = timezone.make_aware(
            datetime.combine(candidate_date, scheduled_time),
            tz,
        )
        if candidate <= reference_time:
            candidate += timedelta(days=7)
        return candidate


class EmailOAuthState(models.Model):
    provider = models.CharField(max_length=20, choices=EmailIntegration.PROVIDER_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_oauth_states",
    )
    state_hash = models.CharField(max_length=255, unique=True)
    redirect_uri = models.URLField(max_length=500)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "provider", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.provider} / expires {self.expires_at}"


class EmailSyncRun(models.Model):
    PRESET_DAY = "day"
    PRESET_48_HOURS = "48h"
    PRESET_WEEK = "week"

    DATE_PRESET_CHOICES = [
        (PRESET_DAY, "Day"),
        (PRESET_48_HOURS, "48 Hours"),
        (PRESET_WEEK, "Week"),
    ]
    TRIGGER_MANUAL = "manual"
    TRIGGER_BACKGROUND = "background"

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]
    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL, "Manual"),
        (TRIGGER_BACKGROUND, "Background"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_sync_runs",
    )
    integration = models.ForeignKey(
        EmailIntegration,
        on_delete=models.CASCADE,
        related_name="sync_runs",
    )
    date_preset = models.CharField(max_length=20, choices=DATE_PRESET_CHOICES)
    from_datetime = models.DateTimeField()
    to_datetime = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    trigger_type = models.CharField(
        max_length=20,
        choices=TRIGGER_CHOICES,
        default=TRIGGER_MANUAL,
    )
    emails_scanned_count = models.PositiveIntegerField(default=0)
    suggestions_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["integration", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.integration.provider} / {self.status}"


class AssistantInboxItem(models.Model):
    TYPE_EMAIL_DIGEST = "email_digest"

    ITEM_TYPE_CHOICES = [
        (TYPE_EMAIL_DIGEST, "Email Digest"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_inbox_items",
    )
    sync_run = models.ForeignKey(
        EmailSyncRun,
        on_delete=models.CASCADE,
        related_name="assistant_inbox_items",
        null=True,
        blank=True,
    )
    item_type = models.CharField(max_length=30, choices=ITEM_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    body = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_read", "created_at"]),
            models.Index(fields=["sync_run", "item_type"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.item_type} / {'read' if self.is_read else 'unread'}"


class EmailSuggestion(models.Model):
    TYPE_TASK = "task"
    TYPE_EVENT = "event"

    SUGGESTION_TYPE_CHOICES = [
        (TYPE_TASK, "Task"),
        (TYPE_EVENT, "Event"),
    ]
    TASK_TYPE_DAILY = "daily"
    TASK_TYPE_LONG_TERM = "long_term"
    TASK_TYPE_CHOICES = [
        (TASK_TYPE_DAILY, "Daily"),
        (TASK_TYPE_LONG_TERM, "Long Term"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_DUPLICATE = "duplicate"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_DUPLICATE, "Duplicate"),
        (STATUS_FAILED, "Failed"),
    ]
    REJECTION_REASON_NOT_ACTIONABLE = "not_actionable"
    REJECTION_REASON_NEWSLETTER = "newsletter_or_automated"
    REJECTION_REASON_WRONG_TASK = "wrong_task"
    REJECTION_REASON_WRONG_EVENT = "wrong_event"
    REJECTION_REASON_QUOTED_THREAD = "quoted_old_thread"
    REJECTION_REASON_OTHER = "other"
    REJECTION_REASON_CHOICES = [
        (REJECTION_REASON_NOT_ACTIONABLE, "Not Actionable"),
        (REJECTION_REASON_NEWSLETTER, "Newsletter Or Automated"),
        (REJECTION_REASON_WRONG_TASK, "Wrong Task"),
        (REJECTION_REASON_WRONG_EVENT, "Wrong Event"),
        (REJECTION_REASON_QUOTED_THREAD, "Quoted Old Thread"),
        (REJECTION_REASON_OTHER, "Other"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_suggestions",
    )
    sync_run = models.ForeignKey(
        EmailSyncRun,
        on_delete=models.CASCADE,
        related_name="suggestions",
    )
    suggestion_type = models.CharField(max_length=10, choices=SUGGESTION_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type_hint = models.CharField(max_length=10, choices=TASK_TYPE_CHOICES, blank=True)
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    model_confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    reason = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=255, blank=True)
    ai_payload = models.JSONField(default=dict, blank=True)
    source_message_refs = models.JSONField(default=list, blank=True)
    digest_eligible = models.BooleanField(default=False)
    rejection_reason = models.CharField(
        max_length=40,
        choices=REJECTION_REASON_CHOICES,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_suggestions",
    )
    created_event = models.ForeignKey(
        Event,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_suggestions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["sync_run", "created_at"]),
            models.Index(fields=["suggestion_type"]),
            models.Index(fields=["fingerprint"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.suggestion_type} / {self.status} / {self.title[:40]}"


class EmailSyncedMessage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_synced_messages",
    )
    integration = models.ForeignKey(
        EmailIntegration,
        on_delete=models.CASCADE,
        related_name="synced_messages",
    )
    sync_run = models.ForeignKey(
        EmailSyncRun,
        on_delete=models.CASCADE,
        related_name="synced_messages",
    )
    message_id = models.CharField(max_length=255)
    sender = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField()
    encrypted_subject = models.TextField(blank=True)
    encrypted_body = models.TextField(blank=True)
    stored_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["integration", "message_id"],
                name="unique_synced_message_per_integration",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["integration", "received_at"]),
            models.Index(fields=["sync_run", "stored_at"]),
        ]

    @staticmethod
    def _get_fernet():
        if Fernet is None:
            raise ImproperlyConfigured(
                "Missing dependency: install 'cryptography' to use email sync encryption."
            )
        encryption_key = getattr(settings, "EMAIL_TOKEN_ENCRYPTION_KEY", None)
        if not encryption_key:
            raise ImproperlyConfigured("EMAIL_TOKEN_ENCRYPTION_KEY must be configured.")
        try:
            return Fernet(encryption_key.encode("utf-8"))
        except Exception as exc:  # pragma: no cover
            raise ImproperlyConfigured("EMAIL_TOKEN_ENCRYPTION_KEY is invalid.") from exc

    @classmethod
    def encrypt_value(cls, value: str) -> str:
        if not value:
            return ""
        return cls._get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt_value(cls, value: str) -> str:
        if not value:
            return ""
        return cls._get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")

    @property
    def subject(self) -> str:
        return self.decrypt_value(self.encrypted_subject)

    @subject.setter
    def subject(self, value: str):
        self.encrypted_subject = self.encrypt_value(value or "")

    @property
    def body(self) -> str:
        return self.decrypt_value(self.encrypted_body)

    @body.setter
    def body(self, value: str):
        self.encrypted_body = self.encrypt_value(value or "")
