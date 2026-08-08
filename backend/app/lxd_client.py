"""
backend/app/lxd_client.py

pylxd singleton and lxd_safe() error wrapper.

Usage:
    from app.lxd_client import get_client, lxd_safe

    client = get_client()
    containers, err = lxd_safe(client.containers.all)
    if err:
        log.warning("LXD unavailable: %s", err)
    else:
        for c in containers:
            print(c.name, c.status)

Design notes:
    - Module-level singleton: the pylxd Client is created once per process.
    - Connects via Unix socket when LXD_ENDPOINT is blank (recommended).
      The OS user running the backend must be in the 'lxd' group.
    - lxd_safe() catches every known pylxd/socket failure mode and returns
      a (result, error_string) tuple so callers never need bare try/except.
    - lxd_safe() is intentionally simple — it does NOT retry. Callers that
      need retry logic (e.g. the collector) implement it themselves.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pylxd
import pylxd.exceptions

from app.config import settings

log = logging.getLogger(__name__)

# ── Singleton ─────────────────────────────────────────────────────────────────

_client: pylxd.Client | None = None


def get_client() -> pylxd.Client:
    """
    Return the module-level pylxd Client, creating it on first call.

    Connection strategy:
        - LXD_ENDPOINT = ""  (blank)  → connect via Unix socket /var/lib/lxd/unix.socket
                                         or /var/snap/lxd/common/lxd/unix.socket (snap LXD)
        - LXD_ENDPOINT = "https://..." → connect to remote LXD over HTTPS
    """
    global _client

    if _client is None:
        endpoint = settings.LXD_ENDPOINT.strip() or None  # None → Unix socket

        if endpoint:
            log.info("Connecting to LXD at %s", endpoint)
            _client = pylxd.Client(endpoint=endpoint)
        else:
            log.info("Connecting to LXD via Unix socket")
            _client = pylxd.Client()

    return _client


def reset_client() -> None:
    """
    Discard the singleton so the next get_client() call creates a fresh one.
    Used when the connection is known to be broken.
    """
    global _client
    _client = None


# ── Safe wrapper ──────────────────────────────────────────────────────────────

# All exception types that indicate an LXD communication failure.
_LXD_ERRORS = (
    pylxd.exceptions.LXDAPIException,
    pylxd.exceptions.NotFound,
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    FileNotFoundError,   # Unix socket not found
    OSError,
    BrokenPipeError,
)


def lxd_safe(fn: Callable, /, *args: Any, **kwargs: Any) -> tuple[Any, str | None]:
    """
    Call fn(*args, **kwargs) and catch all LXD-related exceptions.

    Returns:
        (result, None)       on success
        (None, error_string) on any LXD/socket error

    Example:
        containers, err = lxd_safe(client.containers.all)
        if err:
            log.warning("LXD error: %s", err)
            return

        state, err = lxd_safe(container.state)
        if err:
            continue
    """
    try:
        result = fn(*args, **kwargs)
        return result, None
    except _LXD_ERRORS as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        log.warning("lxd_safe caught: %s", error_msg)
        return None, error_msg
    except Exception as exc:
        # Unexpected exception — log at ERROR level and re-raise so we don't
        # silently swallow programming mistakes.
        log.error("lxd_safe: unexpected exception in %s: %s", getattr(fn, '__name__', fn), exc)
        raise
