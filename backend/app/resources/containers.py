"""
backend/app/resources/containers.py

Falcon API endpoints for LXD container lifecycle management:
- GET /api/containers          -> List non-deleted containers (role-filtered)
- POST /api/containers         -> Create container with full validation & quota checks
- GET /api/containers/{name}   -> Detail view combining DB record and live LXD state
- PATCH /api/containers/{name} -> Update description / limits
- DELETE /api/containers/{name}-> Delete container in LXD and soft-delete in DB
- POST /api/containers/{name}/action -> Perform lifecycle action (start, stop, restart, etc.)
"""

import json
import logging
import falcon

from app.db import get_db
from app.lxd_client import get_client, lxd_safe
from app.util.validators import validate_container_name, validate_action
from app.util.quota import check_quota

log = logging.getLogger(__name__)


def write_audit_log(db, actor_id: int | None, action: str, target: str, detail: dict | None = None) -> None:
    """Record administrative or user action in audit_log table."""
    detail_json = json.dumps(detail) if detail else None
    db.execute(
        "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (?, ?, ?, ?)",
        (actor_id, action, target, detail_json)
    )
    db.commit()


class ContainerCollection:
    """Resource handler for /api/containers."""

    def on_get(self, req: falcon.Request, resp: falcon.Response):
        """
        List all non-deleted containers.
        Combines DB records with runtime state from LXD.
        Role-filtered: Users see only assigned containers; Admins see all.
        """
        db = get_db()

        user = getattr(req.context, "user", None)
        if user and user.get("role") != "admin":
            query = """
                SELECT c.name, c.description, c.created_by, c.ram_mb, c.cpu_cores, c.disk_gb, c.created_at 
                FROM containers c
                JOIN assignments a ON c.name = a.container_name
                WHERE a.user_id = ? AND a.revoked_at IS NULL AND c.deleted_at IS NULL
                ORDER BY c.created_at DESC
            """
            db_containers = db.execute(query, (user["id"],)).fetchall()
        else:
            query = "SELECT name, description, created_by, ram_mb, cpu_cores, disk_gb, created_at FROM containers WHERE deleted_at IS NULL ORDER BY created_at DESC"
            db_containers = db.execute(query).fetchall()

        client = get_client()
        lxd_containers_list, err = lxd_safe(lambda: client.containers.all())

        lxd_map = {}
        if lxd_containers_list:
            for c in lxd_containers_list:
                lxd_map[c.name] = c

        results = []
        for row in db_containers:
            name = row["name"]
            c_data = {
                "name": name,
                "description": row["description"],
                "created_by": row["created_by"],
                "limits": {
                    "ram_mb": row["ram_mb"],
                    "cpu_cores": row["cpu_cores"],
                    "disk_gb": row["disk_gb"],
                },
                "created_at": row["created_at"],
                "status": "Unknown",
                "status_code": 0,
            }

            if name in lxd_map:
                c_obj = lxd_map[name]
                c_data["status"] = c_obj.status
                c_data["status_code"] = c_obj.status_code
            else:
                c_data["status"] = "Missing (LXD)"

            results.append(c_data)

        resp.media = {
            "count": len(results),
            "containers": results,
            "lxd_available": err is None
        }

    def on_post(self, req: falcon.Request, resp: falcon.Response):
        """
        Create a new LXD container with validation pipeline:
        1. Validate payload and name regex
        2. Verify DB and LXD uniqueness
        3. User resource quota check (dynamic RAM, CPU, Disk)
        4. Create container in LXD via pylxd (with local image fallback)
        5. Auto-start container if autostart=True
        6. Record in DB, auto-assign to creator, and write audit log
        """
        data = req.media or {}
        raw_name = data.get("name", "")
        description = data.get("description", "")
        image_alias = data.get("image", "ubuntu/24.04")
        ram_mb = int(data.get("ram_mb", 512))
        cpu_cores = int(data.get("cpu_cores", 1))
        disk_gb = int(data.get("disk_gb", 5))
        autostart = bool(data.get("autostart", True))

        # 1. Name validation
        name = validate_container_name(raw_name)

        db = get_db()

        # 2. DB Uniqueness check
        existing_db = db.execute(
            "SELECT name FROM containers WHERE name = ? AND deleted_at IS NULL", (name,)
        ).fetchone()

        if existing_db:
            raise falcon.HTTPConflict(
                title="Container Exists",
                description=f"A container named '{name}' already exists in the database."
            )

        # 3. LXD Uniqueness check
        client = get_client()
        lxd_containers, err = lxd_safe(lambda: [c.name for c in client.containers.all()])
        if err:
            raise falcon.HTTPInternalServerError(
                title="LXD Unavailable",
                description=f"Failed to query LXD hypervisor: {err}"
            )

        if name in lxd_containers:
            raise falcon.HTTPConflict(
                title="Container Exists in LXD",
                description=f"A container named '{name}' already exists on the LXD host."
            )

        # 4. Dynamic Quota check
        user = getattr(req.context, "user", None)
        actor_id = user["id"] if user else 1
        check_quota(db, actor_id, req_ram_mb=ram_mb, req_cpu_cores=cpu_cores, req_disk_gb=disk_gb)

        # 5. Create container in LXD
        config = {
            "name": name,
            "source": {
                "type": "image",
                "alias": image_alias,
            },
            "config": {
                "limits.memory": f"{ram_mb}MB",
                "limits.cpu": str(cpu_cores),
            }
        }

        created_ct, create_err = lxd_safe(lambda: client.containers.create(config, wait=True))
        
        # Fallback to local image fingerprint if alias resolution fails
        if create_err:
            local_imgs, _ = lxd_safe(lambda: client.images.all())
            if local_imgs:
                fp = local_imgs[0].fingerprint
                config["source"] = {"type": "image", "fingerprint": fp}
                created_ct, create_err = lxd_safe(lambda: client.containers.create(config, wait=True))

        if create_err or not created_ct:
            raise falcon.HTTPBadRequest(
                title="Container Creation Failed",
                description=f"LXD failed to create container '{name}': {create_err}"
            )

        # 6. Auto-start if requested
        if autostart and created_ct.status != "Running":
            lxd_safe(lambda: created_ct.start(wait=True))
            created_ct, _ = lxd_safe(lambda: client.containers.get(name))

        # 7. Insert into DB, auto-assign to actor, and write audit log
        db.execute(
            "INSERT INTO containers (name, description, created_by, ram_mb, cpu_cores, disk_gb) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, actor_id, ram_mb, cpu_cores, disk_gb)
        )
        db.execute(
            "INSERT OR IGNORE INTO assignments (user_id, container_name) VALUES (?, ?)",
            (actor_id, name)
        )
        write_audit_log(
            db,
            actor_id=actor_id,
            action="container.create",
            target=name,
            detail={"image": image_alias, "ram_mb": ram_mb, "cpu_cores": cpu_cores, "disk_gb": disk_gb, "autostart": autostart}
        )
        db.commit()

        final_status = created_ct.status if created_ct else ("Running" if autostart else "Stopped")

        resp.status = falcon.HTTP_201
        resp.media = {
            "message": f"Container '{name}' created successfully.",
            "container": {
                "name": name,
                "description": description,
                "status": final_status,
                "image": image_alias,
                "limits": {
                    "ram_mb": ram_mb,
                    "cpu_cores": cpu_cores,
                    "disk_gb": disk_gb,
                }
            }
        }


