"""
backend/app/util/validators.py

Input validation helper functions for request parameters and payloads.
Raises appropriate Falcon HTTP errors on validation failures.
"""

import re
import falcon

# LXD container name rule: 2 to 63 chars, starts with letter, lowercase alphanumeric and hyphens, ends with alphanumeric.
CONTAINER_NAME_REGEX = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")

VALID_CONTAINER_ACTIONS = {"start", "stop", "restart", "freeze", "unfreeze"}


def validate_container_name(name: str) -> str:
    """
    Validate LXD container name string.
    Must be lowercase, 2-63 characters, start with letter, end with alphanumeric, contain only letters, numbers, hyphens.
    Raises falcon.HTTPBadRequest if invalid.
    """
    if not name or not isinstance(name, str):
        raise falcon.HTTPBadRequest(
            title="Invalid Container Name",
            description="Container name must be a non-empty string."
        )

    name = name.strip()
    if not CONTAINER_NAME_REGEX.match(name):
        raise falcon.HTTPBadRequest(
            title="Invalid Container Name",
            description=(
                f"Container name '{name}' is invalid. "
                "Must be 2-63 characters long, start with a lowercase letter, "
                "end with a letter or digit, and contain only lowercase letters, digits, and hyphens."
            )
        )

    return name


def validate_action(action: str) -> str:
    """
    Validate container state lifecycle action.
    Must be one of: start, stop, restart, freeze, unfreeze.
    Raises falcon.HTTPBadRequest if invalid.
    """
    if not action or not isinstance(action, str):
        raise falcon.HTTPBadRequest(
            title="Invalid Action",
            description="Action must be a non-empty string."
        )

    action = action.strip().lower()
    if action not in VALID_CONTAINER_ACTIONS:
        allowed = ", ".join(sorted(VALID_CONTAINER_ACTIONS))
        raise falcon.HTTPBadRequest(
            title="Invalid Action",
            description=f"Action '{action}' is not supported. Allowed actions: {allowed}."
        )

    return action
