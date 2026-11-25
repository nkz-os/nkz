"""A parcel route asks the broker only for well-formed ids."""
import os
import sys
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import requests
from fastapi import HTTPException

from app.routers.parcels import _normalize_parcel_id

RANGE = "start=2026-11-01&end=2026-11-02"
ROUTES = ["", "/agro-status", "/forecast", "/alerts", f"/daily?{RANGE}&fill=open_meteo", f"/zones/daily?{RANGE}", "/history?start=2026-01-01&end=2026-01-02"]
OTHER_IDS = ["urn:a:b?attrs=x", "urn:a:b#x", 'urn:a:b"', "urn:a:b\\", "urn:a:b%00", "urn:a:b%3Fx", "x?y"]
DOTTED_IDS = ["urn:a:b%2F..%2Fc", "urn:a:b/../c", ".."]


@pytest.fixture
def broker(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth
    from app.main import app

    reply = MagicMock(status_code=404, text="", content=b"x")
    reply.json.return_value = {}
    get = MagicMock(return_value=reply)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setitem(app.dependency_overrides, auth.require_auth, lambda: "tenant-a")
    monkeypatch.setitem(app.dependency_overrides, auth.require_auth_optional, lambda: "tenant-a")
    return TestClient(app, raise_server_exceptions=False), get


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("parcel_id", OTHER_IDS)
def test_other_ids_are_refused(broker, route, parcel_id):
    client, get = broker
    resp = client.get(f"/api/weather/parcel/{quote(parcel_id, safe='')}{route}")
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid parcel id"}
    assert get.call_args_list == []


@pytest.mark.parametrize("route", ["", "/agro-status", "/forecast", "/alerts"])
@pytest.mark.parametrize("parcel_id", ["urn:ngsi-ld:AgriParcel:p1", "urn:ngsi-ld:AgriParcel:a b", "urn:ngsi-ld:AgriParcel:ñ"])
def test_well_formed_ids_are_asked(broker, route, parcel_id):
    client, get = broker
    client.get(f"/api/weather/parcel/{quote(parcel_id, safe='')}{route}")
    assert get.call_args_list[0].args[0].endswith(f"/ngsi-ld/v1/entities/{parcel_id}")


@pytest.mark.parametrize("parcel_id", OTHER_IDS + DOTTED_IDS)
def test_normalizing_refuses_other_ids(parcel_id):
    with pytest.raises(HTTPException) as caught:
        _normalize_parcel_id(parcel_id)
    assert caught.value.status_code == 400
