"""The official Device attributes and the DeviceModel, written on registration, profile change
and profile edit; a ManufacturingMachine is written exactly as before."""
import copy
import json
import logging
import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import requests

from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client, post_json,
                                   seed_datalogger, wired)

import dataloggers  # noqa: E402
import deployments  # noqa: E402
import device_models  # noqa: E402
import blueprints.sensors as sensors  # noqa: E402
from blueprints.measurements import build_measurements  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
MACHINE = f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:M1"
MODEL = f"urn:ngsi-ld:DeviceModel:{TENANT}:p"
MODEL2 = f"urn:ngsi-ld:DeviceModel:{TENANT}:p2"
BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL],
        "datalogger_id": DL, "validFrom": "2026-03-01T08:00:00+00:00"}

AIR = {"measurements": [
    {"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "°C"},
    {"type": "hum", "sdmAttribute": "relativeHumidity", "unit": "%"}]}
SOIL = {"measurements": [{"type": "x", "sdmAttribute": "soilMoisture", "unit": "%"}]}
UNMAPPED = {"measurements": [{"type": "d", "sdmAttribute": "stemDiameter", "unit": "mm"}]}


def _prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def _rel(urn):
    return {"type": "Relationship", "object": urn}


def _state(**profiles):
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = {**PROFILE, "name": "Profile P"}
    for code, mapping in profiles.items():
        st.profiles[code] = {"tenant_id": TENANT, "name": f"Profile {code}", "mapping": mapping}
    return st


def _register(**over):
    return post_json(client(), "/api/sensors/register", {**BASE, **over})


def _change_profile(urn, code="p2"):
    return client().put(f"/api/sensors/devices/{urn}/profile", headers=AUTH,
                        content_type="application/json", data=json.dumps({"profile": code}))


def _orion_log(st):
    return [e for e in st.log if e[0] == "orion"]


def _iota_updates(st):
    return [e for e in st.log if e[:2] == ("iota", "update")]


@contextmanager
def _deployment_log(st):
    """Deployment opens and deletes land in the state log too."""
    open_deployment = deployments.open_deployment

    def _open(*a, **kw):
        st.log.append(("dep", "open"))
        return open_deployment(*a, **kw)

    with patch.object(deployments, "open_deployment", _open), \
         patch.object(deployments, "delete_deployment",
                      lambda tenant, dep_id: st.log.append(("dep", "delete", dep_id))):
        yield


# ---- registration ------------------------------------------------------------

def test_register_device_writes_the_official_attributes_and_keeps_the_old_ones():
    st = _state()
    with wired(st) as w:
        r = _register()
    assert r.status_code == 201, r.get_json()
    ent = w.orion.entities[URN]
    assert ent["refDeviceModel"] == _rel(MODEL)
    assert ent["deviceCategory"] == _prop(["sensor"])
    assert ent["controlledProperty"] == _prop(["humidity", "temperature"])
    assert ent["dateInstalled"] == _prop("2026-03-01T08:00:00Z") == ent["installedAt"]
    assert ent["deviceState"] == _prop("active") == ent["status"]
    assert ent["profileCode"] == ent["sensorType"] == _prop("p")
    assert ent["externalId"] == _prop("S1")


def test_register_writes_the_device_model_of_this_tenant_before_the_device():
    st = _state()
    st.profiles["p"].update(manufacturer_name="Acme Instruments", brand_name="AcmeSense",
                            model_name="AS-100")
    with wired(st) as w:
        assert _register().status_code == 201
    assert _orion_log(st) == [("orion", "upsert", MODEL), ("orion", "post")]
    assert w.orion.client_args == (TENANT, device_models.ORION_URL, device_models.CONTEXT_URL)
    assert w.orion.device_models == {MODEL: {
        "id": MODEL, "type": "DeviceModel", "name": _prop("Profile P"),
        "manufacturerName": _prop("Acme Instruments"), "brandName": _prop("AcmeSense"),
        "modelName": _prop("AS-100"), "category": _prop(["sensor"]),
        "controlledProperty": _prop(["humidity", "temperature"])}}


def test_register_with_a_profile_that_maps_to_nothing_omits_controlled_property():
    st = _state(dendro=UNMAPPED)
    model = f"urn:ngsi-ld:DeviceModel:{TENANT}:dendro"
    with wired(st) as w:
        r = _register(profile="dendro")
    assert r.status_code == 201, r.get_json()  # the attribute was never there: 404 is fine
    assert "controlledProperty" not in w.orion.entities[URN]
    assert w.orion.entities[URN]["refDeviceModel"] == _rel(model)
    assert "controlledProperty" not in w.orion.device_models[model]
    assert ("orion", "delete_attr", model, "controlledProperty") in st.log


@pytest.mark.parametrize("fail", [
    {"upsert": requests.exceptions.ConnectionError("down")},
    {"upsert": requests.exceptions.HTTPError("500")},
    {"upsert": [{"entityId": MODEL, "error": {"status": 400}}]},
    {"orion_attr_delete": 500},
    {"orion_attr_delete": requests.exceptions.ConnectionError("down")},
], ids=["network", "http", "partial-207", "delete-500", "delete-network"])
def test_register_device_model_failure_is_502_and_creates_nothing(fail):
    st = _state(dendro=UNMAPPED)
    st.fail.update(fail)
    with wired(st) as w, _deployment_log(st):
        r = _register(profile="dendro")
    assert r.status_code == 502 and r.get_json()["step"] == "device_model", r.get_json()
    assert w.orion.entities == {} and st.devices == []
    assert ("orion", "post") not in st.log
    assert not any(e[0] == "iota" for e in st.log)
    assert [e for e in st.log if e[0] == "dep"] == [("dep", "open"), ("dep", "delete", "dep-1")]


@pytest.mark.parametrize("step,fail", [("orion_entity", "orion_post"), ("iota_device", "provision")])
def test_register_failure_after_the_device_model_never_undoes_it(step, fail):
    st = _state()
    st.fail[fail] = True
    with wired(st) as w:
        r = _register()
    assert r.status_code == 502 and r.get_json()["step"] == step
    assert w.orion.entities == {}
    assert w.orion.device_models[MODEL]["controlledProperty"] == _prop(["humidity", "temperature"])
    assert not any(e[1].startswith("delete") and e[2] == MODEL for e in _orion_log(st))


def test_register_with_a_profile_code_that_fits_no_model_id_is_400_before_any_write():
    st = _state(**{"bad code": SOIL})
    with wired(st) as w, _deployment_log(st):
        r = _register(profile="bad code")
    assert r.status_code == 400, r.get_json()
    assert st.log == [] and w.orion.entities == {} == w.orion.device_models


@pytest.mark.parametrize("status,body,ok", [
    (201, [MODEL], True), (204, None, True),
    (207, {"success": [], "errors": [{"entityId": MODEL, "error": {"status": 400}}]}, False),
    (500, None, False)])
def test_the_device_model_goes_through_the_real_sdk_client(status, body, ok):
    sent = []

    def _post(self, url, **kw):
        sent.append((url, kw))
        resp = requests.Response()
        resp.status_code, resp.url = status, url
        resp._content = json.dumps(body).encode() if body is not None else b""
        return resp

    profile = {"code": "p", "name": "Profile P", "mapping": PROFILE["mapping"]}
    with patch.object(requests.Session, "post", _post):
        if ok:
            device_models.ensure_device_model(TENANT, profile)
        else:
            with pytest.raises(dataloggers.ApiError) as err:
                device_models.ensure_device_model(TENANT, profile)
            assert (err.value.status, err.value.step) == (502, "device_model")
    (url, kw), = sent
    assert url == f"{device_models.ORION_URL}/ngsi-ld/v1/entityOperations/upsert"
    assert kw["params"] == {"options": "update"}
    assert kw["headers"]["NGSILD-Tenant"] == TENANT
    assert kw["headers"]["Content-Type"] == "application/ld+json"
    assert [(e["id"], e["@context"]) for e in kw["json"]] == [(MODEL, [device_models.CONTEXT_URL])]


# Recorded from the code before the Device gained the official attributes.
MACHINE_BODY = (
    '{"@context": ["http://context.test/ngsi-ld-context.json"], '
    '"id": "urn:ngsi-ld:ManufacturingMachine:tenant-a:M1", "type": "ManufacturingMachine", '
    '"name": {"type": "Property", "value": "Tractor 1"}, '
    '"location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-2.0, 42.0]}}, '
    '"externalId": {"type": "Property", "value": "M1"}, '
    '"sensorType": {"type": "Property", "value": "machine"}, '
    '"profileCode": {"type": "Property", "value": "machine"}, '
    '"installedAt": {"type": "Property", "value": "2026-03-01T08:00:00Z"}, '
    '"status": {"type": "Property", "value": "active"}, '
    '"controlledAsset": {"type": "Relationship", "object": ["urn:ngsi-ld:AgriParcel:p1"]}, '
    '"category": {"type": "Property", "value": "tractor"}, '
    '"metadata": {"type": "Property", "value": {"vin": "X1", "group": "st-1", "station_id": "st-1"}}, '
    '"isUnderCanopy": {"type": "Property", "value": true}, '
    '"stationId": {"type": "Property", "value": "st-1"}, '
    '"altitudeMeters": {"type": "Property", "value": 410}, '
    '"isSilenced": {"type": "Property", "value": false}, '
    '"mobility": {"type": "Property", "value": "fixed"}, '
    '"deploymentId": {"type": "Property", "value": "dep-1"}}')
MACHINE_PATCH = (
    '{"profileCode": {"type": "Property", "value": "machine2"}, '
    '"sensorType": {"type": "Property", "value": "machine2"}}')


def _machine_state():
    st = _state(machine2=SOIL)
    st.profiles["machine"] = {"id": 2, "name": "Machine", "sdm_entity_type": "ManufacturingMachine",
                              "sdm_device_category": "tractor", "mapping": PROFILE["mapping"]}
    return st


def test_register_machine_is_byte_identical_to_the_recorded_request():
    st = _machine_state()
    with wired(st) as w, patch.object(sensors, "CONTEXT_URL",
                                      "http://context.test/ngsi-ld-context.json"):
        r = _register(external_id="M1", name="Tractor 1", profile="machine", metadata={"vin": "X1"},
                      station_id="st-1", is_under_canopy=True, altitude_meters=410, isSilenced=False)
    assert r.status_code == 201, r.get_json()
    assert json.dumps(w.orion.entities[MACHINE]) == MACHINE_BODY
    assert st.log == [("orion", "post"), ("iota", "provision", "M1"), ("db", "link"),
                      ("sql", "commit"), ("sql", "FOR UPDATE"),
                      ("dynsec", "set_role_topics", "dl_aaaaaaaaaaaaaaaa"), ("sql", "commit"),
                      ("bridge", (TENANT,))]


# ---- profile change ----------------------------------------------------------

def _legacy_device(code="p"):
    """As registered before the official attributes existed."""
    return {"id": URN, "type": "Device", "name": _prop("Sensor 1"), "externalId": _prop("S1"),
            "profileCode": _prop(code), "sensorType": _prop(code),
            "installedAt": _prop("2026-01-01T00:00:00Z"), "status": _prop("active")}


def _device(code="p", model=MODEL, controlled=("humidity", "temperature")):
    out = {**_legacy_device(code), "refDeviceModel": _rel(model), "deviceCategory": _prop(["sensor"])}
    if controlled:
        out["controlledProperty"] = _prop(list(controlled))
    return out


@contextmanager
def _changing(st, entity):
    """Wiring for a profile change of one linked Device; records the IoT Agent attributes sent."""
    urn = entity["id"]
    st.devices.append(dict(datalogger_id=DL, tenant_id=TENANT, device_urn=urn,
                           external_id=urn.rsplit(":", 1)[-1], dm_writer="em", dm_shape=None))
    with wired(st) as w:
        w.orion.entities[urn] = copy.deepcopy(entity)
        w.sent = []
        update = w.iota.update_device_attributes

        def _update(url, headers, device_id, attributes):
            w.sent.append([a["name"] for a in attributes])
            return update(url, headers, device_id, attributes)

        w.iota.update_device_attributes = _update
        yield w


def test_a_device_registered_before_the_official_attributes_changes_profile():
    st = _state(p2=SOIL)
    with _changing(st, _legacy_device()) as w:
        r = _change_profile(URN)
    assert r.status_code == 200, r.get_json()
    assert w.sent == [["soilMoisture"]]  # updated once: not rolled back
    assert w.orion.attr_posts == [(URN, {
        "profileCode": _prop("p2"), "sensorType": _prop("p2"), "refDeviceModel": _rel(MODEL2),
        "deviceCategory": _prop(["sensor"]), "controlledProperty": _prop(["soilMoisture"])})]
    assert [e for e in st.log if e[0] in ("iota", "orion")] == [
        ("iota", "update", "S1"), ("orion", "upsert", MODEL2), ("orion", "attrs", URN)]
    ent = w.orion.entities[URN]
    assert ent["installedAt"] == _prop("2026-01-01T00:00:00Z") and ent["status"] == _prop("active")
    assert w.orion.device_models[MODEL2]["controlledProperty"] == _prop(["soilMoisture"])


def test_change_to_a_profile_that_maps_to_nothing_removes_the_old_controlled_property():
    st = _state(p2=UNMAPPED)
    with _changing(st, _device()) as w:
        w.orion.device_models[MODEL2] = {"id": MODEL2, "type": "DeviceModel",
                                         "controlledProperty": _prop(["temperature"])}
        r = _change_profile(URN)
    assert r.status_code == 200, r.get_json()
    assert "controlledProperty" not in w.orion.entities[URN]
    assert w.orion.entities[URN]["refDeviceModel"] == _rel(MODEL2)
    assert "controlledProperty" not in w.orion.device_models[MODEL2]
    assert _orion_log(st) == [
        ("orion", "upsert", MODEL2), ("orion", "delete_attr", MODEL2, "controlledProperty"),
        ("orion", "attrs", URN), ("orion", "delete_attr", URN, "controlledProperty")]


@pytest.mark.parametrize("fail", [
    {"upsert": requests.exceptions.ConnectionError("down")},
    {"upsert": [{"entityId": MODEL2, "error": {"status": 400}}]},
    {"orion_attr_delete": 500},
], ids=["network", "partial-207", "model-attr-delete"])
def test_change_profile_restores_the_iot_agent_when_the_device_model_fails(fail):
    st = _state(p2=UNMAPPED)
    st.fail.update(fail)
    with _changing(st, _device()) as w:
        r = _change_profile(URN)
    assert r.status_code == 502 and r.get_json()["step"] == "device_model", r.get_json()
    assert w.sent == [["stemDiameter"], ["airTemperature", "relativeHumidity"]]
    assert w.orion.entities[URN] == _device() and w.orion.attr_posts == []


@pytest.mark.parametrize("fail", [500, 207, 404, requests.exceptions.ConnectionError("down")])
def test_change_profile_restores_the_iot_agent_when_the_device_write_fails(fail):
    st = _state(p2=UNMAPPED)
    st.fail["orion_attrs"] = fail
    with _changing(st, _device()) as w:
        r = _change_profile(URN)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()
    assert w.sent == [["stemDiameter"], ["airTemperature", "relativeHumidity"]]
    assert ("orion", "delete_attr", URN, "controlledProperty") not in st.log


@pytest.mark.parametrize("fail", [500, requests.exceptions.ConnectionError("down")])
def test_change_profile_does_not_undo_when_only_the_stale_attribute_stays(fail):
    """IoT Agent and entity are both on the new profile: undoing would split them."""
    st = _state(p2=UNMAPPED)
    with _changing(st, _device()) as w:
        delete = w.orion.delete

        def _delete(url, **kw):
            if url.endswith(f"{URN}/attrs/controlledProperty") and "orion_attr_delete" not in st.fail:
                st.fail["orion_attr_delete"] = fail
            return delete(url, **kw)

        w.orion.delete = _delete
        r = _change_profile(URN)
        assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()
        assert w.sent == [["stemDiameter"]]
        assert w.orion.entities[URN]["profileCode"] == _prop("p2")
        assert "controlledProperty" in w.orion.entities[URN]

        # Repeating the call finishes the job.
        del w.orion.delete
        st.fail.clear()
        assert _change_profile(URN).status_code == 200
        assert w.sent == [["stemDiameter"], ["stemDiameter"]]
        assert "controlledProperty" not in w.orion.entities[URN]


@pytest.mark.parametrize("fail", [{"upsert": RuntimeError("down")}, {"orion_attrs": 500}])
def test_change_profile_logs_critical_when_there_is_nothing_to_restore(fail, caplog):
    st = _state(p2=SOIL)
    st.fail.update(fail)
    with _changing(st, _legacy_device("vanished")) as w:
        r = _change_profile(URN)
    assert r.status_code == 502
    assert w.sent == [["soilMoisture"]]
    assert any(rec.levelname == "CRITICAL" and "S1" in rec.getMessage() for rec in caplog.records)


def test_change_to_a_profile_code_that_fits_no_model_id_is_400_before_any_write():
    st = _state(**{"bad:code": SOIL})
    with _changing(st, _device()) as w:
        r = _change_profile(URN, "bad:code")
    assert r.status_code == 400, r.get_json()
    assert w.sent == [] and _orion_log(st) == []


def test_an_unexpected_error_after_the_iot_agent_update_also_restores_it():
    st = _state(p2=SOIL)
    with _changing(st, _device()) as w, \
         patch.object(device_models, "write_profile_attrs", side_effect=RuntimeError("bug")):
        r = _change_profile(URN)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()
    assert w.sent == [["soilMoisture"], ["airTemperature", "relativeHumidity"]]


def _machine_entity():
    return {"id": MACHINE, "type": "ManufacturingMachine", "profileCode": _prop("machine"),
            "sensorType": _prop("machine"), "status": _prop("active")}


def test_change_profile_of_a_machine_sends_the_recorded_requests():
    st = _machine_state()
    with _changing(st, _machine_entity()) as w:
        patches = []
        original = w.orion.patch
        w.orion.patch = lambda url, **kw: patches.append((url, json.dumps(kw.pop("json")), kw)) or \
            original(url, json=json.loads(patches[-1][1]), **kw)
        r = _change_profile(MACHINE, "machine2")
    assert r.status_code == 200, r.get_json()
    assert patches == [(f"{dataloggers.ORION_URL}/ngsi-ld/v1/entities/{MACHINE}/attrs", MACHINE_PATCH,
                        {"headers": {}, "timeout": 10})]
    assert [e for e in st.log if e[0] in ("iota", "orion")] == [
        ("iota", "update", "M1"), ("orion", "patch")]
    assert w.sent == [["soilMoisture"]]


def test_change_profile_of_a_machine_still_undoes_a_failed_patch():
    st = _machine_state()
    st.fail["orion_patch"] = True
    with _changing(st, _machine_entity()) as w:
        r = _change_profile(MACHINE, "machine2")
    assert r.status_code == 502
    assert r.get_json() == {"error": "Entity update failed", "step": "orion_profile"}
    assert w.sent == [["soilMoisture"], ["airTemperature", "relativeHumidity"]]


def test_a_machine_accepts_a_profile_code_that_fits_no_model_id():
    st = _machine_state()
    st.profiles["bad:code"] = {"mapping": SOIL}
    with _changing(st, _machine_entity()):
        assert _change_profile(MACHINE, "bad:code").status_code == 200


# ---- maker fields of a profile -------------------------------------------------

MAKER = {"manufacturerName": "Acme Instruments", "brandName": "AcmeSense", "modelName": "AS-100"}
COLUMNS = {"manufacturerName": "manufacturer_name", "brandName": "brand_name",
           "modelName": "model_name"}
NO_MAKER = dict.fromkeys(MAKER)


def _profile_body(mapping=SOIL, code="p2", **over):
    return {"code": code, "name": "Probe", "mapping": mapping, **over}


def _put_profile(mapping=SOIL, code="p2", **over):
    return client().put(f"/api/sensors/profiles/{code}", headers=AUTH,
                        content_type="application/json",
                        data=json.dumps(_profile_body(mapping, code, **over)))


def _maker_of(body):
    return {k: body[k] for k in MAKER}


def test_profile_post_stores_the_trimmed_maker_fields():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles",
                      _profile_body(**{k: f"  {v}\t" for k, v in MAKER.items()}))
    assert r.status_code == 201, r.get_json()
    assert _maker_of(r.get_json()) == MAKER
    assert {k: st.profiles["p2"][c] for k, c in COLUMNS.items()} == MAKER


