"""
backend/tests/test_audit.py

Unit tests for system-wide Audit Logs and Terminal History.
"""

import json
import sqlite3
import pytest
from falcon import testing

from app.main import create_app
from tests.test_users import _make_session, _make_container


@pytest.fixture
def client():
    return testing.TestClient(create_app())


def test_audit_logs_rbac(client):
    from app.db import get_db
    db = get_db()

    # Clear previous audit logs
    db.execute("DELETE FROM audit_log")
    db.commit()

    # Test GET without session
    res = client.simulate_get("/api/audit")
    assert res.status_code == 401

    # Test GET with standard user
    user_token = _make_session(db, 100, "user@example.com", role="user")
    headers = {"Cookie": f"session_token={user_token}"}
    res = client.simulate_get("/api/audit", headers=headers)
    assert res.status_code == 403

    # Test GET with admin
    admin_token = _make_session(db, 101, "admin@example.com", role="admin")
    headers = {"Cookie": f"session_token={admin_token}"}

    # Insert a dummy audit log
    db.execute(
        "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (100, 'container.create', 'test-ct', '{\"ram_mb\": 512}')"
    )
    db.commit()

    res = client.simulate_get("/api/audit", headers=headers)
    assert res.status_code == 200
    data = res.json
    assert "audit_logs" in data
    assert len(data["audit_logs"]) >= 1
    assert data["audit_logs"][0]["action"] == "container.create"
    assert data["audit_logs"][0]["actor_email"] == "user@example.com"


def test_terminal_history(client):
    from app.db import get_db
    db = get_db()

    # Create user, admin, container, and assignment
    user_token = _make_session(db, 200, "user200@example.com", role="user")
    other_user_token = _make_session(db, 201, "user201@example.com", role="user")
    admin_token = _make_session(db, 202, "admin202@example.com", role="admin")

    _make_container(db, "test-ct-1")
    _make_container(db, "test-ct-2")

    # Assign test-ct-1 to user200
    db.execute(
        "INSERT OR IGNORE INTO assignments (user_id, container_name) VALUES (200, 'test-ct-1')"
    )
    db.commit()

    # Clear previous audit logs
    db.execute("DELETE FROM audit_log")
    db.commit()

    # Insert terminal exec logs
    # User 200 runs a command on test-ct-1
    db.execute(
        "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (200, 'terminal.exec', 'test-ct-1', '{\"command\": \"ls -la\", \"exit_code\": 0}')"
    )
    # User 201 runs a command on test-ct-1
    db.execute(
        "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (201, 'terminal.exec', 'test-ct-1', '{\"command\": \"whoami\", \"exit_code\": 0}')"
    )
    db.commit()

    # Test unauthenticated history access
    res = client.simulate_get("/api/terminal/test-ct-1/history")
    assert res.status_code == 401

    # Test standard user requesting unassigned container
    headers_other = {"Cookie": f"session_token={other_user_token}"}
    res = client.simulate_get("/api/terminal/test-ct-1/history", headers=headers_other)
    assert res.status_code == 403

    # Test standard user requesting assigned container (only sees their own history)
    headers_user = {"Cookie": f"session_token={user_token}"}
    res = client.simulate_get("/api/terminal/test-ct-1/history", headers=headers_user)
    assert res.status_code == 200
    data = res.json
    assert "history" in data
    assert len(data["history"]) == 1
    assert data["history"][0]["command"] == "ls -la"
    assert data["history"][0]["actor_email"] == "user200@example.com"

    # Test admin requesting container (sees both commands)
    headers_admin = {"Cookie": f"session_token={admin_token}"}
    res = client.simulate_get("/api/terminal/test-ct-1/history", headers=headers_admin)
    assert res.status_code == 200
    data = res.json
    assert "history" in data
    assert len(data["history"]) == 2
