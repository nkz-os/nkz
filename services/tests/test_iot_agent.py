"""Shared IoT Agent provisioning helper (requests mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from common import iot_agent

URL = "http://iota.invalid:4041"
HDR = {"Fiware-Service": "acme"}
ORION = "http://orion.invalid:1026"


def _resp(status, body=None, text=""):
    r = MagicMock(status_code=status, text=text)
    r.json.return_value = body if body is not None else {}
    return r


def test_service_group_reused_when_present():
    with patch.object(iot_agent.requests, "get",
                      return_value=_resp(200, {"services": [{"apikey": "k-existing"}]})), \
         patch.object(iot_agent.requests, "post") as post:
        assert iot_agent.get_or_create_service_group("acme", URL, HDR, cbroker=ORION) == "k-existing"
    post.assert_not_called()


def test_service_group_without_apikey_is_a_failure():
    with patch.object(iot_agent.requests, "get",
                      return_value=_resp(200, {"services": [{"resource": "/iot/json"}]})), \
         patch.object(iot_agent.requests, "post") as post:
        assert iot_agent.get_or_create_service_group("acme", URL, HDR, cbroker=ORION) is None
    post.assert_not_called()


def test_service_group_created_with_generated_key():
    with patch.object(iot_agent.requests, "get", return_value=_resp(200, {"services": []})), \
         patch.object(iot_agent.requests, "post", return_value=_resp(201)) as post:
        key = iot_agent.get_or_create_service_group("acme", URL, HDR, cbroker=ORION)
    assert key and key.startswith("nkz_iot_")
    body = post.call_args.kwargs["json"]["services"][0]
    assert body["apikey"] == key and body["resource"] == "/iot/json"
    assert body["cbroker"] == ORION and body["entity_type"] == "Device"
    assert post.call_args.args[0] == f"{URL}/iot/services"
    assert post.call_args.kwargs["headers"] == HDR


def test_service_group_409_race_returns_existing():
    gets = [_resp(200, {"services": []}), _resp(200, {"services": [{"apikey": "k-race"}]})]
    with patch.object(iot_agent.requests, "get", side_effect=gets), \
         patch.object(iot_agent.requests, "post", return_value=_resp(409)):
        assert iot_agent.get_or_create_service_group("acme", URL, HDR, cbroker=ORION) == "k-race"


def test_service_group_failure_returns_none():
    with patch.object(iot_agent.requests, "get", return_value=_resp(200, {"services": []})), \
         patch.object(iot_agent.requests, "post", return_value=_resp(500, text="boom")):
        assert iot_agent.get_or_create_service_group("acme", URL, HDR, cbroker=ORION) is None


def test_attributes_from_profile_measurements():
    attrs = iot_agent.build_iota_attributes([
        {"type": "temperature", "sdmAttribute": "temperature", "unit": "CEL"},
        {"type": "soilMoisture", "sdmAttribute": "moisture", "unit": "P1",
         "incomingKey": "r3", "expression": "r3 * 0.1"},
        {"type": "note", "sdmAttribute": "note"},
        {"type": "flag", "sdmAttribute": "flag", "unit": "C62", "valueType": "Boolean"},
    ])
    assert attrs == [
        {"object_id": "temperature", "name": "airTemperature", "type": "Number"},
        {"object_id": "r3", "name": "moisture", "type": "Number", "expression": "r3 * 0.1"},
        {"object_id": "note", "name": "note", "type": "Text"},
        {"object_id": "flag", "name": "flag", "type": "Boolean"},
        {"object_id": "TimeInstant", "name": "TimeInstant", "type": "DateTime"},
    ]


def test_attributes_declare_timeinstant_once():
    # With explicit attributes the agent drops undeclared keys, TimeInstant included,
    # and observedAt falls back to the arrival time. Declared, it becomes observedAt.
    attrs = iot_agent.build_iota_attributes([
        {"type": "temperature", "sdmAttribute": "temperature", "unit": "CEL"},
        {"type": "TimeInstant", "sdmAttribute": "TimeInstant", "valueType": "DateTime"},
    ])
    assert [a["object_id"] for a in attrs].count("TimeInstant") == 1
    assert iot_agent.build_iota_attributes([])[-1] == {
        "object_id": "TimeInstant", "name": "TimeInstant", "type": "DateTime"}


def test_provision_posts_device_body():
    with patch.object(iot_agent.requests, "post", return_value=_resp(201)) as post:
        iot_agent.provision_device(
            URL, HDR, apikey="k", device_id="s1", entity_name="urn:ngsi-ld:Device:acme:s1",
            entity_type="Device", attributes=[{"object_id": "a", "name": "b", "type": "Number"}])
    assert post.call_args.args[0] == f"{URL}/iot/devices"
    dev = post.call_args.kwargs["json"]["devices"][0]
    assert dev["device_id"] == "s1" and dev["apikey"] == "k"
    assert dev["protocol"] == "IoTA-JSON" and dev["transport"] == "MQTT"
    assert dev["static_attributes"] == [] and dev["lazy"] == [] and dev["commands"] == []


def test_provision_raises_on_409():
    with patch.object(iot_agent.requests, "post", return_value=_resp(409, text="exists")):
        with pytest.raises(iot_agent.IotAgentError) as e:
            iot_agent.provision_device(
                URL, HDR, apikey="k", device_id="s1", entity_name="n",
                entity_type="Device", attributes=[])
    assert e.value.status_code == 409


def test_delete_treats_404_as_gone():
    with patch.object(iot_agent.requests, "delete", return_value=_resp(404)):
        assert iot_agent.delete_device(URL, HDR, "s1") is True
    with patch.object(iot_agent.requests, "delete", return_value=_resp(204)):
        assert iot_agent.delete_device(URL, HDR, "s1") is True
    with patch.object(iot_agent.requests, "delete", return_value=_resp(500)):
        assert iot_agent.delete_device(URL, HDR, "s1") is False


def test_update_device_attributes_puts_and_raises():
    with patch.object(iot_agent.requests, "put", return_value=_resp(204)) as put:
        iot_agent.update_device_attributes(URL, HDR, "s1", [{"object_id": "a", "name": "b", "type": "Number"}])
    assert put.call_args.args[0] == f"{URL}/iot/devices/s1"
    assert put.call_args.kwargs["json"] == {"attributes": [{"object_id": "a", "name": "b", "type": "Number"}]}
    with patch.object(iot_agent.requests, "put", return_value=_resp(404, text="nf")):
        with pytest.raises(iot_agent.IotAgentError):
            iot_agent.update_device_attributes(URL, HDR, "s1", [])


def test_statistics_expand_to_derived_iota_attributes():
    attrs = iot_agent.build_iota_attributes([
        {"type": "temp", "sdmAttribute": "airTemperature", "unit": "\u00b0C",
         "statistics": ["mean", "min"]}])
    assert {"object_id": "tempMin", "name": "airTemperatureMin", "type": "Number"} in attrs
    assert {"object_id": "temp", "name": "airTemperature", "type": "Number"} in attrs
    assert [a["object_id"] for a in attrs].count("TimeInstant") == 1
    assert attrs[-1]["object_id"] == "TimeInstant"


def test_no_statistics_leaves_iota_attributes_unchanged():
    attrs = iot_agent.build_iota_attributes([
        {"type": "temp", "sdmAttribute": "airTemperature", "unit": "\u00b0C"}])
    assert attrs == [{"object_id": "temp", "name": "airTemperature", "type": "Number"},
                     dict(iot_agent.TIME_INSTANT_ATTRIBUTE)]


# -- update succeeds only on 204; entity-type whitelist ----------------------------------

@pytest.mark.parametrize("status", [200, 201, 202])
def test_update_device_attributes_other_2xx_is_a_failure(status):
    """The agent answers 200 with an error body and applies nothing when the entity is absent."""
    with patch.object(iot_agent.requests, "put",
                      return_value=_resp(status, text='{"name":"ENTITY_NOT_FOUND"}')):
        with pytest.raises(iot_agent.IotAgentError) as e:
            iot_agent.update_device_attributes(URL, HDR, "s1", [])
    assert e.value.status_code == status and "ENTITY_NOT_FOUND" in e.value.body


DM_ATTR = {"object_id": "t1", "name": "numValue", "type": "Number",
           "entity_name": "urn:ngsi-ld:DeviceMeasurement:acme:s1:t1", "entity_type": "DeviceMeasurement"}


def test_allowed_entity_types_are_in_the_platform_list():
    from pathlib import Path
    listed = {line.strip() for line in
              (Path(__file__).resolve().parents[2] / "config/entity-types/allowed.txt").read_text().splitlines()
              if line.strip() and not line.startswith("#")}
    assert iot_agent.ALLOWED_ENTITY_TYPES == frozenset({"Device", "ManufacturingMachine"})
    assert iot_agent.ALLOWED_MULTI_ENTITY_TYPES == frozenset({"DeviceMeasurement"})
    assert (iot_agent.ALLOWED_ENTITY_TYPES | iot_agent.ALLOWED_MULTI_ENTITY_TYPES) <= listed


@pytest.mark.parametrize("main,attrs", [
    ("AgriSensor", []),
    ("Device", [dict(DM_ATTR, entity_type="Other")]),
    ("Device", [dict(DM_ATTR, entity_type="Device")]),
])
def test_provision_rejects_disallowed_types_before_any_request(main, attrs):
    with patch.object(iot_agent.requests, "post") as post:
        with pytest.raises(ValueError):
            iot_agent.provision_device(URL, HDR, apikey="k", device_id="s1", entity_name="n",
                                       entity_type=main, attributes=attrs)
    post.assert_not_called()


def test_provision_accepts_main_and_multi_entity_types():
    with patch.object(iot_agent.requests, "post", return_value=_resp(201)) as post:
        for main in ("Device", "ManufacturingMachine"):
            iot_agent.provision_device(URL, HDR, apikey="k", device_id="s1", entity_name="n",
                                       entity_type=main,
                                       attributes=[DM_ATTR, {"object_id": "a", "name": "b", "type": "Number"}])
    assert post.call_count == 2


@pytest.mark.parametrize("bad", ["Other", "Device"])
def test_update_rejects_disallowed_attribute_entity_type(bad):
    with patch.object(iot_agent.requests, "put") as put:
        with pytest.raises(ValueError):
            iot_agent.update_device_attributes(URL, HDR, "s1", [dict(DM_ATTR, entity_type=bad)])
    put.assert_not_called()


def test_update_accepts_dm_attributes():
    with patch.object(iot_agent.requests, "put", return_value=_resp(204)):
        iot_agent.update_device_attributes(URL, HDR, "s1", [DM_ATTR])


# -- reading a device document ------------------------------------------------------------

def test_get_device_returns_the_document():
    doc = {"device_id": "s1", "attributes": [DM_ATTR]}
    with patch.object(iot_agent.requests, "get", return_value=_resp(200, doc)) as get:
        assert iot_agent.get_device(URL, HDR, "s1") == doc
    assert get.call_args.args[0] == f"{URL}/iot/devices/s1"
    assert get.call_args.kwargs["headers"] == HDR


@pytest.mark.parametrize("status", [404, 500])
def test_get_device_non_200_raises(status):
    with patch.object(iot_agent.requests, "get", return_value=_resp(status, text="nope")):
        with pytest.raises(iot_agent.IotAgentError) as e:
            iot_agent.get_device(URL, HDR, "s1")
    assert e.value.status_code == status


def test_get_device_rejects_a_body_that_is_not_an_object():
    with patch.object(iot_agent.requests, "get", return_value=_resp(200, ["x"])):
        with pytest.raises(iot_agent.IotAgentError):
            iot_agent.get_device(URL, HDR, "s1")
