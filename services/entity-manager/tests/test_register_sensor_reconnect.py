"""register_sensor provisions the IoT Agent, links the datalogger and opens the ACL."""
import json

import pytest
from unittest.mock import patch

from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client,
                                   seed_datalogger, seed_device, wired)

DL = "11111111-1111-1111-1111-111111111111"
DL2 = "22222222-2222-2222-2222-222222222222"
ROLE = "dl_aaaaaaaaaaaaaaaa"
ROLE2 = "dl_bbbbbbbbbbbbbbbb"

BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL],
        "datalogger_id": DL}


def _state(**kw):
    st = FakeState()
    seed_datalogger(st, dl_id=DL, **kw)
    st.profiles["p"] = dict(PROFILE)
    return st


def _register(c, **over):
    body = dict(BASE, **over)
    body = {k: v for k, v in body.items() if v is not None}
    return c.post("/api/sensors/register", content_type="application/json",
                  headers=AUTH, data=json.dumps(body))


def _subsequence(log, wanted):
    it = iter(log)
    return all(any(e[:len(w)] == w for e in it) for w in wanted)


def test_register_provisions_iota_link_acl_and_subscription():
    st = _state()
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 201, r.get_json()
    assert r.get_json() == {"entity_id": f"urn:ngsi-ld:Device:{TENANT}:S1",
                            "topic": "/KEY123/S1/attrs", "datalogger_id": DL}
    assert _subsequence(st.log, [("orion", "post"), ("iota", "provision", "S1"),
                                 ("db", "link"), ("dynsec", "set_role_topics", ROLE),
                                 ("bridge", (TENANT,))])
    assert st.role_topics[ROLE] == {"/KEY123/S1/attrs"}
    assert len(st.devices) == 1
    ent = w.orion.entities[f"urn:ngsi-ld:Device:{TENANT}:S1"]
    assert ent["controlledAsset"] == {"type": "Relationship", "object": [PARCEL]}
    assert ent["profileCode"]["value"] == "p"


def test_register_rolls_back_entity_when_iota_fails():
    st = _state()
    st.fail["provision"] = True
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 502
    assert r.get_json()["step"] == "iota_device"
    assert w.orion.entities == {}
    assert st.devices == []
    assert ("orion", "delete", f"urn:ngsi-ld:Device:{TENANT}:S1") in st.log


def test_register_rolls_back_iota_and_entity_when_acl_fails():
    st = _state()
    st.fail["set_role_topics"] = True
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 502
    assert r.get_json()["step"] == "acl"
    assert st.devices == []
    assert w.orion.entities == {}
    assert _subsequence(st.log, [("db", "link"), ("db", "unlink"), ("iota", "delete", "S1"),
                                 ("orion", "delete")])
    assert not any(e[0] == "bridge" for e in st.log)


@pytest.mark.parametrize("ext", ["a/b", "a+b", "a#b", "a:b", "a b", "x" * 65, "ñ"])
def test_register_rejects_unsafe_external_id(ext):
    st = _state()
    with wired(st) as w:
        r = _register(client(), external_id=ext)
    assert r.status_code == 400
    assert st.log == [] and w.orion.entities == {}


def test_register_accepts_lat_lon_zero():
    st = _state()
    with wired(st) as w:
        r = _register(client(), location={"lat": 0, "lon": 0})
    assert r.status_code == 201, r.get_json()
    ent = w.orion.entities[f"urn:ngsi-ld:Device:{TENANT}:S1"]
    assert ent["location"]["value"]["coordinates"] == [0, 0]


def test_register_rejects_missing_location():
    st = _state()
    with wired(st):
        assert _register(client(), location={"lat": 1}).status_code == 400
        assert _register(client(), location=None).status_code == 400
    assert st.log == []


def test_register_rejects_device_already_on_a_datalogger():
    st = _state()
    seed_datalogger(st, dl_id=DL2, username="dl_bbbbbbbbbbbbbbbb")
    seed_device(st, DL2, "S1")
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 409
    assert st.log == [] and w.orion.entities == {}
    assert len(st.devices) == 1


@pytest.mark.parametrize("asset", [None, [], "", [{}]])
def test_register_requires_a_parcel_in_controlled_asset(asset):
    st = _state()
    with wired(st) as w:
        r = _register(client(), controlledAsset=asset)
    assert r.status_code == 400
    assert w.orion.entities == {} and st.log == []


