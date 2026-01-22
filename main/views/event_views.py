from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from datetime import datetime, time, timedelta
import json
from ..models import Event
from zoneinfo import ZoneInfo
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
    print("manual start:", start)
    print("manual end:", end)
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

    return JsonResponse({
        "id": ev.id,
        "title": ev.title,
        "start": ev.start_datetime.isoformat(),
        "end": ev.end_datetime.isoformat(),
        "allDay": ev.all_day,
        "description": ev.description or "",
    }, status=201)

@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_event_detail(request, pk):
    ev = get_object_or_404(Event, pk=pk, user=request.user)

    if request.method == "GET":
        return JsonResponse({
            "id": ev.id,
            "title": ev.title,
            "start": ev.start_datetime.isoformat(),
            "end": ev.end_datetime.isoformat(),
            "allDay": ev.all_day,
            "description": ev.description or "",
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

    if not title or not start or not end:
        return HttpResponseBadRequest("title, start, end required")
    if end <= start:
        return HttpResponseBadRequest("end must be after start")

    if all_day and start.date() == end.date():
        end = timezone.make_aware(datetime.combine(end.date(), time.min), IL_TZ) + timedelta(days=1)

    ev.title = title
    ev.description = description
    ev.start_datetime = start
    ev.end_datetime = end
    ev.all_day = all_day
    ev.save()

    return JsonResponse({
        "id": ev.id,
        "title": ev.title,
        "start": ev.start_datetime.isoformat(),
        "end": ev.end_datetime.isoformat(),
        "allDay": ev.all_day,
        "description": ev.description or "",
    }, status=200)
