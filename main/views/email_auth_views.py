from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import timedelta
from typing import Literal, Optional
from urllib.parse import urlencode

import requests
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from ..models import EmailIntegration, EmailOAuthState, EmailSuggestion, EmailSyncRun, Event, Task

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None


logger = logging.getLogger(__name__)

api = NinjaAPI(title="TaskIt email auth api", urls_namespace="email_auth")

OAUTH_STATE_TTL_MINUTES = 10
SETTINGS_REDIRECT_PATH = "/settings/"
OUTLOOK_SCOPES = ["offline_access", "Mail.Read", "User.Read"]
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
]


class EmailIntegrationStatusOut(Schema):
    is_connected: bool
    provider: Optional[Literal["gmail", "outlook"]] = None
    email: Optional[str] = None


class ConnectionUrlOut(Schema):
    url: str


class SuccessOut(Schema):
    success: bool


class DisconnectOut(SuccessOut):
    disconnected_count: int


class DeleteDataOut(SuccessOut):
    deleted_suggestions: int
    deleted_sync_runs: int
    deleted_tasks: int
    deleted_events: int


def _config(name: str, fallback_name: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    if fallback_name:
        return os.getenv(fallback_name)
    return None


def _microsoft_client_id() -> Optional[str]:
    return _config("MICROSOFT_CLIENT_ID", "Application_ID")


def _microsoft_client_secret() -> Optional[str]:
    return _config("MICROSOFT_CLIENT_SECRET", "Client_secret")


def _microsoft_tenant_id() -> str:
    return _config("MICROSOFT_TENANT_ID", "Directory_ID") or "common"


def _microsoft_redirect_uri(request) -> str:
    return _config("MICROSOFT_REDIRECT_URI") or request.build_absolute_uri("/auth/microsoft/callback")


def _google_client_id() -> Optional[str]:
    return _config("GOOGLE_CLIENT_ID")


def _google_client_secret() -> Optional[str]:
    return _config("GOOGLE_CLIENT_SECRET")


def _google_redirect_uri(request) -> str:
    return _config("GOOGLE_REDIRECT_URI") or request.build_absolute_uri("/auth/google/callback")


def _state_hash(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def _get_fernet() -> Fernet:
    if Fernet is None:
        raise HttpError(
            500,
            "Missing dependency: install 'cryptography' before using email integration.",
        )

    encryption_key = os.getenv("EMAIL_TOKEN_ENCRYPTION_KEY")
    if not encryption_key:
        raise HttpError(500, "Missing EMAIL_TOKEN_ENCRYPTION_KEY in environment.")

    try:
        return Fernet(encryption_key.encode("utf-8"))
    except Exception as exc:
        raise HttpError(500, "EMAIL_TOKEN_ENCRYPTION_KEY is invalid.") from exc


def _encrypt_refresh_token(refresh_token: str) -> str:
    return _get_fernet().encrypt(refresh_token.encode("utf-8")).decode("utf-8")


def _outlook_token_url() -> str:
    tenant_id = _microsoft_tenant_id()
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _exchange_outlook_code_for_tokens(code: str, redirect_uri: str) -> dict:
    client_id = _microsoft_client_id()
    client_secret = _microsoft_client_secret()
    if not client_id or not client_secret:
        raise HttpError(
            500,
            "Microsoft OAuth is not configured. Missing client id or client secret.",
        )

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": " ".join(OUTLOOK_SCOPES),
    }

    try:
        response = requests.post(_outlook_token_url(), data=payload, timeout=20)
    except requests.RequestException as exc:
        logger.exception("Outlook token exchange network error")
        raise HttpError(502, "Could not reach Microsoft token endpoint.") from exc

    if response.status_code >= 400:
        logger.warning("Outlook token exchange failed: %s", response.text[:300])
        raise HttpError(400, "Microsoft rejected the authorization code.")

    token_data = response.json()
    if "access_token" not in token_data:
        raise HttpError(400, "Microsoft token response is missing access_token.")
    return token_data


def _exchange_google_code_for_tokens(code: str, redirect_uri: str) -> dict:
    client_id = _google_client_id()
    client_secret = _google_client_secret()
    if not client_id or not client_secret:
        raise HttpError(
            500,
            "Google OAuth is not configured. Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET.",
        )

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        response = requests.post("https://oauth2.googleapis.com/token", data=payload, timeout=20)
    except requests.RequestException as exc:
        logger.exception("Google token exchange network error")
        raise HttpError(502, "Could not reach Google token endpoint.") from exc

    if response.status_code >= 400:
        logger.warning("Google token exchange failed: %s", response.text[:300])
        raise HttpError(400, "Google rejected the authorization code.")

    token_data = response.json()
    if "access_token" not in token_data:
        raise HttpError(400, "Google token response is missing access_token.")
    return token_data


def _fetch_outlook_user_profile(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers, timeout=20)
    except requests.RequestException as exc:
        logger.exception("Graph /me network error")
        raise HttpError(502, "Could not fetch Outlook user profile.") from exc

    if response.status_code >= 400:
        logger.warning("Graph /me failed: %s", response.text[:300])
        raise HttpError(400, "Could not read Outlook profile.")

    return response.json()


def _build_outlook_authorize_url(request) -> str:
    client_id = _microsoft_client_id()
    if not client_id:
        raise HttpError(
            500,
            "Microsoft OAuth is not configured. Missing MICROSOFT_CLIENT_ID.",
        )

    redirect_uri = _microsoft_redirect_uri(request)
    raw_state = secrets.token_urlsafe(48)
    EmailOAuthState.objects.create(
        user=request.user,
        provider=EmailIntegration.PROVIDER_OUTLOOK,
        state_hash=_state_hash(raw_state),
        redirect_uri=redirect_uri,
        expires_at=timezone.now() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    )

    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(OUTLOOK_SCOPES),
            "state": raw_state,
            "prompt": "select_account",
        }
    )

    tenant_id = _microsoft_tenant_id()
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?{query}"


