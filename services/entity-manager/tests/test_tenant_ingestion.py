"""Suspending a tenant closes its MQTT accounts; restoring finds them as they were."""
from unittest.mock import patch

from unittest.mock import MagicMock

import pytest
from flask import Flask

from tests.telemetry_fakes import AUTH, TENANT, FakeState, client, seed_datalogger, wired

import blueprints.admin as admin  # noqa: E402
import dataloggers  # noqa: E402

A, B, C = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
           "33333333-3333-3333-3333-333333333333")


def _state():
    st = FakeState()
    seed_datalogger(st, dl_id=A, username="dl_a")
    seed_datalogger(st, dl_id=B, username="dl_b", status="revoked")
    seed_datalogger(st, dl_id=C, username="dl_other", tenant="tenant-b")
    return st


def _dynsec(st):
    return [e for e in st.log if e[0] == "dynsec"]


@pytest.mark.parametrize("enabled, op", [(False, "disable"), (True, "enable")])
def test_only_the_tenants_active_accounts_change_and_no_row_does(enabled, op):
    st = _state()
    if not enabled:
        st.suspended.add(TENANT)
    before = {k: dict(v) for k, v in st.dataloggers.items()}
    with wired(st):
        assert dataloggers.set_tenant_ingestion(TENANT, enabled) == {"changed": 1, "failed": []}
    assert st.log.index(("sql", "lock", f"ingestion:{TENANT}")) < st.log.index(("dynsec", op, "dl_a"))
    assert _dynsec(st) == [("dynsec", op, "dl_a")]
    assert st.dataloggers == before


def test_a_client_the_broker_lost_is_already_closed():
    st = _state()
    st.missing_clients.add("dl_a")
    st.suspended.add(TENANT)
    with wired(st):
        assert dataloggers.set_tenant_ingestion(TENANT, False) == {"changed": 1, "failed": []}


def test_a_client_the_broker_lost_cannot_be_opened():
    st = _state()
    st.missing_clients.add("dl_a")
    with wired(st):
        assert dataloggers.set_tenant_ingestion(TENANT, True) == {"changed": 0, "failed": ["dl_a"]}


@pytest.mark.parametrize("enabled", [True, False])
def test_a_late_call_does_not_undo_a_newer_one(enabled):
    st = _state()
    if enabled:
        st.suspended.add(TENANT)
    with wired(st), pytest.raises(dataloggers.ApiError) as e:
        dataloggers.set_tenant_ingestion(TENANT, enabled)
    assert e.value.status == 409 and _dynsec(st) == []


def test_one_failure_does_not_stop_the_others():
    st = _state()
    seed_datalogger(st, dl_id="44444444-4444-4444-4444-444444444444", username="dl_z")
    with wired(st):
        real = dataloggers.mqtt_dynsec.enable

        def flaky(c, name):
            if name == "dl_a":
                raise RuntimeError("down")
            real(c, name)

        with patch.object(dataloggers.mqtt_dynsec, "enable", flaky):
            assert dataloggers.set_tenant_ingestion(TENANT, True) == {
                "changed": 1, "failed": ["dl_a"]}


@pytest.mark.parametrize("path, body", [
    ("/api/sensors/dataloggers", {"name": "L", "kind": "datak"}),
    (f"/api/sensors/dataloggers/{A}/rotate", {}),
    (f"/api/sensors/dataloggers/{A}/enable", {})])
def test_a_suspended_tenant_cannot_open_an_account(path, body):
    st = _state()
    st.suspended.add(TENANT)
    before = {k: dict(v) for k, v in st.dataloggers.items()}
    with wired(st):
        r = client().post(path, headers=AUTH, json=body)
    assert r.status_code == 409 and r.get_json()["step"] == "tenant_suspended"
    assert _dynsec(st) == [] and st.dataloggers == before


def test_a_suspended_tenant_may_still_revoke():
    st = _state()
    st.suspended.add(TENANT)
    with wired(st):
        r = client().post(f"/api/sensors/dataloggers/{A}/revoke", headers=AUTH, json={})
    assert r.status_code == 200 and st.dataloggers[A]["status"] == "revoked"


@pytest.mark.parametrize("action", ["revoke", "enable"])
def test_revoke_and_enable_wait_for_a_suspend_or_restore_in_flight(action):
    """Both take the tenant lock before the row lock: a restore that already read
    the active accounts must finish before a revoke lands, or it reopens it."""
    st = _state()
    path = A if action == "revoke" else B
    with wired(st):
        r = client().post(f"/api/sensors/dataloggers/{path}/{action}", headers=AUTH, json={})
    assert r.status_code == 200
    lock = st.log.index(("sql", "lock", f"ingestion:{TENANT}"))
    assert lock < st.log.index(("sql", "FOR UPDATE"))


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    app = Flask(__name__)
    app.register_blueprint(admin.admin_bp)
    return app.test_client()


def _call(c, tenant="acme", body=None, secret="s3cret"):
    headers = {"X-Internal-Service-Secret": secret} if secret is not None else {}
    return c.post(f"/api/internal/tenants/{tenant}/ingestion", headers=headers,
                  json={"enabled": False} if body is None else body)


@pytest.mark.parametrize("secret", [None, "", "wrong"])
def test_needs_the_secret(internal, secret):
    with patch.object(dataloggers, "set_tenant_ingestion") as f:
        assert _call(internal, secret=secret).status_code == 401
    f.assert_not_called()


def test_without_a_configured_secret_nobody_enters(internal, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "")
    with patch.object(dataloggers, "set_tenant_ingestion") as f:
        assert _call(internal, secret="").status_code == 401
    f.assert_not_called()


@pytest.mark.parametrize("tenant, body", [("acme", {}), ("acme", []), ("acme", {"enabled": "no"}),
                                          ("acme", {"enabled": 0}), ("Acme", None), ("a_b", None)])
def test_bad_requests(internal, tenant, body):
    with patch.object(dataloggers, "set_tenant_ingestion") as f:
        assert _call(internal, tenant, body).status_code == 400
    f.assert_not_called()


@pytest.mark.parametrize("result, status", [({"changed": 1, "failed": []}, 200),
                                            ({"changed": 0, "failed": ["dl_a"]}, 207)])
def test_result(internal, result, status):
    with patch.object(dataloggers, "set_tenant_ingestion", return_value=result) as f:
        r = _call(internal)
    assert (r.status_code, r.get_json()) == (status, result)
    f.assert_called_once_with("acme", False)


def test_unavailable(internal):
    with patch.object(dataloggers, "set_tenant_ingestion", side_effect=RuntimeError("x")):
        assert _call(internal).status_code == 503


def test_account_changes_take_the_tenant_lock_before_asking_its_state():
    cur = MagicMock()
    cur.fetchone.return_value = None
    dataloggers.require_active_tenant(cur, TENANT)
    calls = [c.args for c in cur.execute.call_args_list]
    assert "pg_advisory_xact_lock" in calls[0][0] and calls[0][1] == (f"ingestion:{TENANT}",)
    assert "deleted_at" in calls[1][0]
