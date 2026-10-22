"""Tests for common.api_errors."""

from __future__ import annotations

import importlib.util
import json
import os

import pytest
from flask import Flask

_api_path = os.path.join(os.path.dirname(__file__), "..", "api_errors.py")
_spec = importlib.util.spec_from_file_location("api_errors", _api_path)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
internal_error = _mod.internal_error


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    with app.app_context():
        yield


def test_internal_error_hides_exception_text(app_ctx):
    resp, status = internal_error(RuntimeError("secret-db-password"), "test_ctx")
    assert status == 500
    body = json.loads(resp.get_data(as_text=True))
    assert body["error"] == "Internal server error"
    assert "secret-db-password" not in json.dumps(body)
    assert len(body["request_id"]) >= 8


def test_internal_error_custom_message_and_extra(app_ctx):
    resp, status = internal_error(
        ValueError("x"),
        "ctx",
        user_message="Database error",
        extra={"module_id": "soil"},
    )
    body = json.loads(resp.get_data(as_text=True))
    assert status == 500
    assert body["error"] == "Database error"
    assert body["module_id"] == "soil"
