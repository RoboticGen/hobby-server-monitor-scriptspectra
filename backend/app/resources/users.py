"""
backend/app/resources/users.py

Falcon API endpoints for Admin User Management and Container Assignments:
- GET /api/users                     -> List all users with quotas and assigned containers
- POST /api/users/invite              -> Invite new user by email with default quotas
- PATCH /api/users/{id}              -> Update user role or quota limits
- DELETE /api/users/{id}             -> Revoke user access (soft-delete + delete sessions)
- GET /api/users/{id}/assignments    -> List container access assignments for user
- POST /api/users/{id}/assignments   -> Grant container access to user (with quota check)
- DELETE /api/users/{id}/assignments/{name} -> Revoke container access
"""

import json
import logging
import falcon

from app.db import get_db
from app.util.auth_helpers import require_role
from app.util.quota import get_user_used, check_quota

log = logging.getLogger(__name__)


class UserCollection:
    """Resource handler for /api/users."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        """List all users (Admin only)."""
        require_role(req, "admin")
        db = get_db()

        users_rows = db.execute(
            "SELECT id, email, name, picture_url, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb, created_at, revoked_at FROM users ORDER BY created_at DESC"
        ).fetchall()

        users = []
        for u in users_rows:
            user_id = u["id"]
            used = get_user_used(db, user_id)

            # Get assigned container names
            assign_rows = db.execute(
                "SELECT container_name FROM assignments WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,)
            ).fetchall()
            assigned_containers = [r["container_name"] for r in assign_rows]

            users.append({
                "id": user_id,
                "email": u["email"],
                "name": u["name"],
                "picture_url": u["picture_url"],
                "role": u["role"],
                "quotas": {
                    "ram_mb": u["quota_ram_mb"],
                    "cpu_cores": u["quota_cpu_cores"],
                    "disk_gb": u["quota_disk_gb"],
                },
                "used": used,
                "assigned_containers": assigned_containers,
                "created_at": u["created_at"],
                "is_revoked": u["revoked_at"] is not None,
            })

        resp.media = {
            "count": len(users),
            "users": users
        }

    def on_post(self, req: falcon.Request, resp: falcon.Response):
        """Invite a new user by email (Admin only)."""
        actor = require_role(req, "admin")
        data = req.media or {}

        email = data.get("email", "").strip().lower()
        name = data.get("name", "")
        role = data.get("role", "user").strip().lower()
        quota_ram_mb = int(data.get("quota_ram_mb", 2048))
        quota_cpu_cores = int(data.get("quota_cpu_cores", 2))
        quota_disk_gb = int(data.get("quota_disk_gb", 20))

        if not email or "@" not in email:
            raise falcon.HTTPBadRequest(
                title="Invalid Email",
                description="Please provide a valid email address."
            )

        if role not in ("admin", "user"):
            raise falcon.HTTPBadRequest(
                title="Invalid Role",
                description="Role must be either 'admin' or 'user'."
            )

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise falcon.HTTPConflict(
                title="User Already Exists",
                description=f"User with email '{email}' is already registered or invited."
            )

        cursor = db.execute(
            """INSERT INTO users
               (email, name, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (email, name, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb)
        )
        new_id = cursor.lastrowid

        # Write Audit Log
        db.execute(
            "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (?, ?, ?, ?)",
            (actor["id"], "user.invite", email, json.dumps({"role": role, "ram_mb": quota_ram_mb}))
        )
        db.commit()

        resp.status = falcon.HTTP_201
        resp.media = {
            "message": f"User '{email}' invited successfully.",
            "user": {
                "id": new_id,
                "email": email,
                "role": role,
                "quotas": {
                    "ram_mb": quota_ram_mb,
                    "cpu_cores": quota_cpu_cores,
                    "disk_gb": quota_disk_gb,
                }
            }
        }


