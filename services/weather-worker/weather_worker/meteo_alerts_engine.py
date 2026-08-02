"""
MeteoAlertsEngine — EU-wide weather alerts via MeteoAlarm EDR API.

Fetches active warnings from the MeteoAlarm EDR /warnings/locations/ALL
endpoint, resolves per-warning CAP-JSON attributes and GeoJSON geometry,
and persists as WeatherAlert entities in Orion-LD via batch UPSERT.

Supersedes the legacy Atom-feed + EMMA-zone approach (now removed).
Alerts carry an exact location polygon so parcel↔alert geo-queries work.

Alerts are geographic, cross-tenant. They live in tenant 'default'.
"""

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from common.ngsi_headers import inject_fiware_headers
from weather_worker.edr_client import EdrWarningsClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")

# MeteoAlarm API key (Bearer token for the EDR API).
# Provided via K8s Secret weather-secrets key 'meteoalarm-api-key'.
METEOALARM_API_KEY = os.getenv("METEOALARM_API_KEY", "")

# EDR API defaults (overridable via env).
EDR_BASE_URL = os.getenv("EDR_BASE_URL", "https://api.meteoalarm.org/edr/v1")
EDR_SENT_WINDOW_HOURS = int(os.getenv("EDR_SENT_WINDOW_HOURS", "23"))
EDR_ACTIVE_WINDOW_HOURS = int(os.getenv("EDR_ACTIVE_WINDOW_HOURS", "6"))

# CAP severity → WeatherAlert SDM severity mapping (unchanged from legacy).
_CAP_SEVERITY_MAP: Dict[str, str] = {
    "MINOR": "minor",
    "MODERATE": "moderate",
    "SEVERE": "severe",
    "EXTREME": "critical",
}

# CAP event → WeatherAlert subCategory mapping (unchanged from legacy).
_CAP_EVENT_MAP: Dict[str, str] = {
    "THUNDERSTORM": "thunderstorm",
    "AVALANCHE": "avalanche",
    "COASTAL": "coastalEvent",
    "WILDFIRE": "wildfire",
    "FROST": "frost",
    "FLOOD": "flood",
    "HEAT": "heat",
    "COLD": "cold",
    "WIND": "wind",
    "RAIN": "rain",
    "SNOW": "snow",
    "ICE": "ice",
    "FOG": "fog",
}


def _normalize_event(event: str) -> str:
    """Map a CAP event string to a WeatherAlert subCategory."""
    upper = event.upper()
    for key, value in _CAP_EVENT_MAP.items():
        if key in upper:
            return value
    return event.split()[0].lower() if event else "unknown"


def _extract_emma_id(geocode: Any) -> str:
    """Return the EMMA_ID string from a CAP-JSON area ``geocode`` field.

    EDR detail ``geocode`` is a list of ``{"value", "valueName"}`` objects;
    legacy Atom exposed a bare string. Return the entry whose ``valueName``
    is ``EMMA_ID``; fall back to the first entry's value, then "".
    """
    if isinstance(geocode, str):
        return geocode
    if isinstance(geocode, list):
        for gc in geocode:
            if isinstance(gc, dict) and gc.get("valueName") == "EMMA_ID":
                return str(gc.get("value", ""))
        if geocode and isinstance(geocode[0], dict):
            return str(geocode[0].get("value", ""))
    return ""


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated)."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


# Orion-LD rejects request bodies over its `-inReqPayloadMaxSize` (compiled
# default 1 MB) with `400 BadRequestData {"title":"payload missing"}`. The
# EU-wide alert batch grew past 1 MB (~1600 alerts), silently stopping all
# WeatherAlert writes (incident 2026-07-25 / root-caused 2026-07-29); location
# GeoProperties inflate it further. Chunk each POST to stay well under the limit.
_MAX_UPSERT_BYTES = 800_000


