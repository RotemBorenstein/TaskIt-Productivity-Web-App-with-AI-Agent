# Deployment Proccess (Beginner Memory Guide)

This file is a personal step-by-step memory of the exact deployment flow we did for TaskIt.

## 1) Prerequisites (before touching the server)
### What to do
- Prepare these accounts/services:
  - Hetzner Cloud (for VM)
  - DuckDNS (free domain)
  - Supabase (PostgreSQL, and later pgvector)
  - Upstash Redis (for Celery queue)
  - Google OAuth app
  - Microsoft OAuth app

### Why this is necessary
- Your app needs:
  - one machine to run containers (Hetzner VM),
  - one domain for HTTPS and OAuth callbacks (DuckDNS),
  - managed DB + Redis so you do less ops work.

### How to verify success
- You can log into each provider dashboard and see your project/resources.

---

## 2) Create domain and point it to VM (DuckDNS)
### What to do
- Create `taskit.duckdns.org` in DuckDNS.
- Point it to the VM public IPv4.

### Command(s)
```powershell
nslookup taskit.duckdns.org
```

### Why this is necessary
- DNS maps your human-readable domain to your server IP.
- OAuth and HTTPS depend on a stable hostname.

### How to verify success
- `nslookup` returns your VM IP (we got `204.168.159.57`).

---

## 3) First SSH login and basic server identity check
### What to do
- Connect to VM as root (first time only), then confirm who/where you are.

### Command(s)
```powershell
ssh root@204.168.159.57
```
```bash
whoami
hostname -I
lsb_release -a
```

### Why this is necessary
- Confirms server user, network IP, and Linux version before setup.

### How to verify success
- We saw:
  - user: `root`
  - Ubuntu `24.04.3 LTS`
  - expected public IP.

---

## 4) Server hardening (security baseline)
### What to do
- Create non-root deploy user.
- Add SSH key for deploy user.
- Disable SSH root login and password login.
- Enable firewall for SSH + web ports.

### Command(s)
```bash
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
nano /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```
```bash
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak
nano /etc/ssh/sshd_config
```
Set these in `sshd_config`:
```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```
Then:
```bash
sshd -t && systemctl reload ssh
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```
Test deploy login:
```powershell
ssh deploy@204.168.159.57
```

### Why this is necessary
- Root/password SSH is risky on public internet.
- Firewall reduces attack surface to only required ports.

### How to verify success
- `ssh deploy@...` works.
- `ufw status` shows OpenSSH + 80 + 443 allowed.

---

## 5) Install Docker + Docker Compose (Ubuntu 24.04)
### What to do
- Install Docker Engine + Compose plugin from Docker repo.
- Add `deploy` user to docker group.

### Command(s)
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
```
Then relogin (or run `newgrp docker`) and verify:
```bash
docker --version
docker compose version
docker ps
```

### Why this is necessary
- Docker runs your app services as containers.
- Compose lets you run full stack with one command.

### How to verify success
- Docker/Compose version commands succeed.

---

## 6) Clone repo and branch correctness (`main` vs `deploy`)
### What to do
- Clone repo under `/home/deploy/TaskIt`.
- If deployment files are not visible, switch branch.

### Command(s)
```bash
cd /home/deploy
git clone <YOUR_PUBLIC_HTTPS_REPO_URL> TaskIt
cd TaskIt
ls -la
git branch --show-current
git branch -a
```
If needed:
```bash
git fetch --all
git switch deploy
# or:
git checkout -b deploy origin/deploy
```

### Why this is necessary
- Deployment files (`docker-compose.yml`, `Caddyfile`, `Dockerfile`) may exist only on a specific branch.

### How to verify success
- Correct branch checked out and deployment files are present.

---

## 7) Production env file (`.env.server`) on VM
### What to do
- Create `/home/deploy/TaskIt/.env.server` manually (not committed).
- Fill production values (Django, DB, Redis, OAuth, OpenAI, encryption key).

### Command(s)
```bash
cd /home/deploy/TaskIt
nano .env.server
chmod 600 .env.server
chown deploy:deploy .env.server
```
Generate strong secrets if needed:
```bash
python3 - <<'PY'
import secrets
from cryptography.fernet import Fernet
print("SECRET_KEY=", secrets.token_urlsafe(64))
print("EMAIL_TOKEN_ENCRYPTION_KEY=", Fernet.generate_key().decode())
PY
```

### Why this is necessary
- Production secrets should live on server, not inside Git history.
- `.env.server` is your runtime config for managed Supabase/Upstash/OAuth.

### How to verify success
- File exists with `chmod 600`.
- OAuth callback env values use domain:
  - `https://taskit.duckdns.org/auth/google/callback`
  - `https://taskit.duckdns.org/auth/microsoft/callback`