@pytest.mark.parametrize("given", [{}, NO_MAKER])
def test_profile_post_without_maker_fields_stores_null(given):
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _profile_body(**given))
    assert r.status_code == 201, r.get_json()
    assert _maker_of(r.get_json()) == NO_MAKER
    assert [st.profiles["p2"][c] for c in COLUMNS.values()] == [None, None, None]


@pytest.mark.parametrize("field", sorted(MAKER))
@pytest.mark.parametrize("value", ["", "   ", "x" * 129, 5, True, ["a"], {"a": 1}])
def test_maker_fields_are_text_of_1_to_128_characters(field, value):
    st = _state(p2=SOIL)
    with wired(st):
        assert post_json(client(), "/api/sensors/profiles",
                         _profile_body(code="new", **{field: value})).status_code == 400
        assert _put_profile(**{field: value}).status_code == 400
    assert "new" not in st.profiles and COLUMNS[field] not in st.profiles["p2"]
    assert st.log == []


def test_the_length_is_measured_after_trimming():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _profile_body(brandName=f" {'x' * 128} "))
    assert r.status_code == 201 and r.get_json()["brandName"] == "x" * 128


def test_profile_put_leaves_absent_maker_fields_and_clears_null_ones():
    st = _state(p2=SOIL)
    st.profiles["p2"].update({c: MAKER[k] for k, c in COLUMNS.items()})
    with wired(st):
        r = _put_profile()
        assert _maker_of(r.get_json()) == MAKER
        r = _put_profile(brandName=None, modelName=" AS-200 ")
    assert r.status_code == 200, r.get_json()
    assert _maker_of(r.get_json()) == {"manufacturerName": "Acme Instruments", "brandName": None,
                                       "modelName": "AS-200"}
    assert st.profiles["p2"]["brand_name"] is None
    assert st.profiles["p2"]["manufacturer_name"] == "Acme Instruments"


