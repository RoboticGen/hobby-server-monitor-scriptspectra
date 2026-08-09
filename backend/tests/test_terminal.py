"""
backend/tests/test_terminal.py

Unit tests for Phase 6 — Terminal exec endpoint security pipeline.

Tests cover:
  - Unauthenticated request → 401
  - Command length cap (> 512 chars) → 400
  - Empty command → 400
  - Container not found in DB → 404
  - Container access denied for non-assigned user → 403
  - Container not running → 409
  - Successful exec returns required fields
"""

import json
import sqlite3
import pytest
import falcon
from falcon import testing
from unittest.mock import MagicMock, patch

from app.main import create_app
from app.db import _local


# ── Shared test fixtures ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_thread_local():
    """Reset thread-local DB connection between tests."""
    yield
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        del _local.conn


@pytest.fixture
def client():
    return testing.TestClient(create_app())


def _make_session(db: sqlite3.Connection, user_id: int, email: str, role: str = "user") -> str:
    """Insert a test user + session and return a signed session token."""
    import secrets
    from app.util.auth_helpers import sign_token

    db.execute(
        "INSERT OR IGNORE INTO users (id, email, name, role) VALUES (?, ?, ?, ?)",
        (user_id, email, "Test User", role)
    )
    raw_token = secrets.token_hex(32)
    from datetime import datetime, timedelta, timezone
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (raw_token, user_id, expires)
    )
    db.commit()
    return sign_token(raw_token)


def _make_container(db: sqlite3.Connection, name: str, created_by: int | None = None):
    """Insert a test container record. created_by defaults to NULL to avoid FK issues."""
    db.execute(
        "INSERT OR IGNORE INTO containers (name, description, created_by, ram_mb, cpu_cores, disk_gb) "
        "VALUES (?, ?, ?, 512, 1, 5)",
        (name, "test container", created_by)
    )
    db.commit()


def _make_assignment(db: sqlite3.Connection, user_id: int, container_name: str):
    """Assign a container to a user."""
    db.execute(
        "INSERT OR IGNORE INTO assignments (user_id, container_name) VALUES (?, ?)",
        (user_id, container_name)
    )
    db.commit()


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestTerminalExecAuth:

    def test_unauthenticated_returns_401(self, client):
        """Requests without session cookie must be rejected with 401."""
        result = client.simulate_post("/api/terminal/my-box/exec",
                                      json={"command": "ls"})
        assert result.status_code == 401

    def test_invalid_session_returns_401(self, client):
        """Tampered or expired session tokens must be rejected."""
        result = client.simulate_post(
            "/api/terminal/my-box/exec",
            json={"command": "ls"},
            headers={"Cookie": "session_token=tampered.value.here"}
        )
        assert result.status_code == 401


