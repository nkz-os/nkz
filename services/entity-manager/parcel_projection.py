"""Project AgriParcel (Orion SoT) into the cadastral_parcels read-model. No Flask."""
import json
import logging
import re
from typing import Any, Dict, Optional

from db_helper import get_db_connection_simple, return_db_connection

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def urn_to_uuid(urn: str) -> Optional[str]:
    """Trailing UUID of an AgriParcel URN, or None for legacy non-uuid ids."""
    last = urn.split(":")[-1].strip()
    return last if _UUID_RE.match(last) else None


def _val(attr: Any):
    """Extract .value from NGSI-LD property/relationship dict, or pass through scalar."""
    return attr.get("value") if isinstance(attr, dict) else attr


def parse_agriparcel(tenant_id: str, ent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract read-model fields from an AgriParcel entity.

    Returns dict with keys: id (UUID or None), tenant_id, cadastral_reference,
    municipality, crop_type, geometry_geojson (JSON string or None).
    """
    geom = _val(ent.get("location"))
    return {
        "id": urn_to_uuid(ent["id"]),
        "tenant_id": tenant_id,
        "cadastral_reference": _val(ent.get("cadastralReference")),
        "municipality": _val(ent.get("municipality")),
        "crop_type": _val(ent.get("cropType")),
        "geometry_geojson": json.dumps(geom) if geom else None,
    }


def project_upsert_sql() -> str:
    """
    UPSERT SQL for cadastral_parcels read-model.

    Expects params dict with keys: id (UUID str), tenant_id, cadastral_reference,
    municipality, crop_type, geometry_geojson (GeoJSON dict as JSON string).

    Computes area_hectares and centroid from geometry.
    """
    return """
        INSERT INTO cadastral_parcels
            (id, tenant_id, cadastral_reference, municipality, crop_type, geometry,
             area_hectares, centroid, is_active)
        VALUES (%(id)s::uuid, %(tenant_id)s, %(cadastral_reference)s, %(municipality)s, %(crop_type)s,
                ST_GeomFromGeoJSON(%(geometry_geojson)s),
                COALESCE(ST_Area(ST_GeomFromGeoJSON(%(geometry_geojson)s)::geography)/10000, 0),
                ST_Centroid(ST_GeomFromGeoJSON(%(geometry_geojson)s)), TRUE)
        ON CONFLICT (id) DO UPDATE SET
            cadastral_reference = EXCLUDED.cadastral_reference,
            municipality = EXCLUDED.municipality,
            crop_type = EXCLUDED.crop_type,
            geometry = EXCLUDED.geometry,
            area_hectares = EXCLUDED.area_hectares,
            centroid = EXCLUDED.centroid,
            updated_at = now()
    """


def project_delete_sql() -> str:
    """
    DELETE SQL for cadastral_parcels read-model.

    Expects params dict with keys: id (UUID str), tenant_id.
    """
    return "DELETE FROM cadastral_parcels WHERE id = %(id)s::uuid AND tenant_id = %(tenant_id)s"


def project_rows(tenant: str, entities: list, deleted: bool = False) -> int:
    """Upsert (or delete) AgriParcel rows into the cadastral_parcels read-model.

    Returns the number of rows applied. Skips legacy non-uuid ids and (for upsert)
    geometry-less entities.
    """
    conn = get_db_connection_simple()
    applied = 0
    try:
        cur = conn.cursor()
        sql = project_delete_sql() if deleted else project_upsert_sql()
        for ent in entities:
            row = parse_agriparcel(tenant, ent)
            if row["id"] is None:
                logger.warning("Skip projection (legacy non-uuid id): %s", ent.get("id"))
                continue
            if not deleted and not row["geometry_geojson"]:
                logger.warning("Skip projection (no geometry): %s", ent.get("id"))
                continue
            cur.execute(sql, row)
            applied += 1
        conn.commit()
    finally:
        return_db_connection(conn)
    return applied
