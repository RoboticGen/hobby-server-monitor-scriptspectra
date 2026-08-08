"""
backend/tests/test_quota.py

Unit tests for user resource quota checks.
"""

import pytest
import falcon
from app.db import get_db
from app.util.quota import check_quota, get_user_used


def test_quota_check_for_admin():
    db = get_db()
    # Insert admin user
    db.execute("INSERT OR REPLACE INTO users (id, email, role, quota_ram_mb) VALUES (999, 'admin@test.com', 'admin', 1024)")
    db.commit()

    # Admin should pass even if requesting huge resources
    check_quota(db, 999, req_ram_mb=8192, req_cpu_cores=8, req_disk_gb=100)


def test_quota_exceeded_raises_409():
    db = get_db()
    # Insert standard user with 1024MB RAM quota
    db.execute("INSERT OR REPLACE INTO users (id, email, role, quota_ram_mb, quota_cpu_cores, quota_disk_gb) VALUES (888, 'user@test.com', 'user', 1024, 2, 10)")
    db.commit()

    # Requesting 2048MB should raise 409 Conflict
    with pytest.raises(falcon.HTTPConflict):
        check_quota(db, 888, req_ram_mb=2048, req_cpu_cores=1, req_disk_gb=5)
