from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from ..models import Task, DailyTaskCompletion
from ..forms import TaskForm
from django.views.decorators.http import require_GET
from django.http import JsonResponse
from ..services.reminder_service import (
    get_user_notification_settings,
    refresh_item_reminder,
    sync_task_reminder,
)


@login_required
def tasks(request):
    return render(request, "main/tasks.html", {})


@login_required
def tasks_view(request):
    """
    Render the main Tasks page:
      - daily_tasks: all “daily” tasks for this user that are active and not completed
      - long_tasks: all “long_term” tasks for this user that are active and not completed
      - form: empty TaskForm to add a new task
    """
    update_is_active_for_daily_tasks(request.user)
    daily_tasks = Task.objects.filter(
        user=request.user,
        task_type="daily",
        is_active=True,
        is_completed=False,
    ).order_by("created_at").select_related("reminder")

    long_tasks = Task.objects.filter(
        user=request.user,
        task_type="long_term",
        is_active=True,
        is_completed=False,
    ).order_by("created_at").select_related("reminder")

    notification_settings = get_user_notification_settings(request.user)

    daily_form_data = request.session.pop('daily_form_data', None)
    if daily_form_data:
        daily_form = TaskForm(
            daily_form_data,
            prefix='daily',
            task_type='daily',
            notification_settings=notification_settings,
        )
    else:
        daily_form = TaskForm(
            prefix='daily',
            task_type='daily',
            notification_settings=notification_settings,
        )

    long_form_data = request.session.pop('long_form_data', None)
    if long_form_data:
        long_form = TaskForm(
            long_form_data,
            prefix='long',
            task_type='long_term',
            notification_settings=notification_settings,
        )
    else:
        long_form = TaskForm(
            prefix='long',
            task_type='long_term',
            notification_settings=notification_settings,
        )

    return render(request, "main/tasks.html", {
        "daily_tasks": daily_tasks,
        "long_tasks": long_tasks,
        "daily_form": daily_form,
        "long_form": long_form,
        "notification_settings": notification_settings,
    })


@login_required
def create_task(request):
    if request.method != "POST":
        return redirect(reverse("main:tasks"))

    task_type = request.POST.get("task_type")
    if task_type not in ["daily", "long_term"]:
        messages.error(request, "Invalid task type")
        return redirect(reverse("main:tasks"))

    prefix = "daily" if task_type == "daily" else "long"
    notification_settings = get_user_notification_settings(request.user)
    form = TaskForm(
        request.POST,
        prefix=prefix,
        task_type=task_type,
        notification_settings=notification_settings,
    )
    if form.is_valid():
        new_task = form.save(commit=False)
        new_task.user = request.user
        new_task.task_type = task_type
        new_task.save()
        try:
            sync_task_reminder(
                new_task,
                reminder_enabled=form.cleaned_data.get("reminder_enabled", False),
                reminder_time=form.cleaned_data.get("reminder_time"),
                channel_email=False,
                channel_telegram=bool(form.cleaned_data.get("reminder_enabled", False)),
            )
        except ValidationError as exc:
            new_task.delete()
            messages.error(request, exc.messages[0])
            if task_type == "daily":
                request.session['daily_form_data'] = request.POST.dict()
            else:
                request.session['long_form_data'] = request.POST.dict()
            return redirect(reverse("main:tasks"))
        if new_task.task_type == 'daily':
            DailyTaskCompletion.objects.get_or_create(
                task=new_task, date=timezone.localdate()
            )
    else:
        # Save only the POST data to the session, NOT the form itself!
        if task_type == "daily":
            request.session['daily_form_data'] = request.POST.dict()
        else:
            request.session['long_form_data'] = request.POST.dict()

    return redirect(reverse("main:tasks"))



