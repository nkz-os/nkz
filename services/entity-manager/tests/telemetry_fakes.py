"""In-memory stand-ins for the datalogger tables, dynsec and the IoT Agent helper.

The fake connection keeps a working copy per connection: `commit()` publishes it,
`rollback()` or leaving the block discards it, so tests can assert what really
survives a failure. Every observable step also lands in `state.log`, in order.
"""
import copy
import re
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock


class FakeState:
    def __init__(self):
        self.dataloggers = {}
        self.devices = []
        self.profiles = {}
        self.log = []
        self.missing_clients = set()
        self.role_topics = {}
        self.fail = {}
        self.iota_update_fail_for = set()

    @staticmethod
    def unique_violation():
        import psycopg2.errors
        return psycopg2.errors.UniqueViolation("duplicate")


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    @property
    def rowcount(self):
        return self._rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        w = self.conn.working
        log = self.conn.state.log
        self._rows = []
        self._rowcount = 0
        if "FOR UPDATE" in sql:
            log.append(("sql", "FOR UPDATE"))
        if sql.startswith("INSERT INTO iot_dataloggers"):
            tenant, name, kind, username = params
            did = str(uuid.uuid4())
            w["dataloggers"][did] = dict(id=did, tenant_id=tenant, name=name, kind=kind,
                                         mqtt_username=username, status="active",
                                         created_at=None, credentials_rotated_at=None,
                                         revoked_at=None)
            self._rows = [{"id": did}]
        elif sql.startswith("SELECT") and "FROM iot_dataloggers" in sql:
            if "WHERE id" in sql:
                row = w["dataloggers"].get(str(params[0]))
                ok = row and row["tenant_id"] == params[1]
                self._rows = [dict(row)] if ok else []
            else:
                self._rows = [dict(r) for r in w["dataloggers"].values()
                              if r["tenant_id"] == params[0]]
        elif sql.startswith("UPDATE iot_dataloggers"):
            row = w["dataloggers"].get(str(params[0]))
            if row and row["tenant_id"] == params[1]:
                if "'revoked'" in sql:
                    row["status"] = "revoked"
                elif "'active'" in sql:
                    row["status"] = "active"
                else:
                    row["credentials_rotated_at"] = "now"
                self._rowcount = 1
        elif sql.startswith("DELETE FROM iot_dataloggers"):
            row = w["dataloggers"].get(str(params[0]))
            if row and row["tenant_id"] == params[1]:
                del w["dataloggers"][str(params[0])]
                self._rowcount = 1
        elif sql.startswith("SELECT") and "FROM iot_datalogger_devices" in sql:
            rows = [r for r in w["devices"] if r["tenant_id"] == params[0]]
            if "device_urn = %s" in sql:
                rows = [r for r in rows if r["device_urn"] == params[1]]
            elif "datalogger_id = %s" in sql:
                rows = [r for r in rows if str(r["datalogger_id"]) == str(params[1])]
            self._rows = [dict(r) for r in rows]
        elif sql.startswith("INSERT INTO iot_datalogger_devices"):
            dl, tenant, urn, ext = params
            if any(r["tenant_id"] == tenant and r["device_urn"] == urn for r in w["devices"]):
                self._rows = []
            else:
                w["devices"].append(dict(datalogger_id=str(dl), tenant_id=tenant,
                                         device_urn=urn, external_id=ext))
                self._rows = [{"device_urn": urn}]
                log.append(("db", "link"))
        elif sql.startswith("DELETE FROM iot_datalogger_devices"):
            before = len(w["devices"])
            w["devices"] = [r for r in w["devices"]
                            if not (r["tenant_id"] == params[0] and r["device_urn"] == params[1])]
            self._rowcount = before - len(w["devices"])
            log.append(("db", "unlink"))
        elif sql.startswith("UPDATE iot_datalogger_devices"):
            for r in w["devices"]:
                if r["tenant_id"] == params[1] and r["device_urn"] == params[2]:
                    r["datalogger_id"] = str(params[0])
                    self._rowcount += 1
            log.append(("db", "move"))
        elif sql.startswith("INSERT INTO sensor_profiles"):
            columns = [c.strip() for c in re.search(r"\(([^)]*)\) VALUES", sql).group(1).split(",")]
            row = dict(zip(columns, (getattr(v, "adapted", v) for v in params), strict=True))
            if self.conn.state.fail.get("profile_unique"):
                raise self.conn.state.unique_violation()
            code = row["code"]
            self.conn.state.profiles[code] = row
            self._rows = [{"code": code}]
            self._rowcount = 1
        elif sql.startswith("UPDATE sensor_profiles"):
            *values, code, tenant = params
            assignments = re.search(r" SET (.*?) WHERE ", sql).group(1).split(", ")
            columns = [a.removesuffix(" = %s") for a in assignments]
            row = self.conn.state.profiles.get(code)
            if row and row.get("tenant_id") == tenant:
                row.update(zip(columns, (getattr(v, "adapted", v) for v in values), strict=True))
                self._rowcount = 1
                if "RETURNING" in sql:
                    self._rows = [{c.strip(): row.get(c.strip())
                                   for c in sql.split(" RETURNING ")[1].split(",")}]
        elif "FROM sensor_profiles" in sql and "SELECT tenant_id" in sql:
            p = self.conn.state.profiles.get(params[0])
            self._rows = [{"tenant_id": p.get("tenant_id")}] if p else []
        elif "FROM sensor_profiles" in sql:
            if "ANY(%s)" in sql:
                self._rows = [{"code": c, **self.conn.state.profiles[c]}
                              for c in params[0] if c in self.conn.state.profiles]
            else:
                p = self.conn.state.profiles.get(params[0])
                self._rows = [{"code": params[0], **p}] if p else []
        else:
            raise AssertionError(f"unexpected SQL: {sql}")


