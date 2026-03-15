"""Celery app bootstrap for TaskIt."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TaskIt.settings")

app = Celery("TaskIt")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
