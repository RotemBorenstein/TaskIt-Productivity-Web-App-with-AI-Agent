# Deployment Handoff Notes (TaskIt)

## Current deployment state
- App is containerized and runs with:
  - `web` (Django + Gunicorn)
  - `worker` (Celery worker)
  - `beat` (Celery beat)
  - `caddy` (reverse proxy)
- Compose is split into:
  - `docker-compose.yml` for shared services and common env
  - `docker-compose.override.yml` for local-only behavior
  - `docker-compose.prod.yml` for production-only behavior
- Static files are served by Caddy from collected static volume.

## Data/infra integration status
- Supabase migration completed (schema + data loaded).
- Upstash Redis is connected for Celery broker/result backend.
- For managed-service testing:
  - use `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up --build -d --no-deps web worker beat caddy`
  - this uses the production Caddyfile and production-only env overrides.

## Important config behavior
- `TaskIt/settings.py` is env-driven for:
  - Django security/runtime settings
  - DB connection (+ `DB_SSLMODE`)
  - Celery/Redis URLs
- Upstash `rediss://` requires Celery SSL handling.
  - `CELERY_SSL_CERT_REQS` is supported in settings.
- Local runs use `docker compose up`, which automatically includes `docker-compose.override.yml`.
- Production runs must explicitly include `docker-compose.prod.yml`.
- Do not run production with only `docker compose --env-file .env.prod up ...`, because Compose would also load the local override file.

## Known caveats
- `CELERY_SSL_CERT_REQS=CERT_NONE` works but is less strict security.
  - Prefer `CERT_REQUIRED` in real production if supported by cert chain.
- OAuth redirect URIs must match deployed domain exactly (Google/Microsoft).

## Next recommended steps
1. Deploy this stack to cloud VM staging with domain + HTTPS.
2. Add CI/CD (GitHub Actions) for checks and deploy automation.
3. Add monitoring/error reporting (Sentry + health checks + backups).
