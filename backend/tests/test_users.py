"""
backend/tests/test_users.py

Unit tests for Phase 7 — User Management & Host Accounting.

Tests cover:
  - Non-admin user access to /api/users -> 403 Forbidden
  - Admin GET /api/users -> returns user list with quotas & assignments
  - Admin POST /api/users/invite -> invites user with custom quotas
  - Duplicate user invite -> 409 Conflict
  - Admin PATCH /api/users/{id} -> updates role and quotas
  - Admin DELETE /api/users/{id} -> revokes user access & clears sessions
  - Admin GET/POST/DELETE /api/users/{id}/assignments -> grants/revokes container access
  - Container assignment quota enforcement -> 409 Conflict when quota exceeded
  - Admin GET /api/host -> returns host capacity & allocation metrics
"""

import json
import sqlite3
import pytest
from falcon import testing

from app.main import create_app


@pytest.fixture
def client():
    return testing.TestClient(create_app())


def _make_session(db: sqlite3.Connection, user_id: int, email: str, role: str = "user") -> str:
    """Insert a test user + session and return a signed session token."""
    import secrets
    from datetime import datetime, timedelta, timezone
    from app.util.auth_helpers import sign_token

    db.execute(
        "INSERT OR REPLACE INTO users (id, email, name, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb) VALUES (?, ?, ?, ?, 2048, 2, 20)",
        (user_id, email, "Test User", role)
    )
    raw_token = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (raw_token, user_id, expires)
    )
    db.commit()
    return sign_token(raw_token)


def _make_container(db: sqlite3.Connection, name: str, ram_mb: int = 512, cpu_cores: int = 1, disk_gb: int = 5):
    """Insert a test container record into DB."""
    db.execute(
        "INSERT OR IGNORE INTO containers (name, description, created_by, ram_mb, cpu_cores, disk_gb) VALUES (?, 'test container', 1, ?, ?, ?)",
        (name, ram_mb, cpu_cores, disk_gb)
    )
    db.commit()


class TestUserManagementRBAC:

    def test_non_admin_get_users_returns_403(self, client):
        """Standard user attempting to view user list must be rejected with 403."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9001, email="user_list_std@test.com", role="user")

        result = client.simulate_get("/api/users", headers={"Cookie": f"session_token={token}"})
        assert result.status_code == 403

    def test_admin_get_users_returns_user_list(self, client):
        """Admin requesting user list gets 200 OK with users array."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9002, email="user_list_admin@test.com", role="admin")

        result = client.simulate_get("/api/users", headers={"Cookie": f"session_token={token}"})
        assert result.status_code == 200
        assert "users" in result.json
        assert result.json["count"] >= 1


class TestUserInvite:

    def test_admin_invite_user_success(self, client):
        """Admin can invite a new user email with custom quotas."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9010, email="invite_admin@test.com", role="admin")

        result = client.simulate_post(
            "/api/users/invite",
            json={
                "email": "invited_new@test.com",
                "name": "Invited User",
                "role": "user",
                "quota_ram_mb": 4096,
                "quota_cpu_cores": 4,
                "quota_disk_gb": 40
            },
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 201
        assert result.json["user"]["email"] == "invited_new@test.com"
        assert result.json["user"]["quotas"]["ram_mb"] == 4096

    def test_invite_duplicate_email_returns_409(self, client):
        """Inviting an email that already exists must return 409 Conflict."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9011, email="invite_dup_admin@test.com", role="admin")
        _make_session(db, user_id=9012, email="existing_user@test.com", role="user")

        result = client.simulate_post(
            "/api/users/invite",
            json={"email": "existing_user@test.com"},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 409


class TestUserUpdateAndRevoke:

    def test_admin_patch_user_quotas(self, client):
        """Admin can update a user's role and quota limits."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9020, email="patch_admin@test.com", role="admin")
        _make_session(db, user_id=9021, email="patch_target@test.com", role="user")

        result = client.simulate_patch(
            "/api/users/9021",
            json={"quota_ram_mb": 8192, "quota_cpu_cores": 4},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 200

        # Verify DB
        row = db.execute("SELECT quota_ram_mb, quota_cpu_cores FROM users WHERE id = 9021").fetchone()
        assert row["quota_ram_mb"] == 8192
        assert row["quota_cpu_cores"] == 4

    def test_admin_delete_user_revokes_access(self, client):
        """Admin deleting a user sets revoked_at and clears session tokens."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9030, email="del_admin@test.com", role="admin")
        target_token = _make_session(db, user_id=9031, email="del_target@test.com", role="user")

        result = client.simulate_delete(
            "/api/users/9031",
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 200

        # Verify revoked in DB
        u_row = db.execute("SELECT revoked_at FROM users WHERE id = 9031").fetchone()
        assert u_row["revoked_at"] is not None

        # Verify sessions deleted
        s_count = db.execute("SELECT COUNT(*) AS c FROM sessions WHERE user_id = 9031").fetchone()["c"]
        assert s_count == 0


class TestUserAssignments:

    def test_assign_and_revoke_container(self, client):
        """Admin can grant and revoke container access assignments."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9040, email="assign_admin@test.com", role="admin")
        _make_session(db, user_id=9041, email="assign_user@test.com", role="user")
        _make_container(db, "assign-box-01", ram_mb=512, cpu_cores=1, disk_gb=5)

        # 1. Post assignment
        res1 = client.simulate_post(
            "/api/users/9041/assignments",
            json={"container_name": "assign-box-01"},
            headers={"Cookie": f"session_token={token}"}
        )
        assert res1.status_code == 201

        # 2. Get assignments
        res2 = client.simulate_get("/api/users/9041/assignments", headers={"Cookie": f"session_token={token}"})
        assert res2.status_code == 200
        assert len(res2.json["assignments"]) == 1

        # 3. Revoke assignment
        res3 = client.simulate_delete(
            "/api/users/9041/assignments/assign-box-01",
            headers={"Cookie": f"session_token={token}"}
        )
        assert res3.status_code == 200

    def test_assignment_exceeding_quota_returns_409(self, client):
        """Granting container assignment that exceeds user's remaining quota returns 409."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9050, email="quota_admin@test.com", role="admin")
        # User has RAM quota of 1024 MB
        db.execute(
            "INSERT OR REPLACE INTO users (id, email, name, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb) VALUES (9051, 'small_quota@test.com', 'Small Quota User', 'user', 1024, 1, 10)"
        )
        db.commit()
        # Container requires 2048 MB RAM
        _make_container(db, "huge-ram-box", ram_mb=2048, cpu_cores=1, disk_gb=5)

        result = client.simulate_post(
            "/api/users/9051/assignments",
            json={"container_name": "huge-ram-box"},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 409
        assert "Quota Exceeded" in result.json["title"]


class TestHostAccounting:

    def test_admin_get_host_accounting(self, client):
        """Admin requesting /api/host gets capacity and allocation breakdown."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=9060, email="host_admin@test.com", role="admin")

        result = client.simulate_get("/api/host", headers={"Cookie": f"session_token={token}"})
        assert result.status_code == 200
        assert "host_capacity" in result.json
        assert "container_allocated" in result.json
        assert "user_quota_allocated" in result.json
        assert "unallocated_host" in result.json


