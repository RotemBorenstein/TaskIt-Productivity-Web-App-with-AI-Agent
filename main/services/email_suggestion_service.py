from __future__ import annotations

"""Email suggestion generation with a strict two-call evidence workflow."""

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.utils import timezone
from langchain_openai import ChatOpenAI

from main.models import EmailSuggestion, EmailSyncRun
from main.services.email_sync_service import NormalizedEmailMessage

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
HTML_BREAK_RE = re.compile(r"(?i)<br\s*/?>")
HTML_SEPARATOR_RE = re.compile(r"(?i)<hr\b[^>]*>")
HTML_BLOCK_END_RE = re.compile(r"(?i)</(p|div|li|tr|table|section|article|h\d)>")
HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
MULTISPACE_RE = re.compile(r"[ \t]+")
MULTIBLANK_RE = re.compile(r"\n{3,}")
REPLY_PREFIX_RE = re.compile(r"^(?:re|fw|fwd):\s*", re.IGNORECASE)
QUOTED_THREAD_MARKERS = (
    re.compile(r"^On .+wrote:(?:\s.*)?$", re.IGNORECASE),
    re.compile(r"^From:\s", re.IGNORECASE),
    re.compile(r"^Sent:\s", re.IGNORECASE),
    re.compile(r"^Subject:\s", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
)
@dataclass
class PreprocessedEmailMessage:
    """Readability-focused email payload plus explicit provider metadata."""

    message_id: str
    sender: str
    subject: str
    analysis_body: str
    received_at: datetime
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)
    is_html: bool = False


@dataclass
class SuggestionDraft:
    suggestion_type: str
    title: str
    description: str
    confidence: Decimal
    model_confidence: Decimal
    explanation: str
    evidence: list[str] = field(default_factory=list)
    task_type_hint: str = ""
    normalized_date_key: str = ""
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    all_day: bool = False
    digest_eligible: bool = False
    debug_payload: dict[str, Any] = field(default_factory=dict)