---

## 8) Configure Caddy + start app services on `80/443`
### What to do
- Configure Caddy for domain and reverse proxy to `web:8000`.
- Expose standard ports `80` and `443`.
- Start only app-tier containers (`web`, `worker`, `beat`, `caddy`) because DB/Redis are managed.

### Command(s)
Edit `Caddyfile`:
```bash
nano Caddyfile
```
Use:
```caddy
taskit.duckdns.org {
    encode gzip zstd

    handle /static/* {
        root * /srv/staticfiles
        uri strip_prefix /static
        file_server
    }

    reverse_proxy web:8000
}
```
Edit compose ports:
```bash
nano docker-compose.yml
```
Set under `caddy`:
```yaml
ports:
  - "80:80"
  - "443:443"
```
Run:
```bash
docker compose --env-file .env.server up --build -d --no-deps web worker beat caddy
```

### Why this is necessary
- Caddy handles public HTTPS and forwards traffic to Django app.
- Standard `80/443` is required for trusted cert flow.

### How to verify success
- Containers are up (`docker compose ps`).
- Site reachable at `https://taskit.duckdns.org`.

---

## 9) First-run validation commands
### What to do
- Check service status and logs.
- Check open ports.
- Verify TLS certificate and HTTP response.

### Command(s)
```bash
docker compose ps
docker compose logs --tail=200 web
docker compose logs --tail=200 caddy
docker compose logs --tail=200 worker
docker compose logs --tail=200 beat
sudo ss -tulpn | grep -E ':80|:443'
curl -Iv https://taskit.duckdns.org
```

### Why this is necessary
- Confirms app health, proxy routing, certificate validity, and public accessibility.

### How to verify success
- `curl -Iv` shows:
  - cert issuer Let’s Encrypt
  - SAN includes `taskit.duckdns.org`
  - HTTP `200`.

---

## 10) Real issues we hit and how we fixed them
### A) Django CSRF error
#### Problem
- `CSRF_TRUSTED_ORIGINS` had hostname without scheme.
#### Fix
In `.env.server`:
```env
CSRF_TRUSTED_ORIGINS=https://taskit.duckdns.org
```
Restart:
```bash
docker compose --env-file .env.server up -d web worker beat caddy
```
#### Why
- Django 4+ requires full URL with `http://` or `https://`.

### B) Caddy 502 (cannot resolve `web`)
#### Problem
- Caddy log: `lookup web ... server misbehaving`.
#### Fix
```bash
docker compose down --remove-orphans
docker compose --env-file .env.server up -d --build web worker beat caddy
docker compose ps
docker compose logs --tail=100 caddy
```
#### Why
- Clears stale containers/networks so service DNS (`web`) works correctly.

### C) Chrome “dangerous site” warning
#### What happened
- Often temporary before cert issuance/caching settles.
#### Verification that it became correct
- `curl -Iv https://taskit.duckdns.org` showed valid Let’s Encrypt cert and HTTP 200.

### D) Google `redirect_uri_mismatch`
#### Fix
- Set Google OAuth redirect URI exactly:
```text
https://taskit.duckdns.org/auth/google/callback
```
- Check runtime value:
```bash
docker compose exec web printenv GOOGLE_REDIRECT_URI
```
#### Why
- OAuth redirect URI must match exactly (scheme, host, path, slash).

---

## 11) Operational notes (commits, secrets, branch)
### What to do
- Treat server state and Git repo changes as different things.

### Rules
- No commit needed for server-only actions:
  - creating users/firewall,
  - creating `.env.server`,
  - running Docker commands.
- Commit is needed if tracked repo files changed and should be permanent:
  - e.g., `Caddyfile`, `docker-compose.yml`, app code/templates.
- If secrets were exposed anywhere, rotate them:
  - DB password,
  - Redis URL/token,
  - OAuth secrets,
  - Django `SECRET_KEY`,
  - encryption keys.

### Why this is necessary
- Prevent secret leaks and keep Git history clean.

### How to verify success
- `git status` shows only intended tracked changes.

---

## 12) Next improvements (short roadmap)
1. Protect access for private usage:
- Disable signup route/button or gate site with Caddy Basic Auth.

2. Control API cost:
- Set OpenAI budget limits/alerts.

3. Phase-2 automation:
- Add GitHub Actions CI/CD after manual deployment is stable.

---

## 12.5) CI/CD workflow we are adding
### Goal
- Keep deployment simple, but make code validation and production deploys repeatable.

