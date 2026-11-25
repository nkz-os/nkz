"""Suspending closes the tenant's logins everywhere; restoring opens exactly those."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class Keycloak:
    def __init__(self, users, admins=(), fail_put=(), fail_list=False, page=100):
        self.users = {u["id"]: u for u in users}
        self.admins, self.fail_put, self.fail_list, self.page = set(admins), set(fail_put), fail_list, page
        self.log = []

    @staticmethod
    def _resp(status, body=None):
        r = MagicMock(status_code=status)
        r.json.return_value = body
        r.raise_for_status.side_effect = RuntimeError(status) if status >= 400 else None
        return r

    def get(self, url, params=None, **_):
        params = params or {}
        if url.endswith("/role-mappings/realm/composite"):
            uid = url.split("/")[-4]
            return self._resp(200, [{"name": "Farmer"}]
                              + ([{"name": "PlatformAdmin"}] if uid in self.admins else []))
        if url.endswith("/users"):
            if self.fail_list:
                return self._resp(500)
            return self._resp(200, list(self.users.values())[params.get("first", 0):][:params["max"]])
        uid = url.rsplit("/", 1)[1]
        return self._resp(200, dict(self.users[uid])) if uid in self.users else self._resp(404)

    def put(self, url, json=None, **_):
        uid = url.rsplit("/", 1)[1]
        if uid in self.fail_put:
            return self._resp(500)
        self.users[uid] = json
        self.log.append((uid, json["enabled"]))
        return self._resp(204)

    def post(self, url, **_):
        self.log.append((url.split("/")[-2], "logout"))
        return self._resp(204)


def user(uid, tenant="acme", enabled=True):
    return {"id": uid, "enabled": enabled, "email": f"{uid}@example.test",
            "attributes": {"tenant_id": [tenant], "other": ["kept"]}}


def run(webhook_module, kc, enabled, saved=(), caller=None):
    svc = webhook_module.webhook_service
    store = {"ids": list(saved)}

    def load(tenant):
        assert tenant == "acme"
        return list(store["ids"])

    def save(tenant, ids):
        store["ids"] = list(ids)
        kc.log.append(("saved", tuple(ids)))

    with patch.object(svc, "get_keycloak_token", return_value="t"), \
         patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
         patch.object(webhook_module, "_load_suspended_accounts", load), \
         patch.object(webhook_module, "_save_suspended_accounts", save), \
         patch.object(webhook_module.requests, "get", kc.get), \
         patch.object(webhook_module.requests, "put", kc.put), \
         patch.object(webhook_module.requests, "post", kc.post):
        return webhook_module._set_tenant_accounts("acme", enabled, caller), store["ids"]


def test_suspend_disables_only_the_tenants_enabled_users_and_ends_their_sessions(webhook_module):
    kc = Keycloak([user("a"), user("b", enabled=False), user("c", tenant="acme2"), user("d")])
    assert run(webhook_module, kc, False) == ("ok", ["a", "d"])
    assert {u: kc.users[u]["enabled"] for u in "abcd"} == {"a": False, "b": False, "c": True, "d": False}
    assert kc.users["a"]["attributes"] == {"tenant_id": ["acme"], "other": ["kept"]}
    assert ("a", "logout") in kc.log and ("d", "logout") in kc.log and ("c", "logout") not in kc.log


def test_the_list_is_saved_before_anybody_is_disabled(webhook_module):
    kc = Keycloak([user("a")])
    run(webhook_module, kc, False)
    assert kc.log[0] == ("saved", ("a",))


def test_platform_admins_are_never_disabled(webhook_module):
    kc = Keycloak([user("a"), user("root")], admins=["root"])
    assert run(webhook_module, kc, False) == ("ok", ["a"])
    assert kc.users["root"]["enabled"] is True


def test_the_caller_is_never_disabled(webhook_module):
    kc = Keycloak([user("a"), user("me")])
    assert run(webhook_module, kc, False, caller="me") == ("ok", ["a"])
    assert kc.users["me"]["enabled"] is True


def test_each_one_leaves_the_list_as_it_is_enabled(webhook_module):
    kc = Keycloak([user("a", enabled=False), user("b", enabled=False)])
    run(webhook_module, kc, True, saved=["a", "b"])
    assert [e[1] for e in kc.log if e[0] == "saved"] == [("b",), ()]


def test_the_token_is_renewed_on_a_long_list(webhook_module):
    kc = Keycloak([user(f"u{i}") for i in range(5)])
    clock = iter(range(0, 100000, 4))
    tokens = MagicMock(return_value="t")
    svc = webhook_module.webhook_service
    with patch.object(webhook_module.time, "monotonic", lambda: next(clock)), \
         patch.object(svc, "get_keycloak_token", tokens), \
         patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
         patch.object(webhook_module, "_load_suspended_accounts", return_value=[]), \
         patch.object(webhook_module, "_save_suspended_accounts"), \
         patch.object(webhook_module.requests, "get", kc.get), \
         patch.object(webhook_module.requests, "put", kc.put), \
         patch.object(webhook_module.requests, "post", kc.post):
        webhook_module._set_tenant_accounts("acme", False)
    assert tokens.call_count > 2


def test_it_stops_at_its_time_budget(webhook_module):
    kc = Keycloak([user("a"), user("b")])
    with patch.object(webhook_module, "SUSPENSION_BUDGET_S", -1):
        result, ids = run(webhook_module, kc, False)
    assert result == "failed" and ids == ["a", "b"]
    assert kc.users["a"]["enabled"] is True


def test_every_page_is_read(webhook_module):
    kc = Keycloak([user(f"u{i}") for i in range(250)])
    result, ids = run(webhook_module, kc, False)
    assert result == "ok" and len(ids) == 250
    assert not any(u["enabled"] for u in kc.users.values())


def test_restore_enables_exactly_those(webhook_module):
    kc = Keycloak([user("a", enabled=False), user("b", enabled=False)])
    assert run(webhook_module, kc, True, saved=["a"]) == ("ok", [])
    assert kc.users["a"]["enabled"] is True and kc.users["b"]["enabled"] is False


def test_restore_leaves_alone_who_is_gone_or_moved(webhook_module):
    kc = Keycloak([user("moved", tenant="tenant-b", enabled=False), user("a", enabled=False)])
    assert run(webhook_module, kc, True, saved=["gone", "moved", "a"]) == ("ok", [])
    assert kc.users["moved"]["enabled"] is False and kc.users["a"]["enabled"] is True


def test_what_could_not_be_restored_stays_listed(webhook_module):
    kc = Keycloak([user("a", enabled=False), user("b", enabled=False)], fail_put=["a"])
    assert run(webhook_module, kc, True, saved=["a", "b"]) == ("failed", ["a"])


def test_suspending_again_keeps_those_still_pending(webhook_module):
    kc = Keycloak([user("a", enabled=False), user("b")])
    assert run(webhook_module, kc, False, saved=["a"]) == ("ok", ["a", "b"])


def test_one_failure_does_not_stop_the_others(webhook_module):
    kc = Keycloak([user("a"), user("b")], fail_put=["a"])
    result, ids = run(webhook_module, kc, False)
    assert result == "failed" and kc.users["b"]["enabled"] is False and "a" in ids


def test_an_unreadable_keycloak_changes_nothing(webhook_module):
    kc = Keycloak([user("a")], fail_list=True)
    assert run(webhook_module, kc, False) == ("failed", [])
    assert kc.log == []


@pytest.mark.parametrize("enabled", [True, False])
def test_no_token(webhook_module, enabled):
    with patch.object(webhook_module.webhook_service, "get_keycloak_token", return_value=None):
        assert webhook_module._set_tenant_accounts("acme", enabled) == "failed"


def test_storage_of_the_list(webhook_module):
    svc = webhook_module.webhook_service
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = {"ids": ["a"]}
    with patch.object(svc, "get_db_connection", return_value=conn), patch.object(svc, "_apply_admin_context") as svc_context:
        assert webhook_module._load_suspended_accounts("acme") == ["a"]
        webhook_module._save_suspended_accounts("acme", ["a", "b"])
        webhook_module._save_suspended_accounts("acme", [])
    sqls = [" ".join(c.args[0].split()) for c in conn.cursor.return_value.execute.call_args_list]
    params = [c.args[1] for c in conn.cursor.return_value.execute.call_args_list]
    assert "jsonb_build_object('suspended_accounts', %s::jsonb)" in sqls[1]
    assert params[1] == (json.dumps(["a", "b"]), "acme")
    assert "- 'suspended_accounts'" in sqls[2] and params[2] == ("acme",)
    assert all(q.endswith("WHERE tenant_id = %s") for q in sqls)
    assert params[0] == ("acme",)
    assert conn.commit.call_count == 2 and svc_context.call_count == 3


@pytest.mark.parametrize("tenant, expected", [("acme", True), ("tenant-b", False), ("", False)])
def test_who_was_closed_by_the_suspension(webhook_module, tenant, expected):
    lists = {"acme": ["a", "b"], "tenant-b": ["b"]}
    with patch.object(webhook_module, "_load_suspended_accounts", lists.__getitem__):
        assert webhook_module._closed_by_suspension(tenant, "a") is expected


def test_forgetting_keeps_the_others(webhook_module):
    with patch.object(webhook_module, "_load_suspended_accounts", return_value=["a", "b"]), \
         patch.object(webhook_module, "_save_suspended_accounts") as save:
        webhook_module._forget_suspended_account("acme", "a")
    save.assert_called_once_with("acme", ["b"])


def test_forgetting_never_fails_the_move(webhook_module):
    with patch.object(webhook_module, "_load_suspended_accounts", side_effect=RuntimeError("db")):
        webhook_module._forget_suspended_account("acme", "a")


def move(webhook_module, view, kc, closed):
    svc = webhook_module.webhook_service
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = {"tenant_id": "tenant-b", "deleted_at": None}
    body = {"tenant_id": "tenant-b", "user_id": "a", "user_email": "a@example.test",
            "email": "a@example.test", "roles": ["Farmer"]}
    with patch.object(svc, "get_db_connection", return_value=conn), \
         patch.object(svc, "_apply_admin_context"), \
         patch.object(svc, "get_keycloak_token", return_value="t"), \
         patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
         patch.object(svc, "find_keycloak_user_by_email",
                      return_value={"success": True, "user_id": "a"}), \
         patch.object(svc, "_ensure_tenant_group", return_value="grp"), \
         patch.object(svc, "_add_user_to_group"), patch.object(svc, "_assign_role_to_user"), \
         patch.object(webhook_module, "_closed_by_suspension", return_value=closed) as was, \
         patch.object(webhook_module, "_forget_suspended_account") as forget, \
         patch.object(webhook_module.requests, "get", kc.get), \
         patch.object(webhook_module.requests, "put", kc.put), \
         patch.object(webhook_module, "audit_log"), \
         webhook_module.app.test_request_context("/", method="PUT", json=body):
        webhook_module.g.current_user = {}
        fn = getattr(webhook_module, view).__wrapped__
        _, status = fn("a" if view == "reassign_user_tenant" else "tenant-b")
    if status == 200:
        was.assert_called_once_with("acme", "a")
    return status, forget


MOVES = pytest.mark.parametrize("view", ["reassign_user_tenant", "assign_user_to_tenant"])


@MOVES
@pytest.mark.parametrize("closed", [True, False])
def test_moving_opens_a_login_only_if_the_suspension_closed_it(webhook_module, view, closed):
    kc = Keycloak([user("a", enabled=False)])
    status, forget = move(webhook_module, view, kc, closed)
    assert status == 200 and kc.users["a"]["enabled"] is closed
    assert kc.users["a"]["attributes"]["tenant_id"] == ["tenant-b"]
    assert forget.call_args_list == ([(("acme", "a"),)] if closed else [])


@MOVES
def test_a_move_keycloak_refused_stays_listed(webhook_module, view):
    kc = Keycloak([user("a", enabled=False)], fail_put=["a"])
    status, forget = move(webhook_module, view, kc, True)
    assert status >= 500
    forget.assert_not_called()


@pytest.mark.parametrize("call", ["_load_suspended_accounts", "_save_suspended_accounts"])
def test_storage_needs_the_database(webhook_module, call):
    with patch.object(webhook_module.webhook_service, "get_db_connection", return_value=None), \
         pytest.raises(RuntimeError):
        getattr(webhook_module, call)(*(["acme"] if "load" in call else ["acme", []]))
