import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError

from .models import DailyTaskCompletion, EmailIntegration, EmailSuggestion, EmailSyncRun, Event, Task


class EmailApiTests(TestCase):
    """Integration-style tests for email sync/suggestion APIs."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

        self.integration = EmailIntegration.objects.create(
            user=self.user,
            provider=EmailIntegration.PROVIDER_GMAIL,
            provider_account_id="acc-1",
            email_address="alice@gmail.com",
            encrypted_refresh_token="encrypted",
            is_active=True,
        )
        now = timezone.now()
        self.sync_run = EmailSyncRun.objects.create(
            user=self.user,
            integration=self.integration,
            date_preset=EmailSyncRun.PRESET_DAY,
            from_datetime=now - timedelta(hours=24),
            to_datetime=now,
            status=EmailSyncRun.STATUS_COMPLETED,
            emails_scanned_count=2,
            suggestions_count=0,
            started_at=now - timedelta(minutes=1),
            finished_at=now,
        )

    @patch("main.views.email_scan_views.email_auth_views.EmailSuggestionService.generate_suggestions")
    @patch("main.views.email_scan_views.email_auth_views.EmailSyncService.run_manual_sync")
    def test_sync_now_success(self, mock_run_manual_sync, mock_generate_suggestions):
        mock_run_manual_sync.return_value = (self.sync_run, [])
        payload = {"interval": "day"}
        response = self.client.post(
            "/api/email/sync-now",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["interval"], "day")
        self.assertEqual(body["emails_scanned_count"], 2)
        self.assertEqual(body["suggestions_count"], 0)
        mock_generate_suggestions.assert_called_once()

    @patch("main.views.email_scan_views.email_auth_views.EmailSyncService.run_manual_sync")
    def test_sync_now_bad_interval_error(self, mock_run_manual_sync):
        mock_run_manual_sync.side_effect = HttpError(400, "interval must be one of: day, week")
        response = self.client.post(
            "/api/email/sync-now",
            data=json.dumps({"interval": "day"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("interval", response.json()["detail"])

    def test_suggestions_default_pending_and_confidence_filter(self):
        EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="High confidence task",
            task_type_hint="long_term",
            description="desc",
            confidence=Decimal("0.900"),
            explanation="Strong signal",
            status=EmailSuggestion.STATUS_PENDING,
        )
        EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Low confidence task",
            task_type_hint="long_term",
            description="desc",
            confidence=Decimal("0.100"),
            explanation="Weak signal",
            status=EmailSuggestion.STATUS_PENDING,
        )
        EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_EVENT,
            title="Approved event",
            description="desc",
            confidence=Decimal("0.900"),
            explanation="signal",
            status=EmailSuggestion.STATUS_APPROVED,
        )

        response = self.client.get("/api/email/suggestions")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["title"], "High confidence task")
        self.assertEqual(body["items"][0]["description"], "desc")

    def test_suggestions_include_low_confidence_when_min_is_zero(self):
        EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Low confidence task",
            task_type_hint="long_term",
            confidence=Decimal("0.100"),
            explanation="Weak signal",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.get("/api/email/suggestions?status=pending&min_confidence=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

    def test_suggestions_limit_validation(self):
        response = self.client.get("/api/email/suggestions?limit=21")
        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.json()["detail"])

    def test_approve_task_suggestion_creates_task(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Read paper",
            description="Read chapter 2",
            task_type_hint="daily",
            confidence=Decimal("0.9"),
            explanation="Action item",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/approve")
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, EmailSuggestion.STATUS_APPROVED)
        self.assertIsNotNone(suggestion.created_task_id)
        created_task = Task.objects.get(id=suggestion.created_task_id)
        self.assertEqual(created_task.task_type, "daily")
        self.assertTrue(
            DailyTaskCompletion.objects.filter(
                task=created_task, date=timezone.localdate()
            ).exists()
        )

    def test_approve_event_all_day_normalization(self):
        tz = timezone.get_current_timezone()
        same_day_noon = timezone.make_aware(datetime(2026, 3, 10, 12, 0), tz)
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_EVENT,
            title="All day conference",
            description="desc",
            start_datetime=same_day_noon,
            end_datetime=same_day_noon,
            all_day=True,
            confidence=Decimal("0.9"),
            explanation="Calendar item",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/approve")
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        event = Event.objects.get(id=suggestion.created_event_id)
        local_start = timezone.localtime(event.start_datetime)
        local_end = timezone.localtime(event.end_datetime)
        self.assertEqual(local_start.time(), datetime.min.time())
        self.assertEqual(local_end.date(), local_start.date() + timedelta(days=1))
        self.assertTrue(event.all_day)

    def test_approve_idempotent_returns_already_created(self):
        task = Task.objects.create(
            user=self.user,
            title="Existing",
            description="",
            task_type="long_term",
        )
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Existing",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.8"),
            explanation="",
            status=EmailSuggestion.STATUS_APPROVED,
            created_task=task,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/approve")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["already_created"])
        self.assertEqual(response.json()["created_task_id"], task.id)

    def test_edit_approve_task_with_description_and_type(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Old title",
            description="old",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_PENDING,
        )
        payload = {
            "title": "New task title",
            "description": "Updated description",
            "task_type_hint": "daily",
        }
        response = self.client.post(
            f"/api/email/suggestions/{suggestion.id}/edit-approve",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, EmailSuggestion.STATUS_APPROVED)
        self.assertEqual(suggestion.title, "New task title")
        self.assertEqual(suggestion.description, "Updated description")
        created_task = Task.objects.get(id=suggestion.created_task_id)
        self.assertEqual(created_task.task_type, "daily")

    def test_reject_pending_suggestion(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Reject me",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/reject")
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, EmailSuggestion.STATUS_REJECTED)

    def test_reject_idempotent(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Already rejected",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_REJECTED,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/reject")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["already_created"])

    def test_action_for_other_user_suggestion_returns_404(self):
        other_integration = EmailIntegration.objects.create(
            user=self.other_user,
            provider=EmailIntegration.PROVIDER_GMAIL,
            provider_account_id="acc-2",
            email_address="bob@gmail.com",
            encrypted_refresh_token="encrypted",
            is_active=True,
        )
        other_sync_run = EmailSyncRun.objects.create(
            user=self.other_user,
            integration=other_integration,
            date_preset=EmailSyncRun.PRESET_DAY,
            from_datetime=timezone.now() - timedelta(hours=24),
            to_datetime=timezone.now(),
            status=EmailSyncRun.STATUS_COMPLETED,
        )
        suggestion = EmailSuggestion.objects.create(
            user=self.other_user,
            sync_run=other_sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Bob suggestion",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(f"/api/email/suggestions/{suggestion.id}/approve")
        self.assertEqual(response.status_code, 404)
