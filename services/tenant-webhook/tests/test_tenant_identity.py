"""Which tenant a new account lands in.

A tenant is identified by its canonical id or by the verified primary email,
never by a display name typed by the caller. A new id is inserted, never
upserted; an activation code decides its own tenant.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class FakeDb:
    """The slice of `tenants` that ensure_tenant_record reads and writes."""

    def __init__(self, rows=(), taken_on_insert=()):
        self.rows = {r["tenant_id"]: dict(r) for r in rows}
        self.taken_on_insert = list(taken_on_insert)
        self.updated, self.inserted, self.locked = [], [], []
        self.commits = 0
        self._result = None

    def cursor(self):
        return self

    def close(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def fetchone(self):
        return self._result

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self._result = None
        if "pg_advisory_xact_lock" in sql:
            self.locked.append(params[0])
        elif sql.startswith("SELECT tenant_id, metadata"):
            assert "WHERE metadata->>'primary_email' = %s ORDER BY (deleted_at IS NOT NULL)" in sql
            assert self.locked, "the lookup must run under the account lock"
            hits = [r for r in self.rows.values()
                    if (r.get("metadata") or {}).get("primary_email") == params[0]]
            hits.sort(key=lambda r: r.get("deleted_at") is not None)
            self._result = hits[0] if hits else None
        elif sql.startswith("SELECT 1 FROM tenants"):
            self._result = {"?column?": 1} if params[0] in self.rows else None
        elif sql.startswith("INSERT INTO tenants"):
            tid = params[0]
            if self.taken_on_insert:  # another request wins the race
                self.rows[self.taken_on_insert.pop(0)] = {"tenant_id": tid, "metadata": {}}
            if "DO NOTHING" in sql:
                if tid in self.rows:
                    return
                self.rows[tid] = {"tenant_id": tid, "metadata": json.loads(params[5])}
                self.inserted.append(tid)
            else:
                self.updated.append((tid, params[2]))
            self._result = {"tenant_id": tid}


OWNER = "owner@example.test"
ACME = {"tenant_id": "acme", "deleted_at": None, "metadata": {"primary_email": OWNER}}
OLD = {"tenant_id": "old", "deleted_at": "2026-01-01", "metadata": {"primary_email": OWNER}}


def _ensure(webhook_module, db, email, name, storage=(), **kw):
    svc = webhook_module.webhook_service
    with patch.object(svc, "_apply_admin_context"), \
         patch.object(svc, "_tenant_id_in_use", side_effect=lambda t: t in storage):
        return svc.ensure_tenant_record(db, email, "premium", {}, name, "test", **kw)


class TestEnsureTenantRecord:
    @pytest.mark.parametrize("name", ["acme", "Acme", "ACME ", "Ácme!", None])
    def test_neither_name_nor_email_local_part_resolves_to_a_tenant(self, webhook_module, name):
        db = FakeDb([ACME])
        assert _ensure(webhook_module, db, "acme@elsewhere.test", name) == "acme2"
        assert db.updated == []

    def test_the_primary_email_resolves_to_its_tenant(self, webhook_module):
        db = FakeDb([ACME, {"tenant_id": "other", "metadata": {}}])
        assert _ensure(webhook_module, db, "Owner@example.test", "other") == "acme"
        assert db.inserted == [] and [u[0] for u in db.updated] == ["acme"]

    def test_the_lock_is_per_account(self, webhook_module):
        for mode in ("email", "none", "new_account"):
            db = FakeDb()
            _ensure(webhook_module, db, "New@example.test", "farm", match=mode)
            assert db.locked == ["new@example.test"]

    def test_a_live_tenant_wins_over_a_suspended_one(self, webhook_module):
        db = FakeDb([OLD, ACME])
        assert _ensure(webhook_module, db, OWNER, "x") == "acme"

    def test_a_suspended_tenant_is_refused(self, webhook_module):
        db = FakeDb([OLD])
        with pytest.raises(webhook_module.TenantSuspendedError):
            _ensure(webhook_module, db, OWNER, "x")
        assert db.updated == [] and db.inserted == []
        assert _ensure(webhook_module, db, OWNER, "x", allow_suspended=True) == "old"

    def test_a_lost_race_takes_the_next_id(self, webhook_module):
        db = FakeDb([], taken_on_insert=["farm"])
        assert _ensure(webhook_module, db, "a@example.test", "Farm") == "farm2"
        assert db.updated == []

    @pytest.mark.parametrize("mode", ["none", "new_account"])
    def test_a_taken_id_is_refused(self, webhook_module, mode):
        db = FakeDb([ACME])
        with pytest.raises(webhook_module.TenantExistsError):
            _ensure(webhook_module, db, "stranger@example.test", "acme", match=mode)
        assert db.updated == [] and db.inserted == []

    @pytest.mark.parametrize("mode", ["none", "new_account"])
    def test_a_lost_race_is_refused(self, webhook_module, mode):
        db = FakeDb([], taken_on_insert=["farm"])
        with pytest.raises(webhook_module.TenantExistsError):
            _ensure(webhook_module, db, "a@example.test", "farm", match=mode)

    @pytest.mark.parametrize("tenant", [ACME, OLD])
    def test_a_new_account_needs_a_new_email(self, webhook_module, tenant):
        db = FakeDb([tenant])
        with pytest.raises(webhook_module.AccountExistsError):
            _ensure(webhook_module, db, OWNER, "second", match="new_account")
        assert db.updated == [] and db.inserted == []

    def test_an_admin_may_give_an_owner_a_second_tenant(self, webhook_module):
        db = FakeDb([ACME])
        assert _ensure(webhook_module, db, OWNER, "second", match="none") == "second"
        assert db.updated == []

    @pytest.mark.parametrize("name", ["platform", "Shared", "default", "master"])
    def test_reserved_ids(self, webhook_module, name):
        with pytest.raises(webhook_module.TenantExistsError):
            _ensure(webhook_module, FakeDb(), "a@example.test", name, match="new_account")
        assert _ensure(webhook_module, FakeDb(), "a@example.test", name).endswith("2")

    def test_an_id_with_leftover_storage_is_taken(self, webhook_module):
        with pytest.raises(webhook_module.TenantExistsError):
            _ensure(webhook_module, FakeDb(), "a@example.test", "farm", storage={"farm"},
                    match="new_account")
        assert _ensure(webhook_module, FakeDb(), "a@example.test", "farm",
                       storage={"farm"}) == "farm2"
        # the admin endpoint provisions storage before the record
        assert _ensure(webhook_module, FakeDb(), "a@example.test", "farm",
                       storage={"farm"}, match="none") == "farm"

    def test_unknown_storage_state_stops(self, webhook_module):
        svc = webhook_module.webhook_service
        db = FakeDb()
        with patch.object(svc, "_apply_admin_context"), \
             patch.object(svc, "_tenant_id_in_use", side_effect=RuntimeError("down")), \
             pytest.raises(RuntimeError):
            svc.ensure_tenant_record(db, "a@example.test", "pro", {}, "farm", "t",
                                     match="new_account")
        assert db.inserted == []

    def test_commit_is_the_callers_choice(self, webhook_module):
        for commit, expected in ((True, 1), (False, 0)):
            db = FakeDb()
            _ensure(webhook_module, db, "a@example.test", "farm", commit=commit)
            assert db.commits == expected

    def test_unknown_mode(self, webhook_module):
        with pytest.raises(ValueError):
            _ensure(webhook_module, FakeDb(), "a@example.test", "farm", match="name")


@pytest.fixture
def client(webhook_module):
    return webhook_module.app.test_client()


class FakeOtp:
    def __init__(self, code="123456"):
        self.data = {"registro:otp:a@example.test": code} if code else {}

    def get(self, key):
        return self.data.get(key)

    def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    def expire(self, *_):
        pass

    def delete(self, *keys):
        return sum(self.data.pop(k, None) is not None for k in keys)


class TestRegister:
    BODY = {"email": "A@example.test", "organization_name": "Acme",
            "password": "x" * 12, "otp": "123456", "plan": "Premium"}

    def _post(self, client, webhook_module, body=None, otp=None, ensure=None, kc=None,
              conn=None, available=True):
        svc = webhook_module.webhook_service
        free = MagicMock(side_effect=available) if isinstance(available, Exception) \
            else MagicMock(return_value=available)
        self.conn = conn or MagicMock()
        self.otp = otp or FakeOtp()
        ensure = ensure or MagicMock(return_value="acme")
        with patch("redis.Redis.from_url", return_value=self.otp), \
             patch.object(svc, "get_db_connection", return_value=self.conn), \
             patch.object(webhook_module, "_tenant_id_available", free), \
             patch.object(svc, "_apply_admin_context"), \
             patch.object(svc, "ensure_tenant_record", ensure), \
             patch.object(svc, "create_keycloak_user",
                          return_value=kc or {"success": True, "user_id": "u"}) as self.kc, \
             patch.object(svc, "provision_tenant_core",
                          return_value=webhook_module.TenantProvisioning("t", True, "k")) as self.prov, \
             patch.object(webhook_module, "_delete_keycloak_user_best_effort") as self.undo, \
             patch.object(webhook_module, "_send_email_async"), \
             patch.object(webhook_module, "audit_log"):
            resp = client.post("/webhook/register", json=self.BODY if body is None else body)
        self.ensure = ensure
        return resp

    def test_only_creates(self, client, webhook_module):
        resp = self._post(client, webhook_module)
        assert resp.status_code == 201, resp.data
        kw = self.ensure.call_args.kwargs
        assert (kw["match"], kw["commit"], kw["plan"], kw["email"]) == (
            "new_account", False, "premium", "a@example.test")
        assert self.kc.call_args.kwargs["fail_if_exists"] is True
        assert self.kc.call_args.kwargs["tenant_id"] == "acme"
        self.conn.commit.assert_called_once_with()

    @pytest.mark.parametrize("available, status", [(False, 409), (RuntimeError("down"), 503)])
    def test_the_code_is_not_spent_on_an_unavailable_name(
        self, client, webhook_module, available, status
    ):
        resp = self._post(client, webhook_module, available=available)
        assert resp.status_code == status
        assert "registro:otp:a@example.test" in self.otp.data
        self.ensure.assert_not_called()

    @pytest.mark.parametrize("error, code", [("AccountExistsError", "EMAIL_EXISTS"),
                                             ("TenantExistsError", "TENANT_EXISTS")])
    def test_taken(self, client, webhook_module, error, code):
        ensure = MagicMock(side_effect=getattr(webhook_module, error)("x"))
        resp = self._post(client, webhook_module, ensure=ensure)
        assert resp.status_code == 409 and resp.get_json()["error_code"] == code
        self.kc.assert_not_called()
        self.prov.assert_not_called()
        self.conn.commit.assert_not_called()

    @pytest.mark.parametrize("kc, status", [({"success": False, "conflict": True}, 409),
                                            ({"success": False, "error": "x"}, 500)])
    def test_no_account_no_tenant(self, client, webhook_module, kc, status):
        resp = self._post(client, webhook_module, kc=kc)
        assert resp.status_code == status
        self.conn.rollback.assert_called()
        self.conn.commit.assert_not_called()
        self.prov.assert_not_called()

    def test_no_tenant_no_account(self, client, webhook_module):
        conn = MagicMock()
        conn.commit.side_effect = RuntimeError("down")
        resp = self._post(client, webhook_module, conn=conn)
        assert resp.status_code == 500
        self.undo.assert_called_once_with("u", "register_tenant")
        self.prov.assert_not_called()

    def test_wrong_code_does_nothing_and_five_burn_it(self, client, webhook_module):
        otp = FakeOtp()
        for _ in range(5):
            resp = self._post(client, webhook_module, {**self.BODY, "otp": "000000"}, otp=otp)
            assert resp.status_code == 401
            self.ensure.assert_not_called()
        assert self._post(client, webhook_module, otp=otp).status_code == 401

    def test_four_wrong_codes_do_not_burn_it(self, client, webhook_module):
        otp = FakeOtp()
        for _ in range(4):
            self._post(client, webhook_module, {**self.BODY, "otp": "000000"}, otp=otp)
        assert self._post(client, webhook_module, otp=otp).status_code == 201

    def test_a_code_registers_once(self, client, webhook_module):
        class Lost(FakeOtp):
            def delete(self, *keys):  # the other submit deleted it first
                return 0
        resp = self._post(client, webhook_module, otp=Lost())
        assert resp.status_code == 401
        self.ensure.assert_not_called()

    @pytest.mark.parametrize("body", [
        [], "x", {}, {**BODY, "email": ["a@example.test"]}, {**BODY, "otp": 123456},
        {**BODY, "plan": "basic"}, {**BODY, "organization_name": "  "},
        {**BODY, "organization_name": "!!"}])
    def test_bad_bodies_keep_the_code(self, client, webhook_module, body):
        resp = self._post(client, webhook_module, body)
        assert resp.status_code == 400, resp.data
        assert "registro:otp:a@example.test" in self.otp.data
        self.ensure.assert_not_called()


class TestCheckTenant:
    def _post(self, client, webhook_module, name, conn="ok", row=None, storage=False):
        svc = webhook_module.webhook_service
        db = None
        if conn == "ok":
            db = MagicMock()
            db.cursor.return_value.fetchone.return_value = row
        with patch.object(svc, "get_db_connection", return_value=db), \
             patch.object(svc, "_tenant_id_in_use", return_value=storage):
            return client.post("/webhook/register/check-tenant", json={"name": name})

    def test_free(self, client, webhook_module):
        assert self._post(client, webhook_module, "farm").get_json()["available"] is True

    @pytest.mark.parametrize("kw", [{"row": {"tenant_id": "farm"}}, {"storage": True}])
    def test_taken(self, client, webhook_module, kw):
        assert self._post(client, webhook_module, "farm", **kw).get_json()["available"] is False

    def test_unknown_is_not_available(self, client, webhook_module):
        assert self._post(client, webhook_module, "farm", conn=None).status_code == 503


class TestInUse:
    def _accounts(self, webhook_module, users, status=200, token="t"):
        svc = webhook_module.webhook_service
        resp = MagicMock(status_code=status)
        resp.json.return_value = users
        resp.raise_for_status.side_effect = RuntimeError(status) if status >= 400 else None
        with patch.object(svc, "get_keycloak_token", return_value=token), \
             patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
             patch.object(webhook_module.requests, "get", return_value=resp):
            return svc._keycloak_has_accounts("acme")

    def test_accounts_by_exact_tenant_id(self, webhook_module):
        assert self._accounts(webhook_module, [{"attributes": {"tenant_id": ["acme"]}}])
        for users in ([], [{}], [{"attributes": {"tenant_id": ["acme2"]}}]):
            assert self._accounts(webhook_module, users) is False

    @pytest.mark.parametrize("kw", [{"status": 500}, {"token": None}])
    def test_accounts_unknown(self, webhook_module, kw):
        with pytest.raises(RuntimeError):
            self._accounts(webhook_module, [], **kw)

    @pytest.mark.parametrize("docs, expected", [({}, False), ({"entities": None}, False),
                                                ({"entities": {"_id": 1}}, True),
                                                ({"entities": None, "csubs": {"_id": 1}}, True)])
    def test_broker_data(self, webhook_module, docs, expected):
        db = MagicMock()
        db.list_collection_names.return_value = list(docs)
        db.__getitem__.side_effect = lambda n: MagicMock(find_one=MagicMock(return_value=docs[n]))
        mongo = MagicMock()
        mongo.get_database.return_value = db
        with patch.object(webhook_module.pymongo, "MongoClient", return_value=mongo):
            assert webhook_module.webhook_service._broker_has_data("acme") is expected

    @pytest.mark.parametrize("tid, data, accounts, expected", [
        ("acme", False, False, False), ("acme", True, False, True),
        ("acme", False, True, True), ("platform", False, False, True)])
    def test_in_use(self, webhook_module, tid, data, accounts, expected):
        svc = webhook_module.webhook_service
        with patch.object(svc, "_broker_has_data", return_value=data), \
             patch.object(svc, "_keycloak_has_accounts", return_value=accounts):
            assert svc._tenant_id_in_use(tid) is expected


def test_a_new_code_starts_with_fresh_tries(client, webhook_module):
    svc = webhook_module.webhook_service
    store = MagicMock()
    no_users = MagicMock(status_code=200)
    no_users.json.return_value = []
    with patch("redis.Redis.from_url", return_value=store), \
         patch.object(svc, "get_keycloak_token", return_value="t"), \
         patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
         patch.object(webhook_module.requests, "get", return_value=no_users), \
         patch.object(webhook_module, "_email_post", return_value=MagicMock(status_code=200)):
        client.post("/webhook/register/request-otp", json={"email": "a@example.test"})
    store.delete.assert_called_once_with("registro:otp-tries:a@example.test")


class TestActivate:
    BODY = {"code": "NEK-AAAA-BBBB-CCCC", "email": "a@example.test",
            "tenant_name": "acme", "password": "x" * 12}
    PLAN = {"id": 1, "code": "NEK-AAAA-BBBB-CCCC", "email": "a@example.test", "plan": "basic",
            "max_users": 1, "max_robots": 1, "max_sensors": 1, "expires_at": None,
            "duration_days": 30, "order_id": None}

    def _post(self, client, webhook_module, code_tenant, conn="ok", ensure="own-farm",
              suspended=False, primary="a@example.test", missing=False, body=None):
        svc = webhook_module.webhook_service
        db = None
        if conn == "ok":
            db = MagicMock()
            db.cursor.return_value.fetchone.return_value = {
                "tenant_id": code_tenant, "deleted_at": "2026-01-01" if suspended else None,
                "primary_email": primary, "id": 1}
            if missing:
                db.cursor.return_value.fetchone.return_value = None
        ensure_mock = MagicMock(return_value=ensure)
        with patch.object(svc, "validate_activation_code",
                          return_value={**self.PLAN, "tenant_id": code_tenant}), \
             patch.object(svc, "get_db_connection", return_value=db), \
             patch.object(svc, "_apply_admin_context"), \
             patch.object(svc, "ensure_tenant_record", ensure_mock), \
             patch.object(svc, "provision_tenant_core",
                          return_value=webhook_module.TenantProvisioning("t", True, "k")) as prov, \
             patch.object(svc, "create_keycloak_user",
                          return_value={"success": True, "user_id": "u"}) as kc, \
             patch.object(svc, "mark_activation_code_used", return_value=True) as used, \
             patch.object(webhook_module, "grafana_manager", None), \
             patch.object(webhook_module, "_send_email_async"), \
             patch.object(webhook_module, "_email_post"), \
             patch.object(webhook_module, "audit_log"):
            resp = client.post("/webhook/activate", json=body or self.BODY)
        return resp, prov, kc, used, ensure_mock

    def test_the_code_decides_the_tenant(self, client, webhook_module):
        resp, prov, kc, used, _ = self._post(client, webhook_module, "own-farm")
        assert resp.status_code == 200, resp.data
        assert prov.call_args.args[0] == "own-farm"
        assert kc.call_args.args[1] == "own-farm"
        assert used.call_args.args[1] == "own-farm"
        assert resp.get_json()["tenant_id"] == "own-farm"

    def test_a_code_without_tenant_gets_a_record_before_any_storage(self, client, webhook_module):
        resp, prov, kc, _, ensure = self._post(client, webhook_module, None, ensure="acme2")
        assert resp.status_code == 200, resp.data
        assert ensure.call_args.kwargs["match"] == "email"
        assert prov.call_args.args[0] == "acme2"
        assert kc.call_args.args[1] == "acme2"

    @pytest.mark.parametrize("code_tenant", [None, "own-farm"])
    def test_without_database_nothing_is_provisioned(self, client, webhook_module, code_tenant):
        resp, prov, kc, used, _ = self._post(client, webhook_module, code_tenant, conn=None)
        assert resp.status_code == 503, resp.data
        prov.assert_not_called()
        kc.assert_not_called()
        used.assert_not_called()

    @pytest.mark.parametrize("state", [{"suspended": True}, {"missing": True},
                                       {"primary": "owner@example.test"}, {"primary": None}])
    def test_an_unavailable_tenant_is_not_activated(self, client, webhook_module, state):
        resp, prov, kc, used, _ = self._post(client, webhook_module, "own-farm", **state)
        assert resp.status_code == 409, resp.data
        prov.assert_not_called()
        kc.assert_not_called()
        used.assert_not_called()

    def test_a_suspended_account_without_code_tenant(self, client, webhook_module):
        svc = webhook_module.webhook_service
        with patch.object(svc, "validate_activation_code",
                          return_value={**self.PLAN, "tenant_id": None}), \
             patch.object(svc, "get_db_connection", return_value=MagicMock()), \
             patch.object(svc, "ensure_tenant_record",
                          side_effect=webhook_module.TenantSuspendedError("x")), \
             patch.object(svc, "provision_tenant_core") as prov:
            assert client.post("/webhook/activate", json=self.BODY).status_code == 409
        prov.assert_not_called()

    @pytest.mark.parametrize("body", [None, {}, [], {"code": "x"}, {**BODY, "email": ["a"]},
                                      {**BODY, "password": ""}])
    def test_bad_requests_are_400(self, client, webhook_module, body):
        assert client.post("/webhook/activate", json=body).status_code == 400


@pytest.mark.parametrize("change", [{"status": "active"}, None])
def test_a_code_is_bound_to_its_email_and_single_use(webhook_module, change):
    svc = webhook_module.webhook_service
    conn = MagicMock()
    row = {"id": 1, "code": "C", "email": "a@example.test", "plan": "basic", "status": "pending",
           "max_users": 1, "max_robots": 1, "max_sensors": 1, "expires_at": None,
           "duration_days": 30, "order_id": None, "tenant_id": "own-farm"}
    conn.cursor.return_value.fetchone.return_value = {**row, **change} if change else None
    with patch.object(svc, "get_db_connection", return_value=conn):
        assert svc.validate_activation_code("c", "A@example.test") is None
    sql, params = conn.cursor.return_value.execute.call_args.args
    assert "WHERE code = %s AND email = %s" in " ".join(sql.split())
    assert params == ("C", "a@example.test")


def test_the_code_carries_its_tenant(webhook_module):
    svc = webhook_module.webhook_service
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = {
        "id": 1, "code": "C", "email": "a@example.test", "plan": "basic", "status": "pending",
        "max_users": 1, "max_robots": 1, "max_sensors": 1, "expires_at": None,
        "duration_days": 30, "order_id": None, "tenant_id": "own-farm"}
    with patch.object(svc, "get_db_connection", return_value=conn):
        assert svc.validate_activation_code("c", "a@example.test")["tenant_id"] == "own-farm"
    assert "tenant_id" in conn.cursor.return_value.execute.call_args.args[0]


class TestIssuers:
    """Admin and shop codes follow the email, never a typed name."""

    @pytest.mark.parametrize("func", ["generate_activation_code", "woocommerce_webhook"])
    def test_match_mode(self, func):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "enhanced-tenant-webhook.py"
        tree = ast.parse(src.read_text())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
        call, = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "ensure_tenant_record"]
        kw = {k.arg: getattr(k.value, "value", None) for k in call.keywords}
        assert kw["match"] == "email" and kw["allow_suspended"] is True


class TestNewKeycloakAccount:
    """With fail_if_exists an existing account is never adopted."""

    def _create(self, webhook_module, search, post, group="g", password_ok=True,
                roles=("TenantAdmin",)):
        svc = webhook_module.webhook_service
        mapped = self._resp(200, [{"name": r} for r in roles])
        pw = MagicMock()
        if not password_ok:
            pw.raise_for_status.side_effect = RuntimeError("policy")
        with patch.object(svc, "get_keycloak_token", return_value="t"), \
             patch.object(svc, "_get_keycloak_base_url", return_value="http://kc"), \
             patch.object(svc, "_ensure_tenant_group", return_value=group), \
             patch.object(svc, "_ensure_group_has_roles"), \
             patch.object(svc, "_add_user_to_group"), \
             patch.object(svc, "_assign_role_to_user") as role, \
             patch.object(webhook_module.requests, "get",
                          side_effect=lambda url, **_: mapped if "role-mappings" in url else search), \
             patch.object(webhook_module.requests, "post", return_value=post), \
             patch.object(webhook_module.requests, "put", return_value=pw) as put, \
             patch.object(webhook_module, "_delete_keycloak_user_best_effort") as undo:
            result = svc.create_keycloak_user("a@example.test", "acme",
                                              {"plan": "pro", "max_users": 1, "max_robots": 1,
                                               "max_sensors": 1},
                                              "x" * 12, is_owner=True, fail_if_exists=True)
        return result, role, put, undo

    @staticmethod
    def _resp(status, body=None, location=None):
        r = MagicMock(status_code=status, headers={"Location": location or ""})
        r.json.return_value = body if body is not None else []
        r.raise_for_status.side_effect = RuntimeError(status) if status >= 400 else None
        return r

    def test_created(self, webhook_module):
        result, role, _, undo = self._create(
            webhook_module, self._resp(200), self._resp(201, location="http://kc/users/new"))
        assert result["success"] and result["user_id"] == "new"
        assert role.call_args.args[1:] == ("new", "TenantAdmin")
        undo.assert_not_called()

    @pytest.mark.parametrize("search, post", [((200, [{"id": "old"}]), 201), ((200, []), 409)])
    def test_existing_is_a_conflict(self, webhook_module, search, post):
        result, role, put, undo = self._create(
            webhook_module, self._resp(*search), self._resp(post, location="http://kc/users/new"))
        assert result.get("conflict") is True
        role.assert_not_called()
        put.assert_not_called()
        undo.assert_not_called()

    def test_unknown_is_a_failure(self, webhook_module):
        result, role, put, _ = self._create(
            webhook_module, self._resp(500), self._resp(201, location="http://kc/users/new"))
        assert result["success"] is False
        role.assert_not_called()
        put.assert_not_called()

    @pytest.mark.parametrize("kw", [{"group": None}, {"password_ok": False}, {"roles": ()}])
    def test_a_half_made_account_is_removed(self, webhook_module, kw):
        result, _, _, undo = self._create(
            webhook_module, self._resp(200), self._resp(201, location="http://kc/users/new"), **kw)
        assert result["success"] is False
        undo.assert_called_once_with("new", "create_keycloak_user")
