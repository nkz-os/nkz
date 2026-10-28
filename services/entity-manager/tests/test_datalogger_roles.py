"""Datalogger and sensor set-up is for admins and technical consultants; a Farmer gets 403."""
import json

import pytest
from unittest.mock import patch

from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client,
                                   seed_datalogger, seed_device, wired)

DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"

WRITES = [
    ("post", "/api/sensors/register",
     {"external_id": "S2", "name": "Sensor 2", "profile": "p",
      "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL], "datalogger_id": DL}),
    ("post", "/api/sensors/dataloggers", {"name": "PC", "kind": "datak"}),
    ("get", f"/api/sensors/dataloggers/{DL}/config", None),
    ("post", f"/api/sensors/dataloggers/{DL}/rotate", None),
    ("post", f"/api/sensors/dataloggers/{DL}/revoke", None),
    ("post", f"/api/sensors/dataloggers/{DL}/enable", None),
    ("delete", f"/api/sensors/dataloggers/{DL}", None),
    ("put", f"/api/sensors/devices/{URN}/datalogger", {"datalogger_id": DL}),
    ("put", f"/api/sensors/devices/{URN}/profile", {"profile": "p"}),
    ("delete", f"/api/sensors/devices/{URN}", None),
]


def _state():
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    seed_device(st, DL, "S1")
    st.profiles["p"] = dict(PROFILE)
    return st


def _call(c, method, path, body):
    kw = {"headers": AUTH}
    if body is not None:
        kw.update(content_type="application/json", data=json.dumps(body))
    return getattr(c, method)(path, **kw)


@pytest.mark.parametrize("method,path,body", WRITES)
def test_farmer_is_refused_and_nothing_changes(method, path, body):
    import blueprints.sensors as sensors
    st = _state()
    with wired(st), patch.object(sensors, "_get_user_roles", return_value=["Farmer"]):
        r = _call(client(roles=("Farmer",)), method, path, body)
    assert r.status_code == 403, r.get_json()
    assert "TechnicalConsultant" in r.get_json()["error"]
    assert not any(e[0] in ("dynsec", "iota", "orion", "bridge") for e in st.log)
    assert len(st.devices) == 1 and st.dataloggers[DL]["status"] == "active"


@pytest.mark.parametrize("role", ["TechnicalConsultant", "TenantAdmin", "PlatformAdmin"])
def test_installer_roles_may_create_a_datalogger(role):
    import blueprints.sensors as sensors
    st = FakeState()
    with wired(st), patch.object(sensors, "_get_user_roles", return_value=[role]):
        r = _call(client(roles=(role,)), "post", "/api/sensors/dataloggers",
                  {"name": "PC", "kind": "datak"})
    assert r.status_code == 201, r.get_json()


def test_farmer_may_still_list_dataloggers():
    import blueprints.sensors as sensors
    st = _state()
    with wired(st), patch.object(sensors, "_get_user_roles", return_value=["Farmer"]):
        r = _call(client(roles=("Farmer",)), "get", "/api/sensors/dataloggers", None)
    assert r.status_code == 200


def test_roles_come_from_g_or_the_token_realm_roles():
    from flask import Flask, g
    import blueprints.sensors as sensors
    app = Flask(__name__)
    with app.test_request_context():
        g.roles = ["TechnicalConsultant"]
        assert sensors._get_user_roles() == ["TechnicalConsultant"]
    with app.test_request_context():
        g.current_user = {"realm_access": {"roles": ["Farmer"]}}
        assert sensors._get_user_roles() == ["Farmer"]
    with app.test_request_context():
        assert sensors._get_user_roles() == []


def test_require_installer_rejects_no_roles():
    import dataloggers
    with pytest.raises(dataloggers.ApiError) as e:
        dataloggers.require_installer(None)
    assert e.value.status == 403
    dataloggers.require_installer(["Farmer", "TechnicalConsultant"])


def test_generic_delete_of_a_registered_device_refuses_a_farmer():
    from flask import Flask, g
    import blueprints.entities as entities
    st = _state()
    app = Flask(__name__)
    with wired(st) as w:
        w.orion.entities[URN] = {"id": URN}
        view = entities.delete_instance
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        with app.test_request_context(), patch.object(entities, "log_entity_operation"):
            g.tenant, g.farmer_id, g.roles = TENANT, "f1", ["Farmer"]
            resp = view("Device", URN)
    assert resp[1] == 403
    assert URN in w.orion.entities and len(st.devices) == 1
    assert not any(e[0] == "iota" for e in st.log)