class UserResource:
    """Resource handler for /api/users/{id}."""

    def on_patch(self, req: falcon.Request, resp: falcon.Response, id: str):
        """Update user role or resource quota limits (Admin only)."""
        actor = require_role(req, "admin")
        try:
            user_id = int(id)
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid User ID", description="User ID must be an integer.")

        data = req.media or {}
        role = data.get("role")
        ram_mb = data.get("quota_ram_mb")
        cpu_cores = data.get("quota_cpu_cores")
        disk_gb = data.get("quota_disk_gb")

        db = get_db()
        target_user = db.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target_user:
            raise falcon.HTTPNotFound(title="User Not Found", description=f"User with ID {user_id} not found.")

        updates = []
        params = []
        if role is not None:
            if role not in ("admin", "user"):
                raise falcon.HTTPBadRequest(title="Invalid Role", description="Role must be 'admin' or 'user'.")
            updates.append("role = ?")
            params.append(role)
        if ram_mb is not None:
            updates.append("quota_ram_mb = ?")
            params.append(int(ram_mb))
        if cpu_cores is not None:
            updates.append("quota_cpu_cores = ?")
            params.append(int(cpu_cores))
        if disk_gb is not None:
            updates.append("quota_disk_gb = ?")
            params.append(int(disk_gb))

        if updates:
            params.append(user_id)
            db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
            db.execute(
                "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (?, ?, ?, ?)",
                (actor["id"], "user.update", target_user["email"], json.dumps(data))
            )
            db.commit()

        resp.media = {"message": f"User {user_id} updated successfully."}

    def on_delete(self, req: falcon.Request, resp: falcon.Response, id: str):
        """Revoke user access (soft-delete user & delete all active sessions) (Admin only)."""
        actor = require_role(req, "admin")
        try:
            user_id = int(id)
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid User ID", description="User ID must be an integer.")

        db = get_db()
        target_user = db.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target_user:
            raise falcon.HTTPNotFound(title="User Not Found", description=f"User with ID {user_id} not found.")

        # Soft-revoke user
        db.execute("UPDATE users SET revoked_at = datetime('now') WHERE id = ?", (user_id,))
        # Cascade delete active sessions
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.execute(
            "INSERT INTO audit_log (actor_id, action, target) VALUES (?, ?, ?)",
            (actor["id"], "user.revoke", target_user["email"])
        )
        db.commit()

        resp.media = {"message": f"User '{target_user['email']}' access revoked successfully."}


class UserAssignmentsResource:
    """Resource handler for /api/users/{id}/assignments."""

    def on_get(self, req: falcon.Request, resp: falcon.Response, id: str):
        """List container assignments for user (Admin only)."""
        require_role(req, "admin")
        try:
            user_id = int(id)
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid User ID", description="User ID must be an integer.")

        db = get_db()
        assign_rows = db.execute(
            "SELECT id, container_name, granted_at FROM assignments WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,)
        ).fetchall()

        resp.media = {
            "user_id": user_id,
            "assignments": [dict(r) for r in assign_rows]
        }

    def on_post(self, req: falcon.Request, resp: falcon.Response, id: str):
        """Grant container access assignment to user (Admin only)."""
        actor = require_role(req, "admin")
        try:
            user_id = int(id)
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid User ID", description="User ID must be an integer.")

        data = req.media or {}
        container_name = data.get("container_name", "").strip()

        if not container_name:
            raise falcon.HTTPBadRequest(title="Container Name Required", description="Specify container_name to assign.")

        db = get_db()
        target_user = db.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target_user:
            raise falcon.HTTPNotFound(title="User Not Found", description=f"User ID {user_id} not found.")

        container_row = db.execute("SELECT name, ram_mb, cpu_cores, disk_gb FROM containers WHERE name = ? AND deleted_at IS NULL", (container_name,)).fetchone()
        if not container_row:
            raise falcon.HTTPNotFound(title="Container Not Found", description=f"Active container '{container_name}' not found.")

        # Check user quota before assigning container
        check_quota(
            db,
            user_id,
            req_ram_mb=container_row["ram_mb"],
            req_cpu_cores=container_row["cpu_cores"],
            req_disk_gb=container_row["disk_gb"],
        )

        # Check existing assignment
        existing = db.execute(
            "SELECT id FROM assignments WHERE user_id = ? AND container_name = ? AND revoked_at IS NULL",
            (user_id, container_name)
        ).fetchone()

        if existing:
            raise falcon.HTTPConflict(title="Already Assigned", description=f"Container '{container_name}' is already assigned to this user.")

        db.execute(
            "INSERT INTO assignments (user_id, container_name) VALUES (?, ?)",
            (user_id, container_name)
        )
        db.execute(
            "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (?, ?, ?, ?)",
            (actor["id"], "user.grant_assignment", target_user["email"], json.dumps({"container": container_name}))
        )
        db.commit()

        resp.status = falcon.HTTP_201
        resp.media = {"message": f"Assigned container '{container_name}' to user '{target_user['email']}'."}

    def on_delete(self, req: falcon.Request, resp: falcon.Response, id: str, name: str):
        """Revoke container assignment for user (Admin only)."""
        actor = require_role(req, "admin")
        try:
            user_id = int(id)
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid User ID", description="User ID must be an integer.")

        db = get_db()
        db.execute(
            "UPDATE assignments SET revoked_at = datetime('now') WHERE user_id = ? AND container_name = ? AND revoked_at IS NULL",
            (user_id, name)
        )
        db.commit()

        resp.media = {"message": f"Revoked container '{name}' from user {user_id}."}
