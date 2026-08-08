"""
backend/tests/test_auth.py

Unit tests for authentication, session lookup, and RBAC require_role.
"""

import pytest
import falcon
from falcon import testing

from app.db import get_db
from app.util.auth_helpers import create_session, lookup_session, require_role, sign_token, unsign_token


def test_token_signing_and_unsigning():
    raw_token = "test-secret-token-12345"
    signed = sign_token(raw_token)
    assert signed != raw_token

    unsigned = unsign_token(signed)
    assert unsigned == raw_token


def test_invalid_token_signature_returns_none():
    assert unsign_token("invalid-signed-token-string") is None


def test_create_and_lookup_session():
    db = get_db()
    # Insert test user
    cursor = db.execute("INSERT OR REPLACE INTO users (email, name, role) VALUES ('auth_test@example.com', 'Auth Test', 'user')")
    user_id = cursor.lastrowid
    db.commit()

    # Create session
    signed_token = create_session(user_id, db)
    assert signed_token is not None

    # Lookup session
    user = lookup_session(signed_token, db)
    assert user is not None
    assert user["email"] == "auth_test@example.com"
    assert user["role"] == "user"


def test_require_role_rbac():
    class DummyContext:
        user = None

    class DummyReq:
        context = DummyContext()

    req = DummyReq()

    # Unauthenticated raises HTTPUnauthorized
    with pytest.raises(falcon.HTTPUnauthorized):
        require_role(req, "user")

    # User role satisfies min_role='user'
    req.context.user = {"role": "user"}
    assert require_role(req, "user")["role"] == "user"

    # User role fails min_role='admin'
    with pytest.raises(falcon.HTTPForbidden):
        require_role(req, "admin")

    # Admin role satisfies min_role='admin'
    req.context.user = {"role": "admin"}
    assert require_role(req, "admin")["role"] == "admin"
