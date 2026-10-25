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
    ]


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