def test_profile_list_carries_the_maker_fields():
    rows = [{"code": "a", "name": "A", "description": None, "sdm_entity_type": "Device",
             "sdm_category": None, "mapping": {}, "manufacturer_name": "Acme Instruments",
             "brand_name": None, "model_name": None}]
    executed = []

    class _Cur:
        def execute(self, sql, params=()):
            executed.append(sql)

        def fetchall(self):
            return rows

        def close(self):
            pass

    class _Conn:
        def cursor(self, *a, **kw):
            return _Cur()

    @contextmanager
    def _db(tenant):
        yield _Conn()

    with wired(FakeState()), patch.object(sensors, "get_db_connection_with_tenant", _db):
        r = client().get("/api/sensors/profiles", headers=AUTH)
    assert r.status_code == 200, r.get_json()
    assert _maker_of(r.get_json()["profiles"][0]) == {
        "manufacturerName": "Acme Instruments", "brandName": None, "modelName": None}
    assert all(c in executed[0] for c in COLUMNS.values())


# ---- profile edit ------------------------------------------------------------

@contextmanager
def _editing(st, *entities, model=True):
    """Linked Devices on profile `p2`, and its DeviceModel unless `model` is False."""
    for e in entities:
        st.devices.append(dict(datalogger_id=DL, tenant_id=TENANT, device_urn=e["id"],
                               external_id=e["id"].rsplit(":", 1)[-1], dm_writer="em", dm_shape=None))
    with wired(st) as w:
        for e in entities:
            w.orion.entities[e["id"]] = copy.deepcopy(e)
        if model:
            w.orion.device_models[MODEL2] = {"id": MODEL2, "type": "DeviceModel",
                                             "controlledProperty": _prop(["soilMoisture"])}
        yield w