class FakeConn:
    def __init__(self, state):
        self.state = state
        self.working = copy.deepcopy({"dataloggers": state.dataloggers, "devices": state.devices})

    def cursor(self, *a, **kw):
        return _Cursor(self)

    def commit(self):
        if self.state.fail.get("commit"):
            raise RuntimeError("commit failed")
        self.state.dataloggers = self.working["dataloggers"]
        self.state.devices = self.working["devices"]
        self.state.log.append(("sql", "commit"))

    def rollback(self):
        self.state.log.append(("sql", "rollback"))
        self.working = copy.deepcopy({"dataloggers": self.state.dataloggers,
                                      "devices": self.state.devices})


def fake_db(state):
    @contextmanager
    def _helper(tenant):
        yield FakeConn(state)

    return _helper


class _DynsecError(Exception):
    def __init__(self, command, error):
        super().__init__(f"dynsec {command} failed: {error}")
        self.command = command
        self.error = error


class _IotAgentError(Exception):
    def __init__(self, status_code, body=""):
        super().__init__(f"IoT Agent error: {status_code}")
        self.status_code = status_code


def fake_dynsec(state):
    """Module-shaped stand-in for common.mqtt_dynsec."""
    client = MagicMock(name="dynsec_client")

    def run(cmds):
        for c in cmds:
            if c["command"] == "getClient" and c["username"] in state.missing_clients:
                raise _DynsecError("getClient", "Client not found")
        return [{}]

    client.run.side_effect = run

    def _maybe_fail(step, *a):
        if state.fail.get(step):
            raise _DynsecError(step, "boom")

    def create_account(c, name, password):
        state.log.append(("dynsec", "create_account", name))
        _maybe_fail("create_account")
        state.missing_clients.discard(name)

    def set_role_topics(c, role, topics):
        state.log.append(("dynsec", "set_role_topics", role))
        _maybe_fail("set_role_topics")
        state.role_topics[role] = set(topics)

    def set_password(c, name, password):
        state.log.append(("dynsec", "set_password", name))
        if name in state.missing_clients:
            raise _DynsecError("setClientPassword", "Client not found")

    def disable(c, name):
        state.log.append(("dynsec", "disable", name))
        if name in state.missing_clients:
            raise _DynsecError("disableClient", "Client not found")

    def enable(c, name):
        state.log.append(("dynsec", "enable", name))

    def delete_account(c, name):
        state.log.append(("dynsec", "delete_account", name))
        state.role_topics.pop(name, None)

    return SimpleNamespace(
        DynsecError=_DynsecError, DynsecTimeout=_DynsecError,
        dynsec_from_env=lambda: client, create_account=create_account,
        set_role_topics=set_role_topics, set_password=set_password, disable=disable,
        enable=enable, delete_account=delete_account, client=client)


