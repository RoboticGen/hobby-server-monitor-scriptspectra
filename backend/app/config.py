"""
backend/app/config.py

Single source of truth for all configuration.
Loaded once at import time from the .env file via python-dotenv.
All other modules import `settings` from here — no scattered os.environ calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Resolve the .env file relative to this file's location.
# backend/app/config.py → backend/.env (or project root .env)
_HERE = Path(__file__).resolve().parent          # backend/app/
_BACKEND = _HERE.parent                          # backend/
_PROJECT_ROOT = _BACKEND.parent                  # project root

# Load .env from project root first, fall back to backend/ directory.
_env_file = _PROJECT_ROOT / ".env"
if not _env_file.exists():
    _env_file = _BACKEND / ".env"

load_dotenv(dotenv_path=_env_file, override=False)


def _require(key: str) -> str:
    """Read an env var and raise a clear error if it is missing."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"[config] Required environment variable '{key}' is not set. "
            f"Check your .env file at {_env_file}."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"[config] Environment variable '{key}' must be an integer, got: {raw!r}"
        )


@dataclass(frozen=True)
class Settings:
    # ── Google OAuth ───────────────────────────────────────────────────────────
    GOOGLE_OAUTH_CLIENT_ID: str
    GOOGLE_OAUTH_CLIENT_SECRET: str
    GOOGLE_OAUTH_REDIRECT_URI: str

    # ── Session signing ────────────────────────────────────────────────────────
    # Must be a long random string. Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    SESSION_SECRET: str

    # ── Bootstrap admin ────────────────────────────────────────────────────────
    # This email is always granted admin on first login, regardless of bootstrap_state.
    BOOTSTRAP_ADMIN_EMAIL: str

    # ── Data paths ─────────────────────────────────────────────────────────────
    SQLITE_DB_PATH: str
    TINYFLUX_DB_PATH: str

    # ── LXD ────────────────────────────────────────────────────────────────────
    # Leave blank to use the Unix socket (recommended). Set to https://... for remote LXD.
    LXD_ENDPOINT: str

    # ── Collector tuning ───────────────────────────────────────────────────────
    COLLECTOR_POLL_INTERVAL_SECONDS: int
    METRICS_RETENTION_DAYS: int

    # ── Backend server ─────────────────────────────────────────────────────────
    BACKEND_HOST: str
    BACKEND_PORT: int

    # ── Environment ────────────────────────────────────────────────────────────
    # 'development' → Secure cookie flag OFF (plain HTTP localhost)
    # 'production'  → Secure cookie flag ON  (requires HTTPS)
    APP_ENV: str

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def sqlite_db_path(self) -> Path:
        """Resolve SQLITE_DB_PATH relative to project root if not absolute."""
        p = Path(self.SQLITE_DB_PATH)
        if not p.is_absolute():
            p = (_PROJECT_ROOT / p).resolve()
        return p

    @property
    def tinyflux_db_path(self) -> Path:
        """Resolve TINYFLUX_DB_PATH relative to project root if not absolute."""
        p = Path(self.TINYFLUX_DB_PATH)
        if not p.is_absolute():
            p = (_PROJECT_ROOT / p).resolve()
        return p


def _load_settings() -> Settings:
    """Build and validate the Settings object from environment variables."""
    return Settings(
        GOOGLE_OAUTH_CLIENT_ID=_require("GOOGLE_OAUTH_CLIENT_ID"),
        GOOGLE_OAUTH_CLIENT_SECRET=_require("GOOGLE_OAUTH_CLIENT_SECRET"),
        GOOGLE_OAUTH_REDIRECT_URI=_require("GOOGLE_OAUTH_REDIRECT_URI"),
        SESSION_SECRET=_require("SESSION_SECRET"),
        BOOTSTRAP_ADMIN_EMAIL=_require("BOOTSTRAP_ADMIN_EMAIL"),
        SQLITE_DB_PATH=_optional("SQLITE_DB_PATH", "data/app.db"),
        TINYFLUX_DB_PATH=_optional("TINYFLUX_DB_PATH", "data/metrics.tinyflux"),
        LXD_ENDPOINT=_optional("LXD_ENDPOINT", ""),
        COLLECTOR_POLL_INTERVAL_SECONDS=_int("COLLECTOR_POLL_INTERVAL_SECONDS", 10),
        METRICS_RETENTION_DAYS=_int("METRICS_RETENTION_DAYS", 30),
        BACKEND_HOST=_optional("BACKEND_HOST", "0.0.0.0"),
        BACKEND_PORT=_int("BACKEND_PORT", 8000),
        APP_ENV=_optional("APP_ENV", "development"),
    )


# Module-level singleton — imported by every other module.
# Raises RuntimeError at startup if required env vars are missing.
settings: Settings = _load_settings()
