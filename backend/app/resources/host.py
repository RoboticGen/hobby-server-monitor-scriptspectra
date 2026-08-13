"""
backend/app/resources/host.py

Falcon API endpoint for Host Capacity Accounting:
- GET /api/host -> Exposes total host hardware capacity vs. aggregated allocated resources
"""

import os
import shutil
import logging
import falcon

from app.db import get_db
from app.lxd_client import get_client, lxd_safe
from app.util.auth_helpers import require_role

log = logging.getLogger(__name__)


def get_host_capacity() -> dict:
    """
    Returns physical hardware capacity of the host machine (RAM MB, CPU cores, Disk GB).
    """
    # 1. Total CPU cores
    try:
        cpu_cores = os.cpu_count() or 1
    except Exception:
        cpu_cores = 1

    # 2. Total RAM in MB from /proc/meminfo or sysconf
    ram_mb = 2048
    try:
        if hasattr(os, "sysconf"):
            mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            ram_mb = mem_bytes // (1024 * 1024)
    except Exception:
        pass

    # 3. Total Disk space in GB for root partition
    disk_gb = 20
    try:
        total_bytes, _, _ = shutil.disk_usage("/")
        disk_gb = total_bytes // (1024 * 1024 * 1024)
    except Exception:
        pass

    return {
        "ram_mb": ram_mb,
        "cpu_cores": cpu_cores,
        "disk_gb": disk_gb,
    }


class HostResource:
    """Resource handler for GET /api/host."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        """
        Get host hardware capacity and allocation accounting (Admin only).
        """
        require_role(req, "admin")
        db = get_db()

        capacity = get_host_capacity()

        # Aggregate total allocated container limits across active containers in DB
        ct_row = db.execute("""
            SELECT COALESCE(SUM(ram_mb), 0) AS total_ram,
                   COALESCE(SUM(cpu_cores), 0) AS total_cpu,
                   COALESCE(SUM(disk_gb), 0) AS total_disk,
                   COUNT(name) AS total_containers
            FROM containers
            WHERE deleted_at IS NULL
        """).fetchone()

        # Aggregate total allocated user quotas across all non-revoked users
        user_row = db.execute("""
            SELECT COALESCE(SUM(quota_ram_mb), 0) AS total_quota_ram,
                   COALESCE(SUM(quota_cpu_cores), 0) AS total_quota_cpu,
                   COALESCE(SUM(quota_disk_gb), 0) AS total_quota_disk,
                   COUNT(id) AS total_users
            FROM users
            WHERE revoked_at IS NULL AND role != 'admin'
        """).fetchone()

        container_allocated = {
            "ram_mb": ct_row["total_ram"],
            "cpu_cores": ct_row["total_cpu"],
            "disk_gb": ct_row["total_disk"],
            "container_count": ct_row["total_containers"],
        }

        user_quota_allocated = {
            "ram_mb": user_row["total_quota_ram"],
            "cpu_cores": user_row["total_quota_cpu"],
            "disk_gb": user_row["total_quota_disk"],
            "user_count": user_row["total_users"],
        }

        unallocated_host = {
            "ram_mb": max(0, capacity["ram_mb"] - container_allocated["ram_mb"]),
            "cpu_cores": max(0, capacity["cpu_cores"] - container_allocated["cpu_cores"]),
            "disk_gb": max(0, capacity["disk_gb"] - container_allocated["disk_gb"]),
        }

        resp.media = {
            "host_capacity": capacity,
            "container_allocated": container_allocated,
            "user_quota_allocated": user_quota_allocated,
            "unallocated_host": unallocated_host,
        }
