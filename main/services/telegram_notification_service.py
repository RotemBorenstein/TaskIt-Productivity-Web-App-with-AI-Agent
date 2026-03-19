"""Telegram bot helpers for connection and reminder delivery."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings


@dataclass
class TelegramResult:
    success: bool
    error: str = ""
    payload: dict | None = None


class TelegramNotificationService:
    """Thin Telegram Bot API client for notification delivery."""

    def __init__(self):
        self.bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        self.bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
        self.api_base = getattr(settings, "TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.bot_token)

    def deep_link_url(self, token: str) -> str:
        if not self.bot_username:
            return ""
        return f"https://t.me/{self.bot_username}?start={token}"

    def _request(self, method_name: str, *, json=None, params=None) -> TelegramResult:
        if not self.is_configured():
            return TelegramResult(success=False, error="Telegram bot is not configured.")

        try:
            response = requests.post(
                f"{self.api_base}/bot{self.bot_token}/{method_name}",
                json=json,
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            return TelegramResult(success=False, error=f"Telegram request failed: {exc}")

        if response.status_code >= 400:
            return TelegramResult(success=False, error=response.text[:500])

        payload = response.json()
        if not payload.get("ok"):
            return TelegramResult(success=False, error=str(payload))

        return TelegramResult(success=True, payload=payload)

    def send_message(self, *, chat_id: str, text: str) -> TelegramResult:
        return self._request(
            "sendMessage",
            json={"chat_id": chat_id, "text": text},
        )

    def get_updates(self, *, offset: int | None = None) -> TelegramResult:
        params = {"timeout": 1}
        if offset is not None:
            params["offset"] = offset
        return self._request("getUpdates", params=params)
