"""Weather series are read from telemetry_events WeatherObserved rows.

The legacy weather table has no writer; every weather read must target
telemetry_events (payload.measurements flat keys) filtered by tenant and
WeatherObserved entity type.
"""
import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import pytest  # noqa: E402

import app  # noqa: E402
import urn_resolution as u  # noqa: E402

HEADERS = {"X-Tenant-ID": "testtenant"}
START = datetime(2026, 9, 1)
END = datetime(2026, 9, 19)


class FakeCursor:
    def __init__(self, results=None):
        self.executed = []
        self._results = list(results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

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

    def cursor(self, *a, **k):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _all_sql(cur):
    return "\n".join(s for s, _ in cur.executed)


def test_value_sql_maps_legacy_columns_to_measurement_keys():
    assert "'airTemperature'" in app._weather_value_sql("temp_avg")
    assert "'tempMin'" in app._weather_value_sql("temp_min")
    assert "'humidity'" in app._weather_value_sql("humidity_avg")
    with pytest.raises(ValueError):
        app._weather_value_sql("temp_avg; DROP TABLE x")


def test_entity_patterns_cover_urn_short_and_escape_wildcards():
    pats = app._weather_entity_patterns(FakeCursor(), "t", "parcel-ab_c%", START, END)
    assert "parcel-ab\\_c\\%" in pats
    # Daily totals are not mixed into the live series unless the key names them.
    assert not any(p.endswith("-daily") for p in pats)
    daily = app._weather_entity_patterns(FakeCursor(), "t", "parcel-ab-daily", START, END)
    assert "%:parcel-ab-daily" in daily


def test_municipality_key_resolves_entities_by_nearest_catalog_point():
    cur = FakeCursor(results=[[{"entity_id": "urn:ngsi-ld:WeatherObserved:t:parcel-1"}]])
    pats = app._weather_entity_patterns(cur, "t", "31013", START, END)
    sql = _all_sql(cur)
    assert "catalog_municipalities" in sql and "<->" in sql
    assert "weather_observations" not in sql
    assert pats == ["urn:ngsi-ld:WeatherObserved:t:parcel-1"]


def test_columnar_reads_telemetry_events():
    cur = FakeCursor(results=[[{"timestamp": datetime(2026, 9, 2), "temp_avg": 18.5}]])
    conn = FakeConn(cur)
    out = app._weather_query_columnar(conn, "t", "parcel-1", START, END, ["temp_avg"], 10)
    sql = _all_sql(cur)
    assert "FROM telemetry_events e" in sql
    assert "weather_observations" not in sql
    assert "e.entity_type = ANY" in sql
    assert out["attributes"]["temp_avg"] == [18.5]
    params = cur.executed[-1][1]
    assert params[0] == "t" and app.WEATHER_OBSERVED_ENTITY_TYPES == params[1]


def test_unified_sql_weather_series_uses_telemetry_events():
    cur = FakeCursor()
    conn = FakeConn(cur)
    spec = [{"kind": "weather", "key": "parcel-1", "attr": "temp_avg"}]
    with patch("app.pa"):
        try:
            app._execute_v2_align_unified_sql(conn, "t", START, END, 100, spec)
        except Exception:
            pass
    sql = _all_sql(cur)
    assert "FROM telemetry_events e" in sql
    assert "weather_observations" not in sql


def test_entity_listing_reads_telemetry_events():
    cur = FakeCursor(results=[[{"id": "urn:ngsi-ld:WeatherObserved:t:parcel-1"}], [{"id": "31013"}]])
    with patch("app.get_db_connection", return_value=FakeConn(cur)):
        with app.app.test_client() as c:
            r = c.get("/api/timeseries/entities", headers=HEADERS)
    assert r.status_code == 200
    ids = [e["id"] for e in r.get_json()["entities"]]
    assert ids == ["urn:ngsi-ld:WeatherObserved:t:parcel-1", "31013"]
    assert "weather_observations" not in _all_sql(cur)


def test_stats_reads_telemetry_events():
    cur = FakeCursor()
    with patch("app.get_db_connection", return_value=FakeConn(cur)):
        with app.app.test_client() as c:
            r = c.get(
                "/api/timeseries/entities/parcel-1/stats?start_time=2026-09-01T00:00:00Z&attribute=temp_avg",
                headers=HEADERS,
            )
    assert r.status_code == 200
    assert "FROM telemetry_events e" in _all_sql(cur)
    assert "weather_observations" not in _all_sql(cur)


def _gdd(cur, qs, nearest="UNSET"):
    patches = [patch("app.get_db_connection", return_value=FakeConn(cur))]
    if nearest != "UNSET":
        patches.append(patch("app.nearest_weather_entity", return_value=nearest))
    for p in patches:
        p.start()
    try:
        with app.app.test_client() as c:
            return c.get("/api/weather/gdd?" + qs, headers=HEADERS)
    finally:
        for p in patches:
            p.stop()


def test_gdd_reports_gaps_and_does_not_fill():
    start = date.today() - timedelta(days=3)
    rows = [
        {"obs_date": start, "tmin": 10.0, "tmax": 20.0},
        {"obs_date": start + timedelta(days=2), "tmin": 12.0, "tmax": 24.0},
    ]
    cur = FakeCursor(results=[rows])
    r = _gdd(cur, f"season_start={start.isoformat()}&base_temp=10")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["days_count"] == 2
    assert body["gdd_total"] == 5.0 + 8.0
    missing = body["missing_days"]
    assert (start + timedelta(days=1)).isoformat() in missing
    assert start.isoformat() not in missing
    assert body["missing_days_count"] == len(missing)
    sql = _all_sql(cur)
    assert "FROM telemetry_events e" in sql and "weather_observations" not in sql
    assert "COALESCE(d_min, a_min)" in sql


def test_gdd_uses_nearest_entity_and_its_daily_twin():
    start = date.today() - timedelta(days=2)
    cur = FakeCursor(results=[[]])
    base = "urn:ngsi-ld:WeatherObserved:t:parcel-9"
    r = _gdd(cur, f"season_start={start.isoformat()}&base_temp=10&lat=42.0&lon=-1.6",
             nearest=base + "-daily")
    assert r.status_code == 200
    params = cur.executed[-1][1]
    assert [base, base + "-daily"] in params
    assert r.get_json()["days_count"] == 0


def test_gdd_no_nearby_entity_is_all_gap():
    start = date.today() - timedelta(days=2)
    cur = FakeCursor()
    r = _gdd(cur, f"season_start={start.isoformat()}&base_temp=10&lat=42.0&lon=-1.6",
             nearest=None)
    body = r.get_json()
    assert body["days_count"] == 0 and body["gdd_total"] == 0
    assert body["missing_days_count"] == 3
    assert cur.executed == [("SELECT set_config('app.current_tenant', %s, true)", ("testtenant",))]


# --- urn_resolution -------------------------------------------------------


def test_weather_observed_urn_is_its_own_key():
    ent = {"id": "x", "type": "WeatherObserved"}
    urn = "urn:ngsi-ld:WeatherObserved:t:parcel-1"
    with patch.object(u, "ORION_URL", "http://orion:1026"):
        assert u._resolve_urn_to_weather_key("t", urn, entity=ent) == ("parcel-1", "entity")


def test_parcel_urn_maps_to_its_own_weather_entity_key():
    ent = {"id": "x", "type": "AgriParcel"}
    urn = "urn:ngsi-ld:AgriParcel:t:11111111-1111-1111-1111-111111111111"
    with patch.object(u, "ORION_URL", "http://orion:1026"):
        key, src = u._resolve_urn_to_weather_key("t", urn, entity=ent)
    assert key == "11111111-1111-1111-1111-111111111111" and src == "parcel"


def test_nearest_weather_entity_queries_telemetry_events_only():
    cur = FakeCursor(results=[[{"entity_id": "urn:ngsi-ld:WeatherObserved:t:parcel-2"}]])
    assert u.nearest_weather_entity(cur, "t", 42.0, -1.6) == "urn:ngsi-ld:WeatherObserved:t:parcel-2"
    sql = _all_sql(cur)
    assert "telemetry_events" in sql and "weather_observations" not in sql
    assert "ST_MakePoint(%s, %s)" in sql
    assert cur.executed[0][1][-2:] == (-1.6, 42.0)