def _build_gmail_authorize_url(request) -> str:
    client_id = _google_client_id()
    if not client_id:
        raise HttpError(
            500,
            "Google OAuth is not configured. Missing GOOGLE_CLIENT_ID.",
        )

    redirect_uri = _google_redirect_uri(request)
    raw_state = secrets.token_urlsafe(48)
    EmailOAuthState.objects.create(
        user=request.user,
        provider=EmailIntegration.PROVIDER_GMAIL,
        state_hash=_state_hash(raw_state),
        redirect_uri=redirect_uri,
        expires_at=timezone.now() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    )

    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(GMAIL_SCOPES),
            "state": raw_state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def _redirect_settings_with_status(param: str, value: str):
    base_path = reverse("main:settings")
    join_char = "&" if "?" in base_path else "?"
    return redirect(f"{base_path}{join_char}{param}={value}")


def _handle_outlook_callback(request):
    error = request.GET.get("error")
    if error:
        logger.info("Outlook OAuth rejected by provider: %s", error)
        return _redirect_settings_with_status("email_error", "outlook_rejected")

    code = request.GET.get("code")
    raw_state = request.GET.get("state")
    if not code or not raw_state:
        return _redirect_settings_with_status("email_error", "outlook_missing_params")

    oauth_state = (
        EmailOAuthState.objects.filter(
            provider=EmailIntegration.PROVIDER_OUTLOOK,
            state_hash=_state_hash(raw_state),
            used_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if not oauth_state:
        return _redirect_settings_with_status("email_error", "outlook_invalid_state")

    now = timezone.now()
    if oauth_state.expires_at <= now:
        return _redirect_settings_with_status("email_error", "outlook_state_expired")

    try:
        token_data = _exchange_outlook_code_for_tokens(code=code, redirect_uri=oauth_state.redirect_uri)
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise HttpError(
                400,
                "Microsoft did not return refresh_token. Ensure offline_access scope is enabled.",
            )

        profile = _fetch_outlook_user_profile(token_data["access_token"])
        provider_account_id = profile.get("id")
        email_address = profile.get("mail") or profile.get("userPrincipalName")
        if not provider_account_id or not email_address:
            raise HttpError(400, "Could not identify Outlook account.")

        expires_in_raw = token_data.get("expires_in")
        access_token_expires_at = None
        if expires_in_raw is not None:
            try:
                access_token_expires_at = now + timedelta(seconds=int(expires_in_raw))
            except (TypeError, ValueError):
                access_token_expires_at = None

        scope_value = token_data.get("scope", "")
        scopes = [scope for scope in scope_value.split(" ") if scope]

        with transaction.atomic():
            EmailIntegration.objects.update_or_create(
                user=oauth_state.user,
                provider=EmailIntegration.PROVIDER_OUTLOOK,
                defaults={
                    "provider_account_id": provider_account_id,
                    "email_address": email_address,
                    "scopes": scopes,
                    "encrypted_refresh_token": _encrypt_refresh_token(refresh_token),
                    "access_token_expires_at": access_token_expires_at,
                    "is_active": True,
                    "last_used_at": now,
                },
            )
            oauth_state.used_at = now
            oauth_state.save(update_fields=["used_at"])

        logger.info(
            "Email integration connected: user_id=%s provider=outlook",
            oauth_state.user_id,
        )
        return _redirect_settings_with_status("email_connected", "outlook")
    except HttpError as exc:
        logger.warning("Outlook OAuth callback failed: %s", exc.message)
        return _redirect_settings_with_status("email_error", "outlook_callback_failed")
    except Exception:
        logger.exception("Unexpected error in Outlook callback")
        return _redirect_settings_with_status("email_error", "outlook_callback_failed")


def _handle_gmail_callback(request):
    error = request.GET.get("error")
    if error:
        logger.info("Gmail OAuth rejected by provider: %s", error)
        return _redirect_settings_with_status("email_error", "gmail_rejected")

    code = request.GET.get("code")
    raw_state = request.GET.get("state")
    if not code or not raw_state:
        return _redirect_settings_with_status("email_error", "gmail_missing_params")

    oauth_state = (
        EmailOAuthState.objects.filter(
            provider=EmailIntegration.PROVIDER_GMAIL,
            state_hash=_state_hash(raw_state),
            used_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if not oauth_state:
        return _redirect_settings_with_status("email_error", "gmail_invalid_state")

    now = timezone.now()
    if oauth_state.expires_at <= now:
        return _redirect_settings_with_status("email_error", "gmail_state_expired")

    try:
        token_data = _exchange_google_code_for_tokens(code=code, redirect_uri=oauth_state.redirect_uri)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")

        try:
            profile_res = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
        except requests.RequestException as exc:
            logger.exception("Google userinfo network error")
            raise HttpError(502, "Could not fetch Google user profile.") from exc

        if profile_res.status_code >= 400:
            logger.warning("Google userinfo failed: %s", profile_res.text[:300])
            raise HttpError(400, "Could not read Google profile.")

        profile = profile_res.json()
        provider_account_id = profile.get("sub")
        email_address = profile.get("email")
        if not provider_account_id or not email_address:
            raise HttpError(400, "Could not identify Gmail account.")

        expires_in_raw = token_data.get("expires_in")
        access_token_expires_at = None
        if expires_in_raw is not None:
            try:
                access_token_expires_at = now + timedelta(seconds=int(expires_in_raw))
            except (TypeError, ValueError):
                access_token_expires_at = None

        scope_value = token_data.get("scope", "")
        scopes = [scope for scope in scope_value.split(" ") if scope]

        existing = EmailIntegration.objects.filter(
            user=oauth_state.user,
            provider=EmailIntegration.PROVIDER_GMAIL,
        ).first()

        encrypted_refresh_token = None
        if refresh_token:
            encrypted_refresh_token = _encrypt_refresh_token(refresh_token)
        elif existing and existing.encrypted_refresh_token:
            encrypted_refresh_token = existing.encrypted_refresh_token
        else:
            raise HttpError(
                400,
                "Google did not return refresh_token. Reconnect with consent prompt.",
            )

        with transaction.atomic():
            EmailIntegration.objects.update_or_create(
                user=oauth_state.user,
                provider=EmailIntegration.PROVIDER_GMAIL,
                defaults={
                    "provider_account_id": provider_account_id,
                    "email_address": email_address,
                    "scopes": scopes,
                    "encrypted_refresh_token": encrypted_refresh_token,
                    "access_token_expires_at": access_token_expires_at,
                    "is_active": True,
                    "last_used_at": now,
                },
            )
            oauth_state.used_at = now
            oauth_state.save(update_fields=["used_at"])

        logger.info(
            "Email integration connected: user_id=%s provider=gmail",
            oauth_state.user_id,
        )
        return _redirect_settings_with_status("email_connected", "gmail")
    except HttpError as exc:
        logger.warning("Gmail OAuth callback failed: %s", exc.message)
        return _redirect_settings_with_status("email_error", "gmail_callback_failed")
    except Exception:
        logger.exception("Unexpected error in Gmail callback")
        return _redirect_settings_with_status("email_error", "gmail_callback_failed")


@api.get("/status", response=EmailIntegrationStatusOut)
@login_required
def get_email_integration_status(request):
    """Check if the user has an email account connected."""
    integration = (
        EmailIntegration.objects.filter(user=request.user, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    return {
        "is_connected": bool(integration),
        "provider": integration.provider if integration else None,
        "email": integration.email_address if integration else None,
    }


@api.get("/connect/gmail", response=ConnectionUrlOut)
@login_required
def connect_gmail(request):
    """Step 1: Generate the Gmail OAuth authorization URL."""
    return {"url": _build_gmail_authorize_url(request)}


@api.get("/connect/outlook", response=ConnectionUrlOut)
@login_required
def connect_outlook(request):
    """Step 1: Generate the Outlook OAuth authorization URL."""
    return {"url": _build_outlook_authorize_url(request)}


@api.get("/callback/gmail")
def callback_gmail(request, code: str = "", state: str = ""):
    """Step 2: Handle callback from Google and redirect to settings."""
    return _handle_gmail_callback(request)


@api.get("/callback/outlook")
def callback_outlook(request, code: str = "", state: str = ""):
    """Step 2: Handle callback from Microsoft and redirect to settings."""
    return _handle_outlook_callback(request)


def microsoft_callback_alias(request):
    """Django route alias for Microsoft callback path configured in app registration."""
    return _handle_outlook_callback(request)


def google_callback_alias(request):
    """Django route alias for Google callback path configured in app registration."""
    return _handle_gmail_callback(request)


@api.post("/disconnect", response=DisconnectOut)
@login_required
def disconnect_email(request, provider: str = ""):
    """Soft-disconnect connected email provider(s) for the current user."""
    integrations = EmailIntegration.objects.filter(user=request.user, is_active=True)
    provider = (provider or "").strip().lower()
    if provider and provider not in {
        EmailIntegration.PROVIDER_GMAIL,
        EmailIntegration.PROVIDER_OUTLOOK,
    }:
        raise HttpError(400, "provider must be either 'gmail' or 'outlook'.")

    if provider:
        integrations = integrations.filter(provider=provider)

    disconnected_count = integrations.update(
        is_active=False,
        encrypted_refresh_token="",
        access_token_expires_at=None,
        last_used_at=timezone.now(),
        token_version=F("token_version") + 1,
    )

    if disconnected_count:
        logger.info(
            "Email integration disconnected: user_id=%s provider=%s count=%s",
            request.user.id,
            provider or "all",
            disconnected_count,
        )

    return {"success": True, "disconnected_count": disconnected_count}


@api.delete("/data", response=DeleteDataOut)
@login_required
def delete_email_data(request):
    """Delete TaskIt data derived from email suggestions."""
    suggestions_qs = EmailSuggestion.objects.filter(user=request.user)

    task_ids = list(
        suggestions_qs.exclude(created_task__isnull=True).values_list("created_task_id", flat=True)
    )
    event_ids = list(
        suggestions_qs.exclude(created_event__isnull=True).values_list("created_event_id", flat=True)
    )

    with transaction.atomic():
        deleted_tasks, _ = Task.objects.filter(user=request.user, id__in=task_ids).delete()
        deleted_events, _ = Event.objects.filter(user=request.user, id__in=event_ids).delete()
        deleted_suggestions, _ = suggestions_qs.delete()
        deleted_sync_runs, _ = EmailSyncRun.objects.filter(user=request.user).delete()

    logger.info("Email-derived data deleted: user_id=%s", request.user.id)

    return {
        "success": True,
        "deleted_suggestions": deleted_suggestions,
        "deleted_sync_runs": deleted_sync_runs,
        "deleted_tasks": deleted_tasks,
        "deleted_events": deleted_events,
    }
