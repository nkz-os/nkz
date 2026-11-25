"""Suspending writes the status every reader looks at; nothing undoes it by accident."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[1] / "enhanced-tenant-webhook.py"


def _call(webhook_module, view, deleted_at, ingestion=None, accounts=None, lock=None, own=None):
    accounts = accounts or MagicMock(return_value="ok")
    svc = webhook_module.webhook_service
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = {"tenant_id": "acme", "deleted_at": deleted_at}
    with patch.object(svc, "get_db_connection", return_value=conn), \
         patch.object(svc, "_apply_admin_context"), \
         patch.object(webhook_module, "_set_tenant_accounts", accounts) as acc, \
         patch.object(webhook_module, "_suspension_lock", lock or MagicMock()) as lk, \
         patch.object(webhook_module, "_set_tenant_ingestion", ingestion or MagicMock(return_value="ok")) as ing, \
         patch.object(webhook_module.requests, "post"), \
         patch.object(webhook_module, "audit_log"), \
         webhook_module.app.test_request_context("/", method="POST", json={}):
        webhook_module.g.current_user = {"preferred_username": "admin", "sub": "me"}
        webhook_module.g.tenant_id = own
        resp, status = getattr(webhook_module, view).__wrapped__("acme")
    updates = [" ".join(c.args[0].split()) for c in cursor.execute.call_args_list
               if "UPDATE tenants" in c.args[0]]
    _call.accounts, _call.lock = acc, lk
    return resp.get_json(), status, updates, conn, ing


def test_suspend_writes_both_facts(webhook_module):
    body, status, updates, conn, ing = _call(webhook_module, "suspend_tenant", None)
    assert status == 200 and body["ingestion"] == "ok"
    assert len(updates) == 1
    assert "deleted_at = NOW()" in updates[0] and "status" not in updates[0]
    _call.lock.assert_called_once_with("acme")
    conn.commit.assert_called()
    ing.assert_called_once_with("acme", False)
    _call.accounts.assert_called_once_with("acme", False, "me")
    assert body["accounts"] == "ok"


def test_restore_writes_both_facts(webhook_module):
    body, status, updates, conn, ing = _call(webhook_module, "restore_tenant", "2026-01-01")
    assert status == 200 and body["ingestion"] == "ok"
    assert len(updates) == 1
    assert "deleted_at = NULL" in updates[0] and "status" not in updates[0]
    _call.lock.assert_called_once_with("acme")
    ing.assert_called_once_with("acme", True)
    _call.accounts.assert_called_once_with("acme", True)
    assert body["accounts"] == "ok"


@pytest.mark.parametrize("view, deleted_at", [("suspend_tenant", "2026-01-01"), ("restore_tenant", None)])
def test_twice_is_a_conflict(webhook_module, view, deleted_at):
    _, status, updates, _, ing = _call(webhook_module, view, deleted_at)
    assert status == 409 and updates == []
    ing.assert_not_called()


@pytest.mark.parametrize("view, deleted_at", [("suspend_tenant", None), ("restore_tenant", "2026-01-01")])
def test_the_change_stands_when_the_rest_fails(webhook_module, view, deleted_at):
    body, status, updates, conn, _ = _call(
        webhook_module, view, deleted_at, MagicMock(return_value="failed"),
        MagicMock(return_value="failed"))
    assert status == 200 and len(updates) == 1
    assert body["ingestion"] == "failed" and body["accounts"] == "failed"
    conn.commit.assert_called()


@pytest.mark.parametrize("outcome, expected", [(200, "ok"), (207, "failed"), (500, "failed"),
                                               (RuntimeError("down"), "failed")])
def test_set_tenant_ingestion(webhook_module, outcome, expected):
    post = MagicMock(side_effect=outcome) if isinstance(outcome, Exception) \
        else MagicMock(return_value=MagicMock(status_code=outcome))
    with patch.object(webhook_module.requests, "post", post), \
         patch.object(webhook_module, "internal_service_headers", return_value={"X": "s"}):
        assert webhook_module._set_tenant_ingestion("acme", False) == expected
    assert post.call_args.args[0].endswith("/api/internal/tenants/acme/ingestion")
    assert post.call_args.kwargs["json"] == {"enabled": False}
    assert post.call_args.kwargs["headers"] == {"X": "s"}


@pytest.mark.parametrize("view", ["suspend_tenant", "restore_tenant"])
def test_only_one_change_at_a_time(webhook_module, view):
    busy = MagicMock(side_effect=webhook_module._SuspensionBusy("acme"))
    body, status, updates, _, ing = _call(webhook_module, view, None, lock=busy)
    assert status == 409 and body["error_code"] == "SUSPENSION_BUSY" and updates == []
    ing.assert_not_called()
    _call.accounts.assert_not_called()


def test_nobody_suspends_their_own_tenant(webhook_module):
    _, status, updates, _, ing = _call(webhook_module, "suspend_tenant", None, own="acme")
    assert status == 403 and updates == []
    ing.assert_not_called()


@pytest.mark.parametrize("locked", [True, False])
def test_the_lock_is_a_session_lock_released_with_the_connection(webhook_module, locked):
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = {"locked": locked}
    entered = False
    with patch.object(webhook_module.webhook_service, "get_db_connection", return_value=conn):
        try:
            with webhook_module._suspension_lock("acme"):
                entered = True
                conn.close.assert_not_called()
        except webhook_module._SuspensionBusy:
            pass
    assert entered is locked and conn.autocommit is True
    conn.close.assert_called_once_with()
    sql, params = conn.cursor.return_value.execute.call_args.args
    assert "pg_try_advisory_lock" in sql and params == ("suspension:acme",)


def test_no_lock_no_change(webhook_module):
    with patch.object(webhook_module.webhook_service, "get_db_connection", return_value=None), \
         pytest.raises(RuntimeError), webhook_module._suspension_lock("acme"):
        raise AssertionError("entered")
