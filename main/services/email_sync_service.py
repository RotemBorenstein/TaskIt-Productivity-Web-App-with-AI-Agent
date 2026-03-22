from __future__ import annotations

"""Email sync service for Gmail/Outlook message ingestion.

This module handles manual sync runs, fetches emails from providers, normalizes
them into one internal format, and persists encrypted message content with a
retention window.
"""

import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from datetime import timezone as dt_timezone
from typing import Iterable

import requests
from django.conf import settings
from django.utils import timezone
from ninja.errors import HttpError

from main.models import EmailIntegration, EmailSyncRun, EmailSyncedMessage
from main.services.email_crypto import decrypt_text
from main.services.email_privacy_service import purge_expired_synced_messages

logger = logging.getLogger(__name__)


@dataclass
class NormalizedEmailMessage:
    """Provider-agnostic representation of an email used by the sync pipeline."""

    message_id: str
    sender: str
    subject: str
    body: str
    received_at: timezone.datetime
    provider: str
    metadata: dict[str, object] = field(default_factory=dict)


class EmailSyncService:
    """Coordinates end-to-end email synchronization for a user."""

    def __init__(self):
        """Load configurable limits for sync size and data retention."""
        self.max_messages = int(getattr(settings, "EMAIL_SYNC_MAX_MESSAGES_PER_RUN", 50))
        self.retention_days = int(getattr(settings, "EMAIL_SYNC_RETENTION_DAYS", 30))

    def resolve_window(self, interval: str) -> tuple[timezone.datetime, timezone.datetime]:
        """Translate a preset interval (`day`/`week`) to concrete datetime bounds."""
        now = timezone.now()
        normalized = (interval or "").strip().lower()
        if normalized == EmailSyncRun.PRESET_DAY:
            return now - timedelta(hours=24), now
        if normalized == EmailSyncRun.PRESET_WEEK:
            return now - timedelta(days=7), now
        raise HttpError(400, "interval must be one of: day, week")

    def get_active_integration(self, user) -> EmailIntegration:
        """Return the most recently updated active integration for the user."""
        integration = (
            EmailIntegration.objects.filter(user=user, is_active=True)
            .order_by("-updated_at")
            .first()
        )
        if not integration:
            raise HttpError(400, "No active email integration found.")
        return integration

    def run_manual_sync(self, *, user, interval: str) -> tuple[EmailSyncRun, list[NormalizedEmailMessage]]:
        """Execute one manual sync run and record status/counts in `EmailSyncRun`."""
        purge_expired_synced_messages()
        normalized_interval = (interval or "").strip().lower()
        from_dt, to_dt = self.resolve_window(normalized_interval)
        integration = self.get_active_integration(user)
        sync_run = EmailSyncRun.objects.create(
            user=user,
            integration=integration,
            date_preset=normalized_interval,
            from_datetime=from_dt,
            to_datetime=to_dt,
            status=EmailSyncRun.STATUS_RUNNING,
            started_at=timezone.now(),
        )
        try:
            messages = self.fetch_messages(integration=integration, from_dt=from_dt, to_dt=to_dt)
            self.persist_messages(sync_run=sync_run, messages=messages)
            sync_run.status = EmailSyncRun.STATUS_COMPLETED
            sync_run.emails_scanned_count = len(messages)
            sync_run.finished_at = timezone.now()
            sync_run.save(
                update_fields=["status", "emails_scanned_count", "finished_at"]
            )
            return sync_run, messages
        except Exception as exc:
            sync_run.status = EmailSyncRun.STATUS_FAILED
            sync_run.error_message = str(exc)[:500]
            sync_run.finished_at = timezone.now()
            sync_run.save(update_fields=["status", "error_message", "finished_at"])
            raise

    def persist_messages(self, *, sync_run: EmailSyncRun, messages: Iterable[NormalizedEmailMessage]) -> None:
        """Upsert synced messages and update encrypted subject/body fields."""
        expires_at = timezone.now() + timedelta(days=self.retention_days)
        for msg in messages:
            synced_msg, _ = EmailSyncedMessage.objects.update_or_create(
                integration=sync_run.integration,
                message_id=msg.message_id,
                defaults={
                    "user": sync_run.user,
                    "sync_run": sync_run,
                    "sender": msg.sender[:255],
                    "received_at": msg.received_at,
                    "expires_at": expires_at,
                },
            )
            synced_msg.subject = msg.subject
            synced_msg.body = msg.body
            synced_msg.save(update_fields=["encrypted_subject", "encrypted_body"])

    def fetch_messages(
        self,
        *,
        integration: EmailIntegration,
        from_dt: timezone.datetime,
        to_dt: timezone.datetime,
    ) -> list[NormalizedEmailMessage]:
        """Dispatch provider-specific fetch implementation."""
        if integration.provider == EmailIntegration.PROVIDER_GMAIL:
            return self._fetch_gmail_messages(integration=integration, from_dt=from_dt, to_dt=to_dt)
        if integration.provider == EmailIntegration.PROVIDER_OUTLOOK:
            return self._fetch_outlook_messages(
                integration=integration,
                from_dt=from_dt,
                to_dt=to_dt,
            )
        raise HttpError(400, f"Unsupported provider: {integration.provider}")

    def _get_access_token(self, integration: EmailIntegration) -> str:
        """Decrypt refresh token and exchange it for a short-lived access token."""
        refresh_token = decrypt_text(integration.encrypted_refresh_token)
        if integration.provider == EmailIntegration.PROVIDER_GMAIL:
            return self._refresh_gmail_access_token(refresh_token)
        if integration.provider == EmailIntegration.PROVIDER_OUTLOOK:
            return self._refresh_outlook_access_token(refresh_token)
        raise HttpError(400, f"Unsupported provider: {integration.provider}")

    def _refresh_gmail_access_token(self, refresh_token: str) -> str:
        """Refresh a Gmail access token via Google's OAuth token endpoint."""
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise HttpError(500, "Google OAuth is not configured.")

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        response = requests.post("https://oauth2.googleapis.com/token", data=payload, timeout=20)
        if response.status_code >= 400:
            logger.warning("Gmail token refresh failed for integration=%s", response.text[:200])
            raise HttpError(400, "Failed to refresh Gmail access token.")
        token = response.json().get("access_token")
        if not token:
            raise HttpError(400, "Google token response is missing access_token.")
        return token

    def _refresh_outlook_access_token(self, refresh_token: str) -> str:
        """Refresh an Outlook access token via Microsoft's OAuth token endpoint."""
        client_id = os.getenv("MICROSOFT_CLIENT_ID") or os.getenv("Application_ID")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET") or os.getenv("Client_secret")
        tenant_id = os.getenv("MICROSOFT_TENANT_ID") or os.getenv("Directory_ID") or "common"
        if not client_id or not client_secret:
            raise HttpError(500, "Microsoft OAuth is not configured.")
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "offline_access Mail.Read User.Read",
        }
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        response = requests.post(token_url, data=payload, timeout=20)
        if response.status_code >= 400:
            logger.warning("Outlook token refresh failed for integration=%s", response.text[:200])
            raise HttpError(400, "Failed to refresh Outlook access token.")
        token = response.json().get("access_token")
        if not token:
            raise HttpError(400, "Microsoft token response is missing access_token.")
        return token

    def _fetch_gmail_messages(
        self,
        *,
        integration: EmailIntegration,
        from_dt: timezone.datetime,
        to_dt: timezone.datetime,
    ) -> list[NormalizedEmailMessage]:
        """Fetch Gmail messages in the window and normalize key fields."""
        access_token = self._get_access_token(integration)
        headers = {"Authorization": f"Bearer {access_token}"}
        after_epoch = int(from_dt.timestamp())
        before_epoch = int(to_dt.timestamp())
        query = f"after:{after_epoch} before:{before_epoch}"
        list_response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params={"q": query, "maxResults": self.max_messages},
            timeout=20,
        )
        if list_response.status_code >= 400:
            raise HttpError(400, "Failed to fetch Gmail messages.")

        message_refs = list_response.json().get("messages", []) or []
        normalized: list[NormalizedEmailMessage] = []
        for msg_ref in message_refs[: self.max_messages]:
            msg_id = msg_ref.get("id")
            if not msg_id:
                continue
            detail_res = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                headers=headers,
                params={"format": "full"},
                timeout=20,
            )
            if detail_res.status_code >= 400:
                continue
            detail = detail_res.json()
            headers_list = detail.get("payload", {}).get("headers", [])
            subject = self._gmail_header(headers_list, "Subject")
            sender = self._gmail_header(headers_list, "From")
            internal_date = detail.get("internalDate")
            if internal_date:
                received_at = timezone.datetime.fromtimestamp(
                    int(internal_date) / 1000.0,
                    tz=dt_timezone.utc,
                )
            else:
                received_at = timezone.now()
            body = self._extract_gmail_body(detail.get("payload", {}))
            normalized.append(
                NormalizedEmailMessage(
                    message_id=msg_id,
                    sender=sender,
                    subject=subject,
                    body=body,
                    received_at=received_at,
                    provider=EmailIntegration.PROVIDER_GMAIL,
                    metadata=self._gmail_message_metadata(detail, headers_list),
                )
            )
        return normalized

    def _fetch_outlook_messages(
        self,
        *,
        integration: EmailIntegration,
        from_dt: timezone.datetime,
        to_dt: timezone.datetime,
    ) -> list[NormalizedEmailMessage]:
        """Fetch Outlook messages in the window and normalize key fields."""
        access_token = self._get_access_token(integration)
        headers = {"Authorization": f"Bearer {access_token}"}
        date_filter = (
            f"receivedDateTime ge {from_dt.isoformat()} and receivedDateTime le {to_dt.isoformat()}"
        )
        params = {
            "$top": str(self.max_messages),
            "$orderby": "receivedDateTime desc",
            "$filter": date_filter,
            "$select": "id,subject,body,receivedDateTime,from,internetMessageHeaders",
        }
        response = requests.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            headers=headers,
            params=params,
            timeout=20,
        )
        if response.status_code >= 400:
            raise HttpError(400, "Failed to fetch Outlook messages.")
        items = response.json().get("value", []) or []
        normalized: list[NormalizedEmailMessage] = []
        for item in items[: self.max_messages]:
            message_id = item.get("id")
            if not message_id:
                continue
            sender = (
                item.get("from", {})
                .get("emailAddress", {})
                .get("address", "")
            )
            received_raw = item.get("receivedDateTime")
            if received_raw:
                received_at = timezone.datetime.fromisoformat(
                    received_raw.replace("Z", "+00:00")
                )
            else:
                received_at = timezone.now()
            normalized.append(
                NormalizedEmailMessage(
                    message_id=message_id,
                    sender=sender,
                    subject=item.get("subject", "") or "",
                    body=(item.get("body", {}) or {}).get("content", "") or "",
                    received_at=received_at,
                    provider=EmailIntegration.PROVIDER_OUTLOOK,
                    metadata=self._outlook_message_metadata(item),
                )
            )
        return normalized

    @staticmethod
    def _gmail_header(headers: list[dict], name: str) -> str:
        """Extract a single header value from Gmail's payload header list."""
        target = name.lower()
        for item in headers or []:
            if (item.get("name") or "").lower() == target:
                return item.get("value") or ""
        return ""

    def _extract_gmail_body(self, payload: dict) -> str:
        """Extract preferred body text from Gmail payload/body parts."""
        body_data = (payload.get("body", {}) or {}).get("data")
        if body_data:
            decoded = self._decode_b64url(body_data)
            if decoded:
                return decoded
        for part in payload.get("parts", []) or []:
            mime_type = part.get("mimeType", "")
            if mime_type in {"text/plain", "text/html"}:
                part_data = (part.get("body", {}) or {}).get("data")
                decoded = self._decode_b64url(part_data)
                if decoded:
                    return decoded
        return ""

    def _gmail_message_metadata(
        self,
        detail: dict,
        headers_list: list[dict],
    ) -> dict[str, object]:
        """Collect explicit Gmail metadata that can indicate machine-generated mail."""
        precedence = self._gmail_header(headers_list, "Precedence").strip().lower()
        auto_submitted = self._gmail_header(headers_list, "Auto-Submitted").strip().lower()
        list_unsubscribe = self._gmail_header(headers_list, "List-Unsubscribe")
        list_id = self._gmail_header(headers_list, "List-Id")
        label_ids = detail.get("labelIds", []) or []
        return {
            "provider": EmailIntegration.PROVIDER_GMAIL,
            "auto_submitted": bool(auto_submitted and auto_submitted != "no"),
            "precedence": precedence,
            "has_list_unsubscribe": bool(list_unsubscribe),
            "has_list_id": bool(list_id),
            "gmail_labels": label_ids,
        }

    def _outlook_message_metadata(self, item: dict) -> dict[str, object]:
        """Collect explicit Outlook metadata that can indicate machine-generated mail."""
        headers = item.get("internetMessageHeaders", []) or []
        header_map = {
            (header.get("name") or "").lower(): header.get("value") or ""
            for header in headers
            if isinstance(header, dict)
        }
        auto_submitted = header_map.get("auto-submitted", "").strip().lower()
        precedence = header_map.get("precedence", "").strip().lower()
        return {
            "provider": EmailIntegration.PROVIDER_OUTLOOK,
            "auto_submitted": bool(auto_submitted and auto_submitted != "no"),
            "precedence": precedence,
            "has_list_unsubscribe": bool(header_map.get("list-unsubscribe")),
            "has_list_id": bool(header_map.get("list-id")),
        }

    @staticmethod
    def _decode_b64url(value: str | None) -> str:
        """Decode URL-safe base64 text and return an empty string on failure."""
        if not value:
            return ""
        padding = "=" * (-len(value) % 4)
        try:
            raw = base64.urlsafe_b64decode((value + padding).encode("utf-8"))
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""
