"""Minimal smoke test for sdm-integration."""

import ast
import importlib
import os
import sys

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_MAIN_FILE = os.path.join(_SVC_DIR, "sdm_api.py")


def test_syntax():
    """Verify the main module parses without syntax errors."""
    with open(_MAIN_FILE) as f:
        source = f.read()
    try:
        ast.parse(source)
    except SyntaxError as e:
        pytest.fail(f"Syntax error in {_MAIN_FILE}: {e}")


def test_import():
    """Verify the main module can be imported."""
    try:
        importlib.import_module("sdm_api")
    except (ModuleNotFoundError, ImportError) as e:
        pytest.fail(f"Could not import sdm_api: {e}")
