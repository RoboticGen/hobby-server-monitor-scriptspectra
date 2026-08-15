"""
backend/app/resources/audit.py

Falcon API endpoint for fetching system audit logs:
- GET /api/audit -> Returns system-wide audit logs (Admin only)
"""

import json
import logging
import falcon

from app.db import get_db
from app.util.auth_helpers import require_role

log = logging.getLogger(__name__)


class AuditCollectionResource:
    """Resource handler for GET /api/audit."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        """List all system audit logs (Admin only)."""
        require_role(req, "admin")
        db = get_db()

        # Query latest 500 audit logs, left joining users to resolve actor profile details
        query = """
            SELECT a.id, a.actor_id, a.action, a.target, a.detail, a.at,
                   u.email AS actor_email, u.name AS actor_name
            FROM audit_log a
            LEFT JOIN users u ON a.actor_id = u.id
            ORDER BY a.at DESC
            LIMIT 500
        """
        rows = db.execute(query).fetchall()

        logs = []
        for r in rows:
            detail = None
            if r["detail"]:
                try:
                    detail = json.loads(r["detail"])
                except Exception:
                    detail = r["detail"]

            logs.append({
                "id": r["id"],
                "actor_id": r["actor_id"],
                "action": r["action"],
                "target": r["target"],
                "detail": detail,
                "at": r["at"],
                "actor_email": r["actor_email"] or "System",
                "actor_name": r["actor_name"] or "System Daemon",
            })

        resp.media = {
            "audit_logs": logs
        }
