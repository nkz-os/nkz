"""GET /api/internal/modules/<id>/tenants — where a module is installed."""

from unittest.mock import MagicMock

# Reuse the entity-manager harness (module-level mocks) and its `client` fixture.
from tests.test_module_publish import client  # noqa: F401


def test_requires_internal_secret(client):  # noqa: F811
    assert client.get("/api/internal/modules/risk/tenants").status_code == 401
    r = client.get("/api/internal/modules/risk/tenants", headers={"X-Internal-Service-Secret": "nope"})
    assert r.status_code == 401


def test_unknown_module_is_404_not_empty(client, monkeypatch):  # noqa: F811
    import blueprints.modules as m

    monkeypatch.setattr(m, "_module_tenant_states", lambda mid: None)
    r = client.get("/api/internal/modules/nope/tenants", headers={"X-Internal-Service-Secret": "test-secret"})
    assert r.status_code == 404


def test_states_per_tenant(client, monkeypatch):  # noqa: F811
    import blueprints.modules as m

    states = {"a": "enabled", "b": "disabled", "c": "not_installed"}
    monkeypatch.setattr(m, "_module_tenant_states", lambda mid: states)
    r = client.get("/api/internal/modules/risk/tenants", headers={"X-Internal-Service-Secret": "test-secret"})
    assert r.status_code == 200
    assert r.get_json() == {"module_id": "risk", "tenants": states}


def test_db_failure_is_503_never_empty(client, monkeypatch):  # noqa: F811
    import blueprints.modules as m

    def boom(mid):
        raise RuntimeError("db down")

    monkeypatch.setattr(m, "_module_tenant_states", boom)
    r = client.get("/api/internal/modules/risk/tenants", headers={"X-Internal-Service-Secret": "test-secret"})
    assert r.status_code == 503
    assert "tenants" not in r.get_json()


def _fake_conn(module_row, rows, orphans=0):
    cur = MagicMock()
    cur.fetchone.side_effect = [module_row, {"orphans": orphans}]
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_state_mapping_from_rows(monkeypatch):
    import blueprints.modules as m

    conn, _ = _fake_conn(
        {"?column?": 1},
        [
            {"tenant_id": "a", "installed": True, "enabled": True},
            {"tenant_id": "b", "installed": True, "enabled": False},
            {"tenant_id": "c", "installed": False, "enabled": False},
        ],
    )
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda c: None)
    assert m._module_tenant_states("risk") == {"a": "enabled", "b": "disabled", "c": "not_installed"}


def test_unregistered_module_returns_none(monkeypatch):
    import blueprints.modules as m

    conn, cur = _fake_conn(None, [])
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda c: None)
    assert m._module_tenant_states("nope") is None
    cur.fetchall.assert_not_called()


def test_connection_is_returned_on_db_error(monkeypatch):
    import blueprints.modules as m

    conn, cur = _fake_conn(None, [])
    cur.execute.side_effect = RuntimeError("boom")
    returned = []
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda c: returned.append(c))
    try:
        m._module_tenant_states("risk")
    except RuntimeError:
        pass
    else:
        raise AssertionError("must raise, not return")
    assert returned == [conn]


def _webhook_conn(url):
    cur = MagicMock()
    cur.fetchone.return_value = {"webhook_url": url}
    conn = MagicMock()
    conn.cursor.return_value = cur
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_dispatch_sends_verifiable_token(monkeypatch):
    import blueprints.modules as m
    from nkz_platform_sdk.notify_auth import verify_notification

    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "test-secret")
    monkeypatch.setattr(
        m, "get_db_connection_with_tenant",
        lambda t: _webhook_conn("http://risk-api-service:8000/api/risk/internal/lifecycle"),
    )
    sent = {}
    monkeypatch.setattr(
        m.requests, "post",
        lambda url, data, headers, timeout: sent.update(headers) or MagicMock(status_code=202),
    )
    m._dispatch_module_lifecycle_webhook_if_configured("risk", "Tenant-A", True)
    delivered = {**sent, "Host": "risk-api-service:8000"}
    assert verify_notification(delivered, secret="test-secret", accept_master=False)


def test_auto_install_hint_only_with_a_tenant(monkeypatch):
    import blueprints.modules as m

    hinted = []
    monkeypatch.setattr(m, "_dispatch_module_lifecycle_webhook_if_configured",
                        lambda **kw: hinted.append(kw))
    m._hint_auto_install("demo", "", "ci")
    m._hint_auto_install("demo", "test-tenant", "")
    assert hinted == [{"module_id": "demo", "tenant_id": "test-tenant",
                       "enabled": True, "user_email": None}]


def test_upload_hints_after_commit():
    """The hint fires only once the install row is committed (source-order guard)."""
    import inspect

    import blueprints.modules as m

    src = inspect.getsource(m._upload_dist_and_activate)
    assert "_hint_auto_install(" in src
    assert src.index("conn.commit()") < src.index("_hint_auto_install(")


def test_rls_cannot_hide_rows(monkeypatch):
    """Row security must raise (→ 503), never filter install rows into "not_installed"."""
    import blueprints.modules as m

    conn, cur = _fake_conn({"?column?": 1}, [])
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda c: None)
    m._module_tenant_states("risk")
    first_sql = cur.execute.call_args_list[0].args[0]
    assert first_sql.strip() == "SET LOCAL row_security = off"
    conn.rollback.assert_called()


def test_orphan_install_rows_are_logged(monkeypatch, caplog):
    import logging

    import blueprints.modules as m

    conn, _ = _fake_conn({"?column?": 1}, [], orphans=2)
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda c: None)
    with caplog.at_level(logging.WARNING):
        m._module_tenant_states("risk")
    assert any("2 install rows" in r.getMessage() for r in caplog.records)
