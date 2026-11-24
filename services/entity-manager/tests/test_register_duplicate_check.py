"""Registration looks for an existing sensor by its entity id, for every type.

The id is derived from type, tenant and external id. Only a 404 means "free": an answer
the broker did not give must never let a second sensor be written.
"""
import pytest
import requests

from tests.telemetry_fakes import PARCEL, PROFILE, TENANT, FakeState, client, post_json, seed_datalogger, wired

DL = "11111111-1111-1111-1111-111111111111"
TYPES = ("Device", "ManufacturingMachine")
BODY = {"external_id": "S1", "name": "Sensor 1", "profile": "p", "location": {"lat": 42.0, "lon": -2.0},
        "controlledAsset": [PARCEL], "datalogger_id": DL}


def _urn(entity_type):
    return f"urn:ngsi-ld:{entity_type}:{TENANT}:S1"


def _state(entity_type):
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = {**PROFILE, "name": "Profile P", "sdm_entity_type": entity_type}
    return st


def _nothing_was_written(st):
    return not any(e[0] in ("orion", "iota", "dynsec", "db") for e in st.log)


@pytest.mark.parametrize("attrs", [
    {"externalId": {"type": "Property", "value": "S1"}}, {},
    {"externalId": {"type": "Property", "value": "another"}},
], ids=["with-external-id", "without", "with-another"])
@pytest.mark.parametrize("entity_type", TYPES)
def test_an_existing_entity_is_409_with_the_body_of_always(entity_type, attrs):
    st = _state(entity_type)
    existing = {"id": _urn(entity_type), "type": entity_type,
                "name": {"type": "Property", "value": "Old name"}, **attrs}
    with wired(st) as w:
        w.orion.entities[_urn(entity_type)] = dict(existing)
        r = post_json(client(), "/api/sensors/register", BODY)
    assert r.status_code == 409
    assert r.get_json() == {
        "error": 'Sensor with external_id "S1" already exists',
        "sensor": {"id": _urn(entity_type), "external_id": "S1", "name": "Old name"}}
    assert w.orion.entities == {_urn(entity_type): existing} and _nothing_was_written(st)


@pytest.mark.parametrize("entity_type", TYPES)
def test_an_id_nobody_has_is_free(entity_type):
    """Another entity with the same externalId attribute is not this sensor."""
    st = _state(entity_type)
    with wired(st) as w:
        w.orion.entities["urn:ngsi-ld:Device:twin-1"] = {
            "id": "urn:ngsi-ld:Device:twin-1", "type": "Device",
            "externalId": {"type": "Property", "value": "S1"}}
        r = post_json(client(), "/api/sensors/register", BODY)
    assert r.status_code == 201, r.get_json()
    assert _urn(entity_type) in w.orion.entities


@pytest.mark.parametrize("answer", [500, 503, 400, 401, 207, requests.exceptions.ConnectionError("down"),
                                    requests.exceptions.Timeout("slow")], ids=str)
@pytest.mark.parametrize("entity_type", TYPES)
def test_no_answer_is_never_free(entity_type, answer):
    st = _state(entity_type)
    st.fail["orion_read"] = {_urn(entity_type): answer}
    with wired(st) as w:
        r = post_json(client(), "/api/sensors/register", BODY)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_read", r.get_json()
    assert w.orion.entities == {} and _nothing_was_written(st)
