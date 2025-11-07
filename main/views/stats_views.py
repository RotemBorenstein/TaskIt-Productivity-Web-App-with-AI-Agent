from collections import OrderedDict, defaultdict
from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..models import DailyTaskCompletion, Task
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

@login_required
def stats_page(request):
    return render(request, "main/stats.html")
def _sunday_of(d):
    # Python: Monday=0 ... Sunday=6  → days since Sunday:
    return d - timedelta(days=(d.weekday() + 1) % 7)
@login_required
def api_completion_rate(request):
    """
    GET /api/stats/completion-rate/?granularity=week|month
    Fixed windows:
      - week  -> last 12 weeks (Sunday-start)
      - month -> last 12 months (first-of-month)
    Completion = completed by now (uses boolean `completed`)
    """
    granularity = request.GET.get("granularity", "day").lower()
    today = timezone.localdate()

    if granularity == "month":
        current_start = today.replace(day=1)
        starts = [current_start - relativedelta(months=i) for i in range(11, -1, -1)]
        ends   = [s + relativedelta(months=1) for s in starts]  # exclusive
        label_fmt = "%Y-%m"
    elif granularity == "week":
        current_start = _sunday_of(today)
        starts = [current_start - timedelta(weeks=i) for i in range(11, -1, -1)]
        ends = [s + timedelta(days=7) for s in starts]  # exclusive

    else: #daily
        current_start = today
        starts = [today - timedelta(days=i) for i in range(11,-1,-1) ]
        ends = [s + timedelta(days=1) for s in starts]
        label_fmt = "%a %Y-%m-%d"

    range_start = starts[0]
    range_end = ends[-1]

    rows = (
        DailyTaskCompletion.objects
        .filter(task__user=request.user, date__gte=range_start, date__lt=range_end)
        .values("date", "completed")
    )

    buckets = OrderedDict((s, {"date": s, "created": 0, "completed": 0}) for s in starts)

    def bucket_key(d):
        if granularity == "month":
            return d.replace(day=1)
        elif granularity == "week":
            return _sunday_of(d)
        else:  # "daily"
            return d

    for r in rows:
        key = bucket_key(r["date"])
        if key in buckets:  # safety
            buckets[key]["created"] += 1
            if r["completed"]:
                buckets[key]["completed"] += 1

    data = []
    for s in starts:
        created = buckets[s]["created"]
        completed = buckets[s]["completed"]
        rate = 0.0 if created == 0 else (completed / created) * 100.0
        if granularity == "week":
            label = f"{s.strftime('%b %d')} – {(s + timedelta(days=6)).strftime('%b %d')}"
        else:
            # Use the existing label_fmt
            label = s.strftime(label_fmt)
        data.append({
            "date": s.isoformat(), # bucket start (Today or Sunday or 1st of month)
            "label": label,
            "created": created,
            "completed": completed,
            "completion_rate": round(rate, 2),
        })
    return JsonResponse(data, safe=False)


@login_required
def api_completed_daily_tasks_count(request):
    completed_tasks = DailyTaskCompletion.objects.filter(task__user=request.user, completed=True)
    cntr = defaultdict(int)
    for c_task in completed_tasks:
        cntr[c_task.task.title.lower()] += 1
    return JsonResponse( sorted(cntr.items(), key=lambda item: item[1], reverse=True)[:10], safe=False)


@login_required
def api_per_task_completion_rate(request):
    dtc_records = DailyTaskCompletion.objects.filter(task__user=request.user)
    created, completed = defaultdict(int), defaultdict(int)
    granularity = request.GET.get('granularity', 'count')
    for dtc in dtc_records:
        created[dtc.task.title.lower()] += 1
        if dtc.completed:
            completed[dtc.task.title.lower()] += 1
    if granularity == 'percentage':
        return JsonResponse(sorted([{'task': t_title, 'rate': round(completed[t_title] / created[t_title], 2) * 100} for t_title in created], key=lambda x: x['rate'])[:10], safe=False)
    else:
        return JsonResponse(sorted([{'task': t_title, 'rate': created[t_title] - completed[t_title]} for t_title in created], key=lambda x: x['rate'], reverse=True)[:10], safe=False)





