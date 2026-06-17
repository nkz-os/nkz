"""Parcel→municipality resolves from the Orion entity, not the cadastral_parcels mirror.

Plan 2 / Task 2 (timeseries-reader): the resolver reads the parcel's `municipality`
straight from the Orion AgriParcel entity and looks up catalog_municipalities.ine_code
by name. It must never touch the retired `cadastral_parcels` read-model.
"""
import os
import sys
from unittest.mock import patch, MagicMock

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import urn_resolution as u  # noqa: E402


def _fake_pg(ine_code="31013"):
    """Return a psycopg2.connect mock whose cursor records executes and returns ine_code."""
    captured = {"sql": [], "params": []}
    fake_cur = MagicMock()

    def _exec(sql, params=None):
        captured["sql"].append(sql)
        captured["params"].append(params)
    fake_cur.execute.side_effect = _exec
    fake_cur.fetchone.return_value = {"ine_code": ine_code}
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur
    return fake_conn, captured


def test_municipality_from_orion_entity():
    entity = {"id": "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111",
              "type": "AgriParcel", "municipality": {"type": "Property", "value": "Allo"}}
    fake_conn, captured = _fake_pg()
    with patch.object(u, "psycopg2") as pg:
        pg.connect.return_value = fake_conn
        result = u._parcel_urn_to_municipality_code("montiko", entity["id"], entity)
    assert result == ("31013", "municipality")
    sql = " ".join(captured["sql"])
    assert "cadastral_parcels" not in sql
    assert "catalog_municipalities" in sql
    assert captured["params"][0] == ("Allo",)


def test_municipality_property_precedes_address():
    entity = {"id": "urn:ngsi-ld:AgriParcel:22222222-2222-2222-2222-222222222222",
              "type": "AgriParcel",
              "municipality": {"type": "Property", "value": "Allo"},
              "address": {"type": "Property", "value": {"addressLocality": "Other"}}}
    fake_conn, captured = _fake_pg()
    with patch.object(u, "psycopg2") as pg:
        pg.connect.return_value = fake_conn
        result = u._parcel_urn_to_municipality_code("montiko", entity["id"], entity)
    assert result == ("31013", "municipality")
    assert captured["params"][0] == ("Allo",)