def test_register_keeps_controlled_asset_order_owner_first():
    st = _state()
    owner = "urn:ngsi-ld:AgriParcel:owner"
    with wired(st) as w:
        r = _register(client(), controlledAsset=[{"object": owner}, PARCEL, owner])
    assert r.status_code == 201
    ent = w.orion.entities[f"urn:ngsi-ld:Device:{TENANT}:S1"]
    assert ent["controlledAsset"]["object"] == [owner, PARCEL]


def test_register_requires_datalogger_id():
    st = _state()
    with wired(st):
        r = _register(client(), datalogger_id=None)
    assert r.status_code == 400 and st.log == []


def test_register_rejects_foreign_or_unknown_datalogger_before_any_write():
    st = _state()
    seed_datalogger(st, dl_id=DL2, tenant="tenant-b")
    with wired(st) as w:
        c = client()
        assert _register(c, datalogger_id=DL2).status_code == 404
        assert _register(c, datalogger_id="33333333-3333-3333-3333-333333333333").status_code == 404
        assert _register(c, datalogger_id="not-a-uuid").status_code == 404
    assert st.log == [] and w.orion.entities == {}


def test_register_rejects_revoked_datalogger_before_any_write():
    st = _state(status="revoked")
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 409
    assert st.log == [] and w.orion.entities == {}


def test_register_unknown_profile_is_404():
    st = _state()
    with wired(st):
        r = _register(client(), profile="nope")
    assert r.status_code == 404 and st.log == []


def test_register_survives_a_failed_bridge_subscription():
    """R8: the subscription heals at startup, so the device is still registered."""
    st = _state()
    st.fail["bridge"] = True
    with wired(st):
        r = _register(client())
    assert r.status_code == 201
    assert len(st.devices) == 1


def test_mqtt_credentials_manager_is_not_called():
    st = _state()
    with wired(st) as w:
        r = _register(client())
    assert r.status_code == 201
    assert not any("mqtt" in u for u in w.orion.posted_urls)
    body = r.get_data(as_text=True)
    assert "password" not in body and "mqtt_credentials" not in body


def test_delete_device_cleans_iota_link_and_acl():
    st = _state()
    urn = seed_device(st, DL, "S1")
    st.role_topics[ROLE] = {"/KEY123/S1/attrs"}
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn}
        r = client().delete(f"/api/sensors/devices/{urn}", headers=AUTH)
    assert r.status_code == 204
    assert st.devices == [] and w.orion.entities == {}
    assert st.role_topics[ROLE] == set()
    assert _subsequence(st.log, [("db", "unlink"), ("dynsec", "set_role_topics", ROLE),
                                 ("iota", "delete", "S1"), ("orion", "delete", urn)])


def test_delete_device_reports_the_failed_step():
    st = _state()
    urn = seed_device(st, DL, "S1")
    st.fail["iota_delete"] = True
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn}
        r = client().delete(f"/api/sensors/devices/{urn}", headers=AUTH)
    assert r.status_code == 502 and r.get_json()["step"] == "iota_device"
    assert urn in w.orion.entities  # the entity is only removed once the rest is gone


def test_delete_device_of_another_tenant_is_rejected():
    st = _state()
    with wired(st):
        r = client().delete("/api/sensors/devices/urn:ngsi-ld:Device:tenant-b:S1", headers=AUTH)
    assert r.status_code == 404 and st.log == []


def test_move_device_reconciles_both_roles():
    st = _state()
    seed_datalogger(st, dl_id=DL2, username=ROLE2)
    urn = seed_device(st, DL, "S1")
    st.role_topics[ROLE] = {"/KEY123/S1/attrs"}
    with wired(st):
        r = client().put(f"/api/sensors/devices/{urn}/datalogger", headers=AUTH,
                         content_type="application/json", data=json.dumps({"datalogger_id": DL2}))
    assert r.status_code == 200, r.get_json()
    assert st.devices[0]["datalogger_id"] == DL2
    assert st.role_topics[ROLE] == set()
    assert st.role_topics[ROLE2] == {"/KEY123/S1/attrs"}


