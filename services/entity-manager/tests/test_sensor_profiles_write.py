"""Tenant sensor-profile write routes (POST/PUT /api/sensors/profiles)."""
import json

from tests.telemetry_fakes import AUTH, TENANT, FakeState, client, post_json, wired

M_T = {"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "°C"}
M_H = {"type": "hum", "sdmAttribute": "relativeHumidity", "unit": "%"}


def _body(measurements=None, **over):
    body = {"code": "station-a", "name": "Station A", "sdm_entity_type": "Device",
            "mapping": {"measurements": measurements if measurements is not None else [M_T, M_H]}}
    body.update(over)
    return body


def _put(c, code, body):
    return c.put(f"/api/sensors/profiles/{code}", content_type="application/json",
                 headers=AUTH, data=json.dumps(body))


def test_profile_created_for_tenant():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body())
    assert r.status_code == 201, r.get_json()
    row = st.profiles["station-a"]
    assert row["tenant_id"] == TENANT
    assert row["mapping"]["measurements"][0]["sdmAttribute"] == "airTemperature"


def test_profile_accepts_optional_fields():
    st = FakeState()
    m = dict(M_T, expression="r1 * 0.1", valueType="Number")
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body([m]))
    assert r.status_code == 201, r.get_json()
    assert st.profiles["station-a"]["mapping"]["measurements"][0]["expression"] == "r1 * 0.1"


def test_profile_rejects_unknown_unit():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles",
                      _body([dict(M_T, unit="furlongs")]))
    assert r.status_code == 400
    assert "unit" in r.get_json()["error"].lower()
    assert st.profiles == {}


def test_profile_rejects_duplicate_sdm_attribute():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body([M_T, dict(M_T, type="t2")]))
    assert r.status_code == 400
    assert st.profiles == {}


def test_profile_canonicalizes_sdm_attribute():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles",
                      _body([dict(M_T, sdmAttribute="temperature"), M_H]))
    assert r.status_code == 201
    assert st.profiles["station-a"]["mapping"]["measurements"][0]["sdmAttribute"] == "airTemperature"


def test_profile_duplicate_after_canonicalization_is_rejected():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles",
                      _body([dict(M_T, sdmAttribute="temperature"), M_T]))
    assert r.status_code == 400
    assert st.profiles == {}


def test_profile_rejects_invalid_expression():
    st = FakeState()
    for expr in ("${@r1} * 2", "process.exit()", "r1 ** 2 ; x"):
        with wired(st):
            r = post_json(client(), "/api/sensors/profiles", _body([dict(M_T, expression=expr)]))
        assert r.status_code == 400, expr
    assert st.profiles == {}


def test_profile_rejects_bad_shape():
    st = FakeState()
    with wired(st):
        c = client()
        assert post_json(c, "/api/sensors/profiles", _body(code="bad code")).status_code == 400
        assert post_json(c, "/api/sensors/profiles", _body([])).status_code == 400
        assert post_json(c, "/api/sensors/profiles",
                         _body(sdm_entity_type="Tractor")).status_code == 400
        assert post_json(c, "/api/sensors/profiles",
                         _body([dict(M_T, valueType="Blob")])).status_code == 400
        assert post_json(c, "/api/sensors/profiles",
                         _body([{"type": "t", "unit": "°C"}])).status_code == 400
    assert st.profiles == {}


def test_profile_code_conflict_is_409():
    st = FakeState()
    with wired(st):
        c = client()
        assert post_json(c, "/api/sensors/profiles", _body()).status_code == 201
        assert post_json(c, "/api/sensors/profiles", _body()).status_code == 409


def test_profile_code_of_global_profile_is_409():
    st = FakeState()
    st.profiles["station-a"] = {"tenant_id": None, "mapping": {"measurements": [M_T]}}
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body())
    assert r.status_code == 409


def test_profile_unique_violation_race_is_409():
    st = FakeState()
    st.fail["profile_unique"] = True
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body())
    assert r.status_code == 409


def test_global_profile_is_read_only():
    st = FakeState()
    st.profiles["g1"] = {"tenant_id": None, "mapping": {"measurements": [M_T]}}
    with wired(st):
        r = _put(client(), "g1", _body(code="g1"))
    assert r.status_code == 403
    assert st.profiles["g1"]["mapping"]["measurements"] == [M_T]


