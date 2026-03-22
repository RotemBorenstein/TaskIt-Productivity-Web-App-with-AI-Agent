import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError

from . import tasks as reminder_tasks
from .models import (
    DailyTaskCompletion,
    EmailIntegration,
    EmailSuggestion,
    EmailSyncRun,
    Event,
    Note,
    Reminder,
    Subject,
    Task,
    UserNotificationSettings,
)
from .services.email_suggestion_service import EmailSuggestionService
from .services.email_sync_service import NormalizedEmailMessage
from .services.reminder_service import sync_event_reminder, sync_task_reminder
from .services.telegram_notification_service import TelegramResult


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

    def test_reject_pending_suggestion_stores_reason(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Reject me with reason",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(
            f"/api/email/suggestions/{suggestion.id}/reject",
            data=json.dumps({"reason": EmailSuggestion.REJECTION_REASON_QUOTED_THREAD}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, EmailSuggestion.STATUS_REJECTED)
        self.assertEqual(
            suggestion.rejection_reason,
            EmailSuggestion.REJECTION_REASON_QUOTED_THREAD,
        )

    def test_reject_invalid_reason_returns_400(self):
        suggestion = EmailSuggestion.objects.create(
            user=self.user,
            sync_run=self.sync_run,
            suggestion_type=EmailSuggestion.TYPE_TASK,
            title="Reject invalid reason",
            description="",
            task_type_hint="long_term",
            confidence=Decimal("0.9"),
            explanation="",
            status=EmailSuggestion.STATUS_PENDING,
        )
        response = self.client.post(
            f"/api/email/suggestions/{suggestion.id}/reject",
            data=json.dumps({"reason": "bad_reason"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid rejection reason", response.json()["detail"])

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


class EmailSuggestionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="quality-user",
            email="quality@example.com",
            password="testpass123",
        )
        self.integration = EmailIntegration.objects.create(
            user=self.user,
            provider=EmailIntegration.PROVIDER_GMAIL,
            provider_account_id="quality-acc",
            email_address="quality@gmail.com",
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
        )
        self.service = EmailSuggestionService()

    def _message(
        self,
        *,
        body: str,
        subject: str = "Project update",
        sender: str = "alice@example.com",
        metadata: dict | None = None,
    ):
        return NormalizedEmailMessage(
            message_id=f"msg-{abs(hash((subject, body, sender))) % 100000}",
            sender=sender,
            subject=subject,
            body=body,
            received_at=timezone.now(),
            provider=EmailIntegration.PROVIDER_GMAIL,
            metadata=metadata or {},
        )

    def test_preprocess_html_and_trims_quoted_thread(self):
        msg = self._message(
            subject="Action needed",
            body=(
                "<div>Please submit the final report by Friday.</div>"
                "<div>Thanks,<br>Alice</div>"
                "<hr>On Tue, Bob wrote:<div>Old quoted thread</div>"
            ),
        )

        processed = self.service._preprocess_message(msg)

        self.assertIn("Please submit the final report by Friday.", processed.analysis_body)
        self.assertNotIn("Old quoted thread", processed.analysis_body)
        self.assertTrue(processed.is_html)

    def test_protocol_metadata_suppresses_machine_mail(self):
        msg = self._message(
            subject="Automated response",
            body="Your ticket was updated automatically.",
            metadata={"auto_submitted": True},
        )

        processed = self.service._preprocess_message(msg)

        self.assertEqual(
            self.service._protocol_suppression_reason(processed),
            "auto_submitted",
        )

    def test_list_mail_metadata_is_not_auto_suppressed(self):
        msg = self._message(
            subject="Course update",
            body="HW 3 was posted with a deadline.",
            metadata={
                "precedence": "list",
                "has_list_unsubscribe": True,
                "has_list_id": True,
            },
        )

        processed = self.service._preprocess_message(msg)

        self.assertEqual(self.service._protocol_suppression_reason(processed), "")

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_non_actionable_email_stops_after_one_llm_call(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": False,
                "decision": "none",
                "explanation": "This is informational only.",
                "task_evidence": [],
                "event_evidence": [],
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(body="For your information, the deployment was completed."),
            ],
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(mock_invoke_json.call_count, 1)

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_generate_suggestions_creates_task_in_two_calls(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": True,
                "decision": "task",
                "explanation": "The email contains a direct request.",
                "task_evidence": ["Please send the final project report by Friday."],
                "event_evidence": [],
            },
            {
                "task": {
                    "title": "Send final project report",
                    "relates_to_today": False,
                    "confidence": 0.92,
                    "explanation": "The sender explicitly asked for the report by Friday.",
                    "evidence": ["Please send the final project report by Friday."],
                },
                "event": {},
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(body="Please send the final project report by Friday."),
            ],
        )

        self.assertEqual(len(suggestions), 1)
        suggestion = suggestions[0]
        self.assertEqual(suggestion.title, "Send final project report")
        self.assertEqual(suggestion.model_confidence, Decimal("0.920"))
        self.assertEqual(suggestion.confidence, Decimal("1.000"))
        self.assertTrue(suggestion.digest_eligible)
        self.assertIn("evidence", suggestion.ai_payload)
        self.assertEqual(mock_invoke_json.call_count, 2)

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_generate_suggestions_requires_evidence(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": True,
                "decision": "task",
                "explanation": "Maybe actionable.",
                "task_evidence": [],
                "event_evidence": [],
            },
            {
                "task": {
                    "title": "Review project plan",
                    "relates_to_today": False,
                    "confidence": 0.88,
                    "explanation": "Possible follow-up.",
                    "evidence": [],
                },
                "event": {},
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(body="We should think about the project plan sometime."),
            ],
        )

        self.assertEqual(suggestions, [])
        self.sync_run.refresh_from_db()
        self.assertEqual(self.sync_run.suggestions_count, 0)

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_evidence_not_present_in_cleaned_email_is_rejected(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": True,
                "decision": "task",
                "explanation": "Contains a request.",
                "task_evidence": ["Please send the report today."],
                "event_evidence": [],
            },
            {
                "task": {
                    "title": "Send report",
                    "relates_to_today": True,
                    "confidence": 0.94,
                    "explanation": "Clear request.",
                    "evidence": ["Please send the report tomorrow."],
                },
                "event": {},
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(body="Please send the report today."),
            ],
        )

        self.assertEqual(suggestions, [])

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_event_with_invalid_date_is_rejected(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": True,
                "decision": "event",
                "explanation": "Contains a calendar invitation.",
                "task_evidence": [],
                "event_evidence": ["Team sync on Friday at 10:00."],
            },
            {
                "task": {},
                "event": {
                    "title": "Team sync",
                    "date": "Friday",
                    "time": "10:00",
                    "location": "Room A",
                    "confidence": 0.90,
                    "explanation": "Meeting details are present.",
                    "evidence": ["Team sync on Friday at 10:00."],
                },
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(body="Team sync on Friday at 10:00."),
            ],
        )

        self.assertEqual(suggestions, [])

    @patch.object(EmailSuggestionService, "_invoke_json")
    def test_both_decision_uses_two_calls_total(self, mock_invoke_json):
        mock_invoke_json.side_effect = [
            {
                "actionable": True,
                "decision": "both",
                "explanation": "Contains both a request and an event.",
                "task_evidence": ["Please prepare the deck before Monday."],
                "event_evidence": ["Kickoff meeting on 2026-03-30 at 09:00."],
            },
            {
                "task": {
                    "title": "Prepare kickoff deck",
                    "relates_to_today": False,
                    "confidence": 0.89,
                    "explanation": "Preparation requested before the meeting.",
                    "evidence": ["Please prepare the deck before Monday."],
                },
                "event": {
                    "title": "Kickoff meeting",
                    "date": "2026-03-30",
                    "time": "09:00",
                    "location": "",
                    "confidence": 0.91,
                    "explanation": "Meeting details are explicit.",
                    "evidence": ["Kickoff meeting on 2026-03-30 at 09:00."],
                },
            },
        ]

        suggestions = self.service.generate_suggestions(
            sync_run=self.sync_run,
            messages=[
                self._message(
                    body=(
                        "Please prepare the deck before Monday.\n"
                        "Kickoff meeting on 2026-03-30 at 09:00."
                    ),
                ),
            ],
        )

        self.assertEqual(len(suggestions), 2)
        self.assertEqual(mock_invoke_json.call_count, 2)


class ReminderIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="notify-user",
            email="notify@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_notification_settings_status_creates_row_lazily(self):
        self.assertFalse(
            UserNotificationSettings.objects.filter(user=self.user).exists()
        )
        response = self.client.get("/api/notifications/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            UserNotificationSettings.objects.filter(user=self.user).exists()
        )

    def test_update_notification_settings_updates_flags_and_email(self):
        response = self.client.post(
            "/api/notifications/settings/update/",
            data=json.dumps(
                {
                    "email_address": "new@example.com",
                    "email_enabled": True,
                    "telegram_enabled": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        settings_obj = UserNotificationSettings.objects.get(user=self.user)
        self.assertEqual(self.user.email, "new@example.com")
        self.assertTrue(settings_obj.email_enabled)
        self.assertFalse(settings_obj.telegram_enabled)

    @patch("main.views.settings_views.TelegramNotificationService.get_updates")
    def test_telegram_poll_connect_enables_notifications(self, mock_get_updates):
        settings_obj = UserNotificationSettings.objects.create(
            user=self.user,
            telegram_connect_token="connect-me",
            telegram_enabled=False,
        )
        mock_get_updates.return_value = TelegramResult(
            success=True,
            payload={
                "result": [
                    {
                        "message": {
                            "text": "/start connect-me",
                            "chat": {"id": 998877},
                        }
                    }
                ]
            },
        )

        response = self.client.post("/api/notifications/telegram/poll/")

        self.assertEqual(response.status_code, 200)
        settings_obj.refresh_from_db()
        self.assertTrue(settings_obj.telegram_enabled)
        self.assertEqual(settings_obj.telegram_chat_id, "998877")

    def test_long_term_task_reminder_requires_due_date(self):
        task = Task.objects.create(
            user=self.user,
            title="Project",
            task_type="long_term",
        )
        with self.assertRaisesMessage(ValidationError, "require a due date"):
            sync_task_reminder(
                task,
                reminder_enabled=True,
                reminder_time=datetime.strptime("09:00", "%H:%M").time(),
                channel_email=True,
                channel_telegram=False,
            )

    def test_daily_task_reminder_computes_next_run(self):
        task = Task.objects.create(
            user=self.user,
            title="Stretch",
            task_type="daily",
            is_anchored=True,
        )
        reminder = sync_task_reminder(
            task,
            reminder_enabled=True,
            reminder_time=datetime.strptime("08:00", "%H:%M").time(),
            channel_email=True,
            channel_telegram=False,
        )
        self.assertEqual(reminder.kind, Reminder.KIND_DAILY_TASK)
        self.assertIsNotNone(reminder.next_run_at)

    def test_create_task_rejects_reminder_when_telegram_not_connected(self):
        response = self.client.post(
            "/tasks/create/",
            data={
                "task_type": "daily",
                "daily-title": "Meditate",
                "daily-description": "",
                "daily-reminder_enabled": "on",
                "daily-reminder_time": "09:00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.filter(user=self.user, title="Meditate").exists())

    def test_edit_task_preserves_existing_reminder_when_telegram_disconnects(self):
        settings_obj = UserNotificationSettings.objects.create(
            user=self.user,
            telegram_enabled=True,
            telegram_chat_id="12345",
        )
        task = Task.objects.create(
            user=self.user,
            title="Stretch",
            task_type="daily",
            is_anchored=True,
        )
        sync_task_reminder(
            task,
            reminder_enabled=True,
            reminder_time=datetime.strptime("08:00", "%H:%M").time(),
            channel_email=False,
            channel_telegram=True,
        )
        settings_obj.telegram_chat_id = ""
        settings_obj.telegram_enabled = False
        settings_obj.save(update_fields=["telegram_chat_id", "telegram_enabled", "updated_at"])

        response = self.client.post(
            f"/tasks/{task.id}/edit/",
            data={
                "title": "Stretch updated",
                "description": "",
                "reminder_enabled": "on",
                "reminder_time": "08:00",
            },
        )

        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.title, "Stretch updated")
        self.assertTrue(task.reminder.channel_telegram)
        self.assertEqual(task.reminder.remind_at_time.strftime("%H:%M"), "08:00")

    def test_event_api_create_persists_reminder_offset(self):
        UserNotificationSettings.objects.create(
            user=self.user,
            telegram_enabled=True,
            telegram_chat_id="12345",
        )
        payload = {
            "title": "Workshop",
            "start": "2026-04-01T10:00",
            "end": "2026-04-01T11:00",
            "allDay": False,
            "description": "Practice session",
            "reminderOffsetMinutes": 15,
        }
        response = self.client.post(
            "/api/events/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        event = Event.objects.get(title="Workshop", user=self.user)
        self.assertEqual(event.reminder.offset_minutes, 15)
        self.assertTrue(event.reminder.channel_telegram)
        self.assertFalse(event.reminder.channel_email)

    def test_event_api_create_rejects_reminder_when_telegram_not_connected(self):
        payload = {
            "title": "Workshop",
            "start": "2026-04-01T10:00",
            "end": "2026-04-01T11:00",
            "allDay": False,
            "reminderOffsetMinutes": 15,
        }
        response = self.client.post(
            "/api/events/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Connect Telegram in Settings before saving reminders.",
            response.content.decode("utf-8"),
        )

    def test_event_reminder_schedule_updates_when_event_moves(self):
        start = timezone.now() + timedelta(days=1)
        event = Event.objects.create(
            user=self.user,
            title="Review",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
        )
        reminder = sync_event_reminder(
            event,
            offset_minutes=60,
            channel_email=True,
            channel_telegram=False,
        )
        original_next_run = reminder.next_run_at
        event.start_datetime = start + timedelta(hours=2)
        event.end_datetime = event.start_datetime + timedelta(hours=1)
        event.save()
        reminder.refresh_from_db()
        self.assertNotEqual(reminder.next_run_at, original_next_run)

    def test_event_patch_invalid_reminder_rolls_back_event_changes(self):
        start = timezone.now() + timedelta(days=1)
        event = Event.objects.create(
            user=self.user,
            title="Original title",
            description="original description",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            all_day=False,
        )

        response = self.client.patch(
            f"/api/events/{event.id}/",
            data=json.dumps(
                {
                    "title": "Updated title",
                    "reminderOffsetMinutes": 15,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        event.refresh_from_db()
        self.assertEqual(event.title, "Original title")

    def test_soft_deleted_anchored_daily_task_is_inactive_for_reminders(self):
        task = Task.objects.create(
            user=self.user,
            title="Anchored daily",
            task_type="daily",
            is_active=False,
            is_anchored=True,
        )
        reminder = Reminder(
            user=self.user,
            task=task,
            kind=Reminder.KIND_DAILY_TASK,
            remind_at_time=datetime.strptime("08:00", "%H:%M").time(),
            channel_email=True,
        )
        self.assertFalse(reminder.is_target_active())

    @patch("main.tasks.dispatch_reminder")
    def test_failed_delivery_keeps_reminder_due_for_retry(self, mock_dispatch):
        start = timezone.now() + timedelta(hours=2)
        event = Event.objects.create(
            user=self.user,
            title="Retry event",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            all_day=False,
        )
        reminder = sync_event_reminder(
            event,
            offset_minutes=60,
            channel_email=True,
            channel_telegram=False,
        )
        reminder.next_run_at = timezone.now() - timedelta(minutes=1)
        reminder.save(update_fields=["next_run_at"])

        mock_dispatch.return_value = ["Temporary email outage"]
        result = reminder_tasks.send_due_reminder(reminder.id)

        reminder.refresh_from_db()
        self.assertEqual(result["status"], "retry_scheduled")
        self.assertIsNone(reminder.last_sent_at)
        self.assertIsNotNone(reminder.next_run_at)
        self.assertGreater(reminder.next_run_at, timezone.now())

    def test_stale_queued_reminder_is_skipped(self):
        result = reminder_tasks.send_due_reminder(999999)
        self.assertEqual(result["status"], "missing")


class NotesApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="notes-user",
            email="notes@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)
        self.subject = Subject.objects.create(user=self.user, title="School", color="blue")
        self.note = Note.objects.create(
            subject=self.subject,
            title="Draft",
            content="short content",
        )

    def test_note_chunking_handles_long_notes(self):
        from .agent.rag_utils import _note_to_documents

        long_note = Note.objects.create(
            subject=self.subject,
            title="Long note",
            content="A" * 900,
        )

        docs = _note_to_documents(long_note)

        self.assertGreater(len(docs), 1)
        self.assertTrue(all(isinstance(doc.page_content, str) for doc in docs))
        self.assertTrue(all(doc.metadata["note_id"] == long_note.id for doc in docs))

    @patch("main.views.notes_views.index_note")
    def test_update_note_succeeds_when_indexing_fails(self, mock_index_note):
        mock_index_note.side_effect = RuntimeError("embedding service unavailable")

        response = self.client.patch(
            f"/api/notes/{self.note.id}",
            data=json.dumps({"title": "Updated title", "content": "Updated content"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.note.refresh_from_db()
        self.assertEqual(self.note.title, "Updated title")
        self.assertEqual(self.note.content, "Updated content")
