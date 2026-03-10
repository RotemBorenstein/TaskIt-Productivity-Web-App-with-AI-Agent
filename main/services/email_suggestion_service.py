from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.utils import timezone
from langchain_openai import ChatOpenAI

from main.models import EmailSuggestion, EmailSyncRun
from main.services.email_sync_service import NormalizedEmailMessage

logger = logging.getLogger(__name__)


@dataclass
class SuggestionDraft:
    suggestion_type: str
    title: str
    description: str
    confidence: Decimal
    explanation: str
    task_type_hint: str = ""
    normalized_date_key: str = ""
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    all_day: bool = False


class EmailSuggestionService:
    def __init__(self):
        self.model_name = "gpt-4o-mini"
        self.confidence_threshold = Decimal(
            str(getattr(settings, "EMAIL_SUGGESTION_CONFIDENCE_THRESHOLD", 0.65))
        )

    def generate_suggestions(
        self,
        *,
        sync_run: EmailSyncRun,
        messages: list[NormalizedEmailMessage],
    ) -> list[EmailSuggestion]:
        saved: list[EmailSuggestion] = []
        for msg in messages:
            drafts, ai_payload = self._suggestions_for_message(msg)
            for draft in drafts:
                fingerprint = self._build_fingerprint(
                    integration_id=sync_run.integration_id,
                    message_id=msg.message_id,
                    suggestion_type=draft.suggestion_type,
                    title=draft.title,
                    normalized_date_key=draft.normalized_date_key,
                )
                is_duplicate = EmailSuggestion.objects.filter(
                    user=sync_run.user,
                    fingerprint=fingerprint,
                ).exists()
                suggestion = EmailSuggestion.objects.create(
                    user=sync_run.user,
                    sync_run=sync_run,
                    suggestion_type=draft.suggestion_type,
                    title=draft.title[:200],
                    description=draft.description,
                    task_type_hint=draft.task_type_hint,
                    start_datetime=draft.start_datetime,
                    end_datetime=draft.end_datetime,
                    all_day=draft.all_day,
                    confidence=draft.confidence,
                    reason=draft.explanation,
                    explanation=draft.explanation,
                    fingerprint=fingerprint,
                    source_message_refs=[msg.message_id],
                    status=EmailSuggestion.STATUS_DUPLICATE
                    if is_duplicate
                    else EmailSuggestion.STATUS_PENDING,
                    ai_payload=ai_payload,
                )
                saved.append(suggestion)
        sync_run.suggestions_count = len(saved)
        sync_run.save(update_fields=["suggestions_count"])
        return saved

    def _suggestions_for_message(
        self, msg: NormalizedEmailMessage
    ) -> tuple[list[SuggestionDraft], dict[str, Any]]:
        raw = self._call_ai(msg)
        parsed = self._parse_ai_output(raw)
        decision = str(parsed.get("decision", "none")).lower().strip()
        drafts: list[SuggestionDraft] = []

        if decision in {"task", "both"}:
            task_data = parsed.get("task") or {}
            task_draft = self._build_task_draft(task_data)
            if task_draft:
                drafts.append(task_draft)
        if decision in {"event", "both"}:
            event_data = parsed.get("event") or {}
            event_draft = self._build_event_draft(event_data)
            if event_draft:
                drafts.append(event_draft)
        return drafts, parsed

    def _call_ai(self, msg: NormalizedEmailMessage) -> str:
        llm = ChatOpenAI(model=self.model_name, temperature=0)
        prompt = f"""
You extract productivity suggestions from an email.
Return ONLY valid JSON, no markdown, no explanation text.

JSON schema:
{{
  "decision": "none|task|event|both",
  "task": {{
    "title": "string",
    "relates_to_today": true/false,
    "confidence": 0.0-1.0,
    "explanation": "string"
  }},
  "event": {{
    "title": "string",
    "date": "YYYY-MM-DD or null",
    "time": "HH:MM or null",
    "location": "string or null",
    "confidence": 0.0-1.0,
    "explanation": "string"
  }}
}}

Rules:
- If there is no actionable suggestion, set decision to "none".
- Event requires at least title and date.
- Task requires at least title.
- Use the full email body content below.

EMAIL SUBJECT:
{msg.subject}

EMAIL BODY:
{msg.body}
""".strip()
        response = llm.invoke(prompt)
        return getattr(response, "content", "") or ""

    def _parse_ai_output(self, text: str) -> dict[str, Any]:
        if not text:
            return {"decision": "none"}
        cleaned = text.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fenced_match:
            cleaned = fenced_match.group(1).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Email suggestion AI returned non-JSON output.")
            return {"decision": "none"}

    def _build_task_draft(self, task_data: dict[str, Any]) -> SuggestionDraft | None:
        title = (task_data.get("title") or "").strip()
        if not title:
            return None
        relates_to_today = bool(task_data.get("relates_to_today"))
        task_type_label = (
            EmailSuggestion.TASK_TYPE_DAILY
            if relates_to_today
            else EmailSuggestion.TASK_TYPE_LONG_TERM
        )
        confidence = self._to_decimal(task_data.get("confidence"))
        explanation = (task_data.get("explanation") or "").strip()

        description_parts = [f"Suggested as {task_type_label} task from email."]
        return SuggestionDraft(
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title=title,
            description=" ".join(description_parts),
            confidence=confidence,
            explanation=explanation,
            task_type_hint=task_type_label,
        )

    def _build_event_draft(self, event_data: dict[str, Any]) -> SuggestionDraft | None:
        title = (event_data.get("title") or "").strip()
        date_value = self._parse_date(event_data.get("date"))
        if not title or not date_value:
            return None
        event_time = self._parse_time(event_data.get("time"))
        start_dt = datetime.combine(date_value, event_time or time(9, 0))
        start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())
        end_dt = start_dt + timedelta(hours=1)
        location = (event_data.get("location") or "").strip()
        confidence = self._to_decimal(event_data.get("confidence"))
        explanation = (event_data.get("explanation") or "").strip()

        description = "Suggested event from email."
        if location:
            description += f" Location: {location}."

        return SuggestionDraft(
            suggestion_type=EmailSuggestion.TYPE_EVENT,
            title=title,
            description=description,
            confidence=confidence,
            explanation=explanation,
            normalized_date_key=date_value.isoformat(),
            start_datetime=start_dt,
            end_datetime=end_dt,
            all_day=event_time is None,
        )

    @staticmethod
    def _parse_date(value: Any):
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _parse_time(value: Any):
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value.strip(), "%H:%M").time()
        except ValueError:
            return None

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.0")
        if decimal_value < 0:
            return Decimal("0.0")
        if decimal_value > 1:
            return Decimal("1.0")
        return decimal_value

    @staticmethod
    def _build_fingerprint(
        *,
        integration_id: int,
        message_id: str,
        suggestion_type: str,
        title: str,
        normalized_date_key: str,
    ) -> str:
        normalized_title = " ".join((title or "").lower().split())
        raw = f"{integration_id}|{message_id}|{suggestion_type}|{normalized_title}|{normalized_date_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
