"""
backend/app/main.py

Falcon WSGI application factory.
Registers HTTP resources and middleware.
"""

import falcon

from app.config import settings
from app.db import close_db
from app.resources.containers import (
    ContainerCollection,
    ContainerResource,
    ContainerAction,
)


class HealthResource:
    """Simple liveness probe — no auth required."""

    def on_get(self, req, resp):
        resp.media = {
            "status": "ok",
            "env": settings.APP_ENV,
        }


class DBCloseMiddleware:
    """Ensure thread-local SQLite connection is closed after each request."""

    def process_response(self, req, resp, resource, req_succeeded):
        close_db()


def create_app() -> falcon.App:
    """
    Build and return the Falcon WSGI application.
    Middleware and routes are registered here.
    """
    app = falcon.App(middleware=[DBCloseMiddleware()])

    # ── Health (open, no auth) ────────────────────────────────────────────────
    app.add_route("/health", HealthResource())

    # ── Containers CRUD & Actions ─────────────────────────────────────────────
    app.add_route("/api/containers", ContainerCollection())
    app.add_route("/api/containers/{name}", ContainerResource())
    app.add_route("/api/containers/{name}/action", ContainerAction())

    return app


# WSGI entry point used by Gunicorn:
#   gunicorn app.main:application -w 2 -b 0.0.0.0:8000
application = create_app()
