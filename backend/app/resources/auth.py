"""
backend/app/resources/auth.py

Google OAuth 2.0 authentication handlers & user profile endpoint:
- GET /auth/google          -> Redirect to Google OAuth consent page
- GET /auth/google/callback -> Exchange code, perform bootstrap/invite check, create session cookie
- POST /auth/logout         -> Delete session row from SQLite, expire cookie
- GET /api/me               -> Return currently authenticated user profile & role
"""

import secrets
import urllib.parse
import logging
import requests
import falcon

from app.config import settings
from app.db import get_db
from app.util.auth_helpers import create_session, unsign_token, sign_token

log = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def get_ui_url(path: str = "") -> str:
    """Return full UI URL depending on APP_ENV (localhost:4321 for dev)."""
    base = "http://localhost:4321" if not settings.is_production else ""
    return f"{base}{path}"


class GoogleAuthRedirectResource:
    """Resource handler for GET /auth/google."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        state_nonce = secrets.token_urlsafe(32)
        signed_state = sign_token(state_nonce)

        params = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_nonce,
            "prompt": "select_account",
        }

        target_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

        # Set short-lived state nonce cookie (5 minutes)
        resp.set_cookie(
            name="oauth_state",
            value=signed_state,
            max_age=300,
            path="/",
            secure=settings.is_production,
            http_only=True,
            same_site="Lax",
        )

        raise falcon.HTTPFound(target_url)


class GoogleAuthCallbackResource:
    """Resource handler for GET /auth/google/callback."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        code = req.get_param("code")
        state_param = req.get_param("state")
        error_param = req.get_param("error")

        if error_param:
            log.warning("Google OAuth error parameter: %s", error_param)
            raise falcon.HTTPFound(get_ui_url(f"/?error={urllib.parse.quote(error_param)}"))

        if not code or not state_param:
            raise falcon.HTTPFound(get_ui_url("/?error=missing_code_or_state"))

        # Validate OAuth state nonce cookie to prevent CSRF
        signed_state_cookie = req.cookies.get("oauth_state")
        if not signed_state_cookie:
            log.warning("OAuth callback missing oauth_state cookie.")
            raise falcon.HTTPFound(get_ui_url("/?error=missing_state_cookie"))

        raw_state = unsign_token(signed_state_cookie, max_age_seconds=300)
        if not raw_state or raw_state != state_param:
            log.warning("OAuth state mismatch: cookie=%s vs param=%s", raw_state, state_param)
            raise falcon.HTTPFound(get_ui_url("/?error=csrf_state_mismatch"))

        # Exchange authorization code for access token
        token_payload = {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }

        try:
            token_resp = requests.post(GOOGLE_TOKEN_URL, data=token_payload, timeout=10)
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data["access_token"]
        except Exception as exc:
            log.error("Failed to exchange OAuth code: %s", exc)
            raise falcon.HTTPFound(get_ui_url("/?error=token_exchange_failed"))

        # Fetch user info from Google
        try:
            userinfo_resp = requests.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
        except Exception as exc:
            log.error("Failed to fetch Google userinfo: %s", exc)
            raise falcon.HTTPFound(get_ui_url("/?error=userinfo_failed"))

        email = userinfo.get("email", "").strip().lower()
        name = userinfo.get("name", "")
        picture_url = userinfo.get("picture", "")

        if not email:
            raise falcon.HTTPFound(get_ui_url("/?error=no_email_provided"))

        db = get_db()

        # ── Bootstrap Admin & Role Assignment Logic ─────────────────────────────
        assigned_role = "user"

        # Check if user already exists in DB
        existing_user = db.execute(
            "SELECT id, role, revoked_at FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing_user and existing_user["revoked_at"] is not None:
            raise falcon.HTTPFound(get_ui_url("/?error=account_revoked"))

        # Rule 1: Env Var Admin
        if email == settings.BOOTSTRAP_ADMIN_EMAIL.strip().lower():
            assigned_role = "admin"
        else:
            # Rule 2: One-shot first user wins bootstrap
            bootstrap_row = db.execute("SELECT fired FROM bootstrap_state WHERE id = 1").fetchone()
            if bootstrap_row and bootstrap_row["fired"] == 0:
                assigned_role = "admin"
                db.execute("UPDATE bootstrap_state SET fired = 1 WHERE id = 1")
                log.info("Bootstrap admin granted to first user: %s", email)
            elif existing_user:
                assigned_role = existing_user["role"]
            else:
                # Rule 3: User not in DB and bootstrap fired -> Not Invited
                log.warning("Uninvited user sign-in attempt: %s", email)
                raise falcon.HTTPFound(get_ui_url("/?error=not_invited"))

        # Upsert user record
        if existing_user:
            user_id = existing_user["id"]
            db.execute(
                "UPDATE users SET name = ?, picture_url = ?, role = ? WHERE id = ?",
                (name, picture_url, assigned_role, user_id)
            )
        else:
            cursor = db.execute(
                "INSERT INTO users (email, name, picture_url, role) VALUES (?, ?, ?, ?)",
                (email, name, picture_url, assigned_role)
            )
            user_id = cursor.lastrowid

        # Create session row & signed cookie
        signed_session_token = create_session(user_id, db)

        # Set HTTP-only session cookie (7 days)
        resp.set_cookie(
            name="session_token",
            value=signed_session_token,
            max_age=86400 * 7,
            path="/",
            secure=settings.is_production,
            http_only=True,
            same_site="Strict",
        )

        # Clear state cookie
        resp.unset_cookie("oauth_state")

        # Redirect to Dashboard UI
        raise falcon.HTTPFound(get_ui_url("/dashboard"))


class LogoutResource:
    """Resource handler for POST /auth/logout."""

    def on_post(self, req: falcon.Request, resp: falcon.Response):
        cookies = req.cookies
        signed_token = cookies.get("session_token")

        if signed_token:
            raw_token = unsign_token(signed_token)
            if raw_token:
                db = get_db()
                db.execute("DELETE FROM sessions WHERE token = ?", (raw_token,))
                db.commit()

        # Clear session cookie by setting max_age=0
        resp.unset_cookie("session_token")
        resp.media = {"message": "Logged out successfully."}


class UserMeResource:
    """Resource handler for GET /api/me."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        user = getattr(req.context, "user", None)
        if not user:
            raise falcon.HTTPUnauthorized(title="Not Logged In", description="No active session.")

        resp.media = {
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "picture_url": user["picture_url"],
                "role": user["role"],
                "quotas": {
                    "ram_mb": user["quota_ram_mb"],
                    "cpu_cores": user["quota_cpu_cores"],
                    "disk_gb": user["quota_disk_gb"],
                }
            }
        }
