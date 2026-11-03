"""Per-plant series: samples whose hasAgriPlant is exactly one plant, latest import wins."""
import os
import sys
from datetime import datetime, timezone
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

import app  # noqa: E402

PLANT = "urn:ngsi-ld:AgriPlant:acme:demo-parcel-001:T-001"
URL = f"/api/timeseries/type/AgriPlantSample/plant/{PLANT}/data"


def _row(entity_id, observed, ingested, plant=PLANT, n=0.016):
    return {
        "entity_id": entity_id,
        "entity_type": "AgriPlantSample",
        "observed_at": observed,
        "ingested_at": ingested,
        "payload": {
            "measurements": {"nitrogen": n},
            "raw": {"hasAgriPlant": {"type": "Relationship", "object": plant}},
        },
    }


def _call(rows, query="", ids=("urn:ngsi-ld:AgriPlantSample:acme:s1", "urn:ngsi-ld:AgriPlantSample:acme:s2")):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchall.return_value = rows
    with patch("app.plant_sample_ids", return_value=list(ids)), patch("app.get_db_connection") as db:
        db.return_value.__enter__.return_value = conn
        with app.app.test_client() as c:
            r = c.get(URL + query, headers={"X-Tenant-ID": "tenant-a"})
    return r, cur, db


def _sql(cur):
    sql, params = cur.execute.call_args_list[-1].args
    return " ".join(sql.split()), params


def test_plant_route_filters_by_has_agri_plant():
    r, cur, _ = _call([])
    assert r.status_code == 200
    sql, params = _sql(cur)
    assert "payload->'raw'->'hasAgriPlant'->>'object' = %s" in sql
    assert "tenant-a" in params and "AgriPlantSample" in params and PLANT in params


def test_composite_sample_excluded_from_tree_series():
    july = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    rows = [_row("s1", july, july), _row("s2", july, july, plant=[PLANT, PLANT + "b"])]
    r, _, _ = _call(rows)
    assert [p["entity_id"] for p in r.get_json()["data"]] == ["s1"]


def test_reimported_sample_returns_latest_ingested():
    july = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    rows = [
        _row("s1", july, datetime(2026, 8, 1, tzinfo=timezone.utc), n=0.016),
        _row("s1", july, datetime(2026, 10, 1, tzinfo=timezone.utc), n=0.018),
    ]
    r, _, _ = _call(rows, "?attrs=nitrogen")
    data = r.get_json()["data"]
    assert len(data) == 1 and data[0]["nitrogen"] == 0.018


def test_plant_route_default_window_is_ten_years():
    r, cur, _ = _call([])
    _, params = _sql(cur)
    start, end = [p for p in params if isinstance(p, datetime)][:2]
    assert (end - start).days >= 3650


def test_plant_route_requires_tenant():
    with app.app.test_client() as c:
        r = c.get(URL)
    assert r.status_code in (400, 401)


def test_plant_route_filters_by_segment_column():
    """telemetry_events is compressed by (tenant_id, device_id): filter on device_id."""
    r, cur, _ = _call([])
    sql, params = _sql(cur)
    assert "device_id = ANY(%s)" in sql and "entity_id = ANY(%s)" in sql
    assert ["s1", "s2"] in params
    assert ["urn:ngsi-ld:AgriPlantSample:acme:s1", "urn:ngsi-ld:AgriPlantSample:acme:s2"] in params


def test_plant_without_samples_does_not_query_the_database():
    r, _, db = _call([], ids=())
    assert r.status_code == 200 and r.get_json()["count"] == 0
    db.assert_not_called()


def test_orion_unavailable_is_502_not_a_full_scan():
    with patch("app.plant_sample_ids", side_effect=RuntimeError("orion down")), patch("app.get_db_connection") as db:
        with app.app.test_client() as c:
            r = c.get(URL, headers={"X-Tenant-ID": "tenant-a"})
    assert r.status_code == 502
    db.assert_not_called()


def test_plant_sample_ids_pages_orion():
    import urn_resolution as ur

    page1 = [{"id": f"urn:ngsi-ld:AgriPlantSample:acme:{i}"} for i in range(1000)]
    page2 = [{"id": "urn:ngsi-ld:AgriPlantSample:acme:last"}]
    resp = [MagicMock(status_code=200, json=MagicMock(return_value=p)) for p in (page1, page2)]
    with patch.object(ur, "ORION_URL", "http://orion:1026"), patch.object(ur.requests, "get", side_effect=resp) as get:
        ids = ur.plant_sample_ids("tenant-a", "AgriPlantSample", PLANT)
    assert len(ids) == 1001 and get.call_count == 2
    params = get.call_args_list[0].kwargs["params"]
    assert params["type"] == "AgriPlantSample" and params["q"] == f'hasAgriPlant=="{PLANT}"'
