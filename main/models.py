from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings

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