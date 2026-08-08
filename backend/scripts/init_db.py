"""
backend/scripts/init_db.py

Idempotent schema initialization script for SQLite.
Can be executed as:
    python -m scripts.init_db

Also safely callable at application startup.
"""

import sys
import logging
from pathlib import Path

# Ensure backend root is on sys.path when executed directly
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.db import get_db, execute_script

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("init_db")

SCHEMA = """
-- One-shot bootstrap flag: prevents first-user mechanism from firing twice
CREATE TABLE IF NOT EXISTS bootstrap_state (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    fired INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO bootstrap_state VALUES (1, 0);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    name            TEXT,
    picture_url     TEXT,
    role            TEXT    NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    quota_ram_mb    INTEGER NOT NULL DEFAULT 2048,
    quota_cpu_cores INTEGER NOT NULL DEFAULT 2,
    quota_disk_gb   INTEGER NOT NULL DEFAULT 20,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    revoked_at      TEXT                               -- soft revoke
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS containers (
    name        TEXT    PRIMARY KEY,                   -- LXD container name
    description TEXT,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at  TEXT                                   -- soft delete
);

CREATE TABLE IF NOT EXISTS assignments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    container_name TEXT    REFERENCES containers(name) ON DELETE SET NULL,
    granted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    revoked_at     TEXT
);

-- Prevents duplicate active assignments for the same (user, container) pair
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_assignment
    ON assignments(user_id, container_name)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action   TEXT NOT NULL,   -- 'container.delete' | 'container.limit_change' | ...
    target   TEXT,            -- container name or user email
    detail   TEXT,            -- JSON blob
    at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
"""

def init_db() -> None:
    log.info("Initializing database schema...")
    execute_script(SCHEMA)
    log.info("Database schema initialized successfully.")

if __name__ == "__main__":
    init_db()
