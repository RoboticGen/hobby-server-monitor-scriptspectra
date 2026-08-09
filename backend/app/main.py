"""
backend/app/main.py

Falcon WSGI application factory.
Registers middleware and HTTP resources.
"""

import falcon

from app.config import settings
from app.db import close_db
from app.middleware.cors import CORSMiddleware
from app.middleware.auth import AuthMiddleware
from app.resources.auth import (
    GoogleAuthRedirectResource,
    GoogleAuthCallbackResource,
    LogoutResource,
    UserMeResource,
)
from app.resources.containers import (
    ContainerCollection,
    ContainerResource,
    ContainerAction,
)
from app.resources.metrics import (
    MetricsLiveResource,
    MetricsHistoryResource,
)
from app.resources.users import (
    UserCollection,
    UserResource,
    UserAssignmentsResource,
)
from app.resources.terminal import TerminalExecResource


class RootResource:
    """Redirect root GET / to the Astro Dashboard UI."""

    def on_get(self, req, resp):
        target = "http://localhost:4321" if not settings.is_production else "/dashboard"
        raise falcon.HTTPFound(target)


class DashboardRedirectResource:
    """Redirect GET /dashboard on backend port to Astro Dashboard UI port."""

    def on_get(self, req, resp):
        target = "http://localhost:4321/dashboard" if not settings.is_production else "/dashboard"
        raise falcon.HTTPFound(target)


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
    app = falcon.App(
        media_type=falcon.MEDIA_JSON,
        middleware=[
            CORSMiddleware(),
            AuthMiddleware(),
            DBCloseMiddleware(),
        ]
    )

    # ── Root & Health ─────────────────────────────────────────────────────────
    app.add_route("/", RootResource())
    app.add_route("/dashboard", DashboardRedirectResource())
    app.add_route("/health", HealthResource())

    # ── Auth Endpoints ────────────────────────────────────────────────────────
    app.add_route("/auth/google", GoogleAuthRedirectResource())
    app.add_route("/auth/google/callback", GoogleAuthCallbackResource())
    app.add_route("/auth/logout", LogoutResource())

    # ── User Profile & User Management ────────────────────────────────────────
    app.add_route("/api/me", UserMeResource())
    app.add_route("/api/users", UserCollection())
    app.add_route("/api/users/{id}", UserResource())
    app.add_route("/api/users/{id}/assignments", UserAssignmentsResource())
    app.add_route("/api/users/{id}/assignments/{name}", UserAssignmentsResource())

    # ── Containers CRUD & Actions ─────────────────────────────────────────────
    app.add_route("/api/containers", ContainerCollection())
    app.add_route("/api/containers/{name}", ContainerResource())
    app.add_route("/api/containers/{name}/action", ContainerAction())

    # ── Metrics Endpoints ─────────────────────────────────────────────────────
    app.add_route("/api/metrics/{name}/live", MetricsLiveResource())
    app.add_route("/api/metrics/{name}/history", MetricsHistoryResource())

    # ── Terminal Exec ─────────────────────────────────────────────────────────
    # POST /api/terminal/{name}/exec
    # Authenticated, authorized, audited command execution inside LXD containers.
    # See backend/app/resources/terminal.py for full security design documentation.
    app.add_route("/api/terminal/{name}/exec", TerminalExecResource())

    return app


# WSGI entry point used by Gunicorn:
#   gunicorn app.main:application -w 2 -b 0.0.0.0:8000
application = create_app()
