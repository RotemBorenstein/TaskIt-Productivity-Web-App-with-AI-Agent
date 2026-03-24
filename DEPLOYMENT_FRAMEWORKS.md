# TaskIt Deployment Frameworks and Infrastructure

This file documents the main frameworks/services used by TaskIt that matter for deployment and operations.

## 1) Django (Application Framework)
- Role: Core backend web framework (routing, auth, ORM, templates, settings).
- Runtime: `web` container runs Django through Gunicorn.
- Config source:
  - `TaskIt/settings.py`
  - `entrypoint.sh` (runs `migrate`, `collectstatic`, then Gunicorn)
- Deploy-critical env vars:
  - `DJANGO_ENV`, `DEBUG`, `SECRET_KEY`
  - `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
  - `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`
- Notes:
  - Security flags are enforced when `DEBUG=False`.
  - `SECURE_PROXY_SSL_HEADER` is set for reverse-proxy TLS termination.

## 2) Gunicorn (WSGI Server)
- Role: Production HTTP server for Django app.
- Runtime command (from `entrypoint.sh`):
  - `gunicorn TaskIt.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120`
- Notes:
  - Runs inside `web` container.
  - Upstream reverse proxy is Caddy.

## 3) PostgreSQL (Primary Relational Database)
- Role: System of record for users, tasks, events, notes, email integration data.
- DB engine: `django.db.backends.postgresql` (`TaskIt/settings.py`).
- Runtime options:
  - Local container: `db` service (`pgvector/pgvector:pg16`) in `docker-compose.yml`.
  - Managed DB: `.env.prod` is prepared for Supabase (PostgreSQL + SSL).
- Deploy-critical env vars:
  - `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_SSLMODE`
- Persistence:
  - Local Docker volume: `postgres_data`.
- Notes:
  - `entrypoint.sh` blocks startup until DB is reachable.
  - Migrations are applied automatically at web container startup.

## 4) Redis (Broker/Backend for Background Jobs)
- Role: Message broker + result backend for Celery.
- Runtime options:
  - Local container: `redis` service (`redis:7-alpine`) in `docker-compose.yml`.
- Deploy-critical env vars:
  - `REDIS_URL`
  - `CELERY_BROKER_URL`
  - `CELERY_RESULT_BACKEND`
- Notes:
  - Production uses the local Redis container on the VM.
  - The simple production values for this project are:
    - `REDIS_URL=redis://redis:6379/0`
    - `CELERY_BROKER_URL=redis://redis:6379/0`
    - `CELERY_RESULT_BACKEND=redis://redis:6379/0`

## 5) Celery (Async Worker + Scheduler)
- Role: Background execution and periodic scheduling.
- Bootstrap:
  - `TaskIt/celery.py`
- Runtime services:
  - `worker`: `celery -A TaskIt worker --loglevel=info`
  - `beat`: `celery -A TaskIt beat --loglevel=info -s /tmp/celerybeat-schedule`
- Deploy-critical dependency:
  - Requires reachable PostgreSQL and Redis.
- Notes:
  - `beat` writes schedule state to `/tmp/celerybeat-schedule` (ephemeral in container).

## 6) Chroma (Vector Database for RAG)
- Role: Persistent vector store for indexed note content used by agent/RAG features.
- Code path:
  - `main/agent/rag_utils.py` (`langchain_chroma.Chroma`)
- Persist location:
  - `settings.BASE_DIR / "rag_index"`
- Container persistence:
  - Docker volume `rag_index_data` is mounted to `/app/rag_index` for `web` and `worker`.
- Deploy-critical env vars:
  - `OPENAI_API_KEY` (needed for embeddings/model calls through LangChain/OpenAI)
- Notes:
  - Collection name is `taskit_rag`.
  - Without persistent storage, embeddings/indexes are lost between deployments.

## 7) LangChain + OpenAI (LLM/RAG Layer)
- Role: AI agent behavior, email suggestion generation, embeddings, chat model calls.
- Key packages:
  - `langchain`, `langchain-openai`, `langchain-chroma`, `langchain-community`, `openai`, `tiktoken`
- Deploy-critical env vars:
  - `OPENAI_API_KEY`
- Notes:
  - This is an external dependency on OpenAI APIs (network egress required in production).

## 8) Caddy (Reverse Proxy + TLS + Static Files)
- Role: Public edge proxy, HTTPS termination, static file serving.
- Config files:
  - `Caddyfile.local`
  - `Caddyfile.prod`
- Runtime service:
  - `caddy` container (`caddy:2-alpine`) in Docker Compose override files
- Ports:
  - Local: `8000` (HTTP)
  - Production: `80` and `443`
- Behavior:
  - Redirects HTTP to HTTPS.
  - Serves `/static/*` directly from shared volume (`static_data`).
  - Proxies app traffic to `web:8000`.

## 9) Docker / Docker Compose (Orchestration)
- Role: Defines full deployable runtime topology.
- Files:
  - `Dockerfile` (Python 3.11 slim image, installs system deps and pip deps)
  - `docker-compose.yml` (shared services, volumes, env wiring, health checks)
  - `docker-compose.override.yml` (local-only ports, localhost hosts, local Caddy)
  - `docker-compose.prod.yml` (production-only hosts, TLS proxy, prod Caddy)
- Services:
  - `web`, `worker`, `beat`, `db`, `redis`, `caddy`
- Persistent volumes:
  - `postgres_data`, `static_data`, `rag_index_data`, `caddy_data`, `caddy_config`
- Notes:
  - Local runs automatically include `docker-compose.override.yml` when you run `docker compose up`.
  - A production-like local test is supported by layering the prod override with `.env.prod`:
  - `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up --build -d --no-deps web worker beat caddy`

## 10) OAuth Providers (Email Integration Dependency)
- Role: Gmail/Outlook connection and email scan/suggestion features.
- Providers:
  - Google OAuth
  - Microsoft OAuth
- Deploy-critical env vars:
  - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
  - `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`
  - `EMAIL_TOKEN_ENCRYPTION_KEY`
- Notes:
  - Redirect URIs must exactly match deployed domain endpoints.
  - Email token encryption key must be set in production.

## Quick Deployment Checklist
- Set strong secrets: `SECRET_KEY`, `EMAIL_TOKEN_ENCRYPTION_KEY`, OAuth client secrets.
- Configure domain security: `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`.
- Verify database and Redis connectivity (`DB_SSLMODE` for Supabase, `redis://redis:6379/0` for Celery).
- Ensure persistent volumes/backups for:
  - PostgreSQL data (`postgres_data` or managed DB backups)
  - Chroma index (`rag_index_data`)
- Confirm OpenAI key is available to both `web` and `worker`.
- Run with `DEBUG=False` in production.
