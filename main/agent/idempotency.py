# main/agent/idempotency.py
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

_WS_RE = re.compile(r"\s+")


def normalize_title(s: Optional[str]) -> str:
    """
    Title normalization:
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
    Body normalization (conservative):
    - None -> ""
    - normalize newlines
    - strip surrounding whitespace
    """
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.strip()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class IdempotencyContext:
    """
    Per-request idempotency cache.
    Minimal architecture: lives only during a single agent_endpoint POST.
    """
    user_id: int
    request_id: str
    _cache: Dict[str, Any] = field(default_factory=dict)

    def make_key(self, tool_name: str, signature: Dict[str, Any]) -> str:
        payload = {
            "v": 1,
            "user_id": self.user_id,
            "request_id": self.request_id,
            "tool": tool_name,
            "sig": signature,
        }
        return sha256_hex(stable_json(payload))

    def run(self, tool_name: str, signature: Dict[str, Any], fn: Callable[[], Any]) -> Any:
        key = self.make_key(tool_name, signature)
        if key in self._cache:
            #return self._cache[key]
            return (
                "STATUS: noop\n"
                "MESSAGE: This exact action was already executed in this request. "
                "Do NOT call this tool again.\n"
                "STOP"
            )

        result = fn()
        self._cache[key] = result
        return result
