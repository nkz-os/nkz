"""
AemetAlertsEngine — independent engine for AEMET weather alerts.

Downloads AEMET alerts and persists them as WeatherAlert entities in Orion-LD
via batch UPSERT. Runs on its own thread in main.py, completely independent
of the ParcelWeatherEngine and the (deprecated) municipality worker.

Alerts are geographic (per AEMET zone), cross-tenant. They live in tenant 'default'.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv("CONTEXT_URL", "")

# SDM WeatherAlert severity mapping (AEMET → FIWARE standard)
_AEMET_SEVERITY_MAP: Dict[str, str] = {
    "YELLOW": "minor",
    "ORANGE": "moderate",
    "RED": "severe",
}

# AEMET phenomenon → WeatherAlert subCategory mapping
_AEMET_SUBCATEGORY_MAP: Dict[str, str] = {
    "VIENTO": "wind",
    "LLUVIA": "rain",
    "NIEVE": "snow",
    "HELADA": "frost",
    "CALOR": "heat",
    "FRIO": "cold",
    "TORMENTA": "storm",
    "NIEBLA": "fog",
    "COSTERO": "coastalEvent",
    "TORMENTA_TROPICAL": "tropicalCyclone",
    "POLVO": "dust",
}


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated)."""
    n = tenant_id
    headers = {
        "NGSILD-Tenant": n,
        "Fiware-Service": n,
        "Fiware-ServicePath": "/",
        "Accept": "application/ld+json",
    }
    if CONTEXT_URL:
        headers["Link"] = (
            f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context";'
            f' type="application/ld+json"'
        )
    return headers


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AemetAlertsEngine:
    """Downloads AEMET alerts and persists them as WeatherAlert entities in Orion-LD.

    Independent of ParcelWeatherEngine and the municipality worker.
    Alerts are geographic (by AEMET zone), not per-tenant.
    Stored in tenant 'default'.
    """

    def __init__(
        self,
        orion_url: str = "",
        aemet_api_key: str = "",
        aemet_api_url: str = "",
        interval_hours: int = 1,
    ):
        self.orion_url = orion_url or ORION_URL
        self.aemet_api_key = aemet_api_key or os.getenv("AEMET_API_KEY", "")
        self.aemet_api_url = (
            aemet_api_url
            or os.getenv("AEMET_API_URL", "https://opendata.aemet.es/opendata/api")
        )
        self.interval_hours = interval_hours
        self._session = requests.Session()

        if not self.aemet_api_key:
            logger.warning(
                "AEMET_API_KEY not configured — AemetAlertsEngine will be a no-op"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """Execute one complete pass: download → transform → upsert.

        Returns:
            Dict with counts: {'alerts_fetched': N, 'entities_upserted': N, 'errors': N}
        """
        stats: Dict[str, int] = {
            "alerts_fetched": 0,
            "entities_upserted": 0,
            "errors": 0,
        }

        if not self.aemet_api_key:
            logger.info("AemetAlertsEngine: no API key — skipping")
            return stats

        try:
            # 1. Download alerts from AEMET
            raw_alerts = self._fetch_all_alerts()
            stats["alerts_fetched"] = len(raw_alerts)

            if not raw_alerts:
                logger.info("AemetAlertsEngine: no alerts fetched from AEMET")
                return stats

            # 2. Build WeatherAlert entities (with municipality coordinates)
            entities = self._build_entities(raw_alerts)

            if not entities:
                logger.info("AemetAlertsEngine: no valid entities built")
                return stats

            # 3. UPSERT batch into Orion-LD (tenant 'default')
            ok = self._upsert_batch("default", entities)
            if ok:
                stats["entities_upserted"] = len(entities)
                logger.info(
                    f"AemetAlertsEngine: upserted {len(entities)} WeatherAlert entities"
                )
            else:
                stats["errors"] += 1

        except Exception as e:
            logger.error(f"AemetAlertsEngine run_once failed: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    # ------------------------------------------------------------------
    # AEMET data fetching
    # ------------------------------------------------------------------

    def _fetch_all_alerts(self) -> List[Dict[str, Any]]:
        """Fetch all active AEMET alerts.

        Queries the AEMET OpenData API for the latest CAP alerts, then
        downloads the detailed data payload for all zones.
        """
        try:
            # Step 1: get the latest CAP file URL
            cap_endpoint = "/avisos_cap/ultimoelaborado"
            datos_url = self._aemet_resolve(cap_endpoint)
            if not datos_url:
                logger.warning("AemetAlertsEngine: no CAP data URL resolved")
                return []

            # Step 2: download the CAP XML/JSON payload
            resp = self._session.get(datos_url, timeout=30)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")

            if "xml" in content_type or resp.text.strip().startswith("<?xml"):
                return self._parse_cap_xml(resp.text)
            elif "application/json" in content_type:
                data = resp.json()
                return self._parse_cap_json(data)
            else:
                # Try JSON first, then XML
                try:
                    return self._parse_cap_json(resp.json())
                except Exception:
                    return self._parse_cap_xml(resp.text)

        except Exception as e:
            logger.error(f"AemetAlertsEngine: error fetching alerts: {e}")
            return []

    def _aemet_resolve(self, endpoint: str) -> Optional[str]:
        """Resolve an AEMET endpoint → return the 'datos' URL."""
        try:
            url = f"{self.aemet_api_url}{endpoint}"
            resp = self._session.get(
                url, params={"api_key": self.aemet_api_key}, timeout=15
            )
            resp.raise_for_status()
            payload = resp.json()

            estado = payload.get("estado")
            if estado and int(estado) != 200:
                logger.error(f"AEMET estado={estado} for {endpoint}")
                return None

            return payload.get("datos")
        except Exception as e:
            logger.error(f"AemetAlertsEngine: error resolving {endpoint}: {e}")
            return None

    def _parse_cap_json(self, data: Any) -> List[Dict[str, Any]]:
        """Parse AEMET CAP alerts from JSON (if available).

        AEMET typically returns XML; JSON parsing is a fallback.
        """
        alerts: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for entry in data:
                alert = self._normalize_alert(entry)
                if alert:
                    alerts.append(alert)
        elif isinstance(data, dict):
            alert = self._normalize_alert(data)
            if alert:
                alerts.append(alert)
        return alerts

    def _parse_cap_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse AEMET CAP alerts from XML."""
        try:
            import xml.etree.ElementTree as ET

            ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
            root = ET.fromstring(xml_text)

            alerts: List[Dict[str, Any]] = []
            for info in root.findall(".//cap:info", ns):
                alert = {
                    "id": _text(info, "cap:identifier", ns),
                    "fenomeno": _text(info, "cap:event", ns),
                    "nivel": _text(info, "cap:severity", ns),
                    "inicio": _text(info, "cap:onset", ns),
                    "fin": _text(info, "cap:expires", ns),
                    "texto": _text(info, "cap:description", ns),
                    "zona": _text(info.find(".//cap:area", ns), "cap:areaDesc", ns)
                    if info.find(".//cap:area", ns) is not None
                    else None,
                    "idZona": _text(
                        info.find(".//cap:area/cap:geocode", ns), "cap:value", ns
                    )
                    if info.find(".//cap:area/cap:geocode", ns) is not None
                    else None,
                }
                alerts.append(alert)

            return alerts
        except Exception as e:
            logger.error(f"AemetAlertsEngine: error parsing CAP XML: {e}")
            return []

    def _normalize_alert(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize a raw alert dict into a standard internal format."""
        nivel = (raw.get("nivel") or raw.get("severity") or "").upper()
        fenomeno = (raw.get("fenomeno") or raw.get("event") or "").upper()

        if not nivel or not fenomeno:
            return None

        return {
            "aemet_alert_id": raw.get("id") or raw.get("identifier"),
            "aemet_zone_id": raw.get("idZona") or raw.get("zona"),
            "zone_name": raw.get("zona") or raw.get("areaDesc"),
            "fenomeno": fenomeno,
            "nivel": nivel,
            "inicio": raw.get("inicio") or raw.get("onset"),
            "fin": raw.get("fin") or raw.get("expires"),
            "texto": raw.get("texto") or raw.get("description") or "",
        }

    # ------------------------------------------------------------------
    # Entity building (SDM WeatherAlert)
    # ------------------------------------------------------------------

    def _build_entities(self, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build NGSI-LD WeatherAlert entities from raw AEMET alerts.

        Each entity follows the FIWARE Smart Data Model for WeatherAlert:
        https://github.com/smart-data-models/dataModel.Weather/tree/master/WeatherAlert
        """
        entities: List[Dict[str, Any]] = []

        for alert in alerts:
            try:
                entity = self._build_single_entity(alert)
                if entity:
                    entities.append(entity)
            except Exception as e:
                logger.warning(
                    f"AemetAlertsEngine: error building entity for alert "
                    f"{alert.get('aemet_alert_id')}: {e}"
                )

        return entities

    def _build_single_entity(self, alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a single WeatherAlert NGSI-LD entity."""
        aemet_id = alert.get("aemet_alert_id")
        if not aemet_id:
            logger.warning("Skipping alert without aemet_alert_id")
            return None

        # --- Entity ID (deterministic for UPSERT) ---
        entity_id = f"urn:ngsi-ld:WeatherAlert:aemet:{aemet_id}"

        # --- Severity mapping ---
        nivel = alert.get("nivel", "")
        severity = _AEMET_SEVERITY_MAP.get(nivel)
        if not severity:
            logger.warning(f"Unknown AEMET severity '{nivel}' for alert {aemet_id}")
            severity = "informational"

        # --- subCategory mapping ---
        fenomeno = alert.get("fenomeno", "")
        subcategory = _AEMET_SUBCATEGORY_MAP.get(fenomeno, fenomeno.lower())

        # --- Timestamps ---
        valid_from = self._parse_datetime(alert.get("inicio"))
        valid_to = self._parse_datetime(alert.get("fin"))

        # --- Coordinates (from zone → municipality resolution) ---
        zone_id = alert.get("aemet_zone_id")
        coords = self._resolve_zone_coordinates(zone_id) if zone_id else None

        # --- Municipality metadata ---
        municipality_code = None
        municipality_name = alert.get("zone_name") or ""

        if zone_id:
            mun_info = self._resolve_municipality_from_zone(zone_id)
            if mun_info:
                municipality_code = mun_info.get("ine_code")
                municipality_name = mun_info.get("name") or municipality_name
                if not coords and mun_info.get("latitude") and mun_info.get("longitude"):
                    coords = (float(mun_info["longitude"]), float(mun_info["latitude"]))

        # --- Build entity ---
        entity: Dict[str, Any] = {
            "id": entity_id,
            "type": "WeatherAlert",
            "category": {"type": "Property", "value": ["meteorological"]},
            "subCategory": {"type": "Property", "value": [subcategory]},
            "severity": {"type": "Property", "value": severity},
            "validFrom": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": valid_from},
            },
            "validTo": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": valid_to},
            },
            "description": {
                "type": "Property",
                "value": alert.get("texto") or "",
            },
            "address": {
                "type": "Property",
                "value": {"addressLocality": municipality_name},
            },
            "aemetZoneId": {
                "type": "Property",
                "value": zone_id or "",
            },
        }

        if coords:
            entity["location"] = {
                "type": "GeoProperty",
                "value": {"type": "Point", "coordinates": [coords[0], coords[1]]},
            }

        if municipality_code:
            entity["municipalityCode"] = {
                "type": "Property",
                "value": municipality_code,
            }

        return entity

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> str:
        """Parse an AEMET datetime string into ISO 8601 UTC."""
        if not value:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            # Handle formats like "2026-06-05T12:00:00Z" or "2026-06-05T12:00:00+00:00"
            clean = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            logger.warning(f"Could not parse datetime: {value}")
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _resolve_zone_coordinates(
        self, zone_id: str
    ) -> Optional[tuple]:
        """Resolve AEMET zone ID → (longitude, latitude) from catalog_municipalities.

        Returns None if the zone can't be resolved.
        """
        try:
            import psycopg2

            pg_url = os.getenv("POSTGRES_URL", "")
            if not pg_url:
                return None

            conn = psycopg2.connect(pg_url)
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT ST_X(geom) as lon, ST_Y(geom) as lat
                    FROM catalog_municipalities
                    WHERE ine_code = %s
                    LIMIT 1
                    """,
                    (zone_id,),
                )
                row = cur.fetchone()
                cur.close()
                if row and row[0] is not None:
                    return (float(row[0]), float(row[1]))
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not resolve coordinates for zone {zone_id}: {e}")

        return None

    def _resolve_municipality_from_zone(
        self, zone_id: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve AEMET zone ID → municipality info from catalog_municipalities."""
        try:
            import psycopg2

            pg_url = os.getenv("POSTGRES_URL", "")
            if not pg_url:
                return None

            conn = psycopg2.connect(pg_url)
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT ine_code, name, latitude, longitude
                    FROM catalog_municipalities
                    WHERE ine_code = %s
                    LIMIT 1
                    """,
                    (zone_id,),
                )
                row = cur.fetchone()
                cur.close()
                if row:
                    return {
                        "ine_code": row[0],
                        "name": row[1],
                        "latitude": row[2],
                        "longitude": row[3],
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not resolve municipality for zone {zone_id}: {e}")

        return None

    # ------------------------------------------------------------------
    # Orion-LD UPSERT batch
    # ------------------------------------------------------------------

    def _upsert_batch(self, tenant_id: str, entities: List[Dict[str, Any]]) -> bool:
        """Batch UPSERT WeatherAlert entities into Orion-LD.

        Uses POST /ngsi-ld/v1/entityOperations/upsert?options=update.
        The deterministic entity ID ensures idempotency — same ID = same alert,
        no race conditions.

        Content-Type: application/json + Link header (no @context embedded in
        each entity — cleaner for batch operations). This is approach B per
        NGSI-LD spec §5.2: Link header provides the @context once for the
        entire batch.
        """
        if not entities:
            return True

        headers = _make_headers(tenant_id)
        # requests.post(json=...) defaults to Content-Type: application/json.
        # We keep the Link header for @context resolution (no need to embed
        # @context in every entity).

        try:
            url = f"{self.orion_url}/ngsi-ld/v1/entityOperations/upsert?options=update"
            resp = requests.post(
                url,
                json=entities,
                headers=headers,
                timeout=30,
            )

            if resp.status_code in (200, 201, 204):
                logger.debug(
                    f"UPSERT batch: {len(entities)} entities → Orion-LD OK"
                )
                return True
            else:
                logger.error(
                    f"UPSERT batch failed: HTTP {resp.status_code} — "
                    f"{resp.text[:500]}"
                )
                return False

        except Exception as e:
            logger.error(f"UPSERT batch error: {e}")
            return False

    # ------------------------------------------------------------------
    # Loop (run by main.py thread)
    # ------------------------------------------------------------------

    def run_loop(self):
        """Run the engine in a continuous loop (called from a daemon thread)."""
        logger.info(
            f"AemetAlertsEngine loop starting: interval={self.interval_hours}h"
        )

        while True:
            try:
                stats = self.run_once()
                logger.info(f"AemetAlertsEngine cycle: {stats}")
            except Exception as e:
                logger.error(
                    f"AemetAlertsEngine loop error: {e}", exc_info=True
                )

            time.sleep(self.interval_hours * 3600)


# ---------------------------------------------------------------------------
# XML namespace helper
# ---------------------------------------------------------------------------


def _text(element, tag, ns):
    """Safely extract text from an XML element with namespace."""
    child = element.find(tag, ns)
    return child.text.strip() if child is not None and child.text else ""