class ContainerResource:
    """Resource handler for /api/containers/{name}."""

    def on_get(self, req: falcon.Request, resp: falcon.Response, name: str):
        """Get detail for a specific container."""
        name = validate_container_name(name)
        db = get_db()

        c_row = db.execute(
            "SELECT name, description, created_by, ram_mb, cpu_cores, disk_gb, created_at FROM containers WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()

        if not c_row:
            raise falcon.HTTPNotFound(
                title="Container Not Found",
                description=f"No active container named '{name}' was found."
            )

        client = get_client()
        ct_obj, err = lxd_safe(lambda: client.containers.get(name))

        state_data = None
        if ct_obj and not err:
            st, st_err = lxd_safe(lambda: ct_obj.state())
            if st and not st_err:
                state_data = {
                    "status": st.status,
                    "status_code": st.status_code,
                    "cpu_usage": getattr(st.cpu, 'usage', None) if getattr(st, 'cpu', None) else None,
                    "memory_usage": getattr(st.memory, 'usage', None) if getattr(st, 'memory', None) else None,
                    "processes": getattr(st, 'processes', None),
                }

        resp.media = {
            "container": {
                "name": c_row["name"],
                "description": c_row["description"],
                "created_by": c_row["created_by"],
                "limits": {
                    "ram_mb": c_row["ram_mb"],
                    "cpu_cores": c_row["cpu_cores"],
                    "disk_gb": c_row["disk_gb"],
                },
                "created_at": c_row["created_at"],
                "lxd_status": ct_obj.status if ct_obj else "Missing",
                "lxd_state": state_data,
            }
        }

    def on_patch(self, req: falcon.Request, resp: falcon.Response, name: str):
        """Update container description or limits."""
        name = validate_container_name(name)
        data = req.media or {}
        description = data.get("description")
        ram_mb = data.get("ram_mb")
        cpu_cores = data.get("cpu_cores")
        disk_gb = data.get("disk_gb")

        db = get_db()
        c_row = db.execute(
            "SELECT name, ram_mb, cpu_cores, disk_gb FROM containers WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()

        if not c_row:
            raise falcon.HTTPNotFound(
                title="Container Not Found",
                description=f"No container named '{name}' found."
            )

        updates = []
        params = []
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if ram_mb is not None:
            updates.append("ram_mb = ?")
            params.append(int(ram_mb))
        if cpu_cores is not None:
            updates.append("cpu_cores = ?")
            params.append(int(cpu_cores))
        if disk_gb is not None:
            updates.append("disk_gb = ?")
            params.append(int(disk_gb))

        if updates:
            params.append(name)
            db.execute(
                f"UPDATE containers SET {', '.join(updates)} WHERE name = ?",
                tuple(params)
            )
            actor_id = getattr(req.context, "user", {}).get("id", 1) if hasattr(req.context, "user") else 1
            write_audit_log(db, actor_id=actor_id, action="container.update", target=name, detail=data)
            db.commit()

        resp.media = {"message": f"Container '{name}' updated successfully."}

    def on_delete(self, req: falcon.Request, resp: falcon.Response, name: str):
        """
        Delete container. Requires {"confirm": true} body.
        Stops container if running, deletes in LXD, soft-deletes in DB.
        """
        name = validate_container_name(name)
        data = req.media or {}

        if not data.get("confirm", False):
            raise falcon.HTTPBadRequest(
                title="Confirmation Required",
                description="Deletion requires explicit confirmation: {\"confirm\": true}"
            )

        db = get_db()
        c_row = db.execute(
            "SELECT name FROM containers WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()

        if not c_row:
            raise falcon.HTTPNotFound(
                title="Container Not Found",
                description=f"No active container named '{name}' found to delete."
            )

        client = get_client()
        ct_obj, get_err = lxd_safe(lambda: client.containers.get(name))

        if ct_obj and not get_err:
            # Stop if running
            if ct_obj.status == "Running":
                lxd_safe(lambda: ct_obj.stop(wait=True))

            # Delete LXD container
            _, del_err = lxd_safe(lambda: ct_obj.delete(wait=True))
            if del_err:
                log.warning("Failed to delete LXD container '%s': %s", name, del_err)

        # Soft-delete in DB
        db.execute(
            "UPDATE containers SET deleted_at = datetime('now') WHERE name = ?",
            (name,)
        )

        actor_id = getattr(req.context, "user", {}).get("id", 1) if hasattr(req.context, "user") else 1
        write_audit_log(db, actor_id=actor_id, action="container.delete", target=name)
        db.commit()

        resp.media = {"message": f"Container '{name}' deleted successfully."}


class ContainerAction:
    """Resource handler for /api/containers/{name}/action."""

    def on_post(self, req: falcon.Request, resp: falcon.Response, name: str):
        """
        Execute state action on container: start, stop, restart, freeze, unfreeze.
        """
        name = validate_container_name(name)
        data = req.media or {}
        raw_action = data.get("action", "")
        action = validate_action(raw_action)

        db = get_db()
        c_row = db.execute(
            "SELECT name FROM containers WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()

        if not c_row:
            raise falcon.HTTPNotFound(
                title="Container Not Found",
                description=f"No active container named '{name}' found."
            )

        client = get_client()
        ct_obj, get_err = lxd_safe(lambda: client.containers.get(name))

        if get_err or not ct_obj:
            raise falcon.HTTPNotFound(
                title="Container Not Found in LXD",
                description=f"Container '{name}' exists in DB but could not be loaded from LXD: {get_err}"
            )

        # Map action string to pylxd method call
        action_map = {
            "start": lambda: ct_obj.start(wait=True),
            "stop": lambda: ct_obj.stop(wait=True),
            "restart": lambda: ct_obj.restart(wait=True),
            "freeze": lambda: ct_obj.freeze(wait=True),
            "unfreeze": lambda: ct_obj.unfreeze(wait=True),
        }

        _, action_err = lxd_safe(action_map[action])

        if action_err:
            raise falcon.HTTPInternalServerError(
                title="Action Failed",
                description=f"LXD failed to execute action '{action}' on container '{name}': {action_err}"
            )

        actor_id = getattr(req.context, "user", {}).get("id", 1) if hasattr(req.context, "user") else 1
        write_audit_log(db, actor_id=actor_id, action=f"container.{action}", target=name)
        db.commit()

        # Fetch updated status
        updated_status, _ = lxd_safe(lambda: ct_obj.status)

        resp.media = {
            "message": f"Action '{action}' executed successfully on container '{name}'.",
            "name": name,
            "action": action,
            "status": updated_status or "Unknown"
        }
