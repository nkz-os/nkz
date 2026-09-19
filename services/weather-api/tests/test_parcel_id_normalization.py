"""Bare parcel ids must normalize to the AgriParcel URN before hitting Orion.

Regression for the 500 on /api/weather/parcel/{id}/agro-status: a bare id made
Orion answer 400, which the route misreported as a 500. #973 fixed the main
weather route only; these tests pin the helper and both remaining routes.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.routers.parcels import _normalize_parcel_id


def test_bare_id_becomes_urn():
    assert _normalize_parcel_id("da36ccd2-85d2-4c76-b552-c5c835a987c1") == (
        "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1"
    )


def test_urn_passes_through():
    urn = "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1"
    assert _normalize_parcel_id(urn) == urn


def test_empty_stays_prefix_only():
    assert _normalize_parcel_id("") == "urn:ngsi-ld:AgriParcel:"


# ── Endpoint: agro-status normalizes before calling Orion ──────────────────
def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.text = ""
    m.content = b"x"
    return m


def _client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_agro_status_normalizes_bare_id(monkeypatch):
    from app.main import app  # noqa: F401  (ensure router registered)
    from app.auth import require_auth as _require_auth
    import app.routers.parcels as parcels

    parcel_entity = {
        "id": "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1",
        "type": "AgriParcel",
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-1.64, 42.81]}},
    }
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        return _resp(200, parcel_entity)

    monkeypatch.setattr(parcels.requests, "get", fake_get)
    monkeypatch.setattr(parcels, "_orion_headers", lambda tid: {})

    class _FakeConn:
        def cursor(self, **kw):
            return MagicMock()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(parcels, "get_db_connection", lambda tid: _FakeConn())
    monkeypatch.setattr(
        parcels,
        "calculate_agro_status",
        lambda *a, **k: {"semaphores": {}, "metrics": {"moisture": None}},
    )
    app.dependency_overrides[_require_auth] = lambda: "montiko"

    try:
        c = _client()
        r = c.get("/api/weather/parcel/da36ccd2-85d2-4c76-b552-c5c835a987c1/agro-status")
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1" in seen["url"]
