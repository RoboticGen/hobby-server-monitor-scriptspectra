"""
backend/app/util/quota.py

Quota accounting helpers for user resource allocation checks.
Enforces quota bounds regardless of container runtime state (stopped containers still count).
"""

from sqlite3 import Connection
import falcon


def get_user_used(db: Connection, user_id: int) -> dict:
    """
    Returns the sum of configured limits for all non-deleted containers assigned to user_id.
    Note: Stopped containers still count toward user quota to prevent quota bypass via container stop.

    Returns dict:
        {
            "ram_mb": int,
            "cpu_cores": int,
            "disk_gb": int
        }
    """
    # Find all active container assignments for user where container is not soft-deleted
    query = """
        SELECT c.name
        FROM assignments a
        JOIN containers c ON a.container_name = c.name
        WHERE a.user_id = ?
          AND a.revoked_at IS NULL
          AND c.deleted_at IS NULL
    """
    rows = db.execute(query, (user_id,)).fetchall()
    container_names = [row["name"] for row in rows]

    # For now, default allocation per container if not stored directly in DB:
    # 512 MB RAM, 1 CPU core, 5 GB Disk per container default estimate.
    # In full system, container limits come from container config/database metadata.
    total_ram = len(container_names) * 512
    total_cpu = len(container_names) * 1
    total_disk = len(container_names) * 5

    return {
        "ram_mb": total_ram,
        "cpu_cores": total_cpu,
        "disk_gb": total_disk,
        "assigned_containers_count": len(container_names),
    }


def check_quota(
    db: Connection,
    user_id: int,
    req_ram_mb: int = 512,
    req_cpu_cores: int = 1,
    req_disk_gb: int = 5,
) -> None:
    """
    Check if adding the requested container resources would exceed the user's allocated quota.
    Raises falcon.HTTPConflict (409) if quota limit is exceeded.
    """
    user_row = db.execute(
        "SELECT role, quota_ram_mb, quota_cpu_cores, quota_disk_gb FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if not user_row:
        # If user does not exist in DB yet (e.g. bootstrap/dev stub), skip quota check
        return

    # Admin role is exempt from user quotas
    if user_row["role"] == "admin":
        return

    used = get_user_used(db, user_id)

    new_ram = used["ram_mb"] + req_ram_mb
    new_cpu = used["cpu_cores"] + req_cpu_cores
    new_disk = used["disk_gb"] + req_disk_gb

    exceeded = []
    if new_ram > user_row["quota_ram_mb"]:
        exceeded.append(f"RAM limit exceeded ({new_ram}MB requested/used vs {user_row['quota_ram_mb']}MB quota)")
    if new_cpu > user_row["quota_cpu_cores"]:
        exceeded.append(f"CPU limit exceeded ({new_cpu} cores requested/used vs {user_row['quota_cpu_cores']} cores quota)")
    if new_disk > user_row["quota_disk_gb"]:
        exceeded.append(f"Disk limit exceeded ({new_disk}GB requested/used vs {user_row['quota_disk_gb']}GB quota)")

    if exceeded:
        raise falcon.HTTPConflict(
            title="Quota Exceeded",
            description="Container creation denied: " + "; ".join(exceeded)
        )