def test_put_updates_tenant_profile():
    st = FakeState()
    with wired(st):
        c = client()
        post_json(c, "/api/sensors/profiles", _body())
        r = _put(c, "station-a", _body([M_H], name="Renamed"))
    assert r.status_code == 200, r.get_json()
    assert st.profiles["station-a"]["name"] == "Renamed"
    assert st.profiles["station-a"]["mapping"]["measurements"] == [M_H]


def test_put_unknown_profile_is_404():
    st = FakeState()
    with wired(st):
        r = _put(client(), "nope", _body(code="nope"))
    assert r.status_code == 404


def test_put_code_in_body_must_match():
    st = FakeState()
    with wired(st):
        c = client()
        post_json(c, "/api/sensors/profiles", _body())
        r = _put(c, "station-a", _body(code="other"))
    assert r.status_code == 400


def test_expression_may_use_only_the_profile_keys():
    st = FakeState()
    with wired(st):
        c = client()
        ok = post_json(c, "/api/sensors/profiles",
                       _body([dict(M_T, incomingKey="r3", expression="r3 * 0.1")]))
        bad = post_json(c, "/api/sensors/profiles",
                        _body([dict(M_T, incomingKey="r3", expression="foo * 2")], code="b"))
    assert ok.status_code == 201, ok.get_json()
    assert bad.status_code == 400


def test_profile_code_with_a_trailing_newline_is_rejected():
    st = FakeState()
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", _body(code="station-a\n"))
    assert r.status_code == 400
    assert st.profiles == {}


def test_put_reprovisions_linked_devices_on_that_profile_best_effort(caplog):
    """R13: source = Orion profileCode of the tenant's linked devices."""
    import logging
    from tests.telemetry_fakes import seed_datalogger, seed_device
    st = FakeState()
    dl = seed_datalogger(st)
    u1 = seed_device(st, dl, "S1")
    u2 = seed_device(st, dl, "S2")
    u3 = seed_device(st, dl, "S3")
    st.iota_update_fail_for.add("S2")
    with wired(st) as w, caplog.at_level(logging.ERROR):
        c = client()
        post_json(c, "/api/sensors/profiles", _body())
        for urn, code in ((u1, "station-a"), (u2, "station-a"), (u3, "other")):
            w.orion.entities[urn] = {"id": urn, "profileCode": {"type": "Property", "value": code}}
        r = _put(c, "station-a", _body([M_H], name="Renamed"))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["reprovisioned"] == [u1]
    assert body["failed"] == [u2]
    assert ("iota", "update", "S1") in st.log and ("iota", "update", "S2") in st.log
    assert not any(e == ("iota", "update", "S3") for e in st.log)
    assert any("re-provision failed" in rec.getMessage() and rec.levelno == logging.ERROR
               for rec in caplog.records)


# ---- the writer move lock (shared): saving a profile against a writer move ---------------------
import logging  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

import blueprints.sensors as sensors_module  # noqa: E402
import dataloggers  # noqa: E402
import device_models  # noqa: E402
from tests.test_profile_change_writer_state import (  # noqa: E402
    LOCK_ALIVE, _after, _Clock, _kill, _traced)

BUSY = ("lock", "busy")


def _station(st, w, devices=("S1",), code="station-a", profile_code=None):
    """A saved profile of the tenant and Devices linked on it (the profile of the entity is `code`)."""
    from tests.telemetry_fakes import seed_datalogger, seed_device
    dl = seed_datalogger(st)
    post_json(client(), "/api/sensors/profiles", _body())
    urns = []
    for ext in devices:
        urn = seed_device(st, dl, ext)
        w.orion.entities[urn] = {"id": urn, "type": "Device",
                                 "profileCode": {"type": "Property", "value": profile_code or code}}
        urns.append(urn)
    return urns



def _logged_visible(st):
    real = sensors_module._profile_visible

    def visible(*a, **kw):
        st.log.append(("read", "profile_visible"))
        return real(*a, **kw)
    return patch.object(sensors_module, "_profile_visible", visible)