def test_move_device_to_revoked_or_unknown_datalogger_is_refused():
    st = _state()
    seed_datalogger(st, dl_id=DL2, username=ROLE2, status="revoked")
    urn = seed_device(st, DL, "S1")
    with wired(st):
        c = client()
        put = lambda dl: c.put(f"/api/sensors/devices/{urn}/datalogger", headers=AUTH,
                               content_type="application/json", data=json.dumps({"datalogger_id": dl}))
        assert put(DL2).status_code == 409
        assert put("44444444-4444-4444-4444-444444444444").status_code == 404
    assert st.devices[0]["datalogger_id"] == DL


def test_move_device_restores_the_row_when_the_target_acl_fails():
    st = _state()
    seed_datalogger(st, dl_id=DL2, username=ROLE2)
    urn = seed_device(st, DL, "S1")
    st.fail["set_role_topics"] = True
    with wired(st):
        r = client().put(f"/api/sensors/devices/{urn}/datalogger", headers=AUTH,
                         content_type="application/json", data=json.dumps({"datalogger_id": DL2}))
    assert r.status_code == 502 and r.get_json()["step"] == "acl"
    assert st.devices[0]["datalogger_id"] == DL


def _profile_setup(st):
    urn = seed_device(st, DL, "S1")
    st.profiles["p2"] = {"mapping": {"measurements": [
        {"type": "x", "sdmAttribute": "soilMoisture", "unit": "P1"}]}}
    return urn


def _put_profile(c, urn, code="p2"):
    return c.put(f"/api/sensors/devices/{urn}/profile", headers=AUTH,
                 content_type="application/json", data=json.dumps({"profile": code}))


def test_profile_change_reprovisions_attributes_and_entity():
    st = _state()
    urn = _profile_setup(st)
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn, "type": "Device",
                                 "profileCode": {"type": "Property", "value": "p"}}
        r = _put_profile(client(), urn)
    assert r.status_code == 200, r.get_json()
    assert _subsequence(st.log, [("iota", "update", "S1"), ("orion", "patch")])


def test_profile_change_restores_iota_attributes_when_orion_fails():
    st = _state()
    urn = _profile_setup(st)
    st.fail["orion_patch"] = True
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn, "type": "Device",
                                 "profileCode": {"type": "Property", "value": "p"}}
        r = _put_profile(client(), urn)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile"
    assert [e for e in st.log if e[:2] == ("iota", "update")] == [("iota", "update", "S1")] * 2


def test_profile_change_unknown_profile_or_device():
    st = _state()
    urn = _profile_setup(st)
    with wired(st):
        assert _put_profile(client(), urn, "nope").status_code == 404
        assert _put_profile(client(), urn).status_code == 404  # entity absent from Orion
    assert not any(e[0] == "iota" for e in st.log)


def test_generic_instance_delete_of_a_registered_device_cleans_everything():
    """The existing generic route is extended, not duplicated."""
    from flask import Flask, g
    import blueprints.entities as entities

    st = _state()
    urn = seed_device(st, DL, "S1")
    st.role_topics[ROLE] = {"/KEY123/S1/attrs"}
    app = Flask(__name__)

    @app.before_request
    def _seed():
        g.tenant, g.farmer_id = TENANT, "f1"

    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn}
        view = entities.delete_instance
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        with app.test_request_context(), patch.object(entities, "log_entity_operation"):
            g.tenant, g.farmer_id = TENANT, "f1"
            resp = view("Device", urn)
    assert resp.status_code == 200
    assert st.devices == [] and w.orion.entities == {}
    assert ("iota", "delete", "S1") in st.log


def test_delete_device_retry_after_acl_failure_closes_acl():
    """A broker failure rolls the link delete back, so the retry still closes the topic."""
    st = _state()
    urn = seed_device(st, DL, "S1")
    st.role_topics[ROLE] = {"/KEY123/S1/attrs"}
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn}
        st.fail["set_role_topics"] = True
        first = client().delete(f"/api/sensors/devices/{urn}", headers=AUTH)
        assert first.status_code == 502 and first.get_json()["step"] == "acl"
        assert len(st.devices) == 1 and urn in w.orion.entities
        st.fail.clear()
        second = client().delete(f"/api/sensors/devices/{urn}", headers=AUTH)
    assert second.status_code == 204
    assert st.devices == [] and st.role_topics[ROLE] == set() and w.orion.entities == {}


