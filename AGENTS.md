# TaskIt - Agent Handoff Notes

## Project at a glance
- `TaskIt` is a full-stack productivity web app.
- Stack: Django + PostgreSQL + JavaScript (FullCalendar).
- Core domains: tasks, events/calendar, notes, stats, AI chat assistant.
- Current focus feature: email integration (Gmail/Outlook) to suggest tasks/events from emails.

## Code layout (high level)
- `main/models.py`: domain models (Task, Event, Note, email integration models).
- `main/views/`: split by feature (`task_views.py`, `event_views.py`, `calendar_views.py`, `notes_views.py`, `agent_views.py`, `email_auth_views.py`).
- `main/urls.py`: all app routes, including Ninja APIs.
- `main/templates/main/`: page templates (`settings.html`, etc.).
- `TaskIt/settings.py`: Django settings and auth redirect config.

## Email sync/suggestions status (current)
- Data models in place:
  - `EmailIntegration`, `EmailOAuthState`, `EmailSyncRun`, `EmailSuggestion`
  - `EmailSyncedMessage` for encrypted raw synced email payloads with retention metadata
- OAuth connect/callback implemented in `main/views/email_auth_views.py` for:
  - Outlook (Microsoft Graph)
  - Gmail (Google OAuth + userinfo)
- Encryption/retention settings in `TaskIt/settings.py`:
  - `EMAIL_TOKEN_ENCRYPTION_KEY`
  - `EMAIL_SYNC_RETENTION_DAYS`
  - `EMAIL_SYNC_MAX_MESSAGES_PER_RUN` (currently 20)
  - `EMAIL_SUGGESTION_CONFIDENCE_THRESHOLD`
- Service layer implemented:
  - `main/services/email_sync_service.py` (provider-agnostic fetch + manual sync run + raw message persistence)
  - `main/services/email_suggestion_service.py` (AI extraction, fingerprint duplicate detection, suggestion persistence)
  - `main/services/email_privacy_service.py` (retention cleanup helpers)
- API endpoints under `/api/email/` now include:
  - Auth/connect: `/status`, `/connect/gmail`, `/connect/outlook`, callbacks
  - Sync/queue: `POST /sync-now`, `GET /suggestions`
  - Review actions: `POST /suggestions/{id}/approve`, `POST /suggestions/{id}/edit-approve`, `POST /suggestions/{id}/reject`
  - Data controls: `POST /disconnect`, `DELETE /data`
- Callback aliases in `main/urls.py`:
  - `/auth/microsoft/callback`
  - `/auth/google/callback`
- UI implemented:
  - Settings quick module (`main/templates/main/settings.html`): interval selector, sync-now button, open-suggestions link, sync status
  - Dedicated review page route: `/email/suggestions/` rendered by `settings_views.email_suggestions_page`
  - Dedicated template/CSS: `main/templates/main/email_suggestions.html`, `main/static/main/email_suggestions.css`
- Current UX defaults:
  - Sync is synchronous for v1
  - Suggestions list default tab is `pending` (duplicates hidden by default)
  - Queue limit is 20 items
  - Low-confidence suggestions hidden by default threshold unless explicitly included
- Test status:
  - `main/tests.py` includes API tests for sync, list filters, approve/edit-approve/reject, idempotency, ownership, and all-day event normalization.


## User preferences (from this chat)
- Work step-by-step.
- Explain non-trivial auth concepts in simple beginner-friendly language.
- Keep architecture simple but industry-standard.
- Prefer practical implementation, not long theory.
- Add concise documentation to generated code.

## Documentation expectations for code agents
- Add concise file-level context when introducing a new file or module.
- Add concise docstrings for non-trivial functions and endpoints.
- Add brief inline comments for logically complicated sections (branching, validation, normalization).
- Keep documentation focused and practical; avoid long theoretical comments.
