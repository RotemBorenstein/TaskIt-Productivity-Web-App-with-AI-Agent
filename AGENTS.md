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

## Feature-specific AGENTS files
- Keep this root `AGENTS.md` general.
- Put feature-specific implementation/state notes in local `AGENTS.md` files near that feature code.
- Email scan/suggestion notes are maintained in:
  - `main/views/email_scan_views/AGENTS.md`


## User preferences 
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
