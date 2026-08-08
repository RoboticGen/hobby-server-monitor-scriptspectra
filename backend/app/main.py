"""
backend/app/main.py

Falcon WSGI application factory.
Phase 0: Bare skeleton — just the app object + a /health route.
Routes for containers, auth, metrics etc. are added in later phases.
"""

import falcon

from app.config import settings


class HealthResource:
    """Simple liveness probe — no auth required."""

    def on_get(self, req, resp):
        resp.media = {
            "status": "ok",
            "env": settings.APP_ENV,
        }


def create_app() -> falcon.App:
    """
    Build and return the Falcon WSGI application.
    Middleware and routes are registered here.
    Additional routes will be added in Phase 2 (containers),
    Phase 4 (auth), Phase 5 (metrics), Phase 6 (terminal), Phase 7 (users).
    """
    app = falcon.App()

    # ── Health (open, no auth) ────────────────────────────────────────────────
    app.add_route("/health", HealthResource())

    return app


# WSGI entry point used by Gunicorn:
#   gunicorn app.main:application -w 2 -b 0.0.0.0:8000
application = create_app()
