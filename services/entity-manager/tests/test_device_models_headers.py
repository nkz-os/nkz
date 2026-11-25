"""The headers of the Orion requests of `device_models`, with the real header helper.

Without a Link the broker expands names with the default vocabulary and answers a write with 204:
a request that needs the platform context is never sent without it.
"""
import importlib.util
import json
import logging
import os

import pytest

from tests.fake_broker import DEVICE_IRI, FakeBroker, prop, rel
from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client, post_json,
                                   seed_datalogger, wired)

import dataloggers  # noqa: E402
import device_models  # noqa: E402

CONTEXT = "http://context.test/ngsi-ld-context.json"
LINK = f'<{CONTEXT}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
MODEL = f"urn:ngsi-ld:DeviceModel:{TENANT}:p"
MAPPED = PROFILE["mapping"]
UNMAPPED = {"measurements": [{"type": "d", "sdmAttribute": "stemDiameter", "unit": "mm"}]}
each_mapping = pytest.mark.parametrize("mapping", [MAPPED, UNMAPPED], ids=["mapped", "unmapped"])


def _real_helper():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "common", "ngsi_headers.py")
    spec = importlib.util.spec_from_file_location("_real_ngsi_headers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inject_fiware_headers


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", CONTEXT)
    monkeypatch.setattr(device_models, "CONTEXT_URL", CONTEXT)
    monkeypatch.setattr(device_models, "ORION_URL", "http://orion.test:1026")
    monkeypatch.setattr(device_models, "inject_fiware_headers", _real_helper())
    fake = FakeBroker(TENANT).install(monkeypatch)
    fake.seed({"id": URN, "type": "Device", "controlledProperty": prop(["temperature"])})
    return fake


@pytest.fixture(params=["", None], ids=["empty", "absent"])
def no_context(request, monkeypatch, broker):
    monkeypatch.delenv("CONTEXT_URL")
    if request.param is not None:
        monkeypatch.setenv("CONTEXT_URL", request.param)


REQUESTS = {
    "read": lambda: device_models.read_entity(TENANT, URN),
    "list": lambda: device_models.list_entities(TENANT, "Device"),
    "append": lambda: device_models.append_attrs(TENANT, URN, {"deviceCategory": prop(["sensor"])}),
    "delete": lambda: device_models.delete_attr(TENANT, URN, "controlledProperty"),
}
each_request = pytest.mark.parametrize("send", REQUESTS.values(), ids=REQUESTS.keys())


def _assert_tenant(headers):
    assert (headers["NGSILD-Tenant"], headers["Fiware-Service"], headers["Fiware-ServicePath"]) == (
        TENANT, TENANT, "/")


@each_request
def test_a_raw_request_carries_the_tenant_and_the_link(send, broker):
    send()
    (headers,) = broker.headers
    _assert_tenant(headers)
    assert headers["Link"] == LINK and headers["Content-Type"] == "application/json"


def test_what_was_written_and_deleted_is_under_the_official_vocabulary(broker):
    REQUESTS["append"]()
    REQUESTS["delete"]()
    assert [m for m, _, _ in broker.log] == ["POST", "DELETE"]
    assert broker.entities[URN]["attrs"] == {DEVICE_IRI + "deviceCategory": prop("sensor")}


@each_request
def test_without_the_context_url_nothing_is_sent(send, broker, no_context):
    with pytest.raises(dataloggers.ApiError) as err:
        send()
    assert err.value.status == 502 and "CONTEXT_URL" in err.value.message
    assert broker.log == []


@pytest.mark.parametrize("url", [CONTEXT, None], ids=["set", "unset"])
def test_a_read_without_link_on_purpose_does_not_need_the_context(url, broker, monkeypatch):
    if url is None:
        monkeypatch.delenv("CONTEXT_URL")
    stored = device_models.read_entity(TENANT, URN, link=False)
    assert stored["type"] == DEVICE_IRI + "Device"
    (headers,) = broker.headers
    _assert_tenant(headers)
    assert "Link" not in headers


@each_mapping
def test_the_device_model_is_not_written_without_the_context_url(mapping, broker, no_context, caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(dataloggers.ApiError) as err:
        device_models.ensure_device_model(TENANT, {"code": "p", "name": "P", "mapping": mapping})
    assert (err.value.status, err.value.step) == (502, "device_model")
    assert broker.log == []
    assert any(MODEL in r.getMessage() and "CONTEXT_URL" in r.getMessage() for r in caplog.records)


@each_mapping
def test_the_device_is_not_written_without_the_context_url(mapping, broker, no_context):
    with pytest.raises(dataloggers.ApiError) as err:
        device_models.write_profile_attrs(TENANT, URN, {"code": "p", "mapping": mapping})
    assert (err.value.status, err.value.step) == (502, "orion_profile")
    assert not isinstance(err.value, device_models.StaleAttributeError)  # the caller must undo
    assert broker.log == []


@each_mapping
def test_every_request_of_a_profile_write_carries_the_tenant_and_its_context(mapping, broker):
    """The batch upsert has the context in its body, so it carries no Link."""
    profile = {"code": "p", "name": "P", "mapping": mapping}
    device_models.ensure_device_model(TENANT, profile)
    device_models.write_profile_attrs(TENANT, URN, profile)
    assert len(broker.log) == (4 if mapping is UNMAPPED else 2)
    for (method, subject, link), headers in zip(broker.log, broker.headers, strict=True):
        assert headers["NGSILD-Tenant"] == TENANT
        assert link is (subject != "entityOperations/upsert"), (method, subject)


# ---- the endpoints -----------------------------------------------------------

@pytest.fixture
def orion(monkeypatch, no_context):
    """The fakes of the endpoints, with the real header helper and no CONTEXT_URL."""
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    for code in ("p", "p2"):
        st.profiles[code] = {"tenant_id": TENANT, "name": code, "mapping": MAPPED}
    st.devices.append(dict(datalogger_id=DL, tenant_id=TENANT, device_urn=URN, external_id="S1",
                           dm_writer="em", dm_shape=None))
    with wired(st) as w:
        monkeypatch.setattr(device_models, "inject_fiware_headers", _real_helper())
        yield w


def _device():
    return {"id": URN, "type": "Device", "profileCode": prop("p"), "refDeviceModel": rel(MODEL)}


def _put(path, body):
    return client().put(path, headers=AUTH, content_type="application/json", data=json.dumps(body))


def _attributes(mapping, code):
    return ("S1", dataloggers._attributes_for_state(("em", None), TENANT, URN, "S1", code,
                                                    mapping["measurements"]))


@each_mapping
def test_a_registration_without_the_context_url_is_502_and_writes_nothing(mapping, orion):
    orion.state.devices.clear()
    orion.state.profiles["p"]["mapping"] = mapping
    r = post_json(client(), "/api/sensors/register", {
        "external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL], "datalogger_id": DL,
        "validFrom": "2026-03-01T08:00:00+00:00"})
    assert r.status_code == 502 and r.get_json()["step"] == "device_model", r.get_json()
    assert not any(e[0] in ("orion", "iota") for e in orion.state.log)
    assert orion.orion.entities == {} == orion.orion.device_models and orion.state.devices == []


@each_mapping
def test_a_profile_change_without_the_context_url_is_502_and_restores_the_agent(mapping, orion):
    orion.state.profiles["p2"]["mapping"] = mapping
    orion.orion.entities[URN] = _device()
    r = _put(f"/api/sensors/devices/{URN}/profile", {"profile": "p2"})
    assert r.status_code == 502 and r.get_json()["step"] == "device_model", r.get_json()
    assert orion.state.iota_attrs == [_attributes(mapping, "p2"), _attributes(MAPPED, "p")]
    assert not any(e[0] == "orion" for e in orion.state.log) and orion.orion.entities[URN] == _device()


def test_a_context_url_that_goes_away_before_the_device_write_restores_the_agent(orion, monkeypatch):
    monkeypatch.setattr(device_models, "ensure_device_model", lambda *a: None)
    orion.orion.entities[URN] = _device()
    r = _put(f"/api/sensors/devices/{URN}/profile", {"profile": "p2"})
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()
    assert orion.state.iota_attrs == [_attributes(MAPPED, "p2"), _attributes(MAPPED, "p")]
    assert not any(e[0] == "orion" for e in orion.state.log) and orion.orion.entities[URN] == _device()


def test_a_profile_edit_without_the_context_url_lists_what_was_not_refreshed(orion):
    orion.orion.entities[URN] = _device()
    orion.orion.device_models[MODEL] = {"id": MODEL, "type": "DeviceModel"}
    r = _put("/api/sensors/profiles/p", {"name": "Renamed", "mapping": UNMAPPED})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["refresh_failed"] == [MODEL, URN]
    assert r.get_json()["refreshed"] == [] == r.get_json()["skipped"]
    assert orion.state.profiles["p"]["name"] == "Renamed"
    assert not any(e[0] == "orion" for e in orion.state.log)
