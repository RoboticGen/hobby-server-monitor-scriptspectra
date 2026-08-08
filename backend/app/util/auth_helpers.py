"""
backend/app/util/auth_helpers.py

Authentication and session security helper functions.
Handles session token signing with `itsdangerous`, session database lookups,
and Role-Based Access Control (RBAC) role enforcement.
"""

import secrets
import logging
from datetime import datetime, timedelta, timezone
from sqlite3 import Connection

import falcon
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings

log = logging.getLogger(__name__)

# Max age for state nonce cookie (5 minutes)
OAUTH_STATE_MAX_AGE = 300

# Session token expiration (7 days)
SESSION_MAX_AGE_DAYS = 7


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="session-token-v1")


def sign_token(token_raw: str) -> str:
    """Sign a raw session token string using HMAC-SHA256."""
    s = _get_serializer()
    return s.dumps(token_raw)


def unsign_token(token_signed: str, max_age_seconds: int = 86400 * SESSION_MAX_AGE_DAYS) -> str | None:
    """
    Validate HMAC signature and return raw token string.
    Returns None if signature is invalid or expired.
    """
    s = _get_serializer()
    try:
        return s.loads(token_signed, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None


def create_session(user_id: int, db: Connection) -> str:
    """
    Generate a new random session token, insert into `sessions` table with 7-day expiry,
    and return the signed session token string for the cookie.
    """
    raw_token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_MAX_AGE_DAYS)

    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (raw_token, user_id, now.isoformat(), expires_at.isoformat())
    )
    db.commit()

    return sign_token(raw_token)


def lookup_session(signed_token: str, db: Connection) -> dict | None:
    """
    Unsign session token, verify it exists in `sessions` DB table and is not expired,
    and return the associated user dict (or None if invalid/expired).
    """
    raw_token = unsign_token(signed_token)
    if not raw_token:
        return None

    query = """
        SELECT u.id, u.email, u.name, u.picture_url, u.role,
               u.quota_ram_mb, u.quota_cpu_cores, u.quota_disk_gb, u.revoked_at
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ?
          AND s.expires_at > ?
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    row = db.execute(query, (raw_token, now_iso)).fetchone()

    if not row:
        return None

    # Check soft revocation
    if row["revoked_at"] is not None:
        return None

    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "picture_url": row["picture_url"],
        "role": row["role"],
        "quota_ram_mb": row["quota_ram_mb"],
        "quota_cpu_cores": row["quota_cpu_cores"],
        "quota_disk_gb": row["quota_disk_gb"],
        "raw_token": raw_token,
    }


def require_role(req: falcon.Request, min_role: str = "user") -> dict:
    """
    Inline role enforcement for API resources.
    Requires `req.context.user` to be set by AuthMiddleware.
    Raises falcon.HTTPForbidden (403) if user role does not meet min_role requirements.

    Role Hierarchy: 'admin' > 'user'
    """
    user = getattr(req.context, "user", None)
    if not user:
        raise falcon.HTTPUnauthorized(
            title="Authentication Required",
            description="You must be logged in to access this resource."
        )

    user_role = user.get("role", "user")

    if min_role == "admin" and user_role != "admin":
        raise falcon.HTTPForbidden(
            title="Access Denied",
            description="Administrator privileges are required for this action."
        )

    return user
