"""Datalogger accounts: DB rows, broker account and role, rebuilt from the rows."""
import json
import logging

import pytest

from tests.telemetry_fakes import (AUTH, TENANT, FakeState, client, seed_datalogger,
                                   seed_device, wired, PROFILE, PARCEL)

import dataloggers  # noqa: E402


def _post(c, path, body=None):
    return c.post(path, content_type="application/json", headers=AUTH, data=json.dumps(body or {}))


def test_create_datalogger_returns_password_once(caplog):
    st = FakeState()
    with wired(st), caplog.at_level(logging.DEBUG):
        r = _post(client(), "/api/sensors/dataloggers", {"name": "Logger", "kind": "datak"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["devices"] == []
    mqtt = body["mqtt"]
    assert mqtt["username"].startswith("dl_") and len(mqtt["username"]) == 19
    assert mqtt["host"] == "mqtt.example.org" and mqtt["port"] == 8883
    password = mqtt["password"]
    assert len(password) >= 32
    assert ("dynsec", "create_account", mqtt["username"]) in st.log
    # never stored, never logged
    assert password not in json.dumps(st.dataloggers)
    assert password not in caplog.text
    assert st.dataloggers[body["id"]]["mqtt_username"] == mqtt["username"]


def test_create_datalogger_without_external_host_returns_the_unconfigured_dict():
    st = FakeState()
    with wired(st, host=""):
        r = _post(client(), "/api/sensors/dataloggers", {"name": "L", "kind": "generic_mqtt"})
    assert r.status_code == 201
    mqtt = r.get_json()["mqtt"]
    assert mqtt["configured"] is False and "host" not in mqtt
    assert mqtt["password"] and mqtt["username"].startswith("dl_")


def test_create_datalogger_rolls_back_row_when_account_fails():
    st = FakeState()
    st.fail["create_account"] = True
    with wired(st):
        r = _post(client(), "/api/sensors/dataloggers", {"name": "L", "kind": "datak"})
    assert r.status_code == 502
    assert r.get_json()["step"] == "mqtt_account"
    assert st.dataloggers == {}
    assert ("sql", "rollback") in st.log


def test_create_datalogger_removes_account_when_commit_fails():
    st = FakeState()
    st.fail["commit"] = True
    with wired(st):
        r = _post(client(), "/api/sensors/dataloggers", {"name": "L", "kind": "datak"})
    assert r.status_code == 502
    assert r.get_json()["step"] == "db_commit"
    assert any(e[:2] == ("dynsec", "delete_account") for e in st.log)


@pytest.mark.parametrize("body", [{"name": "", "kind": "datak"}, {"name": "x", "kind": "weird"}, {}])
def test_create_datalogger_validates_input(body):
    st = FakeState()
    with wired(st):
        r = _post(client(), "/api/sensors/dataloggers", body)
    assert r.status_code == 400
    assert st.log == []


def test_list_never_contains_password():
    st = FakeState()
    dl = seed_datalogger(st)
    seed_device(st, dl, "S1")
    with wired(st):
        r = client().get("/api/sensors/dataloggers", headers=AUTH)
    assert r.status_code == 200
    rows = r.get_json()
    assert rows[0]["id"] == dl and rows[0]["devices"][0]["externalId"] == "S1"
    assert "password" not in r.get_data(as_text=True)


def test_list_hides_other_tenants():
    st = FakeState()
    seed_datalogger(st, tenant="tenant-b")
    with wired(st):
        r = client().get("/api/sensors/dataloggers", headers=AUTH)
    assert r.get_json() == []


def test_delete_datalogger_with_devices_is_409():
    st = FakeState()
    dl = seed_datalogger(st)
    seed_device(st, dl, "S1")
    with wired(st):
        r = client().delete(f"/api/sensors/dataloggers/{dl}", headers=AUTH)
    assert r.status_code == 409
    assert dl in st.dataloggers
    assert not any(e[:2] == ("dynsec", "delete_account") for e in st.log)


def test_delete_empty_datalogger_removes_account_and_row():
    st = FakeState()
    dl = seed_datalogger(st)
    with wired(st):
        r = client().delete(f"/api/sensors/dataloggers/{dl}", headers=AUTH)
    assert r.status_code == 204
    assert st.dataloggers == {}
    assert ("dynsec", "delete_account", "dl_aaaaaaaaaaaaaaaa") in st.log


def test_foreign_datalogger_is_404():
    st = FakeState()
    dl = seed_datalogger(st, tenant="tenant-b")
    with wired(st):
        c = client()
        assert c.delete(f"/api/sensors/dataloggers/{dl}", headers=AUTH).status_code == 404
        assert _post(c, f"/api/sensors/dataloggers/{dl}/rotate").status_code == 404
        assert c.get(f"/api/sensors/dataloggers/{dl}/config", headers=AUTH).status_code == 404


def test_config_has_exactly_the_documented_shape():
    st = FakeState()
    dl = seed_datalogger(st)
    urn = seed_device(st, dl, "S1")
    st.profiles["p"] = {"mapping": PROFILE["mapping"]}
    with wired(st) as w:
        w.orion.entities[urn] = {"id": urn, "type": "Device",
                                 "name": {"type": "Property", "value": "Sensor 1"},
                                 "profileCode": {"type": "Property", "value": "p"}}
        r = client().get(f"/api/sensors/dataloggers/{dl}/config", headers=AUTH)
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {
        "datalogger_id": dl, "name": "Logger",
        "mqtt": {"host": "mqtt.example.org", "port": 8883,
                 "username": "dl_aaaaaaaaaaaaaaaa", "apikey": "KEY123", "password": None},
        "devices": [{"externalId": "S1", "name": "Sensor 1", "topic": "/KEY123/S1/attrs",
                     "measurements": [
                         {"key": "r1", "sdmAttribute": "airTemperature", "unit": "CEL"},
                         {"key": "hum", "sdmAttribute": "relativeHumidity", "unit": "P1"}]}]}


def test_rotate_returns_a_new_password_once(caplog):
    st = FakeState()
    dl = seed_datalogger(st)
    with wired(st), caplog.at_level(logging.DEBUG):
        r = _post(client(), f"/api/sensors/dataloggers/{dl}/rotate")
    assert r.status_code == 200
    pw = r.get_json()["password"]
    assert len(pw) >= 32 and pw not in caplog.text and pw not in json.dumps(st.dataloggers)
    assert ("dynsec", "set_password", "dl_aaaaaaaaaaaaaaaa") in st.log


def test_rotate_recreates_a_client_the_broker_forgot():
    st = FakeState()
    dl = seed_datalogger(st)
    seed_device(st, dl, "S1")
    st.missing_clients.add("dl_aaaaaaaaaaaaaaaa")
    with wired(st):
        r = _post(client(), f"/api/sensors/dataloggers/{dl}/rotate")
    assert r.status_code == 200
    kinds = [e[1] for e in st.log if e[0] == "dynsec"]
    assert kinds.index("create_account") < kinds.index("set_role_topics")
    assert st.role_topics["dl_aaaaaaaaaaaaaaaa"] == {"/KEY123/S1/attrs"}


def test_revoke_and_enable_toggle_status_and_client():
    st = FakeState()
    dl = seed_datalogger(st)
    with wired(st):
        c = client()
        assert _post(c, f"/api/sensors/dataloggers/{dl}/revoke").status_code == 200
        assert st.dataloggers[dl]["status"] == "revoked"
        assert ("dynsec", "disable", "dl_aaaaaaaaaaaaaaaa") in st.log
        assert _post(c, f"/api/sensors/dataloggers/{dl}/enable").status_code == 200
        assert st.dataloggers[dl]["status"] == "active"
        assert ("dynsec", "enable", "dl_aaaaaaaaaaaaaaaa") in st.log


def test_reconcile_recreates_missing_role_from_rows():
    """R1: the role ends up with exactly one topic per device row, apikey from the group."""
    st = FakeState()
    dl = seed_datalogger(st)
    seed_device(st, dl, "S1")
    seed_device(st, dl, "S2")
    st.role_topics["dl_aaaaaaaaaaaaaaaa"] = {"/KEY123/OLD/attrs"}
    with wired(st):
        res = dataloggers.reconcile_datalogger_acl(TENANT, dl)
    assert st.role_topics["dl_aaaaaaaaaaaaaaaa"] == {"/KEY123/S1/attrs", "/KEY123/S2/attrs"}
    assert res["needs_rotation"] is False


def test_reconcile_never_creates_a_client_and_reports_rotation():
    """R3."""
    st = FakeState()
    dl = seed_datalogger(st)
    st.missing_clients.add("dl_aaaaaaaaaaaaaaaa")
    with wired(st):
        res = dataloggers.reconcile_datalogger_acl(TENANT, dl)
    assert res["needs_rotation"] is True
    assert not any(e[:2] == ("dynsec", "create_account") for e in st.log)


def test_reconcile_holds_the_row_lock_across_the_broker_call():
    """R7: SELECT ... FOR UPDATE before set_role_topics, commit only after it."""
    st = FakeState()
    dl = seed_datalogger(st)
    seed_device(st, dl, "S1")
    with wired(st):
        dataloggers.reconcile_datalogger_acl(TENANT, dl)
    order = [e for e in st.log if e in (("sql", "FOR UPDATE"), ("dynsec", "set_role_topics", "dl_aaaaaaaaaaaaaaaa"), ("sql", "commit"))]
    assert order == [("sql", "FOR UPDATE"), ("dynsec", "set_role_topics", "dl_aaaaaaaaaaaaaaaa"), ("sql", "commit")]


def test_create_with_a_bad_external_port_writes_nothing():
    st = FakeState()
    with wired(st, port="not-a-port"):
        r = _post(client(), "/api/sensors/dataloggers", {"name": "L", "kind": "datak"})
    assert r.status_code >= 400
    assert st.dataloggers == {} and not any(e[:2] == ("dynsec", "create_account") for e in st.log)


def test_revoke_succeeds_when_the_broker_lost_the_client():
    st = FakeState()
    dl = seed_datalogger(st)
    st.missing_clients.add("dl_aaaaaaaaaaaaaaaa")
    with wired(st):
        r = _post(client(), f"/api/sensors/dataloggers/{dl}/revoke")
    assert r.status_code == 200
    assert st.dataloggers[dl]["status"] == "revoked"


def test_topic_for_rejects_an_apikey_that_widens_the_acl():
    for bad in ("a+b", "a#", "a/b", "a b", "", "a\n"):
        with pytest.raises(dataloggers.ApiError) as e:
            dataloggers.topic_for(bad, "S1")
        assert e.value.status == 502 and e.value.step == "service_group"
    assert dataloggers.topic_for("KEY123", "S1") == "/KEY123/S1/attrs"


def test_external_id_and_device_urn_reject_a_trailing_newline():
    assert not dataloggers.EXTERNAL_ID_RE.fullmatch("S1\n")
    with pytest.raises(dataloggers.ApiError):
        dataloggers.parse_device_urn(TENANT, f"urn:ngsi-ld:Device:{TENANT}:S1\n")


def test_missing_dynsec_credentials_are_logged_critical_at_startup(monkeypatch, caplog):
    monkeypatch.delenv("MQTT_DYNSEC_USERNAME", raising=False)
    monkeypatch.setenv("MQTT_DYNSEC_PASSWORD", "secret-value")
    with caplog.at_level(logging.CRITICAL):
        dataloggers.check_dynsec_config()
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("MQTT_DYNSEC_USERNAME" in m for m in msgs)
    assert not any("secret-value" in m for m in msgs)
