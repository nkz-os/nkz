"""The tenant purge is disabled: the route answers 409 and runs no phase."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parents[1] / "enhanced-tenant-webhook.py"


@pytest.mark.parametrize("tenant_id", ["acme", "platform"])
def test_purge_answers_409_and_runs_no_phase(webhook_module, monkeypatch, tenant_id):
    phases = [n for n in dir(webhook_module) if n.startswith("_purge_phase_")]
    assert phases
    mocks = {n: MagicMock(return_value={"ok": True}) for n in phases}
    for name, mock in mocks.items():
        monkeypatch.setattr(webhook_module, name, mock)
    view = webhook_module.admin_hard_purge_tenant.__wrapped__
    with webhook_module.app.test_request_context("/", method="DELETE"):
        resp, status = view(tenant_id)
    assert status == 409
    assert resp.get_json()["error"] == "TENANT_PURGE_DISABLED"
    for name, mock in mocks.items():
        assert not mock.called, name


def test_purge_still_requires_a_platform_admin(webhook_module):
    resp = webhook_module.app.test_client().delete("/api/admin/tenants/acme/purge")
    assert resp.status_code in (401, 403)


def test_nothing_calls_the_purge_phases():
    tree = ast.parse(_SRC.read_text())
    used = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id.startswith("_purge_phase_")
    }
    assert used == set()
