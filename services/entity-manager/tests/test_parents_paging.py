"""GET /api/entities/parents pages through Orion instead of stopping at its default 20."""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

_svc_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, g  # noqa: E402

from blueprints import entities as ent  # noqa: E402


def _parcel(i):
    return {
        "id": f"urn:ngsi-ld:AgriParcel:{i}",
        "type": "AgriParcel",
        "name": {"value": f"P{i}"},
        "location": {"value": {"type": "Polygon", "coordinates": []}},
    }


def _resp(items):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = items
    return r


def _call():
    app = Flask(__name__)
    handler = getattr(ent.get_parent_entities, "__wrapped__", ent.get_parent_entities)
    with app.test_request_context("/api/entities/parents?type=AgriParcel"):
        g.tenant = "acme"
        g.current_user = {"tenant_id": "acme"}
        resp, _status = handler()
    return resp.get_json()


def test_parents_pages_until_short_page():
    pages = [_resp([_parcel(i) for i in range(1000)]), _resp([_parcel(i) for i in range(1000, 1003)])]
    with patch.object(ent.requests, "get", side_effect=pages) as get:
        body = _call()
    assert len(body["entities"]) == 1003
    assert get.call_count == 2
    offsets = [c.kwargs["params"]["offset"] for c in get.call_args_list]
    assert offsets == [0, 1000]
    assert all(c.kwargs["params"]["limit"] == 1000 for c in get.call_args_list)


def test_parents_page_cap_stops_runaway_listing():
    full = [_parcel(i) for i in range(1000)]
    with patch.object(ent.requests, "get", side_effect=lambda *a, **k: _resp(full)) as get:
        _call()
    assert get.call_count == ent._PARENT_MAX_PAGES