def _chunk_by_size(entities: List[Dict[str, Any]], max_bytes: int) -> List[List[Dict[str, Any]]]:
    """Split entities into chunks whose JSON body stays under max_bytes.

    Chunks by cumulative serialized size (robust to variable entity size, e.g.
    alerts with vs without a location polygon). A lone entity larger than
    max_bytes is emitted by itself — best effort so one oversized entity can't
    stall the rest of the batch.
    """
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_bytes = 2  # the enclosing "[]"
    for entity in entities:
        entity_bytes = len(json.dumps(entity).encode()) + 1  # +1 for the joining comma
        if current and current_bytes + entity_bytes > max_bytes:
            chunks.append(current)
            current = []
            current_bytes = 2
        current.append(entity)
        current_bytes += entity_bytes
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MeteoAlertsEngine:
    """Downloads EU-wide weather alerts from MeteoAlarm EDR API, persists as
    WeatherAlert entities in Orion-LD.

    Runs on its own thread in main.py. Uses the EDR REST API
    (/collections/warnings/locations/ALL) for index paging and resolves
    per-warning detail payloads (CAP-JSON attributes + GeoJSON geometry).
    """

    def __init__(
        self,
        orion_url: str = "",
        interval_hours: int = 1,
        edr_base_url: str = "",
        edr_api_key: str = "",
        edr_sent_window_hours: int = 23,
        edr_active_window_hours: int = 6,
    ):
        self.orion_url = orion_url or ORION_URL
        self.interval_hours = interval_hours
        self._session = requests.Session()

        self._client = EdrWarningsClient(
            base_url=edr_base_url or EDR_BASE_URL,
            api_key=edr_api_key or METEOALARM_API_KEY,
            sent_window_hours=edr_sent_window_hours or EDR_SENT_WINDOW_HOURS,
            active_window_hours=edr_active_window_hours or EDR_ACTIVE_WINDOW_HOURS,
            session=self._session,
        )
        self._seen: set = set()  # set[alertId] — in-memory dedup
        self._dedup_keys: deque = deque()  # (alertId, hubTime) — MQTT dedup, MRU
        self._dedup_set: set = set()  # companion set for O(1) lookup

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """Execute one complete pass: fetch → build → upsert.

        Pages through EDR index features, resolves detail for new (unseen,
        non-superseded) alerts, builds WeatherAlert entities, and upserts
        in tenant 'default' under Orion-LD's request payload limit.
        """
        stats: Dict[str, int] = {
            "alerts_fetched": 0,
            "entities_upserted": 0,
            "entities_pruned": 0,
            "errors": 0,
        }

        try:
            entities: List[Dict[str, Any]] = []

            for feature in self._client.iter_index():
                props = feature.get("properties", {})
                if props.get("supersededByAlertId"):
                    continue  # superseded alerts are stale — skip
                aid = props.get("alertId")
                if not aid or aid in self._seen:
                    continue
                stats["alerts_fetched"] += 1

                cap, geometry = self._client.fetch_detail(feature)
                if cap is None:
                    # detail fetch failed → log inside fetch_detail, skip this alert
                    stats["errors"] += 1
                    continue

                entity = self._build_single_entity(cap, feature, geometry)
                if entity:
                    entities.append(entity)
                    self._seen.add(aid)

            if entities:
                ok = self._upsert_batch("default", entities)
                if ok:
                    stats["entities_upserted"] = len(entities)
                    logger.info(
                        "MeteoAlertsEngine: upserted %d WeatherAlert entities",
                        len(entities),
                    )
                else:
                    stats["errors"] += 1
            else:
                logger.info("MeteoAlertsEngine: no new entities built")

            # Prune expired alerts (reaper: upsert never removes stale rows).
            pruned = self._prune_expired_alerts("default")
            stats["entities_pruned"] = pruned
            if pruned:
                logger.info(
                    "MeteoAlertsEngine: pruned %d expired WeatherAlert entities",
                    pruned,
                )

        except Exception as e:
            logger.error("MeteoAlertsEngine run_once failed: %s", e, exc_info=True)
            stats["errors"] += 1

        return stats

    # ------------------------------------------------------------------
    # Entity building (SDM WeatherAlert)
    # ------------------------------------------------------------------

    def _build_single_entity(
        self,
        cap: Dict[str, Any],
        feature: Dict[str, Any],
        geometry: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build a WeatherAlert NGSI-LD entity from EDR detail payloads.

        Args:
            cap: the CAP-JSON detail dict (from the ``application/json`` link).
            feature: the index feature dict (for alertId, countryCode, etc.).
            geometry: the GeoJSON Polygon/MultiPolygon from the
                      ``application/geo+json`` link (already extracted from
                      its Feature wrapper).  May be None.
        """
        props = feature.get("properties", {})
        alert_id = props.get("alertId")
        if not alert_id:
            return None

        # --- Entity ID (deterministic for UPSERT; UUID from EDR) ---
        entity_id = f"urn:ngsi-ld:WeatherAlert:meteoalarm:{alert_id}"

        # --- Select the English-language info block ---
        info = _select_english_info(cap.get("info", []))

        # --- Severity (CAP string, language-independent) ---
        cap_severity = (info.get("severity") or "").upper()
        severity = _CAP_SEVERITY_MAP.get(cap_severity, "informational")

        # --- subCategory from CAP event (English block → existing map works) ---
        event = info.get("event") or ""
        subcategory = _normalize_event(event)

        # --- Timestamps (plain ISO strings — preserve 2026-07-25 fix) ---
        #   EDR detail: 'effective' is null; 'onset' is the valid-from time.
        valid_from = self._parse_datetime(info.get("onset"))
        valid_to = self._parse_datetime(info.get("expires"))

        # --- Area fields ---
        area = (info.get("area") or [{}])[0]  # first area block
        area_desc = area.get("areaDesc", "") if isinstance(area, dict) else ""
        emma_id = (
            _extract_emma_id(area.get("geocode")) if isinstance(area, dict) else ""
        )

        entity: Dict[str, Any] = {
            "id": entity_id,
            "type": "WeatherAlert",
            "category": {"type": "Property", "value": ["meteorological"]},
            "subCategory": {"type": "Property", "value": [subcategory]},
            "severity": {"type": "Property", "value": severity},
            # Temporal values are stored as plain ISO8601 strings, NOT the
            # JSON-LD typed form {"@type":"DateTime","@value":...}. Orion-LD
            # stores the typed form as a compound object its `q` relational
            # operators cannot compare into, so validTo</> matched nothing —
            # breaking the active-alert query and expired-alert prune
            # (incident 2026-07-25). ISO8601 sorts lexicographically, so a
            # String value makes q=validTo</> work server-side.
            "validFrom": {"type": "Property", "value": valid_from},
            "validTo": {"type": "Property", "value": valid_to},
            "description": {
                "type": "Property",
                "value": info.get("headline") or event,
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

        # Attach the exact awareness-zone polygon so parcel-alert geo-queries
        # (georel=intersects + geoproperty=location) work.
        if geometry and isinstance(geometry, dict) and geometry.get("type") in ("Polygon", "MultiPolygon"):
            # Orion-LD rejects non-standard GeoJSON members on a GeoProperty
            # (MeteoAlarm's geo+json carries `crs` → 400 BadRequestData
            # "Unexpected Field in value of GeoProperty"). Keep only canonical
            # {type, coordinates}.
            entity["location"] = {
                "type": "GeoProperty",
                "value": {
                    "type": geometry["type"],
                    "coordinates": geometry.get("coordinates", []),
                },
            }

        # Optional CAP metadata
        certainty = info.get("certainty")
        urgency = info.get("urgency")
        if certainty:
            entity["certainty"] = {"type": "Property", "value": certainty}
        if urgency:
            entity["urgency"] = {"type": "Property", "value": urgency}

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

        Splits the batch into chunks under Orion-LD's request payload limit
        (see `_MAX_UPSERT_BYTES`) and POSTs each. Attempts every chunk even if
        one fails (so as much data as possible lands); returns True only if all
        chunks succeeded.
        """
        if not entities:
            return True

        all_ok = True
        for chunk in _chunk_by_size(entities, _MAX_UPSERT_BYTES):
            if not self._post_upsert_chunk(tenant_id, chunk):
                all_ok = False
        return all_ok

    def _post_upsert_chunk(self, tenant_id: str, entities: List[Dict[str, Any]]) -> bool:
        """POST a single upsert chunk to Orion-LD.

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
            elif resp.status_code == 207:
                body = resp.json() if resp.content else {}
                errors = body.get("errors", []) if isinstance(body, dict) else []
                if errors:
                    logger.error(
                        f"UPSERT batch partial failure: {len(errors)} of "
                        f"{len(entities)} entities failed — {str(errors[:2])[:400]}"
                    )
                    return False
                logger.debug(
                    f"UPSERT batch: {len(entities)} entities → Orion-LD OK (207)"
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

    def _prune_expired_alerts(self, tenant_id: str, batch_size: int = 200) -> int:
        """Delete WeatherAlert entities whose validTo is in the past.

        MeteoAlarm UPSERT only updates live alerts; expired rows accumulate in
        tenant 'default' unless explicitly removed.
        """
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        deleted = 0

        while True:
            headers = _make_headers(tenant_id)
            headers["Accept"] = "application/json"
            try:
                resp = self._session.get(
                    f"{self.orion_url}/ngsi-ld/v1/entities",
                    params={
                        "type": "WeatherAlert",
                        "q": f'validTo<"{now_iso}"',
                        "limit": batch_size,
                    },
                    headers=headers,
                    timeout=60,
                )
            except Exception as e:
                logger.warning(f"MeteoAlertsEngine: expired-alert query failed: {e}")
                break

            if resp.status_code != 200:
                logger.warning(
                    f"MeteoAlertsEngine: expired-alert query HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                break

            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break

            ids = [e["id"] for e in batch if e.get("id")]
            if not ids:
                break

            del_headers = _make_headers(tenant_id)
            del_headers["Content-Type"] = "application/json"
            try:
                del_resp = self._session.post(
                    f"{self.orion_url}/ngsi-ld/v1/entityOperations/delete",
                    json=ids,
                    headers=del_headers,
                    timeout=120,
                )
            except Exception as e:
                logger.warning(f"MeteoAlertsEngine: expired-alert delete failed: {e}")
                break

            if del_resp.status_code not in (200, 204):
                logger.warning(
                    f"MeteoAlertsEngine: expired-alert delete HTTP "
                    f"{del_resp.status_code}: {del_resp.text[:200]}"
                )
                break

            deleted += len(ids)
            if len(batch) < batch_size:
                break

        return deleted

    # ------------------------------------------------------------------
    # MQTT handler (WIS 2.0 push ingestion)
    # ------------------------------------------------------------------

    _MQTT_DEDUP_MAX = 20000

    def handle_notification(self, notification: dict) -> bool:
        """Process a single WIS2 notification from the MQTT stream.

        Deduplicates by ``(alertId, hubTime)``, resolves CAP-JSON
        attributes and GeoJSON geometry from pre-signed archive links,
        builds/upserts a ``WeatherAlert`` entity, and deletes any
        entities referenced in ``referencedAlertIds`` (supersede).

        Returns ``True`` on success or benign skip; ``False`` if the
        notification is malformed or detail fetches fail.
        """
        from weather_worker.edr_client import _find_link

        props = notification.get("properties", {})
        alert_id = props.get("alertId")
        if not alert_id:
            logger.debug("MeteoAlertsEngine: notification missing alertId — skipped")
            return False

        # Dedup: same alertId at the same hubTime is a repeat.
        hub_time = props.get("hubTime")
        dedup_key = (alert_id, hub_time) if hub_time else (alert_id, props.get("pubtime", ""))
        if dedup_key in self._dedup_set:
            logger.debug(
                "MeteoAlertsEngine: duplicate notification %s @ %s", alert_id, hub_time,
            )
            return True

        # Resolve detail links from the pre-signed archive (no auth).
        cap_href = _find_link(notification, "application/json")
        geo_href = _find_link(notification, "application/geo+json")

        if not cap_href:
            logger.debug("MeteoAlertsEngine: notification %s has no CAP-JSON link", alert_id)
            return False

        cap = self._client._get_detail(cap_href)
        if cap is None:
            logger.debug("MeteoAlertsEngine: notification %s CAP fetch failed", alert_id)
            return False

        geometry = None
        if geo_href:
            geo_feature = self._client._get_detail(geo_href)
            if isinstance(geo_feature, dict):
                geometry = geo_feature.get("geometry")

        entity = self._build_single_entity(cap, notification, geometry)
        if entity:
            ok = self._upsert_batch("default", [entity])
            if ok:
                logger.info(
                    "MeteoAlertsEngine: MQTT upserted WeatherAlert %s (%s)",
                    entity["id"],
                    (entity.get("subCategory", {}).get("value", ["?"]) or ["?"])[0],
                )
        else:
            logger.debug("MeteoAlertsEngine: notification %s produced no entity", alert_id)

        # Register the dedup key AFTER processing (so a retry-on-failure
        # could reprocess — safe because UPSERT is idempotent).
        self._dedup_keys.append(dedup_key)
        self._dedup_set.add(dedup_key)
        while len(self._dedup_keys) > self._MQTT_DEDUP_MAX:
            oldest = self._dedup_keys.popleft()
            self._dedup_set.discard(oldest)

        # Supersede: delete entities referenced by the new alert revision.
        for ref in props.get("referencedAlertIds") or []:
            if ref and isinstance(ref, str):
                self._delete_entity(
                    "default", f"urn:ngsi-ld:WeatherAlert:meteoalarm:{ref}"
                )

        return True

    def prune_once(self) -> int:
        """Delete expired WeatherAlert entities from tenant 'default'.

        Thin wrapper so prune keeps running while the EDR poll loop is
        disabled (EDR_ENABLED=false).
        """
        pruned = self._prune_expired_alerts("default")
        if pruned:
            logger.info(
                "MeteoAlertsEngine: pruned %d expired WeatherAlert entities",
                pruned,
            )
        return pruned

    def _delete_entity(self, tenant_id: str, entity_id: str) -> bool:
        """DELETE a single entity from Orion-LD.

        Returns ``True`` on 204 (deleted) or 404 (already gone);
        ``False`` on any other HTTP status or transport error.
        """
        headers = _make_headers(tenant_id)
        try:
            resp = self._session.delete(
                f"{self.orion_url}/ngsi-ld/v1/entities/{entity_id}",
                headers=headers,
                timeout=30,
            )
            if resp.status_code in (204, 404):
                if resp.status_code == 404:
                    logger.debug(
                        "MeteoAlertsEngine: delete skipped — %s not found (404)",
                        entity_id,
                    )
                return True
            logger.error(
                "MeteoAlertsEngine: delete %s failed: HTTP %d — %s",
                entity_id,
                resp.status_code,
                resp.text[:300],
            )
            return False
        except Exception as e:
            logger.error("MeteoAlertsEngine: delete %s error: %s", entity_id, e)
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
# Internal helpers
# ---------------------------------------------------------------------------


def _select_english_info(info_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the English-language ``info`` block from a CAP info array.

    Falls back to the first block if no ``language`` starts with ``en``.
    Using the English block is essential because CAP ``event`` strings are
    localized (de-DE "GEWITTER", tr "gök gürültülü", …) and the
    ``_normalize_event`` substring matcher only understands English.
    """
    if not info_list:
        return {}
    for block in info_list:
        if isinstance(block, dict) and (block.get("language") or "").startswith("en"):
            return block
    return info_list[0] if isinstance(info_list[0], dict) else {}
