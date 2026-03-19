from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from datetime import datetime, time, timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
import json
from ..models import Event
from zoneinfo import ZoneInfo
from ..services.reminder_service import sync_event_reminder
IL_TZ = ZoneInfo("Asia/Jerusalem")
CALENDAR_URL = "/calendar/"

def _aware(dt):
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt, IL_TZ)
    return dt

def _normalize_incoming_dt(dt):
    """
    Interpret incoming datetimes consistently as Asia/Jerusalem wall time.
    - If naive: assume local (Asia/Jerusalem) and make aware.
    - If aware: convert to Asia/Jerusalem.
    """
    if not dt:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, IL_TZ)
    return dt.astimezone(IL_TZ)


# --------- JSON APIs ----------

@login_required
@require_http_methods(["POST"])
def api_event_create(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    title = (payload.get("title") or "").strip()
    start = _normalize_incoming_dt(parse_datetime(payload.get("start")))
    end = _normalize_incoming_dt(parse_datetime(payload.get("end")))
    all_day = bool(payload.get("allDay"))

    if not title or not start or not end:
        return HttpResponseBadRequest("title, start, end required")
    if end <= start:
        return HttpResponseBadRequest("end must be after start")

    # All_day: end is exclusive (FullCalendar convention)
    if all_day and start.date() == end.date():
        end = timezone.make_aware(
            datetime.combine(end.date(), time.min), IL_TZ) + timedelta(days=1)

    ev = Event.objects.create(
        user=request.user,
        title=title,
        start_datetime=start,
        end_datetime=end,
        all_day=all_day,
        description=(payload.get("description") or "").strip(),
    )
    try:
        sync_event_reminder(
            ev,
            offset_minutes=payload.get("reminderOffsetMinutes"),
            channel_email=bool(payload.get("reminderChannelEmail")),
            channel_telegram=bool(payload.get("reminderChannelTelegram")),
        )
    except ValidationError as exc:
        ev.delete()
        return HttpResponseBadRequest(exc.messages[0])

    reminder = getattr(ev, "reminder", None)

    return JsonResponse({
        "id": ev.id,
        "title": ev.title,
        "start": ev.start_datetime.isoformat(),
        "end": ev.end_datetime.isoformat(),
        "allDay": ev.all_day,
        "description": ev.description or "",
        "reminderOffsetMinutes": reminder.offset_minutes if reminder else None,
        "reminderChannelEmail": reminder.channel_email if reminder else False,
        "reminderChannelTelegram": reminder.channel_telegram if reminder else False,
    }, status=201)

@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_event_detail(request, pk):
    ev = get_object_or_404(Event, pk=pk, user=request.user)

    if request.method == "GET":
        reminder = getattr(ev, "reminder", None)
        return JsonResponse({
            "id": ev.id,
            "title": ev.title,
            "start": ev.start_datetime.isoformat(),
            "end": ev.end_datetime.isoformat(),
            "allDay": ev.all_day,
            "description": ev.description or "",
            "reminderOffsetMinutes": reminder.offset_minutes if reminder else None,
            "reminderChannelEmail": reminder.channel_email if reminder else False,
            "reminderChannelTelegram": reminder.channel_telegram if reminder else False,
        })

    if request.method == "DELETE":
        ev.delete()
        return HttpResponse(status=204)

    # PATCH
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")


    title = (payload.get("title").strip() if isinstance(payload.get("title"), str) else ev.title)
    description = (payload.get("description").strip() if isinstance(payload.get("description"), str) else (ev.description or ""))
    start = _normalize_incoming_dt(parse_datetime(payload.get("start"))) if payload.get("start") else ev.start_datetime
    end = _normalize_incoming_dt(parse_datetime(payload.get("end"))) if payload.get("end") else ev.end_datetime
    all_day = bool(payload.get("allDay")) if "allDay" in payload else ev.all_day
    existing_reminder = getattr(ev, "reminder", None)
    reminder_offset = (
        payload.get("reminderOffsetMinutes")
        if "reminderOffsetMinutes" in payload
        else (existing_reminder.offset_minutes if existing_reminder else None)
    )
    reminder_channel_email = (
        bool(payload.get("reminderChannelEmail"))
        if "reminderChannelEmail" in payload
        else (existing_reminder.channel_email if existing_reminder else False)
    )
    reminder_channel_telegram = (
        bool(payload.get("reminderChannelTelegram"))
        if "reminderChannelTelegram" in payload
        else (existing_reminder.channel_telegram if existing_reminder else False)
    )

    if not title or not start or not end:
        return HttpResponseBadRequest("title, start, end required")
    if end <= start:
        return HttpResponseBadRequest("end must be after start")

    if all_day and start.date() == end.date():
        end = timezone.make_aware(datetime.combine(end.date(), time.min), IL_TZ) + timedelta(days=1)

    try:
        with transaction.atomic():
            ev.title = title
            ev.description = description
            ev.start_datetime = start
            ev.end_datetime = end
            ev.all_day = all_day
            ev.save()
            sync_event_reminder(
                ev,
                offset_minutes=reminder_offset,
                channel_email=reminder_channel_email,
                channel_telegram=reminder_channel_telegram,
            )
    except ValidationError as exc:
        return HttpResponseBadRequest(exc.messages[0])

    reminder = getattr(ev, "reminder", None)

    return JsonResponse({
        "id": ev.id,
        "title": ev.title,
        "start": ev.start_datetime.isoformat(),
        "end": ev.end_datetime.isoformat(),
        "allDay": ev.all_day,
        "description": ev.description or "",
        "reminderOffsetMinutes": reminder.offset_minutes if reminder else None,
        "reminderChannelEmail": reminder.channel_email if reminder else False,
        "reminderChannelTelegram": reminder.channel_telegram if reminder else False,
    }, status=200)
