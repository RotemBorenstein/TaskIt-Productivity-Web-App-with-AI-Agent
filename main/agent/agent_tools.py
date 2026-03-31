from datetime import datetime, time, timedelta
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from langchain.tools import tool

from main.agent.guardrails import (
    RetrievedDocDecision,
    format_rag_found_result,
    format_rag_not_found_result,
    locally_sanitize_retrieved_text,
    sanitize_retrieved_label,
)
from main.agent.idempotency import IdempotencyContext, normalize_body, normalize_title, sha256_hex
from main.agent.rag_utils import get_vectorstore
from main.models import Event, Note, Subject, Task
from main.stats_utils import detect_granularity, get_completed_daily_tasks_count, get_completion_rate

IL_TZ = ZoneInfo("Asia/Jerusalem")


def _normalize_rag_query(query: str) -> str:
    """Normalize note-search queries for exact duplicate detection."""

    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _canonical_local_minute(dt: datetime) -> str:
    """
    Canonical datetime string in Asia/Jerusalem at minute precision.

    Format: YYYY-MM-DDTHH:MM
    """

    dt = dt.astimezone(IL_TZ).replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M")


def make_user_tools(user, request_id, *, allowed_tool_names=None, retrieval_guard=None):
    """Return LangChain tools bound to one user and one assistant request."""

    ctx = IdempotencyContext(user_id=user.id, request_id=request_id or uuid4().hex)
    cached_knowledge_results: dict[str, str] = {}
    date_only_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _normalize_incoming_dt(dt):
        """
        Interpret incoming datetimes consistently as Asia/Jerusalem wall time.

        - If naive: assume local time and make aware.
        - If aware: convert to Asia/Jerusalem.
        """

        if not dt:
            return None
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, IL_TZ)
        return dt.astimezone(IL_TZ)

    @tool
    def add_task(title: str, task_type: str = "daily") -> str:
        """Add a new task. task_type can be 'daily' or 'long_term'."""

        sig = {"title": normalize_title(title), "task_type": normalize_title(task_type)}

        def _do():
            if task_type not in ["daily", "long_term"]:
                return f"Invalid task type '{task_type}'. Please choose 'daily' or 'long_term'."

            task = Task.objects.create(user=user, title=title, task_type=task_type)
            return f"{'Daily' if task_type == 'daily' else 'Long-term'} task '{task.title}' created."

        return ctx.run("add_task", sig, _do)

    @tool
    def add_event(title: str, start: str, end: str, all_day: bool = False, description: str = "") -> str:
        """Add a calendar event consistent with manual event creation behavior."""

        raw_start = (start or "").strip()
        raw_end = (end or "").strip()
        description = (description or "").strip()
        start_dt = _normalize_incoming_dt(parse_datetime(raw_start))
        end_dt = _normalize_incoming_dt(parse_datetime(raw_end))

        sig = {
            "title": normalize_title(title),
            "start": _canonical_local_minute(start_dt) if start_dt else raw_start,
            "end": _canonical_local_minute(end_dt) if end_dt else raw_end,
        }

        def _do():
            start_value = _normalize_incoming_dt(parse_datetime(raw_start))
            end_value = _normalize_incoming_dt(parse_datetime(raw_end))
            if not title or not start_value or not end_value:
                return "[ERROR] title, start, end are required"

            if all_day:
                start_value = timezone.make_aware(datetime.combine(start_value.date(), time.min), IL_TZ)
                end_value = timezone.make_aware(datetime.combine(start_value.date(), time.min), IL_TZ) + timedelta(days=1)
            elif end_value <= start_value:
                end_value = start_value + timedelta(hours=1)

            with transaction.atomic():
                event, created = Event.objects.get_or_create(
                    user=user,
                    title=(title or "").strip(),
                    start_datetime=start_value,
                    end_datetime=end_value,
                    all_day=all_day,
                    defaults={"description": description},
                )

            if created:
                return (
                    "STATUS: success\n"
                    "MESSAGE: Event created.\n"
                    f"TITLE: {event.title}\n"
                    f"START: {start_value.isoformat()}\n"
                    f"END: {end_value.isoformat()}\n"
                    "STOP"
                )
            return (
                "STATUS: success\n"
                "MESSAGE: Event already exists.\n"
                f"TITLE: {event.title}\n"
                f"START: {start_value.isoformat()}\n"
                f"END: {end_value.isoformat()}\n"
                "STOP"
            )

        return ctx.run("add_event", sig, _do)

    @tool
    def get_tasks(start_date: str, end_date: str, max_results: int = 50) -> str:
        """
        Fetch the user's tasks whose date is between start_date and end_date (inclusive).
        Dates are in the user's local time (Asia/Jerusalem), format 'YYYY-MM-DD'.
        """

        raw_start = (start_date or "").strip()
        raw_end = (end_date or "").strip()
        try:
            start_d = datetime.strptime(raw_start, "%Y-%m-%d").date()
            end_d = datetime.strptime(raw_end, "%Y-%m-%d").date()
            if end_d < start_d:
                start_d, end_d = end_d, start_d
            start_sig = start_d.isoformat()
            end_sig = end_d.isoformat()
        except ValueError:
            start_sig = raw_start
            end_sig = raw_end

        try:
            max_results = int(max_results or 50)
        except (TypeError, ValueError):
            max_results = 50
        max_results = max(1, min(max_results, 50))

        sig = {
            "start_date": start_sig,
            "end_date": end_sig,
            "max_results": max_results,
        }

        def _do():
            try:
                start_value = datetime.strptime(raw_start, "%Y-%m-%d").date()
                end_value = datetime.strptime(raw_end, "%Y-%m-%d").date()
            except ValueError:
                return "[ERROR] Invalid date format. Use 'YYYY-MM-DD'."

            if end_value < start_value:
                start_value, end_value = end_value, start_value

            field_names = {field.name for field in Task._meta.get_fields()}
            qs = Task.objects.filter(user=user)

            date_field = None
            is_datetime_field = False
            if "due_datetime" in field_names:
                date_field = "due_datetime"
                is_datetime_field = True
            elif "due_date" in field_names:
                date_field = "due_date"
            elif "created_at" in field_names:
                date_field = "created_at"
                is_datetime_field = True

            if date_field is not None:
                if is_datetime_field:
                    start_local = timezone.make_aware(datetime.combine(start_value, time.min), IL_TZ)
                    end_local = timezone.make_aware(datetime.combine(end_value, time.max), IL_TZ)
                    qs = qs.filter(
                        **{
                            f"{date_field}__gte": start_local.astimezone(timezone.utc),
                            f"{date_field}__lte": end_local.astimezone(timezone.utc),
                        }
                    )
                else:
                    qs = qs.filter(
                        **{
                            f"{date_field}__gte": start_value,
                            f"{date_field}__lte": end_value,
                        }
                    )
                qs = qs.order_by(date_field)
            else:
                qs = qs.order_by("id")

            qs = qs[: max_results]
            if not qs.exists():
                return f"No tasks found between {start_value.isoformat()} and {end_value.isoformat()}."

            lines = [
                f"Tasks for {start_value.isoformat()} to {end_value.isoformat()} (local time, capped at {max_results} results):"
            ]

            for task in qs:
                date_str = ""
                if date_field:
                    value = getattr(task, date_field, None)
                    if value is not None:
                        if isinstance(value, datetime):
                            date_str = _normalize_incoming_dt(value).strftime("%Y-%m-%d %H:%M")
                        else:
                            date_str = str(value)
                            due_time = getattr(task, "due_time", None)
                            if due_time:
                                date_str = f"{date_str} {due_time.strftime('%H:%M')}"

                status = getattr(task, "status", None)
                task_type = getattr(task, "task_type", None)
                parts = [f"- {task.title}"]
                if task_type:
                    parts.append(f"(type: {task_type})")
                if status:
                    parts.append(f"[{status}]")
                if date_str:
                    parts.append(f"@ {date_str}")
                lines.append(" ".join(parts))

            return "\n".join(lines)

        return ctx.run("get_tasks", sig, _do)

    @tool
    def get_events(start: str, end: str, max_results: int = 50) -> str:
        """
        Fetch the user's calendar events or schedule between start and end (inclusive), in local time.
        Accepts either full ISO datetimes or 'YYYY-MM-DD' interpreted in Asia/Jerusalem.
        """

        def _parse_local_datetime_or_date(value: str, *, is_end: bool = False) -> datetime:
            if not date_only_re.match(value):
                dt = parse_datetime(value)
                if dt is not None:
                    return _normalize_incoming_dt(dt)

            try:
                date_value = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError("Invalid datetime/date format. Use ISO or 'YYYY-MM-DD'.") from exc

            base = datetime.combine(date_value, time.min)
            if is_end:
                base += timedelta(days=1)
            return timezone.make_aware(base, IL_TZ)

        raw_start = (start or "").strip()
        raw_end = (end or "").strip()
        try:
            start_local = _parse_local_datetime_or_date(raw_start, is_end=False)
            end_local = _parse_local_datetime_or_date(raw_end, is_end=True)
            if end_local < start_local:
                start_local, end_local = end_local, start_local
            start_sig = _canonical_local_minute(start_local)
            end_sig = _canonical_local_minute(end_local)
        except ValueError:
            start_sig = raw_start
            end_sig = raw_end

        try:
            max_results = int(max_results or 50)
        except (TypeError, ValueError):
            max_results = 50
        max_results = max(1, min(max_results, 50))

        sig = {
            "start": start_sig,
            "end": end_sig,
            "max_results": max_results,
        }

        def _do():
            try:
                start_value = _parse_local_datetime_or_date(raw_start, is_end=False)
                end_value = _parse_local_datetime_or_date(raw_end, is_end=True)
            except ValueError as exc:
                return f"[ERROR] {exc}"

            if end_value < start_value:
                start_value, end_value = end_value, start_value

            qs = (
                Event.objects.filter(user=user)
                .filter(
                    start_datetime__lt=end_value.astimezone(timezone.utc),
                    end_datetime__gt=start_value.astimezone(timezone.utc),
                )
                .order_by("start_datetime")[: max_results]
            )

            if not qs.exists():
                return (
                    f"No events found between "
                    f"{start_value.strftime('%Y-%m-%d %H:%M')} and "
                    f"{end_value.strftime('%Y-%m-%d %H:%M')} (local time)."
                )

            lines = [
                "Events:",
                f"Window: {start_value.strftime('%Y-%m-%d %H:%M')} -> "
                f"{end_value.strftime('%Y-%m-%d %H:%M')} (local time, capped at {max_results} results)",
            ]

            for event in qs:
                start_display = event.start_datetime.astimezone(IL_TZ)
                end_display = event.end_datetime.astimezone(IL_TZ)
                desc = (event.description or "").strip()
                desc_snippet = f" | {desc[:80]}..." if desc else ""
                lines.append(
                    f"- {start_display.strftime('%Y-%m-%d %H:%M')} - "
                    f"{end_display.strftime('%H:%M')} - {event.title}{desc_snippet}"
                )

            return "\n".join(lines)

        return ctx.run("get_events", sig, _do)

    @tool
    def analyze_stats(query: str = "week") -> str:
        """Analyze user task statistics for the requested period."""

        sig = {"query": normalize_title(query)}

        def _do():
            granularity = detect_granularity(query)
            rates = get_completion_rate(user, granularity)
            top_tasks = get_completed_daily_tasks_count(user)
            last_rate = rates[-1]["completion_rate"] if rates else 0
            return (
                f"Your latest {granularity} completion rate is {last_rate}%. "
                f"Your most completed tasks are: {top_tasks}."
            )

        return ctx.run("analyze_stats", sig, _do)

    @tool
    def add_subject(title: str) -> str:
        """Create a new subject for the user."""

        sig = {"title": normalize_title(title)}

        def _do():
            if Subject.objects.filter(user=user, title=title).exists():
                return "STATUS: error\nMESSAGE: Subject already exists."
            subject = Subject.objects.create(user=user, title=title)
            return f"STATUS: success\nMESSAGE: Subject created.\nID: {subject.id}\nSTOP"

        return ctx.run("add_subject", sig, _do)

    @tool
    def add_note(subject_title: str, title: str, body: str) -> str:
        """Create a new note under an existing subject."""

        sig = {
            "subject_title": normalize_title(subject_title),
            "title": normalize_title(title),
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
        """Search the user's notes for information relevant to the query."""

        query_key = _normalize_rag_query(query)
        try:
            top_k = int(top_k or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 5))

        sig = {"query": query_key, "top_k": top_k}
        cache_key = f"{query_key}:{top_k}"

        def _do():
            if cache_key in cached_knowledge_results:
                return cached_knowledge_results[cache_key]

            retriever = get_vectorstore().as_retriever(
                search_kwargs={
                    "k": top_k,
                    "filter": {"user_id": user.id},
                }
            )
            docs = retriever.invoke(query)
            if not docs:
                result = format_rag_not_found_result()
                cached_knowledge_results[cache_key] = result
                return result

            prepared_docs: list[dict[str, Any]] = []
            for doc in docs:
                metadata = doc.metadata
                distance = metadata.get("distance")
                if distance is not None and float(distance) > settings.AGENT_RAG_MAX_DISTANCE:
                    continue
                prepared_docs.append(
                    {
                        "subject_title": sanitize_retrieved_label(
                            metadata.get("subject_title", "Unknown subject"),
                            fallback="Filtered subject",
                        ),
                        "note_title": sanitize_retrieved_label(
                            metadata.get("note_title", "Untitled note"),
                            fallback="Filtered note",
                        ),
                        "content": doc.page_content,
                        "distance": distance,
                    }
                )

            if not prepared_docs:
                result = format_rag_not_found_result()
                cached_knowledge_results[cache_key] = result
                return result

            if retrieval_guard is not None:
                decisions = retrieval_guard.filter_retrieved_documents(query, prepared_docs)
            else:
                decisions = [
                    RetrievedDocDecision(
                        action="allow",
                        safe_excerpt=locally_sanitize_retrieved_text(doc["content"]),
                        reason_code="local_sanitizer",
                    )
                    for doc in prepared_docs
                ]

            lines = []
            for doc, decision in zip(prepared_docs, decisions):
                if decision.action == "drop":
                    continue
                content = (decision.safe_excerpt or locally_sanitize_retrieved_text(doc["content"])).strip()
                if not content:
                    continue
                lines.append(
                    f"- Subject: {doc['subject_title']} | Note: {doc['note_title']}\n"
                    f"  excerpt: {content}"
                )

            if not lines:
                result = format_rag_not_found_result()
                cached_knowledge_results[cache_key] = result
                return result

            result = format_rag_found_result(lines)
            cached_knowledge_results[cache_key] = result
            return result

        return ctx.run("search_knowledge", sig, _do)

    tool_defs = [add_task, add_event, get_tasks, get_events, analyze_stats, add_subject, add_note, search_knowledge]
    if allowed_tool_names is None:
        return tool_defs
    allowed_tool_names = set(allowed_tool_names)
    return [tool_def for tool_def in tool_defs if tool_def.name in allowed_tool_names]