def fake_iota(state, apikey="KEY123"):
    """Module-shaped stand-in for common.iot_agent."""

    def provision_device(url, headers, **kw):
        state.log.append(("iota", "provision", kw["device_id"]))
        if state.fail.get("provision"):
            raise _IotAgentError(500, "boom")

    def delete_device(url, headers, device_id):
        state.log.append(("iota", "delete", device_id))
        return not state.fail.get("iota_delete")

    def update_device_attributes(url, headers, device_id, attributes):
        state.log.append(("iota", "update", device_id))
        if state.fail.get("iota_update") or device_id in state.iota_update_fail_for:
            raise _IotAgentError(500, "boom")

    return SimpleNamespace(
        IotAgentError=_IotAgentError,
        get_or_create_service_group=lambda *a, **kw: apikey,
        build_iota_attributes=lambda ms: [
            {"object_id": m.get("incomingKey") or m["type"], "name": m["sdmAttribute"]}
            for m in ms],
        provision_device=provision_device, delete_device=delete_device,
        update_device_attributes=update_device_attributes)


# ---------------------------------------------------------------------------
# Orion + wiring
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from functools import wraps  # noqa: E402
from unittest.mock import patch  # noqa: E402

import requests as _real_requests  # noqa: E402

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _require_auth(f=None, **kwargs):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)

        return wrapper

    return decorator(f) if f else decorator


_common_mock = MagicMock()
_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, **kw: dict(h)
_common_mock.internal_error = lambda *a, **kw: ({"error": "internal"}, 500)
# Every common.* module the blueprints import at module level must exist when this file
# is the first to load them (a MagicMock `common` is not a package).
from tests.real_common import seed_real_common  # noqa: E402

seed_real_common()
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers",
             "common.ngsi_payload_guard", "common.tenant_utils", "common.config_manager",
             "common.tier_quotas", "common.iot_agent", "common.mqtt_dynsec",
             "common.mqtt_endpoint"):
    sys.modules.setdefault(_mod, _common_mock if _mod in (
        "common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers")
        else MagicMock())

_spec = importlib.util.spec_from_file_location(
    "_real_mqtt_endpoint", os.path.join(_services_dir, "common", "mqtt_endpoint.py"))
real_mqtt_endpoint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(real_mqtt_endpoint)

TENANT = "tenant-a"
PARCEL = "urn:ngsi-ld:AgriParcel:p1"


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else []
    r.text = ""
    r.headers = {}
    return r


def _as_orion_returns(entity):
    """A one-element array is read back as its bare element."""
    out = copy.deepcopy(entity)
    for attr in out.values():
        if isinstance(attr, dict):
            for member in ("value", "object"):
                if isinstance(attr.get(member), list) and len(attr[member]) == 1:
                    attr[member] = attr[member][0]
    return out


