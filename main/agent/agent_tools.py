from typing import Union, Dict, Any
from django.utils.dateparse import parse_datetime
from zoneinfo import ZoneInfo
from django.utils import timezone
from langchain.tools import tool
from main.models import Task, Event
from main.stats_utils import get_completion_rate, get_completed_daily_tasks_count, detect_granularity
from datetime import datetime, time, timedelta
from django.db import transaction

def make_user_tools(user):
    """Return a list of LangChain tools bound to a specific user."""

    @tool
    def add_task(title: str, task_type: str = "daily") -> str:
        """Add a new task. task_type can be 'daily' or 'long_term'."""
        if task_type not in ["daily", "long_term"]:
            return f"Invalid task type '{task_type}'. Please choose 'daily' or 'long_term'."

        task = Task.objects.create(user=user, title=title, task_type=task_type)
        if task_type == "daily":
            return f"Daily task '{task.title}' created."
        else:
            return f"Long-term task '{task.title}' created."




    IL_TZ = ZoneInfo("Asia/Jerusalem")

    def _aware(dt):
        if dt and timezone.is_naive(dt):
            # Naive → interpret as Israel local time
            return timezone.make_aware(dt, ZoneInfo("Asia/Jerusalem"))
        return dt  # leave offset-aware unchanged

    def _normalize_incoming_dt(dt):
        """
        Interpret incoming datetimes consistently as Asia/Jerusalem wall time.
        - If naive: assume local (Asia/Jerusalem) and make aware.
        - If aware: convert to Asia/Jerusalem.
        """
        if not dt:
            return None
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, IL_TZ)
        return dt.astimezone(IL_TZ)

    @tool
    def add_event(title: str, start: str, end: str, all_day: bool = False, description: str = "") -> Union[
        str, dict[str, Union[Union[bool, str], Any]]]:
        """Add a calendar event consistent with manual event creation behavior."""

        s = _normalize_incoming_dt(parse_datetime(start))
        e = _normalize_incoming_dt(parse_datetime(end))
        desc = (description or "").strip()

        if not title or not s or not e:
            return "[ERROR] title, start, end are required"

        if all_day:
            s = timezone.make_aware(datetime.combine(s.date(), time.min), IL_TZ)
            # ignore provided end time; use exclusive midnight next day
            e = timezone.make_aware(datetime.combine(s.date(), time.min), IL_TZ) + timedelta(days=1)
        elif e <= s:
            e = s + timedelta(hours=1)

        with transaction.atomic():
            ev, created = Event.objects.get_or_create(
                user=user,
                title=(title or "").strip(),
                start_datetime=s,
                end_datetime=e,
                all_day=all_day,
                defaults={"description": desc},
            )
            if not created:
                if desc and ev.description != desc:
                    ev.description = desc
                    ev.save(update_fields=["description"])
        if created:
            return f"done"
        else:
            return f"[OK] Event already exists, stop"




    @tool
    def analyze_stats(query: str = "week") -> str:
        """Analyze user task statistics (completion rate, most completed tasks)."""
        granularity = detect_granularity(query)
        rates = get_completion_rate(user, granularity)
        top_tasks = get_completed_daily_tasks_count(user)

        last_rate = rates[-1]["completion_rate"] if rates else 0
        return (
            f"Your latest {granularity} completion rate is {last_rate}%. "
            f"Your most completed tasks are: {top_tasks}."
        )

    return [add_task, add_event, analyze_stats]