def _on_p2(external_id="S1", **over):
    e = {**_device("p2", MODEL2, ["soilMoisture"]),
         "id": f"urn:ngsi-ld:Device:{TENANT}:{external_id}"}
    e.update(over)
    return {k: v for k, v in e.items() if v is not None}


DATED = {"soilMoisture": _prop(21.5, observedAt="2026-03-02T08:00:00Z")}
REFRESH_KEYS = ("refreshed", "skipped", "refresh_failed")


def _refresh(r):
    assert r.status_code == 200, r.get_json()
    return {k: r.get_json()[k] for k in REFRESH_KEYS}


def test_an_edit_that_changes_nothing_official_sends_no_device_write():
    """One-element arrays are read back bare: that is not a difference."""
    st = _state(p2=SOIL)
    with _editing(st, _on_p2(**DATED)) as w:
        assert w.orion.get(f"http://o/entities/{URN}").json()["controlledProperty"] == _prop("soilMoisture")
        r = _put_profile(name="Renamed")
    assert _refresh(r) == {"refreshed": [], "skipped": [], "refresh_failed": []}
    assert _orion_log(st) == [("orion", "upsert", MODEL2)]  # only the model, whose name changed
    assert w.orion.device_models[MODEL2]["name"] == _prop("Renamed")


