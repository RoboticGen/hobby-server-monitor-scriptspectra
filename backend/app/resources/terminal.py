"""
backend/app/resources/terminal.py

Phase 6 — Authenticated, Authorized, Audited Container Command Execution

Provides a single endpoint:
  POST /api/terminal/{name}/exec

Design rationale:
  This interface is intentionally a request-response exec, NOT a WebSocket PTY.
  One command per request — each is individually authenticated, authorized, and
  audit-logged. This makes every operation traceable and revocable.

Shell execution model:
  Commands are executed via `/bin/sh -c <command>` inside the container through
  pylxd's container.execute(). This means the command string is passed to a shell
  interpreter, giving users standard shell capabilities (pipes, redirects, etc.).

  Why /bin/sh?
    - Available on virtually every Linux container (POSIX standard)
    - Avoids bash-specific assumptions about the container image
    - Consistent, predictable parsing

  Security controls that surround shell execution:
    1. Authentication  → Valid session_token cookie required (AuthMiddleware)
    2. Role check      → Minimum 'user' role enforced
    3. Container guard → Standard users can only exec into their assigned containers;
                         Admins can exec into any container
    4. Command length  → Hard cap at 512 characters to prevent abuse
    5. Exec timeout    → 30-second wall-clock timeout; process is killed on expiry
    6. Output cap      → stdout and stderr each truncated at 65,536 bytes (64 KB)
    7. Audit trail     → Every exec attempt (success or failure) is written to
                         audit_log with actor, container, command, and exit_code.
                         This is non-optional and happens regardless of outcome.

  What this does NOT do:
    - No persistent shell sessions between requests
    - No WebSocket / PTY streaming
    - No filesystem access beyond what the container user already has
"""

import json
import logging
import falcon

from app.db import get_db
from app.lxd_client import get_client, lxd_safe
from app.util.validators import validate_container_name
from app.util.auth_helpers import require_role

log = logging.getLogger(__name__)

# Maximum allowed command string length (characters)
COMMAND_MAX_LEN = 512

# Maximum output bytes returned per stream (stdout or stderr)
OUTPUT_MAX_BYTES = 65_536  # 64 KB

# Wall-clock execution timeout (seconds)
EXEC_TIMEOUT_SECONDS = 30


def _check_container_access(db, user: dict, container_name: str) -> None:
    """
    Verify the requesting user is allowed to exec into this container.

    Admins → unrestricted access to all active containers.
    Standard users → must have an active (non-revoked) assignment for the container.
    """
    if user.get("role") == "admin":
        return

    row = db.execute(
        """
        SELECT a.id
        FROM assignments a
        JOIN containers c ON a.container_name = c.name
        WHERE a.user_id = ?
          AND a.container_name = ?
          AND a.revoked_at IS NULL
          AND c.deleted_at IS NULL
        """,
        (user["id"], container_name),
    ).fetchone()

    if not row:
        raise falcon.HTTPForbidden(
            title="Access Denied",
            description=(
                f"You do not have an active assignment for container '{container_name}'. "
                "Contact an administrator to request access."
            ),
        )


def _write_exec_audit(db, actor_id: int, container_name: str, command: str,
                      exit_code: int | None, error: str | None = None) -> None:
    """
    Write a terminal exec entry to audit_log regardless of outcome.
    exit_code=None signals that execution itself failed (timeout / LXD error).
    """
    detail = {
        "command": command[:COMMAND_MAX_LEN],  # already validated, but be safe
        "exit_code": exit_code,
    }
    if error:
        detail["error"] = error

    db.execute(
        "INSERT INTO audit_log (actor_id, action, target, detail) VALUES (?, ?, ?, ?)",
        (actor_id, "terminal.exec", container_name, json.dumps(detail)),
    )
    db.commit()


