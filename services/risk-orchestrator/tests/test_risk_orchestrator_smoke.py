"""Minimal smoke test for risk-orchestrator."""

import ast
import importlib
import os
import sys

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
# risk-orchestrator hardcodes /app/common, /app/task-queue, /app.
# We simulate those by inserting the real directories first.
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
_TASKQ_DIR = os.path.join(_SERVICES_DIR, "task-queue")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR, _TASKQ_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Simulate /app/common, /app/task-queue, /app that the module expects
for _real, _fake in [(_COMMON_DIR, "/app/common"), (_TASKQ_DIR, "/app/task-queue"), (_SERVICES_DIR, "/app")]:
    if _fake not in sys.path:
        sys.path.insert(0, _fake)

_MAIN_FILE = os.path.join(_SVC_DIR, "risk_orchestrator.py")


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
        importlib.import_module("risk_orchestrator")
    except (ModuleNotFoundError, ImportError) as e:
        pytest.fail(f"Could not import risk_orchestrator: {e}")
