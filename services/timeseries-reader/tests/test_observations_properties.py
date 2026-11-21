"""GET /api/observations/properties, catalogue and geometry read-through."""
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    sys.modules["auth_middleware"] = _auth_stub

import psycopg2.errors  # noqa: E402
import pytest  # noqa: E402

import app  # noqa: E402
from observations import catalogue, geometry  # noqa: E402
from observations.request import RequestError  # noqa: E402

HEADERS = {"X-Tenant-ID": "testtenant"}


class FakeCursor:
    def __init__(self, results=None, fail_on=None):
        self.executed = []
        self._results = list(results or [])
        self._fail_on = fail_on

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self._fail_on and self._fail_on in sql:
            raise psycopg2.errors.QueryCanceled("timeout")

    def fetchall(self):
        return self._results.pop(0) if self._results else []

    def fetchone(self):
        r = self.fetchall()
        return r[0] if r else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.closed = False

    def cursor(self, *a, **k):
        return self.cur

    def close(self):
        self.closed = True

    def commit(self):
        pass

    def rollback(self):
        pass


def prop(name, tenant=None, unit="°C", deprecated=None, **kw):
    row = {
        "id": uuid.uuid4(), "tenant_id": tenant, "name": name, "domain": "environment",
        "unit": unit, "default_medium": "ambientAir", "allowed_media": ["ambientAir"],
        "temporal_semantics": "instant", "valid_min": -40.0, "valid_max": 60.0,
        "max_rate_per_hour": None, "fusion": "median", "nearby_radius_m": 2000.0,
        "max_age_s": 10800, "min_coverage": 0.83, "sub_context_tolerance": {},
        "has_model": True, "deprecated_at": deprecated,
        "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
    }
    row.update(kw)
    return row


def test_properties_tenant_shadows_global():
    g = prop("airTemperature", None, valid_max=60.0)
    t = prop("airTemperature", "testtenant", valid_max=55.0)
    other = prop("relativeHumidity", None, unit="%")
    cur = FakeCursor(results=[[g, other, t]])
    items = catalogue.visible_properties(cur, "testtenant")
    by = {i["name"]: i for i in items}
    assert len(items) == 2 and by["airTemperature"]["valid_max"] == 55.0
    assert cur.executed[-1][1] == ("testtenant",)


def test_deprecated_tenant_row_hides_global_and_deprecated_dropped():
    g = prop("a", None)
    t = prop("a", "x", deprecated=datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert catalogue.visible_properties(FakeCursor(results=[[g, t]]), "x") == []


def test_get_property_unknown_or_deprecated_is_422():
    for rows in ([], [prop("a", None, deprecated=datetime(2026, 1, 1, tzinfo=timezone.utc))]):
        with pytest.raises(RequestError) as e:
            catalogue.get_property(FakeCursor(results=[rows]), "t", "a")
        assert e.value.status == 422
    assert catalogue.get_property(FakeCursor(results=[[prop("a", None)]]), "t", "a")["name"] == "a"


def _get(cur, path="/api/observations/properties"):
    conn = FakeConn(cur)
    with patch("app.get_db_connection", return_value=conn):
        with app.app.test_client() as c:
            return c.get(path, headers=HEADERS), conn


def test_properties_route_ok_camelcase_unitcode():
    cur = FakeCursor(results=[[prop("airTemperature", None), prop("x", None, unit="weird")]])
    r, conn = _get(cur)
    assert r.status_code == 200
    items = {i["name"]: i for i in r.get_json()["items"]}
    a = items["airTemperature"]
    assert a["unitCode"] == "CEL" and a["unit"] == "°C"
    assert a["defaultMedium"] == "ambientAir" and a["createdAt"] == "2026-11-01T00:00:00Z"
    assert a["deprecatedAt"] is None and "allowedMedia" in a and "subContextTolerance" in a
    assert items["x"]["unitCode"] is None  # unknown unit does not 500 the list
    assert conn.closed
    assert "set_config" in cur.executed[0][0]


def test_properties_route_requires_tenant():
    with app.app.test_client() as c:
        r = c.get("/api/observations/properties")
    assert r.status_code == 400


def test_properties_route_timeout_is_503():
    r, conn = _get(FakeCursor(fail_on="observation_properties"))
    assert r.status_code == 503 and r.get_json() == {"error": "observation query timed out"}
    assert conn.closed


# ---- geometry read-through -------------------------------------------------
POINT = {"type": "Point", "coordinates": [-1.6, 42.8]}
URN = "urn:ngsi-ld:AgriParcel:testtenant:p1"


def test_geometry_fresh_cache_hit_no_fetch():
    cur = FakeCursor(results=[[{"geojson": POINT, "fresh": True}]])
    fetch = MagicMock()
    assert geometry.geometry_of(cur, "testtenant", URN, fetch=fetch) == POINT
    fetch.assert_not_called()


def test_geometry_miss_fetches_and_upserts():
    cur = FakeCursor(results=[[]])
    fetch = MagicMock(return_value=("ok", {"id": URN, "type": "AgriParcel",
                                           "location": {"type": "GeoProperty", "value": POINT}}))
    assert geometry.geometry_of(cur, "testtenant", URN, fetch=fetch) == POINT
    assert "INSERT INTO observation_geometry_cache" in cur.executed[-1][0]
    assert cur.executed[-1][1][:3] == ("testtenant", URN, "AgriParcel")


def test_geometry_stale_orion_404_deletes_row():
    cur = FakeCursor(results=[[{"geojson": POINT, "fresh": False}]])
    fetch = MagicMock(return_value=("not_found", None))
    assert geometry.geometry_of(cur, "testtenant", URN, fetch=fetch) is None
    assert "DELETE FROM observation_geometry_cache" in cur.executed[-1][0]


def test_geometry_stale_orion_error_keeps_stale():
    cur = FakeCursor(results=[[{"geojson": POINT, "fresh": False}]])
    fetch = MagicMock(return_value=("error", None))
    assert geometry.geometry_of(cur, "testtenant", URN, fetch=fetch) == POINT
    assert all("DELETE" not in s for s, _ in cur.executed)


def test_geometry_miss_orion_error_none():
    assert geometry.geometry_of(FakeCursor(results=[[]]), "t", URN,
                                fetch=lambda t, u: ("error", None)) is None


def test_orion_status_distinguishes_404_from_error():
    with patch("observations.geometry.urn_resolution.requests.get") as g, \
         patch("observations.geometry.urn_resolution.ORION_URL", "http://orion:1026"):
        g.return_value = MagicMock(status_code=404)
        assert geometry.fetch_orion_entity_status("t", URN) == ("not_found", None)
        g.return_value = MagicMock(status_code=500)
        assert geometry.fetch_orion_entity_status("t", URN) == ("error", None)
        g.side_effect = OSError("down")
        assert geometry.fetch_orion_entity_status("t", URN) == ("error", None)
        g.side_effect = None
        g.return_value = MagicMock(status_code=200, json=lambda: {"id": URN})
        assert geometry.fetch_orion_entity_status("t", URN) == ("ok", {"id": URN})
