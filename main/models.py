from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
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


    def __str__(self):
        return f"{self.title} ({self.task_type})"


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

    PROVIDER_CHOICES = [
        (PROVIDER_GMAIL, "Gmail"),
        (PROVIDER_OUTLOOK, "Outlook"),
    ]

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
        ]

    def __str__(self):
        return f"{self.user_id} / {self.provider} / {self.email_address}"


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
    PRESET_WEEK = "week"

    DATE_PRESET_CHOICES = [
        (PRESET_DAY, "Day"),
        (PRESET_WEEK, "Week"),
    ]

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
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    reason = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=255, blank=True)
    ai_payload = models.JSONField(default=dict, blank=True)
    source_message_refs = models.JSONField(default=list, blank=True)
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
