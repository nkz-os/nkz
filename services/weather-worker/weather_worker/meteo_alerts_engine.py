"""
MeteoAlertsEngine — EU-wide weather alerts via MeteoAlarm (EUMETNET).

Replaces the Spain-only AemetAlertsEngine. Fetches CAP alerts from
MeteoAlarm Atom feeds for all EU+EEA countries, parses them, and
persists as WeatherAlert entities in Orion-LD via batch UPSERT.

Alerts are geographic (CAP standard, EMMA_ID zones), cross-tenant.
They live in tenant 'default'.

No API key required — MeteoAlarm legacy Atom feeds are public.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import requests

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")

# MeteoAlarm legacy Atom feed base URL
METEOALARM_FEED_BASE = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom"

# EU+EEA countries with MeteoAlarm coverage (verified 2026-06-05)
_EU_COUNTRIES: List[str] = [
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
    "denmark", "estonia", "finland", "france", "germany", "greece",
    "hungary", "iceland", "ireland", "italy", "latvia", "lithuania",
    "luxembourg", "malta", "netherlands", "norway", "poland", "portugal",
    "romania", "slovakia", "slovenia", "spain", "sweden", "switzerland",
    "united-kingdom",
]

# CAP severity → WeatherAlert SDM severity mapping
# MeteoAlarm uses: Minor, Moderate, Severe, Extreme
_CAP_SEVERITY_MAP: Dict[str, str] = {
    "MINOR": "minor",
    "MODERATE": "moderate",
    "SEVERE": "severe",
    "EXTREME": "critical",
}

# CAP event → WeatherAlert subCategory mapping
_CAP_EVENT_MAP: Dict[str, str] = {
    "WIND": "wind",
    "RAIN": "rain",
    "SNOW": "snow",
    "THUNDERSTORM": "thunderstorm",
    "FOG": "fog",
    "HEAT": "heat",
    "COLD": "cold",
    "FROST": "frost",
    "ICE": "ice",
    "FLOOD": "flood",
    "COASTAL": "coastalEvent",
    "AVALANCHE": "avalanche",
    "WILDFIRE": "wildfire",
}


def _normalize_event(event: str) -> str:
    """Map a CAP event string to a WeatherAlert subCategory."""
    upper = event.upper()
    for key, value in _CAP_EVENT_MAP.items():
        if key in upper:
            return value
    # Fallback: lowercase the first word
    return event.split()[0].lower() if event else "unknown"


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated)."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MeteoAlertsEngine:
    """Downloads EU-wide weather alerts from MeteoAlarm, persists as WeatherAlert
    entities in Orion-LD.

    Runs on its own thread in main.py. No API key, no catalog_municipalities,
    no AEMET dependency. Pure coordinates + CAP standard.
    """

    def __init__(
        self,
        orion_url: str = "",
        interval_hours: int = 1,
    ):
        self.orion_url = orion_url or ORION_URL
        self.interval_hours = interval_hours
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """Execute one complete pass: fetch → parse → build → upsert.

        Returns:
            Dict with counts: {'alerts_fetched': N, 'entities_upserted': N, 'errors': N}
        """
        stats: Dict[str, int] = {
            "alerts_fetched": 0,
            "entities_upserted": 0,
            "errors": 0,
        }

        try:
            # 1. Fetch alerts from all MeteoAlarm country feeds
            raw_alerts = self._fetch_all_alerts()
            stats["alerts_fetched"] = len(raw_alerts)

            if not raw_alerts:
                logger.info("MeteoAlertsEngine: no alerts fetched")
                return stats

            # 2. Build WeatherAlert entities
            entities = self._build_entities(raw_alerts)

            if not entities:
                logger.info("MeteoAlertsEngine: no valid entities built")
                return stats

            # 3. UPSERT batch into Orion-LD (tenant 'default')
            ok = self._upsert_batch("default", entities)
            if ok:
                stats["entities_upserted"] = len(entities)
                logger.info(
                    f"MeteoAlertsEngine: upserted {len(entities)} WeatherAlert entities"
                )
            else:
                stats["errors"] += 1

        except Exception as e:
            logger.error(f"MeteoAlertsEngine run_once failed: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    # ------------------------------------------------------------------
    # MeteoAlarm data fetching
    # ------------------------------------------------------------------

    def _fetch_all_alerts(self) -> List[Dict[str, Any]]:
        """Fetch all active CAP alerts from MeteoAlarm country feeds.

        Iterates over EU+EEA country Atom feeds, parses CAP entries,
        and deduplicates by CAP identifier.
        """
        all_alerts: Dict[str, Dict[str, Any]] = {}

        for country in _EU_COUNTRIES:
            try:
                feed_url = f"{METEOALARM_FEED_BASE}-{country}"
                resp = self._session.get(feed_url, timeout=30)
                if resp.status_code != 200:
                    logger.debug(
                        f"MeteoAlertsEngine: feed {country} returned {resp.status_code}"
                    )
                    continue

                country_alerts = self._parse_atom_feed(resp.text)

                # Deduplicate by CAP identifier (same alert may appear in
                # multiple feeds for border regions)
                for alert in country_alerts:
                    aid = alert.get("alert_id")
                    if aid and aid not in all_alerts:
                        all_alerts[aid] = alert

            except Exception as e:
                logger.debug(
                    f"MeteoAlertsEngine: error fetching feed for {country}: {e}"
                )
                continue

        alerts_list = list(all_alerts.values())
        logger.info(
            f"MeteoAlertsEngine: fetched {len(alerts_list)} unique alerts "
            f"across {len(_EU_COUNTRIES)} countries"
        )
        return alerts_list

    def _parse_atom_feed(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse a MeteoAlarm Atom feed and extract CAP alert entries.

        The Atom feed structure:
        <feed xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
          <entry>
            <cap:identifier>2.49.0.0.724.0.ES...</cap:identifier>
            <cap:event>Moderate thunderstorm warning</cap:event>
            <cap:severity>Moderate</cap:severity>
            <cap:onset>2026-06-04T18:00:00+00:00</cap:onset>
            <cap:expires>2026-06-04T21:59:59+00:00</cap:expires>
            <cap:areaDesc>Prelitoral norte de Tarragona</cap:areaDesc>
            <cap:geocode>
              <valueName>EMMA_ID</valueName>
              <value>ES190</value>
            </cap:geocode>
          </entry>
        </feed>
        """
        try:
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "cap": "urn:oasis:names:tc:emergency:cap:1.2",
            }
            root = ET.fromstring(xml_text)

            alerts: List[Dict[str, Any]] = []
            for entry in root.findall("atom:entry", ns):
                identifier = _text(entry, "cap:identifier", ns)
                if not identifier:
                    continue

                alert: Dict[str, Any] = {
                    "alert_id": identifier,
                    "event": _text(entry, "cap:event", ns),
                    "severity": _text(entry, "cap:severity", ns),
                    "onset": _text(entry, "cap:onset", ns),
                    "expires": _text(entry, "cap:expires", ns),
                    "area_desc": _text(entry, "cap:areaDesc", ns),
                    "emma_id": _text(
                        entry.find("cap:geocode", ns), "cap:value", ns
                    )
                    if entry.find("cap:geocode", ns) is not None
                    else None,
                    "sent": _text(entry, "cap:sent", ns),
                    "certainty": _text(entry, "cap:certainty", ns),
                    "urgency": _text(entry, "cap:urgency", ns),
                }
                alerts.append(alert)

            return alerts

        except ET.ParseError as e:
            logger.error(f"MeteoAlertsEngine: XML parse error: {e}")
            return []
        except Exception as e:
            logger.error(f"MeteoAlertsEngine: error parsing Atom feed: {e}")
            return []

    # ------------------------------------------------------------------
    # Entity building (SDM WeatherAlert)
    # ------------------------------------------------------------------

    def _build_entities(self, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build NGSI-LD WeatherAlert entities from MeteoAlarm CAP alerts.

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
                    f"MeteoAlertsEngine: error building entity for alert "
                    f"{alert.get('alert_id')}: {e}"
                )

        return entities

    def _build_single_entity(self, alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a single WeatherAlert NGSI-LD entity from a CAP alert."""
        alert_id = alert.get("alert_id")
        if not alert_id:
            return None

        # --- Entity ID (deterministic for UPSERT) ---
        entity_id = f"urn:ngsi-ld:WeatherAlert:meteoalarm:{alert_id}"

        # --- Severity mapping ---
        cap_severity = (alert.get("severity") or "").upper()
        severity = _CAP_SEVERITY_MAP.get(cap_severity, "informational")

        # --- subCategory from CAP event ---
        event = alert.get("event") or ""
        subcategory = _normalize_event(event)

        # --- Timestamps ---
        valid_from = self._parse_datetime(alert.get("onset"))
        valid_to = self._parse_datetime(alert.get("expires"))

        # --- Area description ---
        area_desc = alert.get("area_desc") or ""
        emma_id = alert.get("emma_id") or ""

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
                "value": event,
            },
            "address": {
                "type": "Property",
                "value": {"addressLocality": area_desc},
            },
            "dataProvider": {
                "type": "Property",
                "value": "MeteoAlarm (EUMETNET)",
            },
            "meteoalarmZoneId": {
                "type": "Property",
                "value": emma_id,
            },
        }

        # Include optional CAP metadata
        if alert.get("certainty"):
            entity["certainty"] = {
                "type": "Property",
                "value": alert["certainty"],
            }
        if alert.get("urgency"):
            entity["urgency"] = {
                "type": "Property",
                "value": alert["urgency"],
            }

        return entity

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> str:
        """Parse a CAP datetime string into ISO 8601 UTC."""
        if not value:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            clean = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            logger.warning(f"Could not parse datetime: {value}")
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # Orion-LD UPSERT batch
    # ------------------------------------------------------------------

    def _upsert_batch(self, tenant_id: str, entities: List[Dict[str, Any]]) -> bool:
        """Batch UPSERT WeatherAlert entities into Orion-LD.

        Uses POST /ngsi-ld/v1/entityOperations/upsert?options=update.
        Content-Type: application/json + Link header (no @context per entity).
        """
        if not entities:
            return True

        headers = _make_headers(tenant_id)

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
            f"MeteoAlertsEngine loop starting: interval={self.interval_hours}h"
        )

        while True:
            try:
                stats = self.run_once()
                logger.info(f"MeteoAlertsEngine cycle: {stats}")
            except Exception as e:
                logger.error(
                    f"MeteoAlertsEngine loop error: {e}", exc_info=True
                )

            time.sleep(self.interval_hours * 3600)


# ---------------------------------------------------------------------------
# XML helper
# ---------------------------------------------------------------------------


def _text(element, tag, ns):
    """Safely extract text from an XML element with namespace."""
    child = element.find(tag, ns)
    return child.text.strip() if child is not None and child.text else ""


# ---------------------------------------------------------------------------
# Backward-compat alias (for existing imports)
# ---------------------------------------------------------------------------

AemetAlertsEngine = MeteoAlertsEngine
