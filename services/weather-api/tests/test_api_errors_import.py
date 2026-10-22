"""common.api_errors must be importable by FastAPI services without Flask.

weather-api (FastAPI/uvicorn) imports `fastapi_internal_error` from
common.api_errors. A module-level `from flask import jsonify` there forced Flask
onto every importer and crash-looped weather-api on startup
(ModuleNotFoundError: No module named 'flask'). Flask is only needed by the
Flask variant, so its import must be lazy.
"""

import builtins
import importlib
import sys

import pytest


def _reimport_without_flask():
    """Import common.api_errors with Flask hidden as if not installed."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flask" or name.startswith("flask."):
            raise ModuleNotFoundError("No module named 'flask'")
        return real_import(name, *args, **kwargs)

    for mod in ("common.api_errors", "flask"):
        sys.modules.pop(mod, None)

    orig = builtins.__import__
    builtins.__import__ = fake_import
    try:
        return importlib.import_module("common.api_errors")
    finally:
        builtins.__import__ = orig


def test_module_imports_without_flask():
    mod = _reimport_without_flask()
    assert hasattr(mod, "fastapi_internal_error")


def test_fastapi_internal_error_works_without_flask():
    from fastapi import HTTPException

    mod = _reimport_without_flask()
    with pytest.raises(HTTPException) as exc_info:
        mod.fastapi_internal_error(ValueError("boom"), "test_ctx",
                                   user_message="nope")
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["error"] == "nope"
