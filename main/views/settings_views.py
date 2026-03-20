"""Settings page views and notification-related JSON endpoints."""

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..services.notification_email_service import NotificationEmailService
from ..services.reminder_service import (
    ensure_telegram_connect_token,
    get_user_notification_settings,
)
from ..services.telegram_notification_service import TelegramNotificationService


@login_required
def settings_page(request):
    """Display the user settings page."""
    notification_settings = ensure_telegram_connect_token(
        get_user_notification_settings(request.user)
    )
    telegram_service = TelegramNotificationService()
    return render(
        request,
        "main/settings.html",
        {
            "notification_settings": notification_settings,
            "telegram_bot_configured": telegram_service.is_configured(),
            "telegram_connect_url": telegram_service.deep_link_url(
                notification_settings.telegram_connect_token
            ),
        },
    )


@login_required
def email_suggestions_page(request):
    """Display the dedicated email suggestions review page."""
    return render(request, "main/email_suggestions.html")


def _parse_json(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON.")


@login_required
@require_http_methods(["GET"])
def notification_settings_status(request):
    settings_obj = ensure_telegram_connect_token(
        get_user_notification_settings(request.user)
    )
    telegram_service = TelegramNotificationService()
    return JsonResponse(
        {
            "email_enabled": settings_obj.email_enabled,
            "telegram_enabled": settings_obj.telegram_enabled,
            "telegram_connected": settings_obj.telegram_is_connected,
            "email_address": request.user.email or "",
            "telegram_connect_url": telegram_service.deep_link_url(
                settings_obj.telegram_connect_token
            ),
            "telegram_bot_configured": telegram_service.is_configured(),
        }
    )


@login_required
@require_http_methods(["POST"])
def update_notification_settings(request):
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    settings_obj = get_user_notification_settings(request.user)
    email_address = (payload.get("email_address") or "").strip()
    if email_address != request.user.email:
        request.user.email = email_address
        request.user.save(update_fields=["email"])

    settings_obj.email_enabled = bool(payload.get("email_enabled"))
    settings_obj.telegram_enabled = bool(payload.get("telegram_enabled"))
    settings_obj.save(update_fields=["email_enabled", "telegram_enabled", "updated_at"])

    return JsonResponse({"success": True})


@login_required
@require_http_methods(["GET"])
def telegram_connect_link(request):
    settings_obj = ensure_telegram_connect_token(
        get_user_notification_settings(request.user)
    )
    telegram_service = TelegramNotificationService()
    return JsonResponse(
        {"url": telegram_service.deep_link_url(settings_obj.telegram_connect_token)}
    )


@login_required
@require_http_methods(["POST"])
def telegram_poll_connect(request):
    settings_obj = ensure_telegram_connect_token(
        get_user_notification_settings(request.user)
    )
    telegram_service = TelegramNotificationService()
    result = telegram_service.get_updates()
    if not result.success:
        return JsonResponse({"success": False, "detail": result.error}, status=400)

    matched_chat_id = ""
    for update in result.payload.get("result", []):
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        if not text.startswith("/start"):
            continue

        token = text.replace("/start", "", 1).strip()
        if token != settings_obj.telegram_connect_token:
            continue

        chat = message.get("chat") or {}
        matched_chat_id = str(chat.get("id") or "")
        if matched_chat_id:
            break

    if matched_chat_id:
        settings_obj.telegram_chat_id = matched_chat_id
        settings_obj.telegram_connected_at = timezone.now()
        settings_obj.telegram_enabled = True
        settings_obj.save(
            update_fields=[
                "telegram_chat_id",
                "telegram_connected_at",
                "telegram_enabled",
                "updated_at",
            ]
        )
        return JsonResponse({"success": True, "connected": True})

    return JsonResponse({"success": True, "connected": False})


@login_required
@require_http_methods(["POST"])
def telegram_disconnect(request):
    settings_obj = get_user_notification_settings(request.user)
    settings_obj.telegram_chat_id = ""
    settings_obj.telegram_connected_at = None
    settings_obj.telegram_enabled = False
    settings_obj.save(
        update_fields=[
            "telegram_chat_id",
            "telegram_connected_at",
            "telegram_enabled",
            "updated_at",
        ]
    )
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def send_test_email(request):
    settings_obj = get_user_notification_settings(request.user)
    if not request.user.email:
        return JsonResponse(
            {"success": False, "detail": "Add an email address first."}, status=400
        )

    service = NotificationEmailService()
    result = service.send(
        to_email=request.user.email,
        subject="TaskIt test notification",
        html="<p>This is a TaskIt test notification.</p>",
        text="This is a TaskIt test notification.",
    )
    if not result.success:
        return JsonResponse({"success": False, "detail": result.error}, status=400)

    settings_obj.last_test_email_at = timezone.now()
    settings_obj.save(update_fields=["last_test_email_at", "updated_at"])
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def send_test_telegram(request):
    settings_obj = get_user_notification_settings(request.user)
    if not settings_obj.telegram_chat_id:
        return JsonResponse(
            {"success": False, "detail": "Connect Telegram first."}, status=400
        )

    service = TelegramNotificationService()
    result = service.send_message(
        chat_id=settings_obj.telegram_chat_id,
        text="This is a TaskIt test notification.",
    )
    if not result.success:
        return JsonResponse({"success": False, "detail": result.error}, status=400)

    settings_obj.last_test_telegram_at = timezone.now()
    settings_obj.save(update_fields=["last_test_telegram_at", "updated_at"])
    return JsonResponse({"success": True})
