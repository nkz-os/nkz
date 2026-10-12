"""AgriParcel area: geodesic computation on create and on geometry edits."""

import math
import os
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
from helpers.geometry import geometry_area_ha  # noqa: E402

PARCEL = "urn:ngsi-ld:AgriParcel:0f3c2a1e-5b7d-4c9a-8e21-6d4f0a9b3c55"
M_PER_DEG = 111194.93  # metres per degree of latitude on the sphere used by the helper


def square(lon, lat, side_m):
    """Closed ring of a square whose sides are `side_m` metres at latitude `lat`."""
    dlat = side_m / M_PER_DEG
    dlon = dlat / math.cos(math.radians(lat + dlat / 2))
    return [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]


def poly(*rings):
    return {"type": "Polygon", "coordinates": list(rings)}


@pytest.mark.parametrize("lat", [0.0, 42.7, 60.0])
def test_one_km_square_is_100_ha_at_any_latitude(lat):
    assert geometry_area_ha(poly(square(-1.6, lat, 1000))) == pytest.approx(100.0, rel=0.01)


def test_near_42_7_matches_the_ellipsoid_within_half_a_percent():
    # 0.01 x 0.01 degree cell at 42.7 N; WGS84 geodesic area 910135.41 m2 (pyproj Geod).
    ring = [[-1.0, 42.7], [-0.99, 42.7], [-0.99, 42.71], [-1.0, 42.71], [-1.0, 42.7]]
    assert geometry_area_ha(poly(ring)) == pytest.approx(91.013541, rel=0.005)


def test_orientation_does_not_matter():
    ring = square(-1.6, 42.7, 500)
    assert geometry_area_ha(poly(ring)) == geometry_area_ha(poly(ring[::-1]))


def test_multipolygon_sums_polygons_and_subtracts_holes():
    outer = square(-1.6, 42.7, 1000)
    hole = square(-1.598, 42.701, 500)
    other = square(-1.5, 42.7, 1000)
    single = geometry_area_ha(poly(outer))
    with_hole = geometry_area_ha(poly(outer, hole))
    assert with_hole == pytest.approx(single - geometry_area_ha(poly(hole)), abs=1e-3)
    assert with_hole == pytest.approx(75.0, rel=0.01)
    multi = {"type": "MultiPolygon", "coordinates": [[outer, hole], [other]]}
    assert geometry_area_ha(multi) == pytest.approx(with_hole + geometry_area_ha(poly(other)), abs=1e-3)


def test_other_or_empty_geometry_is_zero():
    assert geometry_area_ha({"type": "Point", "coordinates": [0, 0]}) == 0.0
    assert geometry_area_ha(poly()) == 0.0
    assert geometry_area_ha(None) == 0.0


app = Flask(__name__)


@app.before_request
def _tenant():
    g.tenant = "tenant-a"
    g.tenant_id = "tenant-a"


app.register_blueprint(entities.entities_bp)


def _resp(status):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    return r


@pytest.fixture
def orion():
    calls = {
        "post": MagicMock(return_value=_resp(201)),
        "patch": MagicMock(return_value=_resp(204)),
    }
    with patch.object(entities.requests, "post", calls["post"]), \
            patch.object(entities.requests, "patch", calls["patch"]), \
            patch.object(entities, "inject_fiware_headers", lambda h, *a, **kw: dict(h)):
        yield calls


def _send(method, url, body):
    return getattr(app.test_client(), method)(
        url, json=body, headers={"Authorization": "Bearer t"}, environ_base={"HTTP_COOKIE": "nkz_token=t"})


def test_create_computes_area_from_geometry(orion):
    response = _send("post", "/entities/parcels", {"geometry": poly(square(-1.6, 42.7, 1000))})
    assert response.status_code == 201
    area = orion["post"].call_args.kwargs["json"]["area"]["value"]
    assert area == pytest.approx(100.0, rel=0.01)


def test_create_keeps_explicit_area(orion):
    response = _send("post", "/entities/parcels", {"geometry": poly(square(-1.6, 42.7, 1000)), "area": 12.5})
    assert response.status_code == 201
    assert orion["post"].call_args.kwargs["json"]["area"]["value"] == 12.5


URL = f"/entities/parcels/{PARCEL}"


def test_geometry_edit_recomputes_area(orion):
    geometry = poly(square(-1.6, 42.7, 1000))
    assert _send("patch", URL, {"geometry": geometry}).status_code == 200
    sent = orion["patch"].call_args.kwargs["json"]
    assert sent["area"]["value"] == pytest.approx(100.0, rel=0.01)


def test_geometry_edit_through_attributes_recomputes_area(orion):
    location = {"type": "GeoProperty", "value": poly(square(-1.6, 42.7, 1000))}
    assert _send("patch", URL, {"attributes": {"location": location}}).status_code == 200
    assert orion["patch"].call_args.kwargs["json"]["area"]["value"] == pytest.approx(100.0, rel=0.01)


def test_geometry_edit_keeps_explicit_area(orion):
    geometry = poly(square(-1.6, 42.7, 1000))
    assert _send("patch", URL, {"geometry": geometry, "area": 7.25}).status_code == 200
    assert orion["patch"].call_args.kwargs["json"]["area"]["value"] == 7.25


def test_edit_without_geometry_leaves_area_alone(orion):
    assert _send("patch", URL, {"notes": "x"}).status_code == 200
    assert "area" not in orion["patch"].call_args.kwargs["json"]
