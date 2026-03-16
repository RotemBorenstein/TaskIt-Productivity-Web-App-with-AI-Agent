#!/usr/bin/env bash
set -euo pipefail

echo "== TaskIt Weekly Health Check =="
echo

echo "1) Service status"
docker compose ps
echo

echo "2) Recent logs (web)"
docker compose logs --tail=120 web || true
echo

echo "3) Recent logs (worker)"
docker compose logs --tail=120 worker || true
echo

echo "4) Recent logs (caddy)"
docker compose logs --tail=120 caddy || true
echo

echo "5) Disk and memory"
df -h
free -h
echo

echo "6) HTTPS check"
curl -Iv https://taskit.duckdns.org || true
echo

echo "7) Pgvector count"
docker compose --env-file .env.server run --rm web python manage.py shell -c "from main.models import RagChunk; print(RagChunk.objects.count())"
echo

echo "Done."