def test_register_orion_connection_error_is_502_with_step():
    import requests as rq
    st = _state()
    with wired(st) as w:
        def boom(*a, **k):
            raise rq.ConnectionError("down")
        w.orion.post = boom
        r = _register(client())
    assert r.status_code == 502 and r.get_json()["step"] == "orion_entity"
    assert st.devices == [] and not any(e[0] in ("iota", "db") for e in st.log)


def test_register_orion_read_error_is_502_with_step():
    import requests as rq
    st = _state()
    with wired(st) as w:
        def boom(*a, **k):
            raise rq.Timeout("slow")
        w.orion.get = boom
        r = _register(client())
    assert r.status_code == 502 and r.get_json()["step"] == "orion_read"


def test_register_db_error_at_the_link_step_is_502_and_undone():
    st = _state()
    with wired(st) as w:
        import dataloggers
        with patch.object(dataloggers, "link_device",
                          side_effect=dataloggers.ApiError(502, "x", "link")):
            r = _register(client())
        assert r.status_code == 502 and r.get_json()["step"] == "link"
        assert w.orion.entities == {} and ("iota", "delete", "S1") in st.log


def test_register_raw_db_error_at_the_link_insert_becomes_link_step():
    st = _state()
    import dataloggers
    from tests import telemetry_fakes as tf
    real = tf.fake_db(st)
    calls = {"n": 0}

    from contextlib import contextmanager

    @contextmanager
    def flaky(tenant):
        with real(tenant) as conn:
            orig = conn.cursor

            def cursor(*a, **k):
                cur = orig(*a, **k)
                ex = cur.execute

                def execute(sql, params=()):
                    if "INSERT INTO iot_datalogger_devices" in sql:
                        raise RuntimeError("fk violation")
                    return ex(sql, params)

                cur.execute = execute
                return cur

            conn.cursor = cursor
            yield conn

    with wired(st) as w, patch.object(dataloggers, "get_db_connection_with_tenant", flaky):
        r = _register(client())
    assert r.status_code == 502 and r.get_json()["step"] == "link"
    assert w.orion.entities == {} and ("iota", "delete", "S1") in st.log


@pytest.mark.parametrize("loc", [{"lat": float("nan"), "lon": 0}, {"lat": 91, "lon": 0},
                                 {"lat": 0, "lon": 181}, {"lat": float("inf"), "lon": 0}])
def test_register_rejects_non_finite_or_out_of_range_coordinates(loc):
    st = _state()
    with wired(st):
        r = client().post("/api/sensors/register", content_type="application/json",
                          headers=AUTH, data=json.dumps(dict(BASE, location=loc)))
    assert r.status_code == 400 and st.log == []


def test_tenant_is_normalised_and_invalid_is_400():
    from flask import Flask, g
    import importlib.util, os, sys
    import blueprints.sensors as sensors
    import tests.telemetry_fakes as tf
    spec = importlib.util.spec_from_file_location(
        "_tu", os.path.join(tf._services_dir, "common", "tenant_utils.py"))
    tu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tu)
    app = Flask(__name__)
    with patch.dict(sys.modules, {"common.tenant_utils": tu}), app.test_request_context():
        g.tenant = "Acme Farm"
        assert sensors._tenant() == "acme-farm"
        g.tenant = "***"
        with pytest.raises(sensors.dataloggers.ApiError) as e:
            sensors._tenant()
        assert e.value.status == 400


def test_rotate_of_a_revoked_datalogger_the_broker_lost_stays_disabled():
    from tests.telemetry_fakes import post_json
    st = _state(status="revoked")
    st.missing_clients.add(ROLE)
    with wired(st):
        r = post_json(client(), f"/api/sensors/dataloggers/{DL}/rotate")
    assert r.status_code == 200
    assert ("dynsec", "disable", ROLE) in st.log
    assert st.dataloggers[DL]["status"] == "revoked"


def test_generic_delete_keeps_old_behaviour_without_a_link_row():
    import dataloggers
    st = _state()
    with wired(st):
        assert dataloggers.has_datalogger_link(TENANT, f"urn:ngsi-ld:Device:{TENANT}:S9") is False


def test_profile_change_logs_critical_when_the_old_profile_is_gone(caplog):
    st = _state()
    urn = _profile_setup(st)
    st.fail["orion_patch"] = True
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn, "type": "Device",
                                 "profileCode": {"type": "Property", "value": "vanished"}}
        r = _put_profile(client(), urn)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile"
    assert any(rec.levelname == "CRITICAL" for rec in caplog.records)