class EmailSuggestionService:
    def __init__(self):
        self.model_name = "gpt-4o-mini"
        self.confidence_threshold = Decimal(
            str(getattr(settings, "EMAIL_SUGGESTION_CONFIDENCE_THRESHOLD", 0.65))
        )
        self.digest_confidence_threshold = Decimal(
            str(getattr(settings, "EMAIL_DIGEST_CONFIDENCE_THRESHOLD", 0.82))
        )

    def generate_suggestions(
        self,
        *,
        sync_run: EmailSyncRun,
        messages: list[NormalizedEmailMessage],
    ) -> list[EmailSuggestion]:
        """Run preprocessing plus at most two LLM calls per actionable email."""
        saved: list[EmailSuggestion] = []
        for msg in messages:
            processed = self._preprocess_message(msg)
            suppression_reason = self._protocol_suppression_reason(processed)
            if suppression_reason:
                logger.info(
                    "Email suggestion skipped via provider metadata: user_id=%s message_id=%s reason=%s",
                    sync_run.user_id,
                    msg.message_id,
                    suppression_reason,
                )
                continue

            classification = self._classify_message(processed)
            if not self._is_actionable_classification(classification):
                logger.info(
                    "Email suggestion classified as non-actionable: user_id=%s message_id=%s decision=%s",
                    sync_run.user_id,
                    msg.message_id,
                    classification.get("decision", "none"),
                )
                continue

            extraction = self._extract_candidates(processed, classification)
            drafts = self._build_drafts(
                msg=processed,
                classification=classification,
                extraction=extraction,
            )

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
                    model_confidence=draft.model_confidence,
                    confidence=draft.confidence,
                    reason=draft.explanation,
                    explanation=draft.explanation,
                    fingerprint=fingerprint,
                    source_message_refs=[msg.message_id],
                    status=EmailSuggestion.STATUS_DUPLICATE
                    if is_duplicate
                    else EmailSuggestion.STATUS_PENDING,
                    ai_payload=self._build_ai_payload(
                        processed=processed,
                        classification=classification,
                        extraction=extraction,
                        draft=draft,
                    ),
                    digest_eligible=draft.digest_eligible and not is_duplicate,
                )
                saved.append(suggestion)

        sync_run.suggestions_count = len(saved)
        sync_run.save(update_fields=["suggestions_count"])
        return saved

    def _preprocess_message(self, msg: NormalizedEmailMessage) -> PreprocessedEmailMessage:
        """Convert provider bodies into cleaner text without semantic filtering."""
        subject = MULTISPACE_RE.sub(" ", (msg.subject or "").strip())
        raw_body = msg.body or ""
        is_html = bool(re.search(r"<[a-z][^>]*>", raw_body, re.IGNORECASE))
        normalized_body = self._html_to_text(raw_body)

        lines = [MULTISPACE_RE.sub(" ", line).strip() for line in normalized_body.splitlines()]
        lines = [line for line in lines if line]
        lines = self._trim_quoted_thread(lines)
        lines = self._trim_signature(lines)
        analysis_body = MULTIBLANK_RE.sub("\n\n", "\n".join(lines)).strip()

        return PreprocessedEmailMessage(
            message_id=msg.message_id,
            sender=msg.sender,
            subject=subject,
            analysis_body=analysis_body,
            received_at=msg.received_at,
            provider=msg.provider,
            metadata=dict(msg.metadata or {}),
            is_html=is_html,
        )

    def _protocol_suppression_reason(self, msg: PreprocessedEmailMessage) -> str:
        """Use only explicit provider or protocol metadata for deterministic suppression."""
        metadata = msg.metadata or {}
        if bool(metadata.get("auto_submitted")):
            return "auto_submitted"
        return ""

    def _classify_message(self, msg: PreprocessedEmailMessage) -> dict[str, Any]:
        prompt = f"""
You classify a cleaned email for a productivity assistant.
Return ONLY valid JSON.

JSON schema:
{{
  "actionable": true/false,
  "decision": "none|task|event|both",
  "explanation": "string",
  "task_evidence": ["exact short snippet"],
  "event_evidence": ["exact short snippet"]
}}

Rules:
- "actionable" is true only when the email contains a direct obligation, request, follow-up, deadline, or a clear event the user should attend.
- Ignore quoted old thread content that is not part of the newest visible message.
- Evidence snippets must be copied exactly from the cleaned email text.
- If not actionable, use decision "none" and empty evidence arrays.

EMAIL METADATA:
{json.dumps(self._classifier_metadata(msg), sort_keys=True)}

EMAIL SUBJECT:
{msg.subject}

CLEANED EMAIL BODY:
{self._trim_prompt_text(msg.analysis_body)}
""".strip()
        parsed = self._invoke_json(prompt)
        parsed.setdefault("actionable", False)
        parsed.setdefault("decision", "none")
        parsed.setdefault("explanation", "")
        parsed["task_evidence"] = self._normalize_evidence(parsed.get("task_evidence"))
        parsed["event_evidence"] = self._normalize_evidence(parsed.get("event_evidence"))
        return parsed

    def _extract_candidates(
        self,
        msg: PreprocessedEmailMessage,
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_task = str(classification.get("decision", "none")).lower().strip() in {"task", "both"}
        allowed_event = str(classification.get("decision", "none")).lower().strip() in {"event", "both"}
        prompt = f"""
You extract productivity objects from a cleaned email.
Return ONLY valid JSON.

JSON schema:
{{
  "task": {{
    "title": "string",
    "relates_to_today": true/false,
    "confidence": 0.0-1.0,
    "explanation": "string",
    "evidence": ["exact short snippet"]
  }},
  "event": {{
    "title": "string",
    "date": "YYYY-MM-DD or null",
    "time": "HH:MM or null",
    "location": "string or null",
    "confidence": 0.0-1.0,
    "explanation": "string",
    "evidence": ["exact short snippet"]
  }}
}}

Rules:
- Extract only the object types allowed below.
- If an allowed type is not clearly present, return an empty object for that type.
- Evidence snippets must be copied exactly from the cleaned email text.
- Do not invent dates, times, or tasks without supporting evidence.

ALLOWED TYPES:
{json.dumps({"task": allowed_task, "event": allowed_event})}

CLASSIFIER EXPLANATION:
{classification.get("explanation", "")}

CLASSIFIER TASK EVIDENCE:
{json.dumps(classification.get("task_evidence", []))}

CLASSIFIER EVENT EVIDENCE:
{json.dumps(classification.get("event_evidence", []))}

EMAIL SUBJECT:
{msg.subject}

CLEANED EMAIL BODY:
{self._trim_prompt_text(msg.analysis_body)}
""".strip()
        parsed = self._invoke_json(prompt)
        parsed["task"] = parsed.get("task") if isinstance(parsed.get("task"), dict) else {}
        parsed["event"] = parsed.get("event") if isinstance(parsed.get("event"), dict) else {}
        parsed["task"]["evidence"] = self._normalize_evidence(parsed["task"].get("evidence"))
        parsed["event"]["evidence"] = self._normalize_evidence(parsed["event"].get("evidence"))
        return parsed

    def _build_drafts(
        self,
        *,
        msg: PreprocessedEmailMessage,
        classification: dict[str, Any],
        extraction: dict[str, Any],
    ) -> list[SuggestionDraft]:
        decision = str(classification.get("decision", "none")).lower().strip()
        drafts: list[SuggestionDraft] = []

        if decision in {"task", "both"}:
            task_draft = self._build_task_draft(
                task_data=extraction.get("task") or {},
                msg=msg,
                classification=classification,
                decision=decision,
            )
            if task_draft:
                drafts.append(task_draft)

        if decision in {"event", "both"}:
            event_draft = self._build_event_draft(
                event_data=extraction.get("event") or {},
                msg=msg,
                classification=classification,
                decision=decision,
            )
            if event_draft:
                drafts.append(event_draft)

        return drafts

    def _build_task_draft(
        self,
        *,
        task_data: dict[str, Any],
        msg: PreprocessedEmailMessage,
        classification: dict[str, Any],
        decision: str,
    ) -> SuggestionDraft | None:
        title = (task_data.get("title") or "").strip()
        if not title:
            return None

        evidence = self._merge_evidence(
            task_data.get("evidence"),
            classification.get("task_evidence"),
        )
        evidence_valid = self._evidence_matches_source(evidence, msg)
        if not evidence_valid:
            return None

        relates_to_today = bool(task_data.get("relates_to_today"))
        task_type_label = (
            EmailSuggestion.TASK_TYPE_DAILY
            if relates_to_today
            else EmailSuggestion.TASK_TYPE_LONG_TERM
        )
        type_matches = decision in {"task", "both"}
        title_valid = bool(title)
        normalization_valid = task_type_label in {
            EmailSuggestion.TASK_TYPE_DAILY,
            EmailSuggestion.TASK_TYPE_LONG_TERM,
        }
        if not (type_matches and title_valid and normalization_valid):
            return None

        final_confidence = self._task_confidence(
            evidence_count=len(evidence),
            title_valid=title_valid,
            type_matches=type_matches,
            normalization_valid=normalization_valid,
        )
        digest_eligible = bool(final_confidence >= self.digest_confidence_threshold)

        return SuggestionDraft(
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title=title,
            description=f"Suggested as {task_type_label} task from email.",
            confidence=final_confidence,
            model_confidence=self._to_decimal(task_data.get("confidence")),
            explanation=(task_data.get("explanation") or "").strip()
            or (classification.get("explanation") or "").strip()
            or "Detected an actionable task in the email.",
            evidence=evidence,
            task_type_hint=task_type_label,
            digest_eligible=digest_eligible,
            debug_payload={
                "task": task_data,
                "validation": {
                    "evidence_valid": evidence_valid,
                    "type_matches": type_matches,
                    "title_valid": title_valid,
                    "normalization_valid": normalization_valid,
                    "final_confidence": float(final_confidence),
                    "digest_eligible": digest_eligible,
                },
            },
        )

    def _build_event_draft(
        self,
        *,
        event_data: dict[str, Any],
        msg: PreprocessedEmailMessage,
        classification: dict[str, Any],
        decision: str,
    ) -> SuggestionDraft | None:
        title = (event_data.get("title") or "").strip()
        date_value = self._parse_date(event_data.get("date"))
        if not title or not date_value:
            return None

        evidence = self._merge_evidence(
            event_data.get("evidence"),
            classification.get("event_evidence"),
        )
        evidence_valid = self._evidence_matches_source(evidence, msg)
        if not evidence_valid:
            return None

        event_time = self._parse_time(event_data.get("time"))
        if event_data.get("time") and event_time is None:
            return None

        start_dt = datetime.combine(date_value, event_time or time(9, 0))
        start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())
        end_dt = start_dt + timedelta(hours=1)
        normalized_start, normalized_end = self._normalize_event_datetimes(
            start_dt=start_dt,
            end_dt=end_dt,
            all_day=event_time is None,
        )
        if not normalized_start or not normalized_end:
            return None

        type_matches = decision in {"event", "both"}
        if not type_matches:
            return None

        final_confidence = self._event_confidence(
            evidence_count=len(evidence),
            type_matches=type_matches,
            has_explicit_time=event_time is not None,
            normalization_valid=True,
        )
        digest_eligible = bool(final_confidence >= self.digest_confidence_threshold)
        location = (event_data.get("location") or "").strip()
        description = "Suggested event from email."
        if location:
            description += f" Location: {location}."

        return SuggestionDraft(
            suggestion_type=EmailSuggestion.TYPE_EVENT,
            title=title,
            description=description,
            confidence=final_confidence,
            model_confidence=self._to_decimal(event_data.get("confidence")),
            explanation=(event_data.get("explanation") or "").strip()
            or (classification.get("explanation") or "").strip()
            or "Detected an event in the email.",
            evidence=evidence,
            normalized_date_key=date_value.isoformat(),
            start_datetime=normalized_start,
            end_datetime=normalized_end,
            all_day=event_time is None,
            digest_eligible=digest_eligible,
            debug_payload={
                "event": event_data,
                "validation": {
                    "evidence_valid": evidence_valid,
                    "type_matches": type_matches,
                    "date_valid": True,
                    "time_valid": event_time is not None or not event_data.get("time"),
                    "normalization_valid": True,
                    "final_confidence": float(final_confidence),
                    "digest_eligible": digest_eligible,
                },
            },
        )

    def _build_ai_payload(
        self,
        *,
        processed: PreprocessedEmailMessage,
        classification: dict[str, Any],
        extraction: dict[str, Any],
        draft: SuggestionDraft,
    ) -> dict[str, Any]:
        """Store enough internal detail to debug false positives and suppressions."""
        return {
            "preprocessed": {
                "sender": processed.sender,
                "subject": processed.subject,
                "analysis_preview": self._trim_prompt_text(processed.analysis_body, limit=800),
                "is_html": processed.is_html,
                "metadata": processed.metadata,
            },
            "classification": classification,
            "extraction": extraction,
            "evidence": draft.evidence,
            "draft": draft.debug_payload,
        }

    def _invoke_json(self, prompt: str) -> dict[str, Any]:
        llm = ChatOpenAI(model=self.model_name, temperature=0)
        response = llm.invoke(prompt)
        return self._parse_ai_output(getattr(response, "content", "") or "")

    def _parse_ai_output(self, text: str) -> dict[str, Any]:
        if not text:
            return {}
        cleaned = text.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fenced_match:
            cleaned = fenced_match.group(1).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Email suggestion AI returned non-JSON output.")
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _classifier_metadata(msg: PreprocessedEmailMessage) -> dict[str, Any]:
        return {
            "provider": msg.provider,
            "received_at": msg.received_at.isoformat() if msg.received_at else "",
            "protocol_metadata": msg.metadata,
        }

    @staticmethod
    def _html_to_text(value: str) -> str:
        if not value:
            return ""
        cleaned = HTML_COMMENT_RE.sub(" ", value)
        cleaned = SCRIPT_STYLE_RE.sub(" ", cleaned)
        cleaned = HTML_BREAK_RE.sub("\n", cleaned)
        cleaned = HTML_SEPARATOR_RE.sub("\n", cleaned)
        cleaned = HTML_BLOCK_END_RE.sub("\n", cleaned)
        cleaned = HTML_TAG_RE.sub(" ", cleaned)
        cleaned = html.unescape(cleaned).replace("\xa0", " ")
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = MULTISPACE_RE.sub(" ", cleaned)
        return MULTIBLANK_RE.sub("\n\n", cleaned).strip()

    @staticmethod
    def _trim_quoted_thread(lines: list[str]) -> list[str]:
        trimmed: list[str] = []
        for line in lines:
            if any(pattern.match(line) for pattern in QUOTED_THREAD_MARKERS):
                break
            if line.startswith(">"):
                break
            trimmed.append(line)
        return trimmed

    @staticmethod
    def _trim_signature(lines: list[str]) -> list[str]:
        for index, line in enumerate(lines):
            if line.strip() == "--":
                return lines[:index]
        return lines

    @staticmethod
    def _trim_prompt_text(value: str, *, limit: int = 4000) -> str:
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."

    @staticmethod
    def _normalize_evidence(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []

        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            snippet = MULTISPACE_RE.sub(" ", item.strip())
            if not snippet:
                continue
            key = snippet.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(snippet[:240])
        return cleaned[:3]

    def _merge_evidence(self, primary: Any, secondary: Any) -> list[str]:
        return self._normalize_evidence(
            [*self._normalize_evidence(primary), *self._normalize_evidence(secondary)]
        )

    @staticmethod
    def _is_actionable_classification(classification: dict[str, Any]) -> bool:
        decision = str(classification.get("decision", "none")).lower().strip()
        actionable = bool(classification.get("actionable"))
        return actionable and decision in {"task", "event", "both"}

    @staticmethod
    def _source_text(msg: PreprocessedEmailMessage) -> str:
        return "\n".join([msg.subject or "", msg.analysis_body or ""]).strip()

    def _evidence_matches_source(
        self,
        evidence: list[str],
        msg: PreprocessedEmailMessage,
    ) -> bool:
        if not evidence:
            return False
        source_text = self._source_text(msg)
        return all(snippet in source_text for snippet in evidence)

    @staticmethod
    def _task_confidence(
        *,
        evidence_count: int,
        title_valid: bool,
        type_matches: bool,
        normalization_valid: bool,
    ) -> Decimal:
        score = Decimal("0.0")
        if type_matches:
            score += Decimal("0.25")
        if evidence_count > 0:
            score += Decimal("0.35")
        if title_valid:
            score += Decimal("0.20")
        if normalization_valid:
            score += Decimal("0.20")
        return EmailSuggestionService._clamp_decimal(score)

    @staticmethod
    def _event_confidence(
        *,
        evidence_count: int,
        type_matches: bool,
        has_explicit_time: bool,
        normalization_valid: bool,
    ) -> Decimal:
        score = Decimal("0.0")
        if type_matches:
            score += Decimal("0.25")
        if evidence_count > 0:
            score += Decimal("0.35")
        score += Decimal("0.15") if has_explicit_time else Decimal("0.10")
        if normalization_valid:
            score += Decimal("0.25")
        return EmailSuggestionService._clamp_decimal(score)

    @staticmethod
    def _normalize_event_datetimes(
        *,
        start_dt: datetime | None,
        end_dt: datetime | None,
        all_day: bool,
    ) -> tuple[datetime | None, datetime | None]:
        tz = timezone.get_current_timezone()
        if all_day:
            if not start_dt:
                return None, None
            base_date = start_dt.astimezone(tz).date()
            start_normalized = timezone.make_aware(datetime.combine(base_date, time.min), tz)
            end_normalized = timezone.make_aware(
                datetime.combine(base_date + timedelta(days=1), time.min),
                tz,
            )
            return start_normalized, end_normalized
        if not start_dt or not end_dt or end_dt <= start_dt:
            return None, None
        return start_dt, end_dt

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
        return EmailSuggestionService._clamp_decimal(decimal_value)

    @staticmethod
    def _clamp_decimal(value: Decimal) -> Decimal:
        if value < 0:
            return Decimal("0.0")
        if value > 1:
            return Decimal("1.0")
        return value.quantize(Decimal("0.001"))

    @staticmethod
    def _build_fingerprint(
        *,
        integration_id: int,
        message_id: str,
        suggestion_type: str,
        title: str,
        normalized_date_key: str,
    ) -> str:
        normalized_title = " ".join(REPLY_PREFIX_RE.sub("", (title or "").lower()).split())
        raw = f"{integration_id}|{message_id}|{suggestion_type}|{normalized_title}|{normalized_date_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
