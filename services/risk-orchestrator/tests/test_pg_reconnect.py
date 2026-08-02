"""Tests for risk-orchestrator PostgreSQL lazy reconnect.

The connection is opened once and reused; without reconnect a PostgreSQL restart
or a startup-time outage left it None/broken and the orchestrator silently
stopped dispatching webhooks/notifications until the pod was restarted.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# ── Path setup (mirrors the smoke test: module hardcodes /app/*) ──
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
_TASKQ_DIR = os.path.join(_SERVICES_DIR, "task-queue")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR, _TASKQ_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
for _real, _fake in [(_COMMON_DIR, "/app/common"), (_TASKQ_DIR, "/app/task-queue"), (_SERVICES_DIR, "/app")]:
    if _fake not in sys.path:
        sys.path.insert(0, _fake)

import risk_orchestrator as mod


def _orch(postgres):
    """Build an instance without running __init__ (avoids Redis at import)."""
    o = mod.RiskOrchestrator.__new__(mod.RiskOrchestrator)
    o.postgres = postgres
    return o


def test_reconnects_when_connection_is_none():
    o = _orch(None)
    fake = MagicMock(closed=0)
    with patch.object(mod, "POSTGRES_URL", "postgresql://x"), \
         patch.object(mod.psycopg2, "connect", return_value=fake) as connect:
        assert o._ensure_postgres() is True
        connect.assert_called_once()
    assert o.postgres is fake


def test_reuses_live_connection():
    live = MagicMock(closed=0)
    o = _orch(live)
    with patch.object(mod, "POSTGRES_URL", "postgresql://x"), \
         patch.object(mod.psycopg2, "connect") as connect:
        assert o._ensure_postgres() is True
        connect.assert_not_called()
    assert o.postgres is live


def test_reconnects_when_connection_closed():
    o = _orch(MagicMock(closed=1))
    fake = MagicMock(closed=0)
    with patch.object(mod, "POSTGRES_URL", "postgresql://x"), \
         patch.object(mod.psycopg2, "connect", return_value=fake) as connect:
        assert o._ensure_postgres() is True
        connect.assert_called_once()
    assert o.postgres is fake


def test_returns_false_when_no_url():
    o = _orch(None)
    with patch.object(mod, "POSTGRES_URL", ""):
        assert o._ensure_postgres() is False


def test_returns_false_and_nulls_on_connect_failure():
    o = _orch(None)
    with patch.object(mod, "POSTGRES_URL", "postgresql://x"), \
         patch.object(mod.psycopg2, "connect", side_effect=Exception("down")):
        assert o._ensure_postgres() is False
    assert o.postgres is None
