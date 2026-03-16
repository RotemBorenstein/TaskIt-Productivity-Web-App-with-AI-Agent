# Weekly Health Check (Beginner Checklist)

Use this checklist once a week on the VM (`~/TaskIt`).
Total time: about 10-15 minutes.

## 1) Service Status
### Command
```bash
docker compose ps
```

### Why
- Confirms your main containers are running: `web`, `worker`, `beat`, `caddy`.

### Healthy result
- All required services show `Up`.

### If not healthy
- Restart stack:
```bash
docker compose --env-file .env.server up -d --build web worker beat caddy
```

---

## 2) Recent Logs (Errors)
### Commands
```bash
docker compose logs --tail=120 web
docker compose logs --tail=120 worker
docker compose logs --tail=120 caddy
```

### Why
- Finds hidden issues (500 errors, DB/Redis disconnects, OAuth failures, etc.).

### Healthy result
- No repeating errors.

### If not healthy
- If errors repeat, fix same day.
- If needed, restart and re-check logs.

---

## 3) Disk and Memory
### Commands
```bash
df -h
free -h
```

### Why
- Prevent outages from full disk or low memory.

### Healthy result
- Disk usage is comfortably below 80%.

### If not healthy
- Clean old Docker data:
```bash
docker system df
docker image prune -f
```

---

## 4) HTTPS and Domain
### Command
```bash
curl -Iv https://taskit.duckdns.org
```

### Why
- Verifies public endpoint is reachable and TLS certificate is valid.

### Healthy result
- Response succeeds (e.g., `HTTP/2 200`), and cert is valid.

### If not healthy
- Check Caddy logs:
```bash
docker compose logs --tail=200 caddy
```

---

## 5) Pgvector Sanity
### Command
```bash
docker compose --env-file .env.server run --rm web python manage.py shell -c "from main.models import RagChunk; print(RagChunk.objects.count())"
```

### Why
- Ensures vector index data still exists.

### Healthy result
- Count is greater than 0.

### If not healthy
- Rebuild index:
```bash
docker compose --env-file .env.server run --rm web python manage.py reindex_notes_pgvector
```

---

## 6) Backup Confidence (Supabase)
### What to check
- Open Supabase dashboard and confirm recent successful backup/snapshot.

### Why
- Backups are your recovery path if something breaks.

---

## Alert Thresholds (Simple)
- Any required service not `Up` -> fix immediately.
- Repeating errors in logs -> investigate same day.
- Disk above 80% -> clean up now.
- `RagChunk` count unexpectedly drops -> run reindex.

