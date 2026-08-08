# Hobby Server Monitor — Backend

A Falcon-based API backend for managing LXD containers with Google OAuth authentication, role-based access, live metrics collection, and an in-browser terminal.

## Quick Start (Development)

### Prerequisites
- Python 3.10+
- LXD installed and running
- Your OS user must be in the `lxd` group: `sudo usermod -aG lxd $USER` (log out and back in)

### Setup

```bash
# 1. Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy and fill in environment variables
cp ../.env.example ../.env
# Edit ../.env with your Google OAuth credentials and SESSION_SECRET

# 3. Initialize the database
python -m scripts.init_db

# 4. Start the backend (terminal 1)
gunicorn app.main:application -w 2 -b 0.0.0.0:8000 --reload

# 5. Start the collector (terminal 2)
python -m collector.main
```

### Running Tests

```bash
python -m pytest tests/ -v
```

## Architecture

The backend is split into two independent processes:

| Process | Entry point | Port | Purpose |
|---|---|---|---|
| Falcon API | `app.main:application` | 8000 | Handles all HTTP requests |
| Collector | `collector.main` | — | Polls LXD every 10 s, writes to TinyFlux |

The collector **does not** import Falcon or depend on the API server. It runs independently so metrics continue flowing even when the API restarts.

## Directory Structure

```
backend/
├── app/
│   ├── config.py          # Settings from .env (single import point)
│   ├── db.py              # Thread-local SQLite pool
│   ├── tsdb.py            # TinyFlux wrapper
│   ├── lxd_client.py      # pylxd singleton + lxd_safe()
│   ├── main.py            # Falcon app factory
│   ├── middleware/
│   │   ├── auth.py        # Cookie → session → user
│   │   └── cors.py        # CORS for Astro dev server
│   ├── resources/         # One file per API resource group
│   └── util/              # Shared helpers (validators, quota, auth)
├── collector/
│   ├── main.py            # Standalone metric loop
│   └── scheduler.py       # Nightly compaction
├── scripts/
│   └── init_db.py         # CREATE TABLE IF NOT EXISTS runner
└── tests/
```

## Environment Variables

See `.env.example` at the project root for all variables with descriptions.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | ✅ | — | From Google Cloud Console |
| `GOOGLE_OAUTH_CLIENT_SECRET` | ✅ | — | From Google Cloud Console |
| `GOOGLE_OAUTH_REDIRECT_URI` | ✅ | — | Must match Console config |
| `SESSION_SECRET` | ✅ | — | 32+ random bytes (hex) |
| `BOOTSTRAP_ADMIN_EMAIL` | ✅ | — | Always gets admin on first login |
| `SQLITE_DB_PATH` | ❌ | `data/app.db` | Relative to project root |
| `TINYFLUX_DB_PATH` | ❌ | `data/metrics.tinyflux` | Relative to project root |
| `LXD_ENDPOINT` | ❌ | Unix socket | Leave blank for local LXD |
| `APP_ENV` | ❌ | `development` | `production` enables Secure cookie |
