"""
Resolve NGSI-LD entity URNs to Timescale query keys (WeatherObserved entity/municipality or IoT device id).
Read-only: Orion-LD + PostgreSQL (cadastral / catalog). No writes.
Migrated from entity-manager timeseries-location responsibility (Strangler Fig).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

ORION_URL = (os.getenv("ORION_URL") or "").rstrip("/")
POSTGRES_URL = os.getenv("POSTGRES_URL")

PARCEL_ENTITY_TYPES = set(
    t.strip()
    for t in os.getenv(
        "PARCEL_ENTITY_TYPES",
        "AgriParcel,Parcel,Vineyard,OliveGrove,vineyard,olive_grove",
    ).split(",")
    if t.strip()
)


def _orion_headers(
    tenant_id: str, extra: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    h = inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)
    if extra:
        h.update(extra)
    return h


def normalize_device_id(entity_id: Optional[str]) -> str:
    if not entity_id:
        return ""
    if ":" in entity_id:
        return entity_id.rsplit(":", 1)[-1]
    return entity_id


# Device and ManufacturingMachine are the canonical device types; AgriSensor and
# AgriDevice are legacy ones still read until nothing writes them.
_DEVICE_TYPES = {"Device", "ManufacturingMachine", "AgriSensor", "AgriDevice"}


def _is_device_type(etype: Optional[str]) -> bool:
    """True for a device type, short or expanded (".../Device", "nkz:AgriSensor")."""
    et = (etype or "").strip()
    if not et:
        return False
    return et.rsplit("/", 1)[-1].rsplit(":", 1)[-1] in _DEVICE_TYPES


def _extract_entity_location(entity: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """
    Extract (latitude, longitude) from an NGSI-LD entity's location attribute.
    Handles both GeoProperty (Point, Polygon) and simplified formats.
    Returns None if no resolvable location is found.
    """
    location_attr = entity.get("location", {})
    if isinstance(location_attr, dict):
        loc_value = location_attr.get("value", location_attr)
    else:
        loc_value = location_attr

    if not isinstance(loc_value, dict):
        return None

    coords = loc_value.get("coordinates", [])
    geom_type = loc_value.get("type", "")

    if geom_type == "Point" and len(coords) >= 2:
        return (float(coords[1]), float(coords[0]))  # (lat, lon) from (lon, lat)

    if geom_type in ("Polygon", "MultiPolygon") and coords:
        ring = coords[0] if geom_type == "Polygon" else coords[0][0]
        if ring and len(ring) > 0:
            ys = [p[1] for p in ring if len(p) >= 2]
            xs = [p[0] for p in ring if len(p) >= 2]
            if xs and ys:
                return (sum(ys) / len(ys), sum(xs) / len(xs))  # centroid approx

    return None


WEATHER_OBSERVED_ENTITY_TYPES = [
    "WeatherObserved",
    "https://saref.etsi.org/saref4agri/WeatherObserved",
]
_WEATHER_LOCATION_LOOKBACK_DAYS = 30


def nearest_weather_entity(
    cur,
    tenant_id: str,
    lat: float,
    lon: float,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Optional[str]:
    """
    Entity id of the tenant's WeatherObserved entity nearest to (lat, lon).

    Location comes from the latest telemetry_events row of each entity
    (payload.raw.location coordinates = [lon, lat]); the closed-day ``-daily``
    twin is not a separate station. Default window: last 30 days.
    """
    until = until or datetime.utcnow()
    since = since or until - timedelta(days=_WEATHER_LOCATION_LOOKBACK_DAYS)
    cur.execute(
        """
        SELECT ent.entity_id
        FROM (
            SELECT DISTINCT ON (x.entity_id) x.entity_id,
                   (x.payload#>>'{raw,location,value,coordinates,0}')::double precision AS lon,
                   (x.payload#>>'{raw,location,value,coordinates,1}')::double precision AS lat
            FROM telemetry_events x
            WHERE x.tenant_id = %s AND x.entity_type = ANY(%s)
              AND x.observed_at >= %s AND x.observed_at < %s
              AND x.entity_id NOT LIKE '%%-daily'
              AND x.payload#>>'{raw,location,value,coordinates,1}' IS NOT NULL
            ORDER BY x.entity_id, x.observed_at DESC
        ) ent
        ORDER BY ST_Distance(
            ST_SetSRID(ST_MakePoint(ent.lon, ent.lat), 4326),
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        )
        LIMIT 1
        """,
        (tenant_id, list(WEATHER_OBSERVED_ENTITY_TYPES), since, until, lon, lat),
    )
    row = cur.fetchone()
    return str(row["entity_id"]) if row else None


def _find_nearest_weather_entity(
    tenant_id: str, lat: float, lon: float
) -> Optional[Tuple[str, str]]:
    """Nearest WeatherObserved entity of the tenant, as ``(short key, 'spatial')``."""
    if not POSTGRES_URL:
        return None
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            entity_id = nearest_weather_entity(cur, tenant_id, lat, lon)
        finally:
            cur.close()
            conn.close()
        if entity_id:
            return (normalize_device_id(entity_id), "spatial")
    except Exception as e:
        logger.warning("Spatial weather resolution failed: %s", e)
    return None


def fetch_orion_entity(tenant_id: str, entity_id: str) -> Optional[Dict[str, Any]]:
    if not ORION_URL or not entity_id:
        return None
    url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
    try:
        r = requests.get(url, headers=_orion_headers(tenant_id), timeout=10)
    except Exception as e:
        logger.warning("Orion request failed for %s: %s", entity_id, e)
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except json.JSONDecodeError:
        return None


def _municipality_from_parcel_address_entity(
    parcel_entity: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    """
    Match catalog_municipalities.ine_code from the Orion AgriParcel entity.

    Single source of truth = Orion. Prefers a ``municipality`` Property; falls back
    to ``address.addressLocality`` / ``addressRegion``.
    """
    if not parcel_entity or not POSTGRES_URL:
        return None
    loc = None
    muni = parcel_entity.get("municipality")
    if isinstance(muni, dict):
        muni = muni.get("value")
    if isinstance(muni, str) and muni.strip():
        loc = muni.strip()
    if loc is None:
        addr = parcel_entity.get("address")
        if isinstance(addr, dict) and "value" in addr:
            addr = addr["value"]
        if not isinstance(addr, dict):
            return None
        loc = addr.get("addressLocality") or addr.get("addressRegion") or ""
    if not isinstance(loc, str) or not loc.strip():
        return None
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT ine_code FROM catalog_municipalities
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (loc.strip(),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return (row["ine_code"], "municipality")
    except Exception as e:
        logger.debug("Catalog lookup for municipality name failed: %s", e)
    return None


def _parcel_urn_to_municipality_code(
    tenant_id: str, parcel_urn: str, parcel_entity: Optional[dict] = None
) -> Optional[Tuple[str, str]]:
    """Resolve a parcel URN to ``(municipality INE code, 'municipality')``.

    Single source of truth = Orion: the municipality is read straight from the
    AgriParcel entity. The ``cadastral_parcels`` read-model is retired.
    """
    return _municipality_from_parcel_address_entity(parcel_entity)


def _resolve_urn_to_weather_key(
    tenant_id: str,
    entity_id: str,
    entity: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], str]:
    """
    Resolve an entity URN to a WeatherObserved read key (entity short id or municipality code).
    Returns (timeseries_entity_id, source) or (None, reason).
    """
    if not entity_id or not isinstance(entity_id, str):
        return None, "not_found"
    entity_id = entity_id.strip()
    if not entity_id.lower().startswith("urn:"):
        return entity_id, "passthrough"

    if not ORION_URL:
        return None, "no_orion"

    if entity is None:
        entity = fetch_orion_entity(tenant_id, entity_id)
    if not entity:
        return None, "not_found"

    etype_raw = entity.get("type") or ""
    etype = etype_raw.strip()
    # JSON-LD may use short name or full URI
    etype_short = etype.split("/")[-1] if "/" in etype else etype

    # Helper: try spatial resolution from entity location (global, no admin codes)
    def _resolve_by_location(ent: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        loc = _extract_entity_location(ent)
        if loc is None:
            return None
        lat, lon = loc
        return _find_nearest_weather_entity(tenant_id, lat, lon)

    if etype_short == "WeatherObserved" or etype.endswith("WeatherObserved"):
        # The entity is its own series (rows are keyed by entity id)
        return (normalize_device_id(entity_id), "entity")

    if etype_short in PARCEL_ENTITY_TYPES or "parcel" in etype_short.lower():
        # Per-parcel virtual weather station: WeatherObserved entity of this parcel
        return (normalize_device_id(entity_id), "parcel")

    # Unknown type: try spatial resolution as last resort
    spatial = _resolve_by_location(entity)
    if spatial is not None:
        return spatial

    return None, "no_location"


def plan_timeseries_read(tenant_id: str, entity_urn: str) -> Dict[str, Any]:
    """
    Decide whether to read IoT telemetry or WeatherObserved telemetry (weather mode) for this URN.
    """
    eid = (entity_urn or "").strip()
    device_candidates: List[str] = []
    if eid:
        device_candidates.append(eid)
        short = normalize_device_id(eid)
        if short and short != eid:
            device_candidates.append(short)

    if not eid.lower().startswith("urn:"):
        wkey, wsrc = _resolve_urn_to_weather_key(tenant_id, eid)
        if wkey:
            return {
                "mode": "weather",
                "weather_key": wkey,
                "weather_source": wsrc,
                "device_candidates": device_candidates,
            }
        return {
            "mode": "telemetry",
            "weather_key": None,
            "weather_source": "",
            "device_candidates": device_candidates,
        }

    ent = fetch_orion_entity(tenant_id, eid) if ORION_URL else None
    if ent and _is_device_type(ent.get("type")):
        return {
            "mode": "telemetry",
            "weather_key": None,
            "weather_source": "",
            "device_candidates": device_candidates,
        }

    wkey, wsrc = _resolve_urn_to_weather_key(tenant_id, eid, entity=ent)
    if wkey:
        return {
            "mode": "weather",
            "weather_key": wkey,
            "weather_source": wsrc,
            "device_candidates": device_candidates,
        }

    return {
        "mode": "telemetry",
        "weather_key": None,
        "weather_source": "",
        "device_candidates": device_candidates,
    }
