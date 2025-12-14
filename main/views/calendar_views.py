from django.shortcuts import render
from ..models import Task, DailyTaskCompletion, Event
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest, QueryDict
from django.db import models
from datetime import date as _date
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from datetime import datetime, time
from zoneinfo import ZoneInfo

@login_required
def calendar_view(request):
    return render(request, "main/calendar.html")


def _parse_iso_to_aware(s: str):
    """
    FullCalendar sends 'start'/'end' as ISO (date or datetime).
    Convert to timezone-aware datetimes in the current TZ.
    """
    tz = timezone.get_current_timezone()
    try:
        # If it includes 'T', it's a datetime
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = timezone.make_aware(dt, tz)
            else:
                dt = dt.astimezone(tz)
        else:
            # Date-only -> make it midnight local
            d = datetime.fromisoformat(s).date()
            dt = timezone.make_aware(datetime.combine(d, time.min), tz)
        return dt
    except Exception:
        return None


def _parse_date(date_str):
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None




@login_required
def tasks_of_day(request):
    date_str = request.GET.get("date")
    d = _parse_date(date_str)
    if not d:
        return HttpResponseBadRequest("Invalid or missing date")

    # hide all tasks for future dates
    if d > _date.today():
        return JsonResponse({"date": d.isoformat(), "daily": [], "long_term": []})

    # ---------- DAILY ----------
    # Base: user's daily tasks created on/before d and active
    base_old = DailyTaskCompletion.objects.filter(
        task__user=request.user,
        date__lt=d,
        task__is_active=True
    ).values_list("task_id", flat=True)

    to_remove = DailyTaskCompletion.objects.filter(
        task__user=request.user,
        date__lt=d,
        completed=True
    ).values_list("task_id", flat=True)

    updated_old = [id for id in base_old if id not in to_remove]

    today = DailyTaskCompletion.objects.filter(
        task__user=request.user,
        date=d
    ).values_list("task_id", flat=True)

    anchored_ids = Task.objects.filter(
        user=request.user,
        task_type="daily",
        is_active=True,
        is_anchored=True,
        created_at__date__lte=d,
    ).values_list("id", flat=True)

    daily_ids = set(updated_old) | set(today) | set(anchored_ids)
    daily_qs = Task.objects.filter(id__in=daily_ids)


    completed_daily_ids = set(
        DailyTaskCompletion.objects
        .filter(task__in=daily_qs, date=d, completed=True)
        .values_list("task_id", flat=True)
    )

    daily = [
        {"id": t.id, "title": t.title, "completed": (t.id in completed_daily_ids)}
        for t in daily_qs
    ]

    # ---------- LONG-TERM ----------
    lt_qs = Task.objects.filter(
        user=request.user, task_type="long_term", created_at__date__lte=d
    #).filter(models.Q(completed_at__isnull=True) | models.Q(completed_at__date__gte=d))
    ).filter(
        (models.Q(is_active=True) & models.Q(completed_at__isnull=True))
        |
        models.Q(completed_at__date=d)
    )
    long_term = []
    for t in lt_qs:
        completed_at = getattr(t, "completed_at", None)
        long_term.append({
            "id": t.id,
            "title": t.title,
            "completed_on_that_day": bool(completed_at and completed_at.date() == d),
            "completed_on_or_before_selected_day": bool(completed_at and completed_at.date() <= d),
            "completed_at": completed_at.isoformat() if completed_at else None,
        })

    return JsonResponse({"date": d.isoformat(), "daily": daily, "long_term": long_term})



@require_http_methods(["POST", "DELETE"])
@login_required
def toggle_daily_completion(request):
    """
    POST -> mark completed for that date
    DELETE -> unmark completion for that date
    Body (JSON or form): task_id, date=YYYY-MM-DD
    """
    if request.method == "DELETE":
        data = QueryDict(request.body)  # parse x-www-form-urlencoded body
        task_id = data.get("task_id") or request.GET.get("task_id")
        date_str = data.get("date") or request.GET.get("date")
    else:  # POST
        task_id = request.POST.get("task_id") or request.GET.get("task_id")
        date_str = request.POST.get("date") or request.GET.get("date")
    d = _parse_date(date_str)
    if not task_id or not d:
        return HttpResponseBadRequest("task_id and date are required")

    try:
        task = Task.objects.get(id=task_id, user=request.user, task_type="daily")
    except Task.DoesNotExist:
        return HttpResponseBadRequest("Task not found or not daily")

    if request.method == "POST":
        obj, _ = DailyTaskCompletion.objects.get_or_create(task=task, date=d)
        if not obj.completed:
            obj.completed = True
            #obj.task.is_active = False
            obj.save()
        return JsonResponse({"ok": True, "completed": True})
    else:  # DELETE
        DailyTaskCompletion.objects.filter(task=task, date=d).update(completed=False)
        task.save()
        return JsonResponse({"ok": True, "completed": False})


@require_http_methods(["PATCH", "DELETE"])
@login_required
def toggle_long_term_completion(request, task_id: int):
    """
    PATCH -> set completed_at to the provided date (or now if missing)
    DELETE -> clear completed_at
    Query or body: date=YYYY-MM-DD (optional for PATCH)
    """
    try:
        task = Task.objects.get(id=task_id, user=request.user, task_type="long_term")
    except Task.DoesNotExist:
        return HttpResponseBadRequest("Task not found or not long_term")

    if request.method == "PATCH":
        date_str = request.POST.get("date") or request.GET.get("date")
        if date_str:
            d = _parse_date(date_str)
            if not d:
                return HttpResponseBadRequest("Invalid date")
            dt = timezone.make_aware(datetime(d.year, d.month, d.day))
        else:
            dt = timezone.now()

        task.completed_at = dt
        task.save(update_fields=["completed_at"])
        return JsonResponse({"ok": True, "completed_at": task.completed_at.isoformat()})
    else:  # DELETE
        if hasattr(task, "completed_at"):
            task.completed_at = None
            task.save(update_fields=["completed_at"])
        return JsonResponse({"ok": True, "completed_at": None})



@login_required
def calendar_feed(request):
    start = parse_datetime(request.GET.get("start", ""))
    end = parse_datetime(request.GET.get("end", ""))
    IL_TZ = ZoneInfo("Asia/Jerusalem")

    if start and timezone.is_naive(start): start = timezone.make_aware(start, IL_TZ)
    if end and timezone.is_naive(end): end = timezone.make_aware(end, IL_TZ)

    qs = Event.objects.filter(user=request.user)
    if start and end:
        qs = qs.filter(end_datetime__gte=start, start_datetime__lte=end)
    events = [{
        "id": ev.id,
        "title": ev.title,
        "start": ev.start_datetime.astimezone(IL_TZ).isoformat(),
        "end": ev.end_datetime.astimezone(IL_TZ).isoformat(),
        "allDay": ev.all_day,
    } for ev in qs]

    return JsonResponse(events, safe=False)
