import pytest
from parcel_projection import urn_to_uuid, project_upsert_sql, project_delete_sql, parse_agriparcel


def test_urn_to_uuid_extracts_trailing_uuid():
    assert urn_to_uuid("urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111") == "11111111-1111-1111-1111-111111111111"


def test_urn_to_uuid_none_for_legacy_id():
    assert urn_to_uuid("urn:ngsi-ld:AgriParcel:1780150635995-it24vmsar") is None


def test_parse_agriparcel_pulls_fields():
    ent = {
        "id": "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111",
        "type": "AgriParcel",
        "cadastralReference": {"type": "Property", "value": "REF-1"},
        "name": {"type": "Property", "value": "Montiko"},
        "cropType": {"type": "Property", "value": "olive"},
        "location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": [[[0,0],[0,1],[1,1],[0,0]]]}},
    }
    row = parse_agriparcel("montiko", ent)
    assert row["id"] == "11111111-1111-1111-1111-111111111111"
    assert row["tenant_id"] == "montiko"
    assert row["cadastral_reference"] == "REF-1"
    assert row["crop_type"] == "olive"
    assert row["geometry_geojson"]  # JSON string for ST_GeomFromGeoJSON


def test_parse_agriparcel_handles_missing_fields():
    ent = {
        "id": "urn:ngsi-ld:AgriParcel:22222222-2222-2222-2222-222222222222",
        "type": "AgriParcel",
    }
    row = parse_agriparcel("tenant-a", ent)
    assert row["id"] == "22222222-2222-2222-2222-222222222222"
    assert row["tenant_id"] == "tenant-a"
    assert row["cadastral_reference"] is None
    assert row["municipality"] is None
    assert row["crop_type"] is None
    assert row["geometry_geojson"] is None


def test_parse_agriparcel_skip_on_legacy_uuid():
    ent = {
        "id": "urn:ngsi-ld:AgriParcel:1780150635995-it24vmsar",
        "type": "AgriParcel",
        "cropType": {"type": "Property", "value": "maize"},
    }
    row = parse_agriparcel("tenant-a", ent)
    assert row["id"] is None  # Legacy ID returns None


def test_project_upsert_sql_contains_key_clauses():
    sql = project_upsert_sql()
    assert "INSERT INTO cadastral_parcels" in sql
    assert "ON CONFLICT (id) DO UPDATE SET" in sql
    assert "ST_GeomFromGeoJSON" in sql
    assert "ST_Area" in sql
    assert "ST_Centroid" in sql


def test_project_delete_sql_contains_key_clauses():
    sql = project_delete_sql()
    assert "DELETE FROM cadastral_parcels" in sql
    assert "id = %(id)s::uuid" in sql
    assert "tenant_id = %(tenant_id)s" in sql


def test_delete_orphans_sql_uses_tenant_and_uuid_array():
    """FIX 2a: delete_orphans must DELETE rows not in present_uuids for the tenant."""
    from unittest.mock import patch, MagicMock
    import parcel_projection as pp

    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 2
    conn.cursor.return_value = cur

    with patch("parcel_projection.get_db_connection_simple", return_value=conn), \
         patch("parcel_projection.return_db_connection"):
        n = pp.delete_orphans("montiko", ["11111111-1111-1111-1111-111111111111"])

    assert n == 2
    assert cur.execute.called
    call_args = cur.execute.call_args
    sql = call_args.args[0]
    assert "DELETE FROM cadastral_parcels" in sql
    assert "tenant_id" in sql