@login_required
def complete_task(request):
    """
    AJAX endpoint to mark a Task as completed.
    Expects POST with 'task_id'. Returns JSON {"success": true} on success.
    """
    if request.method != "POST" or request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JsonResponse({"success": False, "error": "Invalid request."}, status=400)

    task_id = request.POST.get("task_id")
    if not task_id:
        return JsonResponse({"success": False, "error": "task_id missing."}, status=400)

    try:
        task = Task.objects.get(pk=task_id, user=request.user, is_active=True)
    except Task.DoesNotExist:
        return JsonResponse({"success": False, "error": "Task not found."}, status=404)

    if task.task_type == "long_term":
        # Mark long-term task as completed
        task.is_completed = True
        task.completed_at = timezone.now()
        task.save()
    elif task.task_type == "daily":
        # Mark today's DailyTaskCompletion as completed
        today = timezone.localdate()
        # update today's record
        DailyTaskCompletion.objects.update_or_create(
            task=task,
            date=today,
            defaults={"completed": True},
        )

        task.is_active = False
        task.save()
    else:
        return JsonResponse({"success": False, "error": "Unknown task type."}, status=400)

    return JsonResponse({"success": True})


@login_required
def edit_task(request, pk):
    """
    GET: Show a TaskForm pre-filled for task=pk.
    POST: Bind form to existing task, save if valid, then redirect to /tasks/.
    """
    task = get_object_or_404(Task, pk=pk, user=request.user, is_active=True)
    notification_settings = get_user_notification_settings(request.user)

    if request.method == "POST":
        form = TaskForm(
            request.POST,
            instance=task,
            task_type=task.task_type,
            notification_settings=notification_settings,
        )
        if form.is_valid():
            task = form.save()
            try:
                sync_task_reminder(
                    task,
                    reminder_enabled=form.cleaned_data.get("reminder_enabled", False),
                    reminder_time=form.cleaned_data.get("reminder_time"),
                    channel_email=False,
                    channel_telegram=bool(
                        form.cleaned_data.get("reminder_enabled", False)
                    ),
                )
                return redirect(reverse("main:tasks"))
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = TaskForm(
            instance=task,
            task_type=task.task_type,
            notification_settings=notification_settings,
        )

    return render(request, "main/edit_task.html", {
        "form": form,
        "task": task,
        "notification_settings": notification_settings,
    })


@login_required
def delete_task(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user, is_active=True)
    task.is_active = False
    task.save(update_fields=["is_active"])
    return redirect(reverse("main:tasks"))


def update_is_active_for_daily_tasks(user):
    today = timezone.localdate()
    now = timezone.now()
    anchored_tasks = Task.objects.filter(user=user, task_type="daily", is_anchored=True)

    for task in anchored_tasks:
        DailyTaskCompletion.objects.get_or_create(
            task=task,
            date=today,
            defaults={"created_at": now, "completed": False}
        )

    done_today = DailyTaskCompletion.objects.filter(
        task__in=anchored_tasks, date=today, completed=True
    ).values_list("task_id", flat=True)
    # Activate all anchored daily tasks that are not completed today
    anchored_tasks.exclude(id__in=done_today).update(is_active=True)
    # Deactivate all anchored daily tasks that are completed today
    anchored_tasks.filter(id__in=done_today).update(is_active=False)
    for task in anchored_tasks:
        task.refresh_from_db(fields=["is_active"])
        refresh_item_reminder(task)





@login_required
def toggle_anchor(request, task_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=400)
    task = get_object_or_404(Task, id=task_id, user=request.user)
    if task.task_type != "daily":
        return JsonResponse({"success": False, "error": "Not a daily task"}, status=400)
    task.is_anchored = not task.is_anchored
    task.save(update_fields=["is_anchored"])
    if task.is_anchored:
        DailyTaskCompletion.objects.get_or_create(task=task, date=timezone.localdate())
    return JsonResponse({"success": True, "anchored": task.is_anchored})



@login_required
@require_GET
def api_tasks_list(request):
    """
    Return all active tasks for the current user as JSON.
    """
    daily_tasks_qs = Task.objects.filter(
        user=request.user, task_type="daily", is_active=True, is_completed=False
    ).order_by("created_at").select_related("reminder")

    long_tasks_qs = Task.objects.filter(
        user=request.user, task_type="long_term", is_active=True, is_completed=False
    ).order_by("created_at").select_related("reminder")

    def serialize(task):
        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "is_completed": task.is_completed,
            "is_anchored": task.is_anchored,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_time": task.due_time.isoformat() if task.due_time else None,
            "reminder_enabled": hasattr(task, "reminder"),
        }

    return JsonResponse({
        "daily_tasks": [serialize(task) for task in daily_tasks_qs],
        "long_tasks": [serialize(task) for task in long_tasks_qs],
    })
