"""Sensor requests accept only well-formed entity ids."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.telemetry_fakes import PARCEL, TENANT, client
from tests.test_sensor_observation_api import URN, _dep, _register, _send, env

BASE_ID = "urn:ngsi-ld:AgriParcel:p2"
UUID = "da36ccd2-85d2-4c76-b552-c5c835a987c1"

OTHER_IDS = {
    "query": f"{BASE_ID}?attrs=x",
    "question-mark": f"{BASE_ID}?",
    "fragment": f"{BASE_ID}#x",
    "slash": f"{BASE_ID}/x",
    "slash-last": f"{BASE_ID}/",
    "slash-twice": f"{BASE_ID}//x",
    "sub-path": f"{BASE_ID}/attrs/x",
    "slash-first": "urn:ngsi-ld:AgriParcel:/p2",
    "dot-segment": f"{BASE_ID}/../x",
    "dot-segment-last": f"{BASE_ID}/..",
    "dot-segment-single": f"{BASE_ID}/./x",
    "encoded-dots": f"{BASE_ID}%2e%2e%2f",
    "encoded-dot-segment": f"{BASE_ID}/%2e%2e%2fx",
    "encoded-slash": f"{BASE_ID}%2Fx",
    "encoded-question-mark": f"{BASE_ID}%3F",
    "encoded-twice": f"{BASE_ID}%253F",
    "encoded-letter": f"{BASE_ID}%41",
    "percent": f"{BASE_ID}%",
    "backslash": f"{BASE_ID}\\x",
    "quote": f'{BASE_ID}"',
    "cr-lf": f"{BASE_ID}\r\nx",
    "line-feed-last": f"{BASE_ID}\n",
    "tab": f"{BASE_ID}\tx",
    "nul": f"{BASE_ID}\x00",
    "over-long": f"{BASE_ID}{'x' * (257 - len(BASE_ID))}",
    "very-long": f"{BASE_ID}{'x' * 5000}",
}
other_ids = pytest.mark.parametrize("other", OTHER_IDS.values(), ids=OTHER_IDS)

REAL_IDS = [
    "urn:ngsi-ld:AgriParcel:p2",
    f"urn:ngsi-ld:AgriParcel:{UUID}",
    f"urn:ngsi-ld:AgriParcel:tenant-a:{UUID}",
    "urn:ngsi-ld:AgriParcel:tenant_a:north.field-2_b",
    "urn:ngsi-ld:AgriParcel:ñ",
    f"urn:ngsi-ld:AgriParcelZone:tenant-a:{UUID}:zn000001-e11-flat",
    "urn:ngsi-ld:AgriManagementZone:z1",
    "urn:ngsi-ld:AgriParcelRecord:photo-20260301T080000-0a1b2c3d",
    "urn:ngsi-ld:AgriCrop:tenant-a:c1",
    "urn:ngsi-ld:AgriPlant:tenant-a:north:R01.P-02_a",
    "urn:ngsi-ld:AgriTree:t1",
    "urn:ngsi-ld:AgriSolarPark:tenant-a:park-1",
    f"urn:ngsi-ld:AgriEnergyTracker:{UUID}",
    "urn:ngsi-ld:AgriGreenhouse:g1",
    "urn:ngsi-ld:Greenhouse:t1:g",
    "urn:ngsi-ld:Building:b1",
    f"urn:ngsi-ld:AgriculturalRobot:tenant-a:{UUID}",
    "urn:ngsi-ld:AutonomousMobileRobot:tenant-a:r1",
    "urn:ngsi-ld:ManufacturingMachine:tenant-a:tractor-1",
    "urn:ngsi-ld:Device:tenant-a:a.b-c_d",
    "urn:ngsi-ld:Device:a.b-c~d",
    "urn:ngsi-ld:DeviceMeasurement:tenant-a:logger:1:airTemperature",
    "urn:ngsi-ld:WeatherObserved:tenant-a:parcel-p1-daily",
    "urn:ngsi-ld:CompostBatch:tenant-a:batch:2026:1",
    f"{BASE_ID}{'x' * (256 - len(BASE_ID))}",
]

STRUCTURE = {"medium": "ambientAirUnderStructure"}
ENCLOSURE = {"medium": "enclosedAir"}


def _in_override(part):
    return {"channelOverrides": {"airTemperature": part}}


FIELDS = {
    "observesFeature": lambda v: {"observesFeature": v},
    "observationContext.structure": lambda v: {"observationContext": {**STRUCTURE, "structure": v}},
    "observationContext.enclosure": lambda v: {"observationContext": {**ENCLOSURE, "enclosure": v}},
    "channelOverrides.*.observesFeature": lambda v: _in_override({"observesFeature": v}),
    "channelOverrides.*.observationContext.structure":
        lambda v: _in_override({"observationContext": {**STRUCTURE, "structure": v}}),
    "channelOverrides.*.observationContext.enclosure":
        lambda v: _in_override({"observationContext": {**ENCLOSURE, "enclosure": v}}),
}
fields = pytest.mark.parametrize("field", FIELDS)

ROUTES = {
    "register": lambda body: _register(client(), **body),
    "move": lambda body: _send(client(), "post", f"/api/sensors/devices/{URN}/deployments",
                               {"reason": "moved", **body}),
    "correct": lambda body: _send(client(), "put", "/api/sensors/deployments/dep-1",
                                  {"reason": "typo", **body}),
}
routes = pytest.mark.parametrize("route", ROUTES)

PARCEL_SHAPES = {
    "string": lambda v: v,
    "object": lambda v: {"object": v},
    "list": lambda v: [PARCEL, v],
    "object-list": lambda v: {"type": "Relationship", "object": [PARCEL, v]},
    "list-of-objects": lambda v: [{"object": PARCEL}, {"object": v}],
}
parcel_shapes = pytest.mark.parametrize("shape", PARCEL_SHAPES)


@contextmanager
def recorded(route="register"):
    """`env` that keeps every Orion request in `calls` and every database use in `db`."""
    import blueprints.sensors as sensors
    import dataloggers

    with env(current=_dep(), registered=(route != "register")) as e:
        e.dep.get.return_value = _dep()
        e.dep.correct.return_value = _dep()
        calls = []
        for verb in ("get", "post", "put", "patch", "delete"):
            def call(url, _verb=verb, _real=getattr(e.orion, verb, None), **kw):
                calls.append((_verb, url.rsplit("/entities", 1)[-1].lstrip("/")))
                return _real(url, **kw)
            setattr(e.orion, verb, call)
        dl_db = MagicMock(wraps=dataloggers.get_db_connection_with_tenant)
        with patch.object(dataloggers, "get_db_connection_with_tenant", dl_db), \
                patch.object(sensors, "get_db_connection_with_tenant", dl_db):
            yield SimpleNamespace(env=e, orion=e.orion, dep=e.dep, calls=calls, db=dl_db)


def _deployment_calls(dep):
    return [name for name in ("open", "delete", "revert", "close", "correct", "current", "get",
                              "listing") if getattr(dep, name).called]


def _nothing_happened(r):
    assert r.calls == []
    assert r.db.called is False
    assert _deployment_calls(r.dep) == []
    assert r.env.state.log == []


def _refused(resp, field, other, caplog):
    assert resp.status_code == 422, resp.get_json()
    assert resp.get_json() == {"error": f"{field} is not a valid entity id"}
    assert other not in caplog.text


# ---- observation fields ------------------------------------------------------

@routes
@fields
@other_ids
def test_other_ids_are_refused_first(route, field, other, caplog):
    with recorded(route) as r:
        resp = ROUTES[route](FIELDS[field](other))
    _refused(resp, field, other, caplog)
    _nothing_happened(r)


@routes
@pytest.mark.parametrize("field", [f for f in FIELDS if f.startswith("channelOverrides")])
def test_an_override_key_is_not_repeated_in_the_answer(route, field, caplog):
    body = FIELDS[field](OTHER_IDS["query"])
    body["channelOverrides"] = {"k\r\n": body["channelOverrides"]["airTemperature"]}
    with recorded(route) as r:
        resp = ROUTES[route](body)
    _refused(resp, field, "k\r\n", caplog)
    _nothing_happened(r)


@routes
def test_the_second_override_is_checked(route, caplog):
    other = OTHER_IDS["fragment"]
    with recorded(route) as r:
        resp = ROUTES[route]({"channelOverrides": {
            "airTemperature": {"observesFeature": BASE_ID},
            "relativeHumidity": {"observesFeature": other}}})
    _refused(resp, "channelOverrides.*.observesFeature", other, caplog)
    _nothing_happened(r)


NOT_URNS = {"number": 5, "true": True, "list": [BASE_ID], "list-of-numbers": [5],
            "object": {"object": BASE_ID}, "object-number": {"object": 5},
            "name": "p2", "empty": "", "other-scheme": "https://example.org/p2?x"}


@routes
@fields
@pytest.mark.parametrize("value", NOT_URNS.values(), ids=NOT_URNS)
def test_values_that_are_not_urns_are_still_400(route, field, value):
    with recorded(route) as r:
        resp = ROUTES[route](FIELDS[field](value))
    assert resp.status_code == 400, resp.get_json()
    # A deployment route reads its own device first, as before; nothing else is asked.
    assert set(r.calls) <= {("get", URN)}
    assert not {"open", "correct", "revert", "close", "delete"} & set(_deployment_calls(r.dep))
    assert r.env.state.log == []


@routes
@pytest.mark.parametrize("body", [
    {"observationContext": ["x"]}, {"observationContext": "x"}, {"channelOverrides": ["x"]},
    {"channelOverrides": {"airTemperature": "x"}},
    {"channelOverrides": {"airTemperature": {"observationContext": [5]}}},
])
def test_other_shapes_are_still_400(route, body):
    with recorded(route) as r:
        assert ROUTES[route](body).status_code == 400
    assert r.env.state.log == []


@routes
@fields
@pytest.mark.parametrize("entity_id", REAL_IDS)
def test_real_ids_are_read_and_accepted(route, field, entity_id):
    with recorded(route) as r:
        r.orion.entities[entity_id] = {"id": entity_id}
        resp = ROUTES[route](FIELDS[field](entity_id))
    assert resp.status_code == (200 if route == "correct" else 201), resp.get_json()
    assert ("get", entity_id) in r.calls


# ---- controlledAsset -----------------------------------------------------------

@parcel_shapes
@other_ids
def test_other_parcel_ids_are_refused_first(shape, other, caplog):
    with recorded() as r:
        resp = _register(client(), controlledAsset=PARCEL_SHAPES[shape](other))
    _refused(resp, "controlledAsset", other, caplog)
    _nothing_happened(r)


NOT_STRINGS = {"number": 5, "zero": 0, "false": False, "true": True, "list": [PARCEL],
               "empty-list": [], "object-number": {"object": 5}, "object-list": {"object": [7]},
               "object-false": {"object": False}}


@pytest.mark.parametrize("shape", ["list", "object-list"])
@pytest.mark.parametrize("item", NOT_STRINGS.values(), ids=NOT_STRINGS)
def test_a_parcel_list_holds_only_ids(shape, item):
    with recorded() as r:
        resp = _register(client(), controlledAsset=PARCEL_SHAPES[shape](item))
    assert resp.status_code == 422, resp.get_json()
    assert resp.get_json() == {"error": "controlledAsset is not a valid entity id"}
    _nothing_happened(r)


@pytest.mark.parametrize("value", [
    None, "", [], {}, 5, [5], {"object": 5}, [[PARCEL]], "p2", ["p2"], [PARCEL, "p2"], ["p2", 5],
    ["urn:ngsi-ld:Device:d1"], "urn:ngsi-ld:Device:d1?x", [PARCEL, "p2", OTHER_IDS["query"]],
])
def test_parcel_values_that_are_not_urns_are_still_400(value):
    with recorded() as r:
        resp = _register(client(), controlledAsset=value)
    assert resp.status_code == 400
    assert resp.get_json() == {
        "error": "controlledAsset must hold NGSI-LD URNs and at least one AgriParcel."}
    _nothing_happened(r)


@pytest.mark.parametrize("shape, entity_id", [
    (shape, entity_id) for shape in PARCEL_SHAPES for entity_id in REAL_IDS
    if ":AgriParcel:" in entity_id or shape not in ("string", "object")])
def test_real_parcel_ids_are_stored(shape, entity_id):
    with recorded() as r:
        resp = _register(client(), controlledAsset=PARCEL_SHAPES[shape](entity_id))
    assert resp.status_code == 201, resp.get_json()
    wanted = [entity_id] if shape in ("string", "object") else [PARCEL, entity_id]
    assert r.orion.entities[URN]["controlledAsset"] == {"type": "Relationship", "object": wanted}


@pytest.mark.parametrize("empty", [None, "", {}, {"object": None}])
def test_empty_parcel_entries_are_still_left_out(empty):
    with recorded() as r:
        assert _register(client(), controlledAsset=[PARCEL, empty, PARCEL]).status_code == 201
    assert r.orion.entities[URN]["controlledAsset"]["object"] == [PARCEL]


# ---- ids the service builds -----------------------------------------------------

@pytest.mark.parametrize("external_id", ["S1", "a.b-c_d", "..", "x" * 64])
def test_the_registered_id_is_read_and_written(external_id):
    urn = f"urn:ngsi-ld:Device:{TENANT}:{external_id}"
    with recorded() as r:
        assert _register(client(), external_id=external_id).status_code == 201
    assert ("get", urn) in r.calls
    assert urn in r.orion.entities


# ---- each step on its own --------------------------------------------------------

NOT_IDS = {**OTHER_IDS, "none": None, "number": 5, "empty": "", "bytes": BASE_ID.encode(),
           "list": [BASE_ID], "object": {"object": BASE_ID}}
not_ids = pytest.mark.parametrize("value", NOT_IDS.values(), ids=NOT_IDS)


@fields
@other_ids
def test_the_observation_check_refuses_other_ids(field, other):
    import blueprints.sensors as sensors
    import dataloggers

    with pytest.raises(dataloggers.ApiError) as caught:
        sensors._validated_observation(FIELDS[field](other), {"airTemperature"})
    assert caught.value.body() == {"error": f"{field} is not a valid entity id"}
    assert caught.value.status == 422


@not_ids
def test_the_entity_read_asks_only_for_ids(value):
    import blueprints.sensors as sensors
    import dataloggers

    broker = MagicMock()
    with patch.object(sensors, "requests", broker), \
            pytest.raises(dataloggers.ApiError) as caught:
        sensors._orion_get_entity(TENANT, value)
    assert (caught.value.status, caught.value.body()) == (422, {"error": "Not a valid entity id"})
    assert broker.mock_calls == []


@pytest.mark.parametrize("entity_id", REAL_IDS)
def test_the_entity_read_asks_for_real_ids(entity_id):
    import blueprints.sensors as sensors

    broker = MagicMock()
    broker.get.return_value = MagicMock(status_code=200, json=lambda: {"id": entity_id})
    with patch.object(sensors, "requests", broker):
        assert sensors._orion_get_entity(TENANT, entity_id) == {"id": entity_id}
    assert broker.get.call_args.args[0].endswith(f"/ngsi-ld/v1/entities/{entity_id}")


@not_ids
def test_the_command_status_is_written_only_to_ids(value, caplog):
    import blueprints.sensors as sensors

    broker = MagicMock()
    with patch.object(sensors, "requests", broker):
        assert sensors._patch_command_status(TENANT, value, "sent") is False
    assert broker.mock_calls == []
    assert "invalid entity id" in caplog.text
    if isinstance(value, str) and value:
        assert value not in caplog.text


def test_the_command_status_is_written_to_the_command():
    import blueprints.sensors as sensors

    broker = MagicMock()
    broker.patch.return_value.status_code = 204
    urn = f"urn:ngsi-ld:DeviceCommand:{TENANT}:{UUID}"
    with patch.object(sensors, "requests", broker):
        assert sensors._patch_command_status(TENANT, urn, "sent") is True
    assert broker.patch.call_args.args[0].endswith(f"/ngsi-ld/v1/entities/{urn}/attrs")