def test_an_edit_writes_only_the_attributes_that_changed():
    st = _state(p2=SOIL)
    with _editing(st, _on_p2(**DATED)) as w:
        r = _put_profile(AIR)
    assert _refresh(r) == {"refreshed": [URN], "skipped": [], "refresh_failed": []}
    assert w.orion.attr_posts == [(URN, {"controlledProperty": _prop(["humidity", "temperature"])})]
    assert w.orion.device_models[MODEL2]["controlledProperty"] == _prop(["humidity", "temperature"])
    assert [e[1] for e in _orion_log(st)] == ["upsert", "attrs"]


def test_an_edit_that_leaves_nothing_mapped_removes_controlled_property_everywhere():
    st = _state(p2=SOIL)
    with _editing(st, _on_p2()) as w:
        r = _put_profile(UNMAPPED)
        assert _refresh(r)["refreshed"] == [URN]
        assert "controlledProperty" not in w.orion.entities[URN]
        assert "controlledProperty" not in w.orion.device_models[MODEL2]
        assert w.orion.attr_posts == []
        st.log.clear()
        assert _refresh(_put_profile(UNMAPPED))["refreshed"] == []
    assert not any(e[1:3] == ("delete_attr", URN) for e in _orion_log(st))


def test_an_edit_gives_a_device_registered_before_the_missing_attributes():
    st = _state(p2=SOIL)
    with _editing(st, _legacy_device("p2")) as w:
        r = _put_profile()
    assert _refresh(r)["refreshed"] == [URN]
    assert w.orion.attr_posts == [(URN, {
        "refDeviceModel": _rel(MODEL2), "deviceCategory": _prop(["sensor"]),
        "controlledProperty": _prop(["soilMoisture"])})]


