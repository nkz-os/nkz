"""Plants endpoints: one tree per tap or a whole file, ids built here, upsert by code."""

import math
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_common_mock = MagicMock()
_common_mock.require_auth = lambda f=None, **kw: f if f else (lambda g: g)
_common_mock.inject_fiware_headers = lambda h, *a, **kw: dict(h)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers",
             "common.ngsi_payload_guard"):
    sys.modules.setdefault(_mod, _common_mock)

from flask import Flask, g  # noqa: E402

from blueprints import entities  # noqa: E402

LAT0, LON0 = 42.8, -1.6
PARCEL_URN = "urn:ngsi-ld:AgriParcel:demo-parcel-001"
CROP_URN = "urn:ngsi-ld:AgriCrop:tenant-a:olive"


def _off(east_m, north_m):
    return (LAT0 + north_m / 110_540.0, LON0 + east_m / (111_320.0 * math.cos(math.radians(LAT0))))


RING = [[lon, lat] for lat, lon in (_off(0, 0), _off(100, 0), _off(100, 100), _off(0, 100), _off(0, 0))]
PARCEL = {"id": PARCEL_URN, "type": "AgriParcel",
          "location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": [RING]}},
          "hasAgriCrop": {"type": "Relationship", "object": CROP_URN}}
CROP = {"id": CROP_URN, "type": "AgriCrop",
        "agroVocConcept": {"type": "Property", "value": "http://aims.fao.org/aos/agrovoc/c_5386"}}

app = Flask(__name__)


@app.before_request
def _tenant():
    g.tenant = "tenant-a"
    g.tenant_id = "tenant-a"


app.register_blueprint(entities.entities_bp)


def _resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    r.content = b"" if body is None else b"x"
    r.json.return_value = body
    return r


class FakeOrion:
    def __init__(self):
        self.existing: list[dict] = []
        self.parcel = PARCEL
        self.upserts: list[list[dict]] = []
        self.upsert_status = 204
        self.fail_all = False

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith(f"/entities/{PARCEL_URN}"):
            return _resp(200, self.parcel) if self.parcel else _resp(404, {})
        if url.endswith(f"/entities/{CROP_URN}"):
            return _resp(200, CROP)
        if url.endswith("/ngsi-ld/v1/entities") and (params or {}).get("type") == "AgriPlant":
            assert params["q"] == f'hasAgriParcel=="{PARCEL_URN}"'
            off = int(params.get("offset", 0))
            return _resp(200, self.existing[off:off + int(params["limit"])])
        return _resp(404, {})

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        assert url.endswith("/ngsi-ld/v1/entityOperations/upsert")
        assert (params or {}).get("options") == "update"
        self.upserts.append(json)
        if self.fail_all:
            return _resp(207, {"success": [], "errors": [
                {"entityId": e["id"], "error": {"title": "x"}} for e in json]})
        return _resp(self.upsert_status)


@pytest.fixture
def orion():
    fake = FakeOrion()
    with patch.object(entities.requests, "get", fake.get), \
            patch.object(entities.requests, "post", fake.post), \
            patch.object(entities, "inject_fiware_headers", lambda h, *a, **kw: dict(h)):
        yield fake


def _post(path, body):
    return app.test_client().post(path, json=body, headers={"Authorization": "Bearer t"},
                                  environ_base={"HTTP_COOKIE": "nkz_token=t"})


def _item(east, north, **kw):
    lat, lon = _off(east, north)
    return {"lat": lat, "lon": lon, **kw}


def test_single_plant_gets_next_free_code(orion):
    orion.existing = [{"id": "x", "plantCode": {"type": "Property", "value": "41"}}]
    r = _post("/entities/plants", {"parcel_id": PARCEL_URN, "plants": [_item(10, 10)]})
    assert r.status_code == 200, r.get_data(as_text=True)
    (w,) = r.get_json()["written"]
    # tenant segment = the authenticated tenant (other test modules stub auth differently)
    assert set(w) == {"id", "plantCode"} and w["plantCode"] == "42"
    assert re.fullmatch(r"urn:ngsi-ld:AgriPlant:[a-z0-9_-]+:demo-parcel-001:42", w["id"])
    (batch,) = orion.upserts
    assert batch[0]["agroVocConcept"]["value"].endswith("c_5386")
    assert batch[0]["@context"]


def test_commit_upserts_accepted_only(orion):
    r = _post("/entities/plants", {"parcel_id": PARCEL_URN, "plants": [
        _item(10, 10, plantCode="A"), _item(-40, 10, plantCode="far")]})
    body = r.get_json()
    assert [p["plantCode"] for p in body["written"]] == ["A"]
    assert body["rejected"][0]["reason"] == "outside_parcel"
    assert [e["plantCode"]["value"] for e in orion.upserts[0]] == ["A"]


def test_preview_writes_nothing(orion):
    r = _post("/entities/plants/preview", {"parcel_id": PARCEL_URN, "plants": [_item(10, 10, plantCode="A")]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["accepted"][0]["plantCode"] == "A" and "entity" not in body["accepted"][0]
    assert orion.upserts == []


def test_reimport_same_ids(orion):
    body = {"parcel_id": PARCEL_URN, "plants": [_item(10, 10, plantCode="A")]}
    first = _post("/entities/plants", body).get_json()["written"]
    orion.existing = [{"id": first[0]["id"], "plantCode": {"type": "Property", "value": "A"}}]
    second = _post("/entities/plants", body).get_json()
    assert second["written"] == first
    assert any(w["code"] == "exists" for w in second["warnings"])


def test_nothing_accepted_is_400(orion):
    r = _post("/entities/plants", {"parcel_id": PARCEL_URN, "plants": [_item(-40, 10)]})
    assert r.status_code == 400 and orion.upserts == []


def test_unknown_parcel_404(orion):
    orion.parcel = None
    r = _post("/entities/plants", {"parcel_id": PARCEL_URN, "plants": [_item(10, 10)]})
    assert r.status_code == 404


def test_file_and_plants_together_400(orion):
    r = _post("/entities/plants/preview", {"parcel_id": PARCEL_URN, "plants": [],
                                           "file": {"format": "csv", "content": "lat,lon\n"}})
    assert r.status_code == 400


def test_short_parcel_id_is_expanded(orion):
    r = _post("/entities/plants/preview", {"parcel_id": "demo-parcel-001", "plants": [_item(10, 10)]})
    assert r.status_code == 200


def test_csv_file_import(orion):
    lat, lon = _off(10, 10)
    r = _post("/entities/plants", {"parcel_id": PARCEL_URN, "file": {
        "format": "csv", "content": f"plantCode,lat,lon\nT-1,{lat},{lon}\nT-2,4700000,600000\n"}})
    body = r.get_json()
    assert [p["plantCode"] for p in body["written"]] == ["T-1"]
    assert body["rejected"][0]["reason"] == "invalid_point"


def test_broker_errors_are_reported(orion):
    orion.fail_all = True
    body = _post("/entities/plants", {"parcel_id": PARCEL_URN, "plants": [_item(10, 10, plantCode="A")]}).get_json()
    assert body["written"] == [] and body["rejected"][0]["reason"] == "broker_error"
