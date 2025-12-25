from typing import Union, Dict, Any
from django.utils.dateparse import parse_datetime
from zoneinfo import ZoneInfo
from django.utils import timezone
from langchain.tools import tool
from main.models import Task, Event, Subject, Note
from main.stats_utils import get_completion_rate, get_completed_daily_tasks_count, detect_granularity
from datetime import datetime, time, timedelta
from django.db import transaction
from functools import lru_cache
from main.agent.rag_utils import get_vectorstore
import re
from main.agent.idempotency import IdempotencyContext, normalize_title, normalize_body, sha256_hex
from uuid import uuid4

IL_TZ = ZoneInfo("Asia/Jerusalem")
def _canonical_local_minute(dt: datetime) -> str:
    """
    Canonical datetime string in Asia/Jerusalem at minute precision.
    Format: YYYY-MM-DDTHH:MM
    """
    dt = dt.astimezone(IL_TZ).replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M")


def make_user_tools(user, request_id):
    """Return a list of LangChain tools bound to a specific user."""
    ctx = IdempotencyContext(user_id=user.id, request_id=request_id or uuid4().hex)

    @tool
    def add_task(title: str, task_type: str = "daily") -> str:
        """Add a new task. task_type can be 'daily' or 'long_term'."""
        sig = {"title": normalize_title(title)}  # per your spec

        def _do():
            if task_type not in ["daily", "long_term"]:
                return f"Invalid task type '{task_type}'. Please choose 'daily' or 'long_term'."

            task = Task.objects.create(user=user, title=title, task_type=task_type)
            return f"{'Daily' if task_type == 'daily' else 'Long-term'} task '{task.title}' created."

        return ctx.run("add_task", sig, _do)


    DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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
    def add_event(title: str, start: str, end: str, all_day: bool = False, description: str = "") -> str:
        """Add a calendar event consistent with manual event creation behavior."""
        s = _normalize_incoming_dt(parse_datetime(start))
        e = _normalize_incoming_dt(parse_datetime(end))
        desc = (description or "").strip()

        if not title or not s or not e:
            return "[ERROR] title, start, end are required"

        # keep your existing behavior
        if all_day:
            s = timezone.make_aware(datetime.combine(s.date(), time.min), IL_TZ)
            e = timezone.make_aware(datetime.combine(s.date(), time.min), IL_TZ) + timedelta(days=1)
        elif e <= s:
            e = s + timedelta(hours=1)

        # per your spec: title + start + end only (normalized + canonicalized)
        sig = {
            "title": normalize_title(title),
            "start": _canonical_local_minute(s),
            "end": _canonical_local_minute(e),
        }

        def _do():
            # you can keep atomic/get_or_create; even if you don't care about concurrency,
            # it's still correct and minimal.
            with transaction.atomic():
                ev, created = Event.objects.get_or_create(
                    user=user,
                    title=(title or "").strip(),
                    start_datetime=s,
                    end_datetime=e,
                    all_day=all_day,
                    defaults={"description": desc},
                )

            # per your decision: do NOT “merge/update” description on duplicates;
            # second identical call should just return the same result.
            if created:
                return (
                    "STATUS: success\n"
                    "MESSAGE: Event created.\n"
                    f"TITLE: {ev.title}\n"
                    f"START: {s.isoformat()}\n"
                    f"END: {e.isoformat()}\n"
                    "STOP"
                )
            return (
                "STATUS: success\n"
                "MESSAGE: Event already exists.\n"
                f"TITLE: {ev.title}\n"
                f"START: {s.isoformat()}\n"
                f"END: {e.isoformat()}\n"
                "STOP"
            )

        return ctx.run("add_event", sig, _do)

    @tool
    def get_tasks(start_date: str, end_date: str, max_results: int = 50) -> str:
        """
        Fetch the user's tasks whose date is between start_date and end_date (inclusive).
        Dates are in the user's local time (Asia/Jerusalem), format 'YYYY-MM-DD'.

        This is intended for when you need information about the user tasks, and for questions like:
        - "What are my tasks for today / tomorrow / this week?"
        - "Show me tasks between 2025-11-20 and 2025-11-25."
        do not use for scheduling requests, only when tasks are mentioned.
        """

        try:
            start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return "[ERROR] Invalid date format. Use 'YYYY-MM-DD'."

        if end_d < start_d:
            # swap to be forgiving
            start_d, end_d = end_d, start_d

        # Determine which field to filter on, based on Task model fields
        field_names = {f.name for f in Task._meta.get_fields()}
        qs = Task.objects.filter(user=user)

        date_field = None
        is_datetime_field = False

        if "due_datetime" in field_names:
            date_field = "due_datetime"
            is_datetime_field = True
        elif "due_date" in field_names:
            date_field = "due_date"
            is_datetime_field = False
        elif "created_at" in field_names:
            date_field = "created_at"
            is_datetime_field = True

        if date_field is not None:
            if is_datetime_field:
                start_local = timezone.make_aware(datetime.combine(start_d, time.min), IL_TZ)
                end_local = timezone.make_aware(datetime.combine(end_d, time.max), IL_TZ)

                # Store datetimes in UTC in DB? If so, convert to UTC.
                start_utc = start_local.astimezone(timezone.utc)
                end_utc = end_local.astimezone(timezone.utc)

                filter_kwargs = {
                    f"{date_field}__gte": start_utc,
                    f"{date_field}__lte": end_utc,
                }
            else:
                # Pure date field
                filter_kwargs = {
                    f"{date_field}__gte": start_d,
                    f"{date_field}__lte": end_d,
                }

            qs = qs.filter(**filter_kwargs)

            # Order by the same field if possible
            qs = qs.order_by(date_field)
        else:
            # No known date field – return all tasks (still limited)
            qs = qs.order_by("id")

        qs = qs[: max_results]

        if not qs.exists():
            return f"No tasks found between {start_date} and {end_date}."

        lines = [
            f"Tasks for {start_date} to {end_date} (local time, capped at {max_results} results):"
        ]

        for t in qs:
            # Try to get some date display for the user
            date_str = ""
            if date_field:
                value = getattr(t, date_field, None)
                if value is not None:
                    if isinstance(value, datetime):
                        value_local = _normalize_incoming_dt(value)
                        date_str = value_local.strftime("%Y-%m-%d %H:%M")
                    else:
                        date_str = str(value)

            # If you have a 'status' or 'task_type' field, they’ll appear here if present.
            status = getattr(t, "status", None)
            task_type = getattr(t, "task_type", None)

            parts = [f"- {t.title}"]
            if task_type:
                parts.append(f"(type: {task_type})")
            if status:
                parts.append(f"[{status}]")
            if date_str:
                parts.append(f"@ {date_str}")

            lines.append(" ".join(parts))

        return "\n".join(lines)


    @tool
    def get_events(start: str, end: str, max_results: int = 50) -> str:
        """
        Fetch the user's calendar events or schedule between start and end (inclusive), in local time.
        - Accepts either full ISO datetimes or 'YYYY-MM-DD' (interpreted in Asia/Jerusalem).
        - Use this for when you need information about the user calendar events or schedule.
        Returns a concise, human-readable list of events.
        """

        def _parse_local_datetime_or_date(value: str, is_end: bool = False) -> datetime:
            # If it's a pure date "YYYY-MM-DD", handle it as a date, not a datetime
            if not DATE_ONLY_RE.match(value):
                # Try full ISO datetime first
                dt = parse_datetime(value)
                if dt is not None:
                    return _normalize_incoming_dt(dt)

            # Fallback (and date-only case): 'YYYY-MM-DD'
            try:
                d = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Invalid datetime/date format. Use ISO or 'YYYY-MM-DD'.")

            base = datetime.combine(d, time.min)
            # For end dates, move to *next* midnight so range is [start, end)
            if is_end:
                base += timedelta(days=1)
            return timezone.make_aware(base, IL_TZ)

        try:
            start_local = _parse_local_datetime_or_date(start, is_end=False)
            end_local = _parse_local_datetime_or_date(end, is_end=True)
        except ValueError as e:
            return f"[ERROR] {e}"

        if end_local < start_local:
            start_local, end_local = end_local, start_local

        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        qs = (
            Event.objects.filter(user=user)
            .filter(start_datetime__lt=end_utc, end_datetime__gt=start_utc)
            .order_by("start_datetime")[: max_results]
        )

        if not qs.exists():
            return (
                f"No events found between "
                f"{start_local.strftime('%Y-%m-%d %H:%M')} and "
                f"{end_local.strftime('%Y-%m-%d %H:%M')} (local time)."
            )

        lines = [
            "Events:",
            f"Window: {start_local.strftime('%Y-%m-%d %H:%M')} -> "
            f"{end_local.strftime('%Y-%m-%d %H:%M')} (local time, capped at {max_results} results)",
        ]

        for ev in qs:
            s_local = ev.start_datetime.astimezone(IL_TZ)
            e_local = ev.end_datetime.astimezone(IL_TZ)

            time_part = (
                f"{s_local.strftime('%Y-%m-%d %H:%M')} - "
                f"{e_local.strftime('%H:%M')}"
            )

            desc = (ev.description or "").strip()
            desc_snippet = f" | {desc[:80]}..." if desc else ""

            lines.append(
                f"- {time_part} — {ev.title}{desc_snippet}"
            )

        return "\n".join(lines)

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

    @tool
    def add_subject(title: str) -> str:
        """Create a new subject for the user."""
        sig = {"title": normalize_title(title)}  # per your spec

        def _do():
            if Subject.objects.filter(user=user, title=title).exists():
                return "STATUS: error\nMESSAGE: Subject already exists."
            subject = Subject.objects.create(user=user, title=title)
            return f"STATUS: success\nMESSAGE: Subject created.\nID: {subject.id}\nSTOP"

        return ctx.run("add_subject", sig, _do)
    @tool
    def add_note(subject_title: str, title: str, body: str) -> str:
        """Create a new note under an existing subject."""
        # per your spec: subject + title + body
        sig = {
            "subject_title": normalize_title(subject_title),
            "title": normalize_title(title),
            # hash the normalized body so signatures stay compact and deterministic
            "body_hash": sha256_hex(normalize_body(body)),
        }

        def _do():
            try:
                subject = Subject.objects.get(user=user, title=subject_title)
            except Subject.DoesNotExist:
                return "STATUS: error\nMESSAGE: Subject not found."

            note = Note.objects.create(subject=subject, title=title, content=body)
            return f"STATUS: success\nMESSAGE: Note created.\nSUBJECT: {subject.title}\nID: {note.id}\nSTOP"

        return ctx.run("add_note", sig, _do)

    @tool
    def search_knowledge(query: str, top_k: int = 5) -> str:
        """
        Search the user's notes for information relevant to the query.
        Use this for questions about what the user wrote in his notes.
        """
        vs = get_vectorstore()
        retriever = vs.as_retriever(
            search_kwargs={
                "k": top_k,
                "filter": {"user_id": user.id},
            }
        )
        docs = retriever.invoke(query)
        if not docs:
            return "No relevant notes found."

        lines = []
        for d in docs:
            m = d.metadata
            subject_title = m.get("subject_title", "Unknown subject")
            note_title = m.get("note_title", "Untitled note")
            content = d.page_content.replace("\n", " ")
            lines.append(
                f"- Subject: {subject_title} | Note: {note_title}\n  content: {content}..."
            )
        return "Relevant notes:\n" + "\n".join(lines)

    return [add_task, add_event, get_tasks, get_events, analyze_stats, add_subject, add_note, search_knowledge]

