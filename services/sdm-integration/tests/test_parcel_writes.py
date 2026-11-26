"""Parcels are written only by their own service, whatever route a write takes."""

import importlib.util
import os
import sys

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from test_route_identifiers import _Upstream, broker, client, sdm  # noqa: E402,F401

# The gateway's own cases, so the two guards judge the same names.
_spec = importlib.util.spec_from_file_location(
    "gateway_parcel_cases", os.path.join(_SERVICES_DIR, "api-gateway", "tests", "test_parcel_guard.py")
)
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)

REFUSED = {
    "error": "AgriParcel writes must go through entity-manager API",
    "hint": "Use POST/PATCH/DELETE /api/entities/parcels",
}
PARCEL = "urn:ngsi-ld:AgriParcel:tenant-a:p1"


@pytest.fixture
def allowed(monkeypatch, sdm):
    monkeypatch.setattr(sdm, "_check_tenant_limits", lambda tenant, entity_type: True)


@pytest.mark.parametrize("entity_type", ["AgriParcel", "saref4agri:AgriParcel", "x:AgriParcel"])
def test_parcel_routes_are_refused(client, broker, allowed, entity_type):
    for method, route, body in (
        ("post", f"/sdm/entities/{entity_type}/instances", {"name": "x"}),
        ("post", f"/sdm/entities/{entity_type}/batch", {"entities": [{"name": "x", "lat": 1, "lng": 1}]}),
        ("patch", f"/sdm/entities/{entity_type}/instances/urn:ngsi-ld:Device:1", {"n": {"type": "Property", "value": 1}}),
        ("delete", f"/sdm/entities/{entity_type}/instances/urn:ngsi-ld:Device:1", None),
    ):
        resp = getattr(client, method)(route, json=body)
        assert resp.status_code == 403, (method, route)
        assert resp.get_json() == REFUSED
    assert broker == []


@pytest.mark.parametrize("body", _cases.PARCEL_BODIES)
def test_parcel_bodies_are_refused(client, broker, allowed, body):
    for method, route in (
        ("post", "/sdm/entities/Building/instances"),
        ("patch", "/sdm/entities/Building/instances/urn:ngsi-ld:Building:1"),
    ):
        resp = getattr(client, method)(route, json={"name": "x", **body})
        assert resp.status_code == 403, (method, body)
    assert broker == []


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_parcel_ids_are_refused_whatever_the_route_type(client, broker, method):
    body = {"n": {"type": "Property", "value": 1}} if method == "patch" else None
    resp = getattr(client, method)(f"/sdm/entities/Device/instances/{PARCEL}", json=body)
    assert resp.status_code == 403
    assert broker == []


def test_a_parcel_id_is_not_created_under_another_type(client, broker, allowed):
    assert client.post("/sdm/entities/Building/instances", json={"id": PARCEL, "name": "x"}).status_code == 403
    assert broker == []


@pytest.mark.parametrize("body", [b for b in _cases.OTHER_BODIES if isinstance(b, dict) and "type" not in b or b == {"type": "Device"}])
def test_other_writes_still_reach_the_broker(client, broker, allowed, body):
    client.patch("/sdm/entities/Building/instances/urn:ngsi-ld:Building:1", json={"n": {"type": "Property", "value": 1}, **body})
    assert broker, body


def test_parcels_can_still_be_read(client, broker):
    client.get(f"/sdm/entities/AgriParcel/instances/{PARCEL}")
    assert broker and broker[0][0][0].endswith(f"/ngsi-ld/v1/entities/{PARCEL}")