def test_saving_a_profile_during_a_move_is_409_and_touches_nothing(move_lock):
    st = FakeState()
    with wired(st) as w:
        _station(st, w)
        before = dict(st.profiles["station-a"])
        st.iota_attrs.clear()
        with _traced(st, move_lock), _logged_visible(st):
            move_lock.plan = ["busy"]
            r = _put(client(), "station-a", _body([M_H], name="Renamed"))
        assert r.status_code == 409
        assert r.get_json() == {"error": "Sensors are being moved; try again in a few minutes",
                                "step": "writer_move"}
        assert st.profiles["station-a"] == before and st.iota_attrs == []
        assert w.orion.attr_posts == [] and w.orion.device_models == {}
    assert st.log == [BUSY, ("lock", "released")]  # not even a read


def test_saving_a_profile_with_the_database_down_is_502_db(move_lock):
    st = FakeState()
    with wired(st) as w:
        _station(st, w)
        before = dict(st.profiles["station-a"])
        with _traced(st, move_lock):
            move_lock.plan = ["unavailable"]
            r = _put(client(), "station-a", _body([M_H], name="Renamed"))
        assert r.status_code == 502 and r.get_json() == {"error": "Database error", "step": "db"}
        assert st.profiles["station-a"] == before


@pytest.mark.parametrize("body", [
    {"code": "other"},
    {"name": ""},
    {"mapping": {"measurements": [dict(M_T, unit="furlongs")]}},
], ids=["code", "name", "mapping"])
def test_a_body_that_is_not_valid_is_400_without_taking_the_lock(body, move_lock):
    st = FakeState()
    with wired(st) as w:
        _station(st, w)
        with _traced(st, move_lock):
            move_lock.plan = ["busy"]  # a 409 would show the lock was asked for first
            r = _put(client(), "station-a", {**_body([M_H]), **body})
    assert r.status_code == 400, r.get_json()
    assert st.log == []


def test_the_lock_covers_a_save_from_the_first_read_to_the_device_model(move_lock):
    st = FakeState()
    with wired(st) as w:
        urns = _station(st, w)
        model = device_models.device_model_id(TENANT, "station-a")
        w.orion.device_models[model] = {"id": model, "type": "DeviceModel"}  # refreshed only if it exists
        with _traced(st, move_lock), _logged_visible(st):
            r = _put(client(), "station-a", _body([M_H], name="Renamed"))
        assert r.status_code == 200, r.get_json()
    log = [e for e in st.log if e != LOCK_ALIVE]
    assert log[0] == ("lock", "taken") and log[1] == ("read", "profile_visible")
    assert log[-1] == ("lock", "released")
    pushes = [i for i, e in enumerate(log) if e == ("iota", "update", "S1")]
    upserts = [i for i, e in enumerate(log) if e == ("orion", "upsert", model)]
    assert pushes and upserts and 1 < pushes[0] < upserts[0] < len(log) - 1
    assert st.log[st.log.index(("iota", "update", "S1")) - 1] == LOCK_ALIVE
    assert st.profiles["station-a"]["name"] == "Renamed" and urns


def test_a_lock_error_on_release_does_not_hide_the_save(move_lock, monkeypatch):
    st = FakeState()
    with wired(st) as w:
        _station(st, w)
        monkeypatch.setattr(type(move_lock.connect()), "close_error", RuntimeError("boom"))
        r = _put(client(), "station-a", _body([M_H], name="Renamed"))
    assert r.status_code == 200, r.get_json()


def test_reprovision_stops_when_the_lock_is_lost(move_lock, caplog):
    st, clock = FakeState(), _Clock()
    with wired(st) as w:
        _station(st, w, devices=("S1", "S2", "S3"))
        with _traced(st, move_lock, clock), caplog.at_level(logging.ERROR):
            _after(st, ("iota", "update", "S1"), 1, lambda: _kill(st, move_lock, clock))
            r = _put(client(), "station-a", _body([M_H], name="Renamed"))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert len(body["reprovisioned"]) == 1 and len(body["failed"]) == 2
    updates = [e for e in st.log if e[:2] == ("iota", "update")]
    assert updates == [("iota", "update", "S1")]
    after = st.log[st.log.index(("iota", "update", "S1")):]
    assert ("read", "device_dm_state") not in after[2:]  # the rest were not even looked at
    assert sum("stopped" in x.getMessage() for x in caplog.records) == 1


def test_reprovision_without_a_lock_is_as_before():
    st = FakeState()
    with wired(st) as w:
        urns = _station(st, w, devices=("S1", "S2"))
        out = dataloggers.reprovision_profile_devices(TENANT, "station-a", [M_H])
    assert out == {"reprovisioned": urns, "failed": []}