class TestTerminalExecCommandValidation:

    def test_command_over_512_chars_returns_400(self, client):
        """Commands longer than 512 characters must return 400 Bad Request."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7001, email="cmd_cap@test.com", role="admin")
        _make_container(db, "cap-test-box", created_by=7001)

        long_command = "echo " + "a" * 510  # total > 512
        result = client.simulate_post(
            "/api/terminal/cap-test-box/exec",
            json={"command": long_command},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 400
        assert "512" in result.json.get("description", "")

    def test_empty_command_returns_400(self, client):
        """Empty command string must return 400 Bad Request."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7002, email="empty_cmd@test.com", role="admin")
        _make_container(db, "empty-cmd-box", created_by=7002)

        result = client.simulate_post(
            "/api/terminal/empty-cmd-box/exec",
            json={"command": ""},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 400

    def test_whitespace_only_command_returns_400(self, client):
        """Whitespace-only command must return 400."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7003, email="ws_cmd@test.com", role="admin")
        _make_container(db, "ws-cmd-box", created_by=7003)

        result = client.simulate_post(
            "/api/terminal/ws-cmd-box/exec",
            json={"command": "   "},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 400


class TestTerminalExecContainerChecks:

    def test_container_not_in_db_returns_404(self, client):
        """Exec on a non-existent container must return 404."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7010, email="no_ct@test.com", role="admin")

        result = client.simulate_post(
            "/api/terminal/does-not-exist/exec",
            json={"command": "ls"},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 404

    def test_user_without_assignment_is_403(self, client):
        """Standard user without container assignment must get 403 Forbidden."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7020, email="unassigned@test.com", role="user")
        _make_container(db, "forbidden-box")  # no created_by to avoid FK constraint
        # deliberately no assignment for user 7020

        result = client.simulate_post(
            "/api/terminal/forbidden-box/exec",
            json={"command": "ls"},
            headers={"Cookie": f"session_token={token}"}
        )
        assert result.status_code == 403
        assert "assignment" in result.json.get("description", "").lower()

    def test_admin_can_access_any_container(self, client):
        """Admin user should not be blocked by container access check."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7030, email="admin_exec@test.com", role="admin")
        _make_container(db, "admin-exec-box")  # no created_by needed for admin access test

        # Mock LXD container as running and returning exec result
        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.stdout = "hello"
        mock_exec.stderr = ""

        mock_ct = MagicMock()
        mock_ct.status = "Running"
        mock_ct.execute.return_value = mock_exec

        with patch("app.resources.terminal.get_client") as mock_client:
            mock_client.return_value.containers.get.return_value = mock_ct
            result = client.simulate_post(
                "/api/terminal/admin-exec-box/exec",
                json={"command": "echo hello"},
                headers={"Cookie": f"session_token={token}"}
            )

        assert result.status_code == 200
        assert result.json["exit_code"] == 0
        assert result.json["stdout"] == "hello"


class TestTerminalExecSuccess:

    def test_successful_exec_returns_all_fields(self, client):
        """Successful exec must return container, command, exit_code, stdout, stderr, truncated."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7040, email="exec_ok@test.com", role="user")
        _make_container(db, "exec-ok-box", created_by=7040)
        _make_assignment(db, user_id=7040, container_name="exec-ok-box")

        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.stdout = "/var/log\n"
        mock_exec.stderr = ""

        mock_ct = MagicMock()
        mock_ct.status = "Running"
        mock_ct.execute.return_value = mock_exec

        with patch("app.resources.terminal.get_client") as mock_client:
            mock_client.return_value.containers.get.return_value = mock_ct
            result = client.simulate_post(
                "/api/terminal/exec-ok-box/exec",
                json={"command": "ls /var/log"},
                headers={"Cookie": f"session_token={token}"}
            )

        assert result.status_code == 200
        data = result.json
        assert data["container"] == "exec-ok-box"
        assert data["command"] == "ls /var/log"
        assert data["exit_code"] == 0
        assert data["stdout"] == "/var/log\n"
        assert data["stderr"] == ""
        assert data["truncated"] is False

    def test_nonzero_exit_code_is_returned(self, client):
        """Non-zero exit code from container must be reported faithfully."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7050, email="exit_nonzero@test.com", role="admin")
        _make_container(db, "nonzero-box", created_by=7050)

        mock_exec = MagicMock()
        mock_exec.exit_code = 127
        mock_exec.stdout = ""
        mock_exec.stderr = "command not found\n"

        mock_ct = MagicMock()
        mock_ct.status = "Running"
        mock_ct.execute.return_value = mock_exec

        with patch("app.resources.terminal.get_client") as mock_client:
            mock_client.return_value.containers.get.return_value = mock_ct
            result = client.simulate_post(
                "/api/terminal/nonzero-box/exec",
                json={"command": "notacommand"},
                headers={"Cookie": f"session_token={token}"}
            )

        assert result.status_code == 200
        assert result.json["exit_code"] == 127
        assert "not found" in result.json["stderr"]

    def test_output_truncation_flag(self, client):
        """Output exceeding 64KB must set truncated=True in the response."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7060, email="trunc@test.com", role="admin")
        _make_container(db, "trunc-box", created_by=7060)

        huge_output = "x" * 100_000  # > 64 KB

        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.stdout = huge_output
        mock_exec.stderr = ""

        mock_ct = MagicMock()
        mock_ct.status = "Running"
        mock_ct.execute.return_value = mock_exec

        with patch("app.resources.terminal.get_client") as mock_client:
            mock_client.return_value.containers.get.return_value = mock_ct
            result = client.simulate_post(
                "/api/terminal/trunc-box/exec",
                json={"command": "cat /dev/urandom"},
                headers={"Cookie": f"session_token={token}"}
            )

        assert result.status_code == 200
        assert result.json["truncated"] is True
        assert len(result.json["stdout"]) <= 65_536

    def test_audit_log_written_on_success(self, client):
        """Successful exec must write an entry to audit_log."""
        from app.db import get_db
        db = get_db()
        token = _make_session(db, user_id=7070, email="audit_ok@test.com", role="admin")
        _make_container(db, "audit-ok-box", created_by=7070)

        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.stdout = "audit test"
        mock_exec.stderr = ""

        mock_ct = MagicMock()
        mock_ct.status = "Running"
        mock_ct.execute.return_value = mock_exec

        before_count = db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='terminal.exec'"
        ).fetchone()[0]

        with patch("app.resources.terminal.get_client") as mock_client:
            mock_client.return_value.containers.get.return_value = mock_ct
            result = client.simulate_post(
                "/api/terminal/audit-ok-box/exec",
                json={"command": "echo audit test"},
                headers={"Cookie": f"session_token={token}"}
            )

        assert result.status_code == 200
        # Re-open DB (DBCloseMiddleware closes it after each request)
        db2 = get_db()
        after_count = db2.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='terminal.exec'"
        ).fetchone()[0]
        assert after_count == before_count + 1