def test_an_edit_corrects_a_relationship_to_a_model_of_another_tenant():
    st = _state(p2=SOIL)
    with _editing(st, _on_p2(refDeviceModel=_rel("urn:ngsi-ld:DeviceModel:acmefarm:p2"))) as w:
        assert _refresh(_put_profile())["refreshed"] == [URN]
    assert w.orion.attr_posts == [(URN, {"refDeviceModel": _rel(MODEL2)})]


@pytest.mark.parametrize("unreadable", [False, True])
def test_an_edit_creates_no_model_and_never_points_a_device_to_a_missing_one(unreadable):
    st = _state(p2=SOIL)
    if unreadable:
        st.fail["orion_read"] = MODEL2
    with _editing(st, _legacy_device("p2"), model=False) as w:
        r = _put_profile()
    assert _refresh(r) == {"refreshed": [URN], "skipped": [],
                           "refresh_failed": [MODEL2] if unreadable else []}
    assert w.orion.device_models == {}
    assert w.orion.attr_posts == [(URN, {"deviceCategory": _prop(["sensor"]),
                                         "controlledProperty": _prop(["soilMoisture"])})]


def test_an_edit_uses_the_stored_maker_of_the_profile():
    st = _state(p2=SOIL)
    st.profiles["p2"].update(manufacturer_name="Acme Instruments", brand_name="AcmeSense",
                             model_name="AS-100")
    with _editing(st) as w:
        _refresh(_put_profile(modelName=None))
    model = w.orion.device_models[MODEL2]
    assert model["manufacturerName"] == _prop("Acme Instruments")
    assert model["brandName"] == _prop("AcmeSense")
    assert model["modelName"] == _prop("p2")


def test_an_edit_touches_only_linked_devices_on_that_profile():
    other = _on_p2("S2", profileCode=_prop("p"), refDeviceModel=_rel(MODEL), controlledProperty=None)
    machine = {**_on_p2(controlledProperty=None), "id": MACHINE, "type": "ManufacturingMachine"}
    unlinked = _on_p2("S9", controlledProperty=None)
    st = _state(p2=SOIL)
    with _editing(st, other, machine) as w:
        w.orion.entities[unlinked["id"]] = unlinked
        r = _put_profile()
    assert _refresh(r) == {"refreshed": [], "skipped": [], "refresh_failed": []}
    assert r.get_json()["reprovisioned"] == [MACHINE]
    assert w.orion.attr_posts == []


def test_an_edit_skips_a_device_whose_reading_has_no_time_of_its_own(caplog):
    """Its write would fire the bridge, which would store the reading as taken now."""
    st = _state(p2=SOIL)
    dated = _on_p2("S2", controlledProperty=None, **DATED)
    with _editing(st, _on_p2(controlledProperty=None, soilMoisture=_prop(21.5)), dated) as w, \
         caplog.at_level(logging.WARNING):
        r = _put_profile()
    assert _refresh(r) == {"refreshed": [dated["id"]], "skipped": [URN], "refresh_failed": []}
    assert [eid for eid, _ in w.orion.attr_posts] == [dated["id"]]
    assert any(URN in rec.getMessage() and "soilMoisture" in rec.getMessage() and TENANT in rec.getMessage()
               for rec in caplog.records)


def test_the_undated_check_uses_the_profile_just_saved():
    """The bridge reads the saved profile: a channel it just gained is a reading."""
    st = _state(p2=SOIL)
    with _editing(st, _on_p2(airTemperature=_prop(20.0))) as w:
        assert _refresh(_put_profile(AIR))["skipped"] == [URN]
    assert w.orion.attr_posts == []


@pytest.mark.parametrize("reading", [{}, {"airTemperature": _prop(20.0)}, {"stemDiameter": _prop(21.5)}],
                         ids=["no-reading", "undated-on-its-profile", "undated-on-the-edited-one"])
