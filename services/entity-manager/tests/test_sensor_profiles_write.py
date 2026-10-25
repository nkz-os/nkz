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
