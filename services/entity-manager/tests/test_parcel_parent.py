"""Subparcels: a parcel created inside another one links to it with hasAgriParcel."""

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

PARENT = "urn:ngsi-ld:AgriParcel:0f3c2a1e-5b7d-4c9a-8e21-6d4f0a9b3c55"
GEOMETRY = {"type": "Polygon", "coordinates": [[[-1.6, 42.7], [-1.59, 42.7], [-1.59, 42.71], [-1.6, 42.7]]]}

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
    r.json.return_value = body
    return r


@pytest.fixture
def orion():
    calls = {
        "post": MagicMock(return_value=_resp(201)),
        "get": MagicMock(return_value=_resp(200, {"id": PARENT, "type": "AgriParcel"})),
    }
    with patch.object(entities.requests, "post", calls["post"]), \
            patch.object(entities.requests, "get", calls["get"]), \
            patch.object(entities, "inject_fiware_headers", lambda h, *a, **kw: dict(h)):
        yield calls


def _create(body):
    return app.test_client().post(
        "/entities/parcels", json=body, headers={"Authorization": "Bearer t"},
        environ_base={"HTTP_COOKIE": "nkz_token=t"})


def test_subparcel_links_to_its_parent(orion):
    assert _create({"geometry": GEOMETRY, "parentId": PARENT}).status_code == 201
    sent = orion["post"].call_args.kwargs["json"]
    assert sent["hasAgriParcel"] == {"type": "Relationship", "object": PARENT}
    assert orion["get"].call_args.args[0].endswith(f"/ngsi-ld/v1/entities/{PARENT}")


def test_parent_given_as_uuid_is_expanded(orion):
    assert _create({"geometry": GEOMETRY, "parentId": PARENT.split(":")[-1]}).status_code == 201
    assert orion["post"].call_args.kwargs["json"]["hasAgriParcel"]["object"] == PARENT


def test_parcel_without_parent_has_no_link(orion):
    assert _create({"geometry": GEOMETRY}).status_code == 201
    assert "hasAgriParcel" not in orion["post"].call_args.kwargs["json"]
    orion["get"].assert_not_called()


@pytest.mark.parametrize("parent", ["urn:ngsi-ld:AgriFarm:f-1", "not-a-parcel", ""])
def test_parent_must_name_a_parcel(orion, parent):
    assert _create({"geometry": GEOMETRY, "parentId": parent}).status_code == 422
    orion["post"].assert_not_called()


def test_missing_parent_is_rejected(orion):
    orion["get"].return_value = _resp(404)
    assert _create({"geometry": GEOMETRY, "parentId": PARENT}).status_code == 422
    orion["post"].assert_not_called()


def test_unreadable_parent_creates_nothing(orion):
    orion["get"].side_effect = entities.requests.Timeout("hung")
    assert _create({"geometry": GEOMETRY, "parentId": PARENT}).status_code == 502
    orion["post"].assert_not_called()


def test_subparcel_skips_cadastral_dedup(orion):
    # Sectors share the parent's cadastral reference; a dedup would overwrite the parent.
    assert _create({"geometry": GEOMETRY, "parentId": PARENT, "cadastralReference": "R-1"}).status_code == 201
    assert orion["get"].call_count == 1
    sent = orion["post"].call_args.kwargs["json"]
    assert sent["cadastralReference"]["value"] == "R-1"


def test_cadastral_dedup_ignores_subparcels(orion):
    child = {"id": "urn:ngsi-ld:AgriParcel:aaaaaaaa-0000-4000-8000-000000000001", "refAgriParcel": PARENT}
    parent = {"id": PARENT}
    orion["get"].return_value = _resp(200, [child, parent])
    with patch.object(entities, "update_parcel", return_value=("updated", 200)) as update:
        _create({"geometry": GEOMETRY, "cadastralReference": "R-1"})
    update.assert_called_once_with(PARENT.split(":")[-1])