@pytest.mark.parametrize("moved_to", [_prop("p"), None], ids=["other-profile", "no-profile"])
def test_a_device_that_left_the_profile_after_the_listing_is_not_written(moved_to, reading, caplog):
    """The attributes of the edited profile would be untrue on it, and stay there."""
    st = _state(p2=SOIL)
    stays = _on_p2("S2", **DATED)
    with _editing(st, _on_p2(**reading), stays) as w, caplog.at_level(logging.WARNING):
        moved = {**_device("p", MODEL), **reading}
        if moved_to is None:
            del moved["profileCode"], moved["refDeviceModel"]
        listed = dataloggers._orion_devices

        def _listing(tenant, urns):
            found = copy.deepcopy(listed(tenant, urns))
            st.listings = getattr(st, "listings", 0) + 1
            if st.listings == 2:  # the first one belongs to the IoT Agent re-provisioning
                w.orion.entities[URN] = copy.deepcopy(moved)
            return found

        with patch.object(dataloggers, "_orion_devices", _listing):
            r = _put_profile(UNMAPPED)
    assert st.listings == 2
    assert _refresh(r) == {"refreshed": [stays["id"]], "skipped": [], "refresh_failed": []}
    assert w.orion.entities[URN] == moved
    assert not any(e[1] in ("attrs", "delete_attr") and e[2] == URN for e in _orion_log(st))
    found = repr(moved_to["value"]) if moved_to else "None"
    assert any(all(part in rec.getMessage() for part in (TENANT, URN, "expected='p2'", f"found={found}"))
               for rec in caplog.records)


def test_a_device_that_needs_no_write_is_not_listed_as_skipped():
    st = _state(p2=SOIL)
    with _editing(st, _on_p2(soilMoisture=_prop(21.5))):
        assert _refresh(_put_profile())["skipped"] == []


@pytest.mark.parametrize("fail", [requests.exceptions.ConnectionError("down"),
                                  [{"entityId": MODEL2, "error": {"status": 400}}]])
def test_a_device_model_failure_does_not_fail_the_edit_or_the_devices(fail, caplog):
    st = _state(p2=SOIL)
    st.fail["upsert"] = fail
    with _editing(st, _on_p2(**DATED)) as w:
        r = _put_profile(AIR)
    assert _refresh(r) == {"refreshed": [URN], "skipped": [], "refresh_failed": [MODEL2]}
    assert st.profiles["p2"]["mapping"]["measurements"][0]["sdmAttribute"] == "airTemperature"
    assert w.orion.entities[URN]["controlledProperty"] == _prop(["humidity", "temperature"])
    assert any(rec.levelno == logging.ERROR and MODEL2 in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("fail,mapping", [
    ({"orion_attrs": 500}, AIR), ({"orion_attrs": 207}, AIR),
    ({"orion_attrs": requests.exceptions.ConnectionError("down")}, AIR),
    ({"orion_attr_delete": 500}, UNMAPPED)])
def test_a_device_failure_is_logged_and_listed_and_never_raised(fail, mapping, caplog):
    st = _state(p2=SOIL)
    st.fail.update(fail)
    second = _on_p2("S2", **DATED)
    with _editing(st, _on_p2(**DATED), second):
        r = _put_profile(mapping)
    assert [u for u in _refresh(r)["refresh_failed"] if u != MODEL2] == [URN, second["id"]]
    assert r.get_json()["reprovisioned"] == [URN, second["id"]]
    for urn in (URN, second["id"]):
        assert any(rec.levelno == logging.ERROR and urn in rec.getMessage() and "p2" in rec.getMessage()
                   for rec in caplog.records)


def test_a_device_that_cannot_be_read_is_listed_and_never_written_blind():
    st = _state(p2=SOIL)
    st.fail["orion_read"] = URN
    with _editing(st, _on_p2(controlledProperty=None)) as w:
        r = _put_profile()
    assert _refresh(r)["refresh_failed"] == [URN]
    assert w.orion.attr_posts == []


def test_an_edit_of_a_profile_whose_code_fits_no_model_id_keeps_the_saved_profile():
    st = _state(**{"bad code": SOIL})
    with _editing(st, _on_p2(profileCode=_prop("bad code"), refDeviceModel=None), model=False) as w:
        r = _put_profile(AIR, "bad code")
    assert _refresh(r) == {"refreshed": [], "skipped": [], "refresh_failed": []}
    assert r.get_json()["error"] == "profile_code_unusable"
    assert r.get_json()["reprovisioned"] == [URN] and w.orion.attr_posts == []


def test_an_edit_survives_a_failed_device_listing():
    st = _state(p2=SOIL)
    with _editing(st, _on_p2()) as w, \
         patch.object(dataloggers, "_orion_devices", side_effect=RuntimeError("down")):
        r = _put_profile(AIR)
    assert _refresh(r) == {"refreshed": [], "skipped": [], "refresh_failed": []}
    assert r.get_json()["error"] == "device_listing_failed"
    assert w.orion.device_models[MODEL2]["controlledProperty"] == _prop(["humidity", "temperature"])


# ---- undated readings --------------------------------------------------------

WHEN = "2026-03-02T08:00:00Z"
STAT_MAPPING = {"measurements": [
    {"type": "t", "incomingKey": "r1", "sdmAttribute": "temperature", "unit": "°C",
     "statistics": ["mean", "min", "max", "stddev"]},
    {"type": "hum", "sdmAttribute": "relativeHumidity", "unit": "%"},
    "not a measurement", {"type": "no attribute"}]}


@pytest.mark.parametrize("attrs,expected", [
    ({}, []),
    ({"airTemperature": _prop(20.0, observedAt=WHEN)}, []),
    ({"airTemperature": _prop(20.0)}, ["airTemperature"]),
    ({"temperature": _prop(20.0)}, ["airTemperature"]),
    ({"humidity": _prop(50.0), "airTemperatureMin": _prop(1.0), "airTemperatureMax": _prop(9.0)},
     ["airTemperatureMax", "airTemperatureMin", "relativeHumidity"]),
    ({"airTemperatureStdDev": _prop(1.0, observedAt=WHEN), "relativeHumidity": _prop(5.0)},
     ["relativeHumidity"]),
    # both aliases: the canonical attribute is the reading
    ({"temperature": _prop(20.0), "airTemperature": _prop(20.0, observedAt=WHEN)}, []),
    ({"airTemperature": _prop(20.0, observedAt=WHEN), "temperature": _prop(20.0)}, []),
    ({"temperature": _prop(20.0, observedAt=WHEN), "airTemperature": _prop(20.0)}, ["airTemperature"]),
    # entity-level time
    ({"airTemperature": _prop(20.0), "observedAt": _prop(WHEN)}, []),
    ({"airTemperature": _prop(20.0), "observedAt": WHEN}, ["airTemperature"]),
    ({"airTemperature": _prop(20.0), "observedAt": _prop(None)}, ["airTemperature"]),
    ({"airTemperature": _prop(20.0, observedAt="")}, ["airTemperature"]),
    # not readings: undeclared, or not a Property
    ({"batteryLevel": _prop(3.1), "airTemperature": _rel("urn:x"), "relativeHumidity": "50"}, []),
    ({"relativeHumidity": _prop(None)}, ["relativeHumidity"]),
])
def test_undated_readings_are_the_ones_the_bridge_would_stamp(attrs, expected):
    profile = {"code": "p", "mapping": STAT_MAPPING}
    entity = {"id": URN, "type": "Device", "@context": ["x"], "status": _prop("active"), **attrs}
    assert device_models.undated_readings(profile, entity) == expected
    # the same entity and profile through the real bridge
    built = build_measurements(entity, profile)
    assert sorted(m["id"].rsplit(":", 1)[-1] for m in built
                  if m["timeSource"]["value"] == "server") == expected


@pytest.mark.parametrize("mapping", [None, {}, {"measurements": None}])
def test_a_profile_without_measurements_has_no_readings(mapping):
    assert device_models.undated_readings({"mapping": mapping}, {"airTemperature": _prop(1)}) == []


# ---- conformance example -----------------------------------------------------
# `config/sdm-examples/Device.json` is the body the registration endpoint sends; refresh it
# after a deliberate change with SDM_EXAMPLES_WRITE=1.

_CONFIG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "..", "config"))
_EXAMPLES_DIR = os.path.join(_CONFIG, "sdm-examples")
DEPLOYMENT_ID = "00000000-0000-4000-8000-000000000001"