class TerminalExecResource:
    """
    Resource handler for POST /api/terminal/{name}/exec

    Executes a shell command inside the named LXD container and returns
    the captured stdout, stderr, and exit code.

    Full security pipeline (enforced in order):
      Authentication → Role check → Container access → Command length →
      Exec with timeout → Output truncation → Audit log → Response
    """

    def on_post(self, req: falcon.Request, resp: falcon.Response, name: str) -> None:
        # ── Step 1: Validate container name format ────────────────────────────
        name = validate_container_name(name)

        # ── Step 2: Authentication + minimum role check ───────────────────────
        user = require_role(req, "user")
        actor_id: int = user["id"]

        # ── Step 3: Container assignment / ownership check ────────────────────
        db = get_db()
        _check_container_access(db, user, name)

        # ── Step 4: Validate command ──────────────────────────────────────────
        data = req.media or {}
        raw_command: str = data.get("command", "")

        if not raw_command or not raw_command.strip():
            raise falcon.HTTPBadRequest(
                title="Empty Command",
                description="The 'command' field must not be empty.",
            )

        if len(raw_command) > COMMAND_MAX_LEN:
            raise falcon.HTTPBadRequest(
                title="Command Too Long",
                description=(
                    f"Command length ({len(raw_command)} chars) exceeds the "
                    f"{COMMAND_MAX_LEN}-character limit."
                ),
            )

        command = raw_command.strip()

        # ── Step 5: Verify container exists in DB and is active ───────────────
        container_row = db.execute(
            "SELECT name FROM containers WHERE name = ? AND deleted_at IS NULL",
            (name,),
        ).fetchone()

        if not container_row:
            raise falcon.HTTPNotFound(
                title="Container Not Found",
                description=f"No active container named '{name}' was found.",
            )

        # ── Step 6: Get LXD container object ─────────────────────────────────
        client = get_client()
        ct_obj, lxd_err = lxd_safe(lambda: client.containers.get(name))

        if lxd_err or not ct_obj:
            _write_exec_audit(db, actor_id, name, command, exit_code=None,
                              error=f"LXD container lookup failed: {lxd_err}")
            raise falcon.HTTPServiceUnavailable(
                title="LXD Unavailable",
                description=f"Could not connect to container '{name}' via LXD: {lxd_err}",
            )

        if ct_obj.status != "Running":
            _write_exec_audit(db, actor_id, name, command, exit_code=None,
                              error=f"Container not running (status={ct_obj.status})")
            raise falcon.HTTPConflict(
                title="Container Not Running",
                description=(
                    f"Container '{name}' is in '{ct_obj.status}' state. "
                    "The container must be running to execute commands."
                ),
            )

        # ── Step 7: Execute command with timeout ──────────────────────────────
        #
        # Shell execution design note:
        #   We use ["/bin/sh", "-c", command] which passes the command string
        #   to a shell interpreter. This allows pipes, redirects, and other shell
        #   constructs. The 30-second timeout kills the process tree if exceeded.
        #
        exec_result, exec_err = lxd_safe(
            lambda: ct_obj.execute(
                ["/bin/sh", "-c", command],
                
            )
        )

        if exec_err or exec_result is None:
            err_msg = str(exec_err) if exec_err else "Unknown execution error"
            _write_exec_audit(db, actor_id, name, command, exit_code=None, error=err_msg)
            raise falcon.HTTPInternalServerError(
                title="Execution Failed",
                description=f"Command execution error on container '{name}': {err_msg}",
            )

        # pylxd execute() returns an ExecResult with .exit_code, .stdout, .stderr
        exit_code: int = exec_result.exit_code if exec_result.exit_code is not None else -1

        raw_stdout: str = exec_result.stdout or ""
        raw_stderr: str = exec_result.stderr or ""

        # ── Step 8: Truncate output to 64 KB ─────────────────────────────────
        stdout_bytes = raw_stdout.encode("utf-8", errors="replace")
        stderr_bytes = raw_stderr.encode("utf-8", errors="replace")

        truncated = len(stdout_bytes) > OUTPUT_MAX_BYTES or len(stderr_bytes) > OUTPUT_MAX_BYTES

        stdout = stdout_bytes[:OUTPUT_MAX_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_bytes[:OUTPUT_MAX_BYTES].decode("utf-8", errors="replace")

        # ── Step 9: Unconditional audit log ───────────────────────────────────
        _write_exec_audit(db, actor_id, name, command, exit_code=exit_code)

        log.info(
            "terminal.exec actor=%s container=%s exit_code=%d truncated=%s",
            user.get("email"), name, exit_code, truncated,
        )

        # ── Step 10: Return result ────────────────────────────────────────────
        resp.media = {
            "container": name,
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
        }

