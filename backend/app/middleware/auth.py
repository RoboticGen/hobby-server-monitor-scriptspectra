"""
backend/app/middleware/auth.py

Global Falcon AuthMiddleware.
Enforces authentication on all non-open routes using signed session cookies.
"""

import falcon
from app.db import get_db
from app.util.auth_helpers import lookup_session

OPEN_ROUTES = {"/", "/dashboard", "/auth/google", "/auth/google/callback", "/health"}


class AuthMiddleware:
    """
    Falcon Middleware running before every request handler.
    Checks HTTP-only signed session cookie, validates against SQLite DB,
    and attaches authenticated user to `req.context.user`.
    """

    def process_request(self, req: falcon.Request, resp: falcon.Response):
        # Open routes bypass authentication
        if req.path in OPEN_ROUTES:
            return

        # OPTIONS pre-flight requests bypass auth
        if req.method == "OPTIONS":
            return

        # Extract signed session cookie
        cookies = req.cookies
        signed_token = cookies.get("session_token")

        if not signed_token:
            raise falcon.HTTPUnauthorized(
                title="Authentication Required",
                description="Missing session cookie. Please sign in with Google."
            )

        db = get_db()
        user = lookup_session(signed_token, db)

        if not user:
            raise falcon.HTTPUnauthorized(
                title="Session Invalid or Expired",
                description="Your session has expired or is invalid. Please sign in again."
            )

        # Attach user to request context for downstream handlers
        req.context.user = user