def _assert_example(file_name, entity):
    path = os.path.join(_EXAMPLES_DIR, file_name)
    if os.environ.get("SDM_EXAMPLES_WRITE") == "1":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entity, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == entity, f"{file_name} no longer matches what the endpoint sends"


def _registered_example():
    st = FakeState()
    seed_datalogger(st, dl_id=DL, tenant="acmefarm")
    st.profiles["weather_probe"] = {**PROFILE, "name": "Weather probe"}

    def _open(tenant, urn, spec, valid_from, actor, reason, first=False):
        return {"id": DEPLOYMENT_ID, "device_urn": urn, "valid_from": valid_from, "valid_to": None,
                "observesFeature": spec.observes_feature,
                "observationContext": spec.observation_context,
                "channelOverrides": spec.channel_overrides, "mobility": spec.mobility,
                "location": None}

    with wired(st) as w, patch.object(sensors, "_tenant", lambda: "acmefarm"), \
         patch.object(deployments, "open_deployment", _open):
        r = _register(external_id="sensor-001", name="Weather probe 1", profile="weather_probe",
                      controlledAsset=["urn:ngsi-ld:AgriParcel:acmefarm:parcel-1"])
    assert r.status_code == 201, r.get_json()
    return w.orion.entities["urn:ngsi-ld:Device:acmefarm:sensor-001"]


def test_device_example():
    entity = _registered_example()
    # The only member taken out: the sibling examples carry no @context, because the checker
    # validates against the platform context of the repository.
    del entity["@context"]
    _assert_example("Device.json", entity)


# Official terms of Device that another change adds to the platform context.
ARRIVING_TERMS = {"refDeviceModel", "dateInstalled", "deviceCategory"}


def test_device_example_has_no_other_attribute_outside_the_platform_context():
    with open(os.path.join(_CONFIG, "ngsi-ld-context.json"), encoding="utf-8") as handle:
        contexts = json.load(handle)["@context"]
    terms = {term for part in contexts if isinstance(part, dict) for term in part}
    with open(os.path.join(_EXAMPLES_DIR, "Device.json"), encoding="utf-8") as handle:
        attributes = set(json.load(handle)) - {"id", "type"}
    assert ARRIVING_TERMS <= attributes
    assert attributes - terms - ARRIVING_TERMS == {"externalId", "profileCode", "installedAt"}