### Branch flow
- Create short-lived `feature/*` branches for each non-trivial change.
- Open a pull request into `main`.
- Let CI validate the branch.
- Merge into `main` only after CI passes.
- Promote production by merging or fast-forwarding `main` into `deploy`.
- Only `deploy` triggers the production deployment workflow.

### CI (`.github/workflows/ci.yml`)
What it does:
- runs on:
  - pull requests to `main`
  - pushes to `main`
  - pushes to `deploy`
- uses Python `3.11` to match the production image
- starts a temporary PostgreSQL service with `pgvector`
- runs:
  - `python manage.py check`
  - `python manage.py test`
- builds the Docker image from `Dockerfile`

Why this is useful:
- catches Django/config/test failures before merge
- catches broken Docker packaging before deployment
- keeps CI close to the real production runtime

### CD (`.github/workflows/deploy.yml`)
What it does:
- runs only on pushes to `deploy`
- targets the GitHub `production` environment
- should be configured with required reviewers so deploys need manual approval
- SSHes into the VM and runs:
```bash
cd /home/deploy/TaskIt
git fetch origin
git switch deploy
git pull --ff-only origin deploy
docker compose --env-file .env.server up --build -d --no-deps web worker beat caddy
docker compose ps
curl -f https://taskit.duckdns.org
```

Why this is useful:
- keeps your existing server setup
- automates the exact deploy command you already trust
- adds a manual safety gate before production changes

### GitHub configuration you must do manually
1. Add repository secrets:
- `SSH_HOST`
- `SSH_USER`
- `SSH_PRIVATE_KEY`
- optional `SSH_PORT`
- optional `SSH_KNOWN_HOSTS`

2. Create a GitHub environment named `production`.

3. In that environment, enable required reviewers so production deploys pause for approval.

4. Add branch protection:
- require CI status checks on `main`
- optionally protect `deploy` too

### Important boundary
- GitHub stores only deployment access secrets.
- App/runtime secrets stay on the VM in `.env.server`.
- This deployment style still causes short downtime during container restarts because it is a single-VM Docker Compose setup.

---

## 13) Pgvector migration process (RAG moved from Chroma to Supabase)
### What we changed
- Added pgvector-backed storage for note chunks (`RagChunk` model).
- Kept tool behavior stable (`search_knowledge` still uses top_k=5).
- Added one-time backfill command:
  - `python manage.py reindex_notes_pgvector`

### Why this was necessary
- We wanted vector search in managed Supabase instead of local Chroma files.
- This is simpler long-term for backup/operations in this deployment.

### Important implementation choices we used
- Embedding model: `text-embedding-3-small` (lower cost, enough for personal notes).
- Chunking: `chunk_size=500`, `chunk_overlap=100` (kept existing behavior).
- Index strategy: no ANN index initially (simple for small dataset).

### Deployment commands (VM / production path)
Run inside VM project (`~/TaskIt`) with production env:
```bash
docker compose --env-file .env.server run --rm web python manage.py migrate
docker compose --env-file .env.server run --rm web python manage.py reindex_notes_pgvector
docker compose --env-file .env.server up -d --build web worker beat caddy
```

### How to verify success
```bash
docker compose --env-file .env.server run --rm web python manage.py shell -c "from main.models import RagChunk; print(RagChunk.objects.count())"
```
- Count should be `> 0` after backfill.
- Agent note search should return known note content.

### Real errors we hit and fixes
1. `extension "vector" is not available` on local Windows Postgres:
- Cause: local DB did not have pgvector installed.
- Fix: run migration against Supabase/VM DB (or install local pgvector separately).

2. `Unknown command: reindex_notes_pgvector` on VM:
- Cause: VM image was old (new code not pulled/built yet).
- Fix:
```bash
git pull origin deploy
docker compose --env-file .env.server build web
docker compose --env-file .env.server run --rm web python manage.py help | grep reindex
```

3. Python type hint error (`dict | None`) during migrate:
- Cause: local Python 3.9 does not support `|` union syntax.
- Fix: switched to `typing.Optional[...]`.

### Safety note
- This migration does not affect user-facing UI directly.
- If backfill is skipped, only new/updated notes may be searchable.

---

## Quick glossary (beginner)
- VM: A cloud server you rent.
- Reverse proxy (Caddy): Front door that handles HTTPS and forwards traffic to app.
- Container (Docker): Packaged app process with dependencies.
- Compose: Tool to run multiple containers together.
- Celery worker: Background jobs executor.
- Celery beat: Scheduler for timed jobs.
- Redis: Queue broker for background tasks.
- OAuth callback URI: URL provider returns to after login; must match exactly.