class FakeOrion:
    """Just enough of Orion-LD, logging every write.

    `DeviceModel` entities live apart from `entities`, so a test can still compare
    everything else the request wrote.
    """

    def __init__(self, state):
        self.state = state
        self.entities = {}
        self.device_models = {}
        self.exceptions = _real_requests.exceptions
        self.posted_urls = []
        self.attr_posts = []

    def _find(self, eid):
        return self.entities.get(eid) or self.device_models.get(eid)

    def get(self, url, **kw):
        m = re.search(r"[?&]id=([^&]+)", url)
        if m:
            found = [self.entities[i] for i in m.group(1).split(",") if i in self.entities]
            attrs = re.search(r"[?&]attrs=([^&]+)", url)
            if attrs:
                kept = {"id", "type", *attrs.group(1).split(",")}
                found = [{k: v for k, v in e.items() if k in kept} for e in found]
            return _resp(200, found)
        m = re.search(r"/entities/([^?]+)$", url)
        if m:
            fail = self.state.fail.get("orion_read")
            if isinstance(fail, dict) and m.group(1) in fail:
                if isinstance(fail[m.group(1)], Exception):
                    raise fail[m.group(1)]
                return _resp(fail[m.group(1)])
            if fail == m.group(1):
                return _resp(500)
            found = self._find(m.group(1))
            return _resp(200, _as_orion_returns(found)) if found else _resp(404, {})
        return _resp(200, [])

    def post(self, url, **kw):
        if url.endswith("/attrs"):
            return self._append(url.rsplit("/entities/", 1)[-1][:-len("/attrs")], kw.get("json"))
        self.posted_urls.append(url)
        if "/ngsi-ld/v1/entities" not in url:
            return _resp(500)
        self.state.log.append(("orion", "post"))
        if self.state.fail.get("orion_post"):
            return _resp(500)
        ent = kw.get("json")
        self.entities[ent["id"]] = ent
        return _resp(201)

    def _append(self, eid, attrs):
        self.state.log.append(("orion", "attrs", eid))
        self.attr_posts.append((eid, copy.deepcopy(attrs)))
        fail = self.state.fail.get("orion_attrs")
        if isinstance(fail, Exception):
            raise fail
        if fail:
            return _resp(fail)
        entity = self._find(eid)
        if entity is None:
            return _resp(404)
        entity.update(copy.deepcopy(attrs))
        return _resp(204)

    def delete(self, url, **kw):
        eid = url.rsplit("/entities/", 1)[-1]
        if "/attrs/" in eid:
            eid, attr = eid.split("/attrs/")
            self.state.log.append(("orion", "delete_attr", eid, attr))
            fail = self.state.fail.get("orion_attr_delete")
            if isinstance(fail, Exception):
                raise fail
            if fail:
                return _resp(fail)
            return _resp(204 if (self._find(eid) or {}).pop(attr, None) else 404)
        self.state.log.append(("orion", "delete", eid))
        if self.state.fail.get("orion_delete"):
            return _resp(500)
        return _resp(204 if self.entities.pop(eid, None) else 404)

    def patch(self, url, **kw):
        self.state.log.append(("orion", "patch"))
        if self.state.fail.get("orion_patch"):
            return _resp(500)
        entity = self._find(url.rsplit("/entities/", 1)[-1][:-len("/attrs")])
        if entity is None:
            return _resp(404)
        # PATCH /attrs updates what exists; a missing attribute is a partial answer.
        known = {k: v for k, v in kw["json"].items() if k in entity}
        entity.update(copy.deepcopy(known))
        return _resp(204 if len(known) == len(kw["json"]) else 207)


def fake_sync_orion_client(state, orion):
    """Class-shaped stand-in for the SDK's SyncOrionClient: the batch upsert only."""

    class _Client:
        def __init__(self, tenant_id, base_url=None, context_url=None):
            orion.client_args = (tenant_id, base_url, context_url)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def upsert_entities_batch(self, entities):
            for entity in entities:
                state.log.append(("orion", "upsert", entity["id"]))
            fail = state.fail.get("upsert")
            if isinstance(fail, Exception):
                raise fail
            if fail:  # what the SDK answers for a 207: it does not raise
                return {"upserted": 0, "errors": fail, "entity_ids": []}
            for entity in entities:
                orion.device_models.setdefault(entity["id"], {}).update(copy.deepcopy(entity))
            return {"upserted": len(entities), "errors": [],
                    "entity_ids": [e["id"] for e in entities]}

    return _Client


