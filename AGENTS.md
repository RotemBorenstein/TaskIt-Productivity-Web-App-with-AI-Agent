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
- Explain non-trivial concepts in simple beginner-friendly language.
- Keep architecture simple but industry-standard.
- Prefer practical implementation, not long theory.
- Add concise documentation to generated code.

## Required Workflow (for non-trivial tasks)

1. **Clarify**
   - Define requirements, constraints, assumptions

2. **Propose**
   - Give 2–3 approaches with pros/cons
   - Avoid unnecessary complexity

3. **Decide**
   - Recommend one approach with justification
   - Do not assume if unclear

4. **Critique**
   - List weaknesses, edge cases, failure scenarios

5. **Implement**
   - Only after steps above

---

## Implementation Rules
- Prefer simple over complex
- No over-engineering (no microservices, Kafka, etc. unless justified)
- Explain key decisions briefly

---

## Critical Thinking
- Challenge your own solution
- Provide edge cases and failure points
- Do not present answers as always correct

---

## Observability (Mandatory)
For any feature, include:

- **Logs**: key events + context for debugging  
- **Metrics**: success rate, latency, or domain-specific signals  
- **Failure detection**: how to detect silent logical errors  

Must answer:
- How will we know if this fails in production?

Keep it lightweight (no heavy infra unless needed).

---

## Docker-First Testing
- For Django tests in this project, prefer Docker over the local Python environment.
- Reason: local runs may fail on PostgreSQL `pgvector`, while `docker-compose` uses the `pgvector/pgvector` database image.
- Default workflow for repo changes that need Django test verification:
  - `docker compose up -d db redis web`
  - `docker compose build web` after local code changes so the container uses the latest code
  - `docker compose up -d web` after rebuild
  - `docker compose exec web python manage.py test --keepdb ...`
- If the container appears to be running stale code or new tests are "missing", rebuild `web` and rerun.
- Local non-Docker checks like `python -m py_compile` or `python manage.py check` are still fine for quick validation when they do not depend on the database/test DB.

---

## Behavior
- Be concise and structured
- Do not blindly agree
- State uncertainty when relevant

---

## Forbidden
- Jumping directly to code
- Assuming requirements without stating them
- Over-complicating solutions

## Documentation expectations for code agents
- Add concise file-level context when introducing a new file or module.
- Add concise docstrings for non-trivial functions and endpoints.
- Add brief inline comments for logically complicated sections (branching, validation, normalization).
- Keep documentation focused and practical; avoid long theoretical comments.
