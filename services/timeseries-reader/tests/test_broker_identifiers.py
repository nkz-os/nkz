"""Only well-formed ids and filters are asked to the broker."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import app  # noqa: E402
import urn_resolution as u  # noqa: E402
from observations import geometry  # noqa: E402

GOOD_IDS = [
    "urn:ngsi-ld:Device:tenant-a:d1",
    "urn:ngsi-ld:DeviceMeasurement:tenant-a:logger:1:airTemperature",
    "urn:ngsi-ld:Device:a.b-c_d/e",
    "https://example.org/d/1",
    "urn:ngsi-ld:Device:ñ 1",
]
OTHER_IDS = ["urn:a:b?attrs=x", "urn:a:b#x", "urn:a:b%00", "urn:a:b%2500", "urn:a:b\x00", "urn:a:b\n", "?", "#", "/"]


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setattr(u, "ORION_URL", "http://orion:1026")
    reply = MagicMock(status_code=200)
    reply.json.return_value = {"id": "x", "type": "Device"}
    get = MagicMock(return_value=reply)
    monkeypatch.setattr(u.requests, "get", get)
    return get


@pytest.mark.parametrize("entity_id", GOOD_IDS)
def test_well_formed_ids_are_asked(broker, entity_id):
    assert u.fetch_orion_entity("tenant-a", entity_id) == {"id": "x", "type": "Device"}
    assert geometry.fetch_orion_entity_status("tenant-a", entity_id)[0] == "ok"
    assert [c.args[0] for c in broker.call_args_list] == [f"http://orion:1026/ngsi-ld/v1/entities/{entity_id}"] * 2


@pytest.mark.parametrize("entity_id", OTHER_IDS)
def test_other_ids_are_not_asked(broker, entity_id):
    assert u.fetch_orion_entity("tenant-a", entity_id) is None
    # Only a real 404 may drop a cached geometry.
    assert geometry.fetch_orion_entity_status("tenant-a", entity_id) == ("error", None)
    assert broker.call_args_list == []


@pytest.mark.parametrize("entity_id", ["urn:a:b?attrs=x", "urn:a:b%2500"])
def test_a_read_plan_does_not_ask_other_ids(broker, entity_id):
    u.plan_timeseries_read("tenant-a", entity_id)
    assert broker.call_args_list == []


@pytest.mark.parametrize("plant_id", ["p1", "urn:ngsi-ld:AgriPlant:tenant-a:demo-001:T-001", "Vid ñ (3)"])
def test_plant_samples_are_asked(broker, plant_id):
    broker.return_value.json.return_value = [{"id": "urn:a:s1"}]
    assert u.plant_sample_ids("tenant-a", "AgriPlantSample", plant_id) == ["urn:a:s1"]
    assert broker.call_args.kwargs["params"]["q"] == f'hasAgriPlant=="{plant_id}"'


@pytest.mark.parametrize(
    "entity_type, plant_id",
    [
        ("AgriPlantSample", 'x"'),
        ("AgriPlantSample", 'x";a==1'),
        ("AgriPlantSample", 'x";a=="y'),
        ("AgriPlantSample", "x\x00"),
        ("AgriPlantSample", "x\\"),
        ("AgriPlantSample", "x" * 300),
        ("Agri Sample", "p1"),
        ("A,B", "p1"),
        ("A&q=a", "p1"),
        ("a" * 129, "p1"),
    ],
)
def test_other_plant_samples_are_not_asked(broker, entity_type, plant_id):
    with pytest.raises(u.InvalidQuery):
        u.plant_sample_ids("tenant-a", entity_type, plant_id)
    assert broker.call_args_list == []


def test_the_plant_route_answers_400(broker):
    with app.app.test_client() as c:
        resp = c.get("/api/timeseries/type/AgriPlantSample/plant/x%22/data", headers={"X-Tenant-ID": "tenant-a"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid entity type or plant id"}


def test_the_plant_route_still_reports_an_unavailable_broker(broker):
    broker.return_value.status_code = 503
    with app.app.test_client() as c:
        resp = c.get("/api/timeseries/type/AgriPlantSample/plant/p1/data", headers={"X-Tenant-ID": "tenant-a"})
    assert resp.status_code == 502
