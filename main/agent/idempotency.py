# main/agent/idempotency.py
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from main.agent.rate_limits import record_assistant_signal

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_RAG_FOUND = "rag_status: found"
_RAG_NOT_FOUND = "rag_status: not_found"
_NO_SAFE_RAG_RESULT = "i couldn't find safe relevant information in your taskit data for that request."


def normalize_title(s: Optional[str]) -> str:
    """
    Normalize short user-facing text for duplicate-call signatures.

    - None -> ""
    - strip
    - collapse whitespace
    - case-insensitive via casefold()
    """
    if not s:
        return ""
    s = _WS_RE.sub(" ", s).strip()
    return s.casefold()


def normalize_body(s: Optional[str]) -> str:
    """
    Normalize larger text fields conservatively for duplicate-call signatures.

    - None -> ""
    - normalize newlines
    - trim surrounding whitespace
    """
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.strip()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AssistantDuplicateLoopAbort(Exception):
    """Raised when the same logical tool call loops after a first duplicate block."""

    def __init__(self, *, final_answer: str, tool_name: str, signature_hash: str):
        super().__init__(final_answer)
        self.final_answer = final_answer
        self.tool_name = tool_name
        self.signature_hash = signature_hash


@dataclass
class _DuplicateRecord:
    """Stored result for one logical tool call within a single assistant request."""

    tool_name: str
    result: Any
    final_answer: str
    duplicate_hits: int = 0


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return str(result)


def _format_duplicate_block_result(previous_result: Any) -> str:
    previous_text = _result_text(previous_result).strip()
    return (
        "STATUS: duplicate_blocked\n"
        "MESSAGE: This exact tool call already ran in this request. "
        "Use the previous result below and do NOT call the same tool again.\n"
        "PREVIOUS_RESULT_START\n"
        f"{previous_text}\n"
        "PREVIOUS_RESULT_END\n"
        "STOP"
    )


def _duplicate_final_answer(tool_name: str, result: Any) -> str:
    text = _result_text(result).strip()
    normalized = text.casefold()

    if tool_name == "get_tasks":
        if "no tasks found" in normalized:
            return "I already checked that and found no matching tasks."
        return "I already retrieved your tasks and stopped the duplicate lookup."

    if tool_name == "get_events":
        if "no events found" in normalized:
            return "I already checked that and found no matching events."
        return "I already retrieved your events and stopped the duplicate lookup."

    if tool_name == "analyze_stats":
        return "I already retrieved your stats and stopped the duplicate lookup."

    if tool_name == "search_knowledge":
        if normalized.startswith(_RAG_NOT_FOUND) or _NO_SAFE_RAG_RESULT in normalized:
            return "I already checked your TaskIt data and couldn't find relevant information."
        if normalized.startswith(_RAG_FOUND):
            return "I already retrieved that information and stopped the duplicate lookup."

    write_labels = {
        "add_task": "task",
        "add_event": "event",
        "add_subject": "subject",
        "add_note": "note",
    }
    if tool_name in write_labels and (
        "status: success" in normalized
        or " created" in normalized
        or "already exists" in normalized
    ):
        return f"The {write_labels[tool_name]} was already created, so I stopped the duplicate action."

    return "I already ran that exact TaskIt action and stopped the duplicate repeat."


@dataclass
class IdempotencyContext:
    """
    Request-scoped duplicate-call guard for assistant tools.

    The first exact duplicate returns a strong structured no-op observation so the
    model can summarize naturally. A repeated duplicate aborts the run with a
    deterministic final answer before the executor burns more iterations.
    """

    user_id: int
    request_id: str
    _records: Dict[str, _DuplicateRecord] = field(default_factory=dict)

    def make_key(self, tool_name: str, signature: Dict[str, Any]) -> str:
        payload = {
            "v": 2,
            "user_id": self.user_id,
            "request_id": self.request_id,
            "tool": tool_name,
            "sig": signature,
        }
        return sha256_hex(stable_json(payload))

    def run(self, tool_name: str, signature: Dict[str, Any], fn: Callable[[], Any]) -> Any:
        key = self.make_key(tool_name, signature)
        record = self._records.get(key)
        if record is None:
            result = fn()
            self._records[key] = _DuplicateRecord(
                tool_name=tool_name,
                result=result,
                final_answer=_duplicate_final_answer(tool_name, result),
            )
            return result

        record.duplicate_hits += 1
        if record.duplicate_hits == 1:
            record_assistant_signal("tool_duplicate_blocked")
            logger.warning(
                "assistant_tool_duplicate_blocked request_id=%s user_id=%s tool_name=%s signature_hash=%s",
                self.request_id,
                self.user_id,
                tool_name,
                key,
            )
            return _format_duplicate_block_result(record.result)

        record_assistant_signal("tool_duplicate_abort")
        logger.warning(
            "assistant_tool_duplicate_abort request_id=%s user_id=%s tool_name=%s signature_hash=%s",
            self.request_id,
            self.user_id,
            tool_name,
            key,
        )
        raise AssistantDuplicateLoopAbort(
            final_answer=record.final_answer,
            tool_name=tool_name,
            signature_hash=key,
        )
