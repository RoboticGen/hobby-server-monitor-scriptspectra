"""
backend/tests/test_validators.py

Unit tests for container name and action validators.
"""

import pytest
import falcon
from app.util.validators import validate_container_name, validate_action


def test_valid_container_names():
    assert validate_container_name("my-container") == "my-container"
    assert validate_container_name("ubuntu-2204-ct") == "ubuntu-2204-ct"
    assert validate_container_name("c1") == "c1"


def test_invalid_container_names():
    # Starts with digit
    with pytest.raises(falcon.HTTPBadRequest):
        validate_container_name("1container")

    # Uppercase letters
    with pytest.raises(falcon.HTTPBadRequest):
        validate_container_name("MyContainer")

    # Special characters
    with pytest.raises(falcon.HTTPBadRequest):
        validate_container_name("ct_test!")

    # Empty string
    with pytest.raises(falcon.HTTPBadRequest):
        validate_container_name("")


def test_valid_actions():
    assert validate_action("start") == "start"
    assert validate_action("STOP") == "stop"
    assert validate_action("restart") == "restart"
    assert validate_action("freeze") == "freeze"
    assert validate_action("unfreeze") == "unfreeze"


def test_invalid_actions():
    with pytest.raises(falcon.HTTPBadRequest):
        validate_action("destroy")

    with pytest.raises(falcon.HTTPBadRequest):
        validate_action("")
