"""
backend/app/db.py

Thread-local SQLite connection pool.

Usage:
    from app.db import get_db

    db = get_db()                          # returns the thread's connection
    row = db.execute("SELECT 1").fetchone()
    db.commit()

Design notes:
    - One connection per thread (safe for Gunicorn sync workers).
    - WAL journal mode: readers never block writers, writers never block readers.
    - PRAGMA foreign_keys = ON is set on EVERY new connection. SQLite resets
      this pragma per-connection, so setting it once at startup is not enough.
    - row_factory = sqlite3.Row gives dict-like access: row['email'] not row[0].
    - close_db() is called at Falcon app teardown (registered in main.py).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# One connection object per OS thread.
_local = threading.local()


def _new_connection() -> sqlite3.Connection:
    """Open a fresh SQLite connection with all required PRAGMAs set."""
    db_path: Path = settings.sqlite_db_path

    # Create parent directories if they don't exist (e.g. data/ on first run).
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,   # we manage thread-safety ourselves via _local
        timeout=10,                # seconds to wait on a locked database
    )

    # dict-like row access: row['email'] instead of row[0]
    conn.row_factory = sqlite3.Row

    # WAL mode: concurrent reads while a write is in progress.
    # Persisted on disk after first SET — subsequent calls are no-ops.
    conn.execute("PRAGMA journal_mode = WAL")

    # CRITICAL: must be set on EVERY connection — SQLite resets it per-connection.
    conn.execute("PRAGMA foreign_keys = ON")

    # Slightly larger cache for read-heavy workloads (metrics queries).
    conn.execute("PRAGMA cache_size = -8000")   # 8 MB

    conn.commit()
    return conn


def get_db() -> sqlite3.Connection:
    """
    Return this thread's SQLite connection, creating one if needed.
    Call this at the start of every request handler or background task.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_connection()
        _local.conn = conn
        log.debug("Opened new SQLite connection for thread %s", threading.current_thread().name)
    return conn


def close_db() -> None:
    """
    Close this thread's connection and remove it from thread-local storage.
    Call this at the end of a request (registered in Falcon app teardown).
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        log.debug("Closed SQLite connection for thread %s", threading.current_thread().name)


def execute_script(sql: str) -> None:
    """
    Run a multi-statement SQL script (used by init_db.py).
    Each statement is separated by ';'.
    """
    db = get_db()
    db.executescript(sql)
    db.commit()
