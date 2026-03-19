"""Resend-backed email delivery for reminder notifications."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings


@dataclass
class DeliveryResult:
    success: bool
    provider_message_id: str = ""
    error: str = ""


class NotificationEmailService:
    """Send reminder emails via the Resend REST API."""

    def __init__(self):
        self.api_key = getattr(settings, "RESEND_API_KEY", "")
        self.base_url = getattr(settings, "RESEND_API_BASE_URL", "https://api.resend.com").rstrip("/")
        self.default_from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")

    def is_configured(self) -> bool:
        return bool(self.api_key and self.default_from_email)

    def send(self, *, to_email: str, subject: str, html: str, text: str = "") -> DeliveryResult:
        if not self.is_configured():
            return DeliveryResult(success=False, error="Resend is not configured.")

        try:
            response = requests.post(
                f"{self.base_url}/emails",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.default_from_email,
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                    "text": text or subject,
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            return DeliveryResult(success=False, error=f"Email request failed: {exc}")

        if response.status_code >= 400:
            return DeliveryResult(success=False, error=response.text[:500])

        payload = response.json()
        return DeliveryResult(
            success=True,
            provider_message_id=payload.get("id", ""),
        )
