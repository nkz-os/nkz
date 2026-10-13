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


def _fake_conn(module_row, rows):
    cur = MagicMock()
    cur.fetchone.return_value = module_row
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
