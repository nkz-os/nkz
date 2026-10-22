"""PATCH /entities/parcels/<id>: entity-manager is the only writer of parcels.

Writes go as one merge-patch, which creates or overwrites attributes (PATCH /attrs only
updates attributes the parcel already has, so a field it lacked was dropped). Besides the
flat fields, the body may carry NGSI-LD ``attributes`` to merge and a ``remove`` list,
which is how the generic entity editor saves a parcel.
"""

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

PARCEL = "urn:ngsi-ld:AgriParcel:0f3c2a1e-5b7d-4c9a-8e21-6d4f0a9b3c55"
URL = f"/entities/parcels/{PARCEL}"
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}

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
    calls = {"patch": MagicMock(return_value=_resp(204)), "delete": MagicMock(return_value=_resp(204))}
    with patch.object(entities.requests, "patch", calls["patch"]), \
            patch.object(entities.requests, "delete", calls["delete"]), \
            patch.object(entities, "inject_fiware_headers", lambda h, *a, **kw: dict(h)):
        yield calls


def _patch(body):
    return app.test_client().patch(
        URL, json=body, headers={"Authorization": "Bearer t"}, environ_base={"HTTP_COOKIE": "nkz_token=t"})


def test_flat_fields_are_merged_so_a_missing_one_is_created(orion):
    response = _patch({"notes": "North gate"})

    assert response.status_code == 200
    url = orion["patch"].call_args.args[0]
    assert url == f"http://orion:1026/ngsi-ld/v1/entities/{PARCEL}"
    assert orion["patch"].call_args.kwargs["json"] == {"notes": {"type": "Property", "value": "North gate"}}


def test_attributes_are_merged_as_given(orion):
    attributes = {
        "description": {"type": "Property", "value": "Rye field"},
        "refAgriFarm": {"type": "Relationship", "object": "urn:ngsi-ld:AgriFarm:f1"},
    }
    response = _patch({"attributes": attributes})

    assert response.status_code == 200
    assert orion["patch"].call_args.kwargs["json"] == attributes
    orion["delete"].assert_not_called()


def test_removed_attributes_are_deleted_one_by_one(orion):
    response = _patch({"remove": ["notes", "refAgriFarm"]})

    assert response.status_code == 200
    orion["patch"].assert_not_called()
    urls = [c.args[0] for c in orion["delete"].call_args_list]
    assert urls == [
        f"http://orion:1026/ngsi-ld/v1/entities/{PARCEL}/attrs/notes",
        f"http://orion:1026/ngsi-ld/v1/entities/{PARCEL}/attrs/refAgriFarm",
    ]


def test_an_attribute_already_gone_counts_as_removed(orion):
    orion["delete"].return_value = _resp(404)

    assert _patch({"remove": ["notes"]}).status_code == 200


def test_merge_comes_before_removal(orion):
    order = []
    orion["patch"].side_effect = lambda *a, **kw: order.append("merge") or _resp(204)
    orion["delete"].side_effect = lambda *a, **kw: order.append("delete") or _resp(204)

    _patch({"attributes": {"name": {"type": "Property", "value": "North"}}, "remove": ["notes"]})

    assert order == ["merge", "delete"]


def test_geometry_attribute_is_validated(orion):
    bad = {"location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [0, 0]}}}

    assert _patch({"attributes": bad}).status_code == 422
    orion["patch"].assert_not_called()


def test_valid_geometry_attribute_is_merged(orion):
    good = {"location": {"type": "GeoProperty", "value": SQUARE}}

    assert _patch({"attributes": good}).status_code == 200


def test_the_geometry_cannot_be_removed(orion):
    assert _patch({"remove": ["location"]}).status_code == 422
    orion["delete"].assert_not_called()


@pytest.mark.parametrize("body", [
    {"attributes": {"id": {"type": "Property", "value": "x"}}},
    {"attributes": {"type": {"type": "Property", "value": "x"}}},
    {"attributes": {"name": "plain value"}},
    {"attributes": ["name"]},
    {"remove": "notes"},
    {"remove": ["type"]},
    {"attributes": {"notes": {"type": "Property", "value": "x"}}, "remove": ["notes"]},
])
def test_malformed_requests_are_rejected(orion, body):
    assert _patch(body).status_code == 400
    orion["patch"].assert_not_called()
    orion["delete"].assert_not_called()


@pytest.mark.parametrize("attribute", [
    "urn:ngsi-ld:null",
    {"type": "Relationship", "object": "urn:ngsi-ld:null"},
    {"type": "Property", "value": "urn:ngsi-ld:null"},
])
def test_the_null_token_is_rejected(orion, attribute):
    """The broker mishandles it; removal goes through the remove list."""
    assert _patch({"attributes": {"refAgriFarm": attribute}}).status_code == 400
    orion["patch"].assert_not_called()


def test_unknown_parcel_is_404(orion):
    orion["patch"].return_value = _resp(404)

    assert _patch({"notes": "x"}).status_code == 404
