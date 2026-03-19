# TaskIt – Productivity Web-App with an AI Agent

**TaskIt** is a full-stack productivity web application built with **Django**, **PostgreSQL**, and **JavaScript (FullCalendar)**.  
It helps users organize tasks, schedule events, and track progress - all enhanced by an integrated **AI chat agent** powered by **LangChain** and the **OpenAI API**.

---

## Features

### Task Management
- Create and manage **daily** and **long-term** tasks.
- Mark tasks as complete; completed daily tasks reset each day.
- **Anchored tasks** automatically reappear the next day after completion.

### Calendar Integration
- Interactive **FullCalendar** view for events and deadlines.
- Create, edit, or drag-and-drop events directly on the calendar.
- View task completion status for any selected day.

### Statistics Dashboard
- Visualize completion rates by **day**, **week**, or **month**.
- Display top tasks not completed to highlight areas for improvement.
- Summarized performance analytics for quick progress tracking.

### AI Chat Agent
- Floating chat button opens an **in-app assistant**.
- Users can type natural-language commands like:
  - “Add a meeting tomorrow at 3 PM.”
  - “Create a long-term task to finish my project.”
  - “Show my completion rate this week.”
- Built using **LangChain** and the **OpenAI API**, with tools for:
  - Task creation (`add_task`)
  - Event creation (`add_event`)
  - Statistics analysis (`analyze_stats`)

---

## Tech Stack

**Backend:** Django, PostgreSQL, REST API  
**Frontend:** JavaScript, HTML, CSS, FullCalendar  
**AI Integration:** LangChain, OpenAI API (ChatGPT model)  
**Other Tools:** Chart.js, Python (timezone & date utilities), Django ORM

---

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/RotemBorenstein/TaskIt.git
   cd TaskIt
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up the database:**
   ```bash
   python manage.py migrate
   ```

4. **Add your environment variables:**
   Create a `.env` file in the project root:
   ```bash
   OPENAI_API_KEY=your_api_key_here
   ```

5. **Run the development server:**
   ```bash
   python manage.py runserver
   ```

6. **Access the app:**
   Open your browser and visit [http://localhost:8000](http://localhost:8000)

---

## Usage

- Navigate to the **Tasks** page to create and track daily or long-term tasks.
- Open the **Calendar** to view or create events interactively.
- Visit the **Stats** page to analyze completion performance.
- Click the **floating chat button** to open the AI agent and issue natural-language commands.

---

## Docker (Local Production-Like Run)

This project includes a full local container stack:
- `web`: Django + Gunicorn
- `worker`: Celery worker
- `beat`: Celery beat scheduler
- `db`: PostgreSQL
- `redis`: Redis
- `caddy`: reverse proxy on `http://localhost:8000`

### 1) Prepare environment
Use your existing `.env` or start from:

```bash
cp .env.example .env
```

Make sure your `.env` has at least:
- `SECRET_KEY`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `EMAIL_TOKEN_ENCRYPTION_KEY` (needed for email features)

### 2) Build and run

```bash
docker compose up --build
```

`web` startup automatically runs:
1. `python manage.py migrate`
2. `python manage.py collectstatic --noinput`
3. `gunicorn TaskIt.wsgi:application`

### 3) Open the app

- App URL: `http://localhost:8000`

### 4) Quick validation commands

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f beat
```

### 5) Stop

```bash
docker compose down
```

### Staging-like run with managed Supabase/Upstash

If you want containers to use `.env.prod` managed services and production routing, run:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up --build -d --no-deps web worker beat caddy
```

### Pgvector rollout (Supabase)

After deploying code that includes pgvector-backed RAG:

1. Run DB migration:
```bash
python manage.py migrate
```

2. Backfill existing notes into pgvector:
```bash
python manage.py reindex_notes_pgvector
```

Optional: reindex only one user:
```bash
python manage.py reindex_notes_pgvector --user-id <ID>
```

## CI/CD Workflow

This project uses GitHub Actions with a simple branch flow:

- `feature/*` branches for active work
- `main` for stable, CI-validated integration
- `deploy` for production releases

### CI

The CI workflow runs on pull requests to `main`, plus pushes to `main` and `deploy`.
It does two things:

- runs `python manage.py check` and `python manage.py test` on Python 3.11
- builds the Docker image from `Dockerfile`

CI uses a temporary PostgreSQL service with `pgvector` available so migrations and tests match the production database shape.

### CD

The deploy workflow runs only on pushes to `deploy` and targets the GitHub `production` environment.
After manual approval in GitHub, it:

- SSHes into the Hetzner VM
- pulls the latest `deploy` branch in `/home/deploy/TaskIt`
- runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.server up --build -d --no-deps web worker beat caddy`
- verifies the deployment with `docker compose ps` and `curl -f https://taskit.duckdns.org`

### GitHub secrets for deployment

Store only deployment access secrets in GitHub Actions:

- `SSH_HOST`
- `SSH_USER`
- `SSH_PRIVATE_KEY`
- optional `SSH_PORT`
- optional `SSH_KNOWN_HOSTS`

Keep application secrets such as Django, Supabase, Upstash, OAuth, and OpenAI values on the server in `.env.server`.