@contextmanager
def wired(state, monkeypatch=None, host="mqtt.example.org", port="8883"):
    """Patch every collaborator of dataloggers.py, device_models.py and blueprints/sensors.py."""
    import dataloggers
    import blueprints.sensors as sensors
    import device_models

    orion = FakeOrion(state)
    db = fake_db(state)
    dyn = fake_dynsec(state)
    iota = fake_iota(state)
    env = {"MQTT_EXTERNAL_HOST": host, "MQTT_EXTERNAL_PORT": port, "IOT_AGENT_URL": "http://iota:4041"}

    def bridge(tenants=None):
        state.log.append(("bridge", tuple(tenants or ())))
        if state.fail.get("bridge"):
            raise RuntimeError("bridge down")
        if state.fail.get("bridge_errors"):
            return {"created": 0, "skipped": 0, "errors": [{"tenant": "t", "error": "orion 500"}]}
        return {"created": 1, "skipped": 0, "errors": []}

    import blueprints.notifications as notifications
    import deployments

    def open_deployment(tenant, urn, spec, valid_from, actor, reason, first=False):
        # No database here: just the row the real function would return.
        return {"id": "dep-1", "device_urn": urn, "valid_from": valid_from, "valid_to": None,
                "observesFeature": spec.observes_feature,
                "observationContext": spec.observation_context,
                "channelOverrides": spec.channel_overrides, "mobility": spec.mobility,
                "location": None}

    with patch.dict(os.environ, env), \
         patch.object(dataloggers, "get_db_connection_with_tenant", db), \
         patch.object(dataloggers, "mqtt_dynsec", dyn), \
         patch.object(dataloggers, "iot_agent", iota), \
         patch.object(dataloggers, "mqtt_endpoint", real_mqtt_endpoint), \
         patch.object(dataloggers, "requests", orion), \
         patch.object(sensors, "get_db_connection_with_tenant", db), \
         patch.object(sensors, "requests", orion), \
         patch.object(device_models, "requests", orion), \
         patch.object(device_models, "SyncOrionClient", fake_sync_orion_client(state, orion)), \
         patch.object(sensors, "mqtt", MagicMock()), \
         patch.object(sensors, "_tenant", lambda: TENANT), \
         patch.object(deployments, "open_deployment", open_deployment), \
         patch.object(deployments, "delete_deployment", lambda tenant, dep_id: None), \
         patch.object(deployments, "close_open_deployment", lambda tenant, urn, actor, reason: None), \
         patch.object(deployments, "list_deployments", lambda tenant, urn: []), \
         patch.object(notifications, "ensure_device_measurement_subscriptions", bridge):
        yield SimpleNamespace(orion=orion, dynsec=dyn, iota=iota, state=state)


def client(roles=("TenantAdmin",)):
    from flask import Flask, g
    import blueprints.sensors as sensors

    app = Flask(__name__)

    # Which `require_auth` stub decorated the views depends on which test module
    # imported first (some overwrite g.tenant), so `wired` pins sensors._tenant too.
    @app.before_request
    def _seed_identity():
        g.tenant = TENANT
        g.current_user = {"tenant_id": TENANT, "user_id": "u1"}
        g.roles = list(roles)

    app.register_blueprint(sensors.sensors_bp)
    return app.test_client()


AUTH = {"Authorization": "Bearer test-token"}


def seed_datalogger(state, status="active", dl_id="11111111-1111-1111-1111-111111111111",
                    tenant=TENANT, username="dl_aaaaaaaaaaaaaaaa", name="Logger"):
    state.dataloggers[dl_id] = dict(id=dl_id, tenant_id=tenant, name=name, kind="datak",
                                    mqtt_username=username, status=status, created_at=None,
                                    credentials_rotated_at=None, revoked_at=None)
    return dl_id


def seed_device(state, dl_id, external_id, tenant=TENANT):
    urn = f"urn:ngsi-ld:Device:{tenant}:{external_id}"
    state.devices.append(dict(datalogger_id=dl_id, tenant_id=tenant, device_urn=urn,
                              external_id=external_id))
    return urn


PROFILE = {"id": 1, "sdm_entity_type": "Device", "sdm_device_category": None,
           "mapping": {"measurements": [
               {"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "CEL"},
               {"type": "hum", "sdmAttribute": "relativeHumidity", "unit": "P1"}]}}


def post_json(c, path, body=None):
    import json as _json
    return c.post(path, content_type="application/json", headers=AUTH, data=_json.dumps(body or {}))
