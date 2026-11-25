"""Suspending writes the status every reader looks at; nothing undoes it by accident."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[1] / "enhanced-tenant-webhook.py"


def _call(webhook_module, view, deleted_at, ingestion=None):
    svc = webhook_module.webhook_service
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = {"tenant_id": "acme", "deleted_at": deleted_at}
    with patch.object(svc, "get_db_connection", return_value=conn), \
         patch.object(svc, "_apply_admin_context"), \
         patch.object(svc, "get_keycloak_token", return_value=None), \
         patch.object(webhook_module, "_set_tenant_ingestion", ingestion or MagicMock(return_value="ok")) as ing, \
         patch.object(webhook_module.requests, "post"), \
         patch.object(webhook_module, "audit_log"), \
         webhook_module.app.test_request_context("/", method="POST", json={}):
        webhook_module.g.current_user = {"preferred_username": "admin"}
        resp, status = getattr(webhook_module, view).__wrapped__("acme")
    updates = [" ".join(c.args[0].split()) for c in cursor.execute.call_args_list
               if "UPDATE tenants" in c.args[0]]
    return resp.get_json(), status, updates, conn, ing


def test_suspend_writes_both_facts(webhook_module):
    body, status, updates, conn, ing = _call(webhook_module, "suspend_tenant", None)
    assert status == 200 and body["ingestion"] == "ok"
    assert len(updates) == 1
    assert "deleted_at = NOW()" in updates[0] and "status = 'suspended'" in updates[0]
    conn.commit.assert_called()
    ing.assert_called_once_with("acme", False)


def test_restore_writes_both_facts(webhook_module):
    body, status, updates, conn, ing = _call(webhook_module, "restore_tenant", "2026-01-01")
    assert status == 200 and body["ingestion"] == "ok"
    assert len(updates) == 1
    assert "deleted_at = NULL" in updates[0] and "status = 'active'" in updates[0]
    ing.assert_called_once_with("acme", True)


@pytest.mark.parametrize("view, deleted_at", [("suspend_tenant", "2026-01-01"), ("restore_tenant", None)])
def test_twice_is_a_conflict(webhook_module, view, deleted_at):
    _, status, updates, _, ing = _call(webhook_module, view, deleted_at)
    assert status == 409 and updates == []
    ing.assert_not_called()


@pytest.mark.parametrize("view, deleted_at", [("suspend_tenant", None), ("restore_tenant", "2026-01-01")])
def test_the_change_stands_when_ingestion_fails(webhook_module, view, deleted_at):
    body, status, updates, conn, _ = _call(
        webhook_module, view, deleted_at, MagicMock(return_value="failed"))
    assert status == 200 and body["ingestion"] == "failed" and len(updates) == 1
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


def test_an_upsert_never_revives_a_suspended_tenant():
    src = " ".join(_SRC.read_text().split())
    assert "status = CASE WHEN tenants.status IN ('cancelled', 'suspended') THEN tenants.status ELSE EXCLUDED.status END" in src
