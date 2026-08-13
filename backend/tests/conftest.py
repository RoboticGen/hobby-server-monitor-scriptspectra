"""
backend/tests/conftest.py

Pytest configuration: redirect all tests to an isolated in-memory SQLite database
so that test artifacts (fake users, sessions, containers) never pollute app.db.

How it works:
  - A single shared in-memory SQLite connection is created once per test session.
  - `app.db.get_db` is patched to always return this shared connection.
  - `app.db.close_db` is patched to be a no-op (prevents DBCloseMiddleware from
    destroying the shared in-memory connection between requests).
  - The schema is applied once at session startup.
  - The per-test `clean_thread_local` fixture re-attaches the shared connection
    to the thread-local so get_db() finds it immediately.
"""

import sqlite3
import threading
import pytest
from unittest.mock import patch


# ── In-memory schema (matches scripts/init_db.py) ──────────────────────────────
_TEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS bootstrap_state (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    fired INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO bootstrap_state VALUES (1, 1);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    name            TEXT,
    picture_url     TEXT,
    role            TEXT    NOT NULL DEFAULT 'user',
    quota_ram_mb    INTEGER NOT NULL DEFAULT 2048,
    quota_cpu_cores INTEGER NOT NULL DEFAULT 2,
    quota_disk_gb   INTEGER NOT NULL DEFAULT 20,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    revoked_at      TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS containers (
    name        TEXT    PRIMARY KEY,
    description TEXT,
    created_by  INTEGER REFERENCES users(id),
    ram_mb      INTEGER NOT NULL DEFAULT 512,
    cpu_cores   INTEGER NOT NULL DEFAULT 1,
    disk_gb     INTEGER NOT NULL DEFAULT 5,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    container_name TEXT    REFERENCES containers(name) ON DELETE SET NULL,
    granted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    revoked_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_assignment
    ON assignments(user_id, container_name)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action   TEXT NOT NULL,
    target   TEXT,
    detail   TEXT,
    at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Module-level shared in-memory connection — created once per pytest session
_TEST_CONN: sqlite3.Connection | None = None


def _get_or_create_test_conn() -> sqlite3.Connection:
    global _TEST_CONN
    if _TEST_CONN is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_TEST_SCHEMA)
        conn.commit()
        _TEST_CONN = conn
    return _TEST_CONN


@pytest.fixture(scope="session", autouse=True)
def isolate_db():
    """
    Session-scoped: patch get_db() and close_db() for the entire test run.

    - get_db() always returns the shared in-memory connection.
    - close_db() becomes a no-op so DBCloseMiddleware can't destroy it.
    """
    global _TEST_CONN
    _TEST_CONN = None
    test_conn = _get_or_create_test_conn()

    with patch("app.db.get_db", return_value=test_conn), \
         patch("app.db.close_db"), \
         patch("app.main.close_db"):   # no-op: never actually close the shared conn
        yield

    if _TEST_CONN:
        _TEST_CONN.close()
        _TEST_CONN = None


@pytest.fixture(autouse=True)
def clean_thread_local():
    """
    Per-test: ensure thread-local also points at the shared in-memory connection
    (needed for test code that calls get_db() directly before the patch kicks in).
    """
    from app.db import _local
    _local.conn = _get_or_create_test_conn()
    yield
    # Detach pointer only — do NOT close the shared connection
    _local.conn = None


