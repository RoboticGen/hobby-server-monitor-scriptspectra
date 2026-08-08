"""
backend/app/resources/metrics.py

Falcon API endpoints for querying time-series metrics from TinyFlux:
- GET /api/metrics/{name}/live    -> Latest metric sample
- GET /api/metrics/{name}/history -> Historical samples over specified period (1h, 6h, 24h, 7d)
"""

import gzip
import json
import logging
from datetime import datetime, timedelta, timezone
import falcon

from app.db import get_db
from app.tsdb import get_latest_metric, get_metric_history
from app.util.validators import validate_container_name
from app.util.auth_helpers import require_role

log = logging.getLogger(__name__)


def check_container_access(db, user: dict, container_name: str) -> None:
    """
    Verify user has access to container_name.
    Admins can access all containers.
    Standard users can only access containers assigned to them in the assignments table.
    """
    if user.get("role") == "admin":
        return

    # Check active assignment for standard user
    query = """
        SELECT a.id
        FROM assignments a
        JOIN containers c ON a.container_name = c.name
        WHERE a.user_id = ?
          AND a.container_name = ?
          AND a.revoked_at IS NULL
          AND c.deleted_at IS NULL
    """
    row = db.execute(query, (user["id"], container_name)).fetchone()
    if not row:
        raise falcon.HTTPForbidden(
            title="Access Denied",
            description=f"You do not have access to container '{container_name}'."
        )


class MetricsLiveResource:
    """Resource handler for GET /api/metrics/{name}/live."""

    def on_get(self, req: falcon.Request, resp: falcon.Response, name: str):
        name = validate_container_name(name)
        user = require_role(req, "user")
        db = get_db()

        check_container_access(db, user, name)

        latest = get_latest_metric(name)

        if not latest:
            resp.media = {
                "container_name": name,
                "state": "Unknown",
                "message": "No metrics recorded yet.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cpu_percent": 0.0,
                "ram_used_mb": 0.0,
                "disk_used_gb": 0.0,
                "net_rx_rate_bps": 0.0,
                "net_tx_rate_bps": 0.0,
                "process_count": 0,
            }
            return

        resp.media = {
            "metrics": latest
        }


class MetricsHistoryResource:
    """Resource handler for GET /api/metrics/{name}/history."""

    PERIOD_MAP = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }

    def on_get(self, req: falcon.Request, resp: falcon.Response, name: str):
        name = validate_container_name(name)
        user = require_role(req, "user")
        db = get_db()

        check_container_access(db, user, name)

        period = req.get_param("period", default="1h").strip().lower()
        if period not in self.PERIOD_MAP:
            allowed = ", ".join(sorted(self.PERIOD_MAP.keys()))
            raise falcon.HTTPBadRequest(
                title="Invalid Period",
                description=f"Period '{period}' is not supported. Allowed values: {allowed}."
            )

        time_delta = self.PERIOD_MAP[period]
        since_dt = datetime.now(timezone.utc) - time_delta

        history = get_metric_history(name, since_dt=since_dt)

        response_payload = {
            "container_name": name,
            "period": period,
            "count": len(history),
            "samples": history,
        }

        # Handle gzip compression if client accepts gzip encoding
        accept_encoding = req.get_header("Accept-Encoding", default="")
        if "gzip" in accept_encoding.lower():
            json_bytes = json.dumps(response_payload).encode("utf-8")
            compressed = gzip.compress(json_bytes)

            resp.set_header("Content-Encoding", "gzip")
            resp.set_header("Content-Type", "application/json; charset=utf-8")
            resp.data = compressed
        else:
            resp.media = response_payload
