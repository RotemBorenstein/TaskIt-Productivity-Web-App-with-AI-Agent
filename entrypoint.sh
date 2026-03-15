#!/bin/sh
set -e

echo "Waiting for database..."
python - <<'PY'
import os
import time
import psycopg2

host = os.getenv("DB_HOST", "db")
port = int(os.getenv("DB_PORT", "5432"))
name = os.getenv("DB_NAME", "taskit_db")
user = os.getenv("DB_USER", "taskit_user")
password = os.getenv("DB_PASSWORD", "")
sslmode = os.getenv("DB_SSLMODE", "prefer")

for i in range(60):
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=name,
            user=user,
            password=password,
            sslmode=sslmode,
        )
        conn.close()
        print("Database is ready.")
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Database did not become ready in time.")
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn TaskIt.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
