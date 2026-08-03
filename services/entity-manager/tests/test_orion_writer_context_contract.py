"""ETSI NGSI-LD contract for orion_writer: @context in body XOR Link header.

Create (body carries @context) must go as application/ld+json WITHOUT Link;
PATCH /attrs fragments (no @context) must go as application/json WITH Link.
Uses the REAL inject_fiware_headers so the header logic under test is genuine.
"""

import os
import sys
import unittest.mock
from unittest.mock import MagicMock, patch

os.environ["CONTEXT_URL"] = "http://ngsi-context.test/ngsi-ld-context.json"
os.environ.setdefault("ORION_URL", "http://orion-test:1026")

_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _dir)
sys.path.insert(0, os.path.join(_dir, "..", "common"))

import ngsi_headers  # real implementation, imported from services/common

_common_stub = MagicMock()
_common_stub.auth_middleware.inject_fiware_headers = ngsi_headers.inject_fiware_headers
unittest.mock.patch.dict(
    "sys.modules",
    {
        "common": _common_stub,
        "common.auth_middleware": _common_stub.auth_middleware,
    },
).start()

# test_module_publish.py permanently stubs sys.modules["orion_writer"]; purge
# the stub so we import the real module under test.
if isinstance(sys.modules.get("orion_writer"), MagicMock):
    del sys.modules["orion_writer"]

import orion_writer  # noqa: E402


def _resp(status=201):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    return r


def _real_inject():
    # Other test modules stub sys.modules["common"]; orion_writer may be cached
    # with a MagicMock inject_fiware_headers. Bind the real one for these tests.
    return patch.object(
        orion_writer, "inject_fiware_headers", ngsi_headers.inject_fiware_headers
    )


def test_create_sends_ld_json_with_context_body_and_no_link():
    with _real_inject(), patch.object(
        orion_writer.requests, "post", return_value=_resp(201)
    ) as post:
        entity_id = orion_writer.create_weather_observed_entity(
            "urn:ngsi-ld:AgriParcel:montiko:p1",
            "montiko",
            (-1.6432, 42.8169),
            {"temperature": 15.5},
        )
    assert entity_id is not None
    kwargs = post.call_args.kwargs
    body = kwargs["json"]
    headers = kwargs["headers"]
    assert "@context" in body
    assert headers["Content-Type"] == "application/ld+json"
    assert "Link" not in headers
    assert headers["NGSILD-Tenant"] == "montiko"
    assert headers["Fiware-Service"] == "montiko"


def test_patch_attrs_sends_json_plus_link_without_context_in_body():
    with _real_inject(), patch.object(
        orion_writer.requests, "patch", return_value=_resp(204)
    ) as pmock:
        entity_id = orion_writer.update_weather_observed_entity(
            "urn:ngsi-ld:WeatherObserved:montiko:parcel-p1",
            "montiko",
            {"temperature": 16.1},
        )
    assert entity_id is not None
    kwargs = pmock.call_args.kwargs
    body = kwargs["json"]
    headers = kwargs["headers"]
    assert "@context" not in body
    assert headers["Content-Type"] == "application/json"
    assert "Link" in headers
    assert "ngsi-context.test" in headers["Link"]
