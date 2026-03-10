# Email Scan / Suggestion Feature Notes

## Scope
- This folder owns email auth/sync/review API behavior via `email_auth_views.py`.
- Endpoints are exposed under `/api/email/` and callback aliases are wired in `main/urls.py`.

## Current status (implemented)
- Data models in place:
  - `EmailIntegration`, `EmailOAuthState`, `EmailSyncRun`, `EmailSuggestion`
  - `EmailSyncedMessage` for encrypted raw synced email payloads with retention metadata
- OAuth connect/callback implemented for:
  - Outlook (Microsoft Graph)
  - Gmail (Google OAuth + userinfo)
- Encryption/retention settings in `TaskIt/settings.py`:
  - `EMAIL_TOKEN_ENCRYPTION_KEY`
  - `EMAIL_SYNC_RETENTION_DAYS`
  - `EMAIL_SYNC_MAX_MESSAGES_PER_RUN` (currently 20)
  - `EMAIL_SUGGESTION_CONFIDENCE_THRESHOLD`
- Service layer used by this view:
  - `main/services/email_sync_service.py`
  - `main/services/email_suggestion_service.py`
  - `main/services/email_privacy_service.py`

## API surface in this feature
- Auth/connect:
  - `GET /api/email/status`
  - `GET /api/email/connect/gmail`
  - `GET /api/email/connect/outlook`
  - `GET /api/email/callback/gmail`
  - `GET /api/email/callback/outlook`
- Sync/queue:
  - `POST /api/email/sync-now`
  - `GET /api/email/suggestions`
- Review actions:
  - `POST /api/email/suggestions/{id}/approve`
  - `POST /api/email/suggestions/{id}/edit-approve`
  - `POST /api/email/suggestions/{id}/reject`
- Data controls:
  - `POST /api/email/disconnect`
  - `DELETE /api/email/data`

## UI dependencies
- Settings quick controls:
  - `main/templates/main/settings.html`
  - `main/static/main/settings.css`
- Dedicated suggestions page:
  - `main/templates/main/email_suggestions.html`
  - `main/static/main/email_suggestions.css`

## Defaults and behavior
- Sync is synchronous for v1.
- Suggestions list default tab is `pending` (duplicates hidden by default).
- Queue limit is 20 items.
- Low-confidence suggestions are hidden by default threshold unless explicitly included.
- Approve/reject actions are idempotent and return already-created info.

## Testing
- `main/tests.py` includes API tests for:
  - sync and list filters
  - approve/edit-approve/reject
  - idempotency
  - ownership checks
  - all-day event normalization
