"""EMMA awareness-zone geometry index.

Loads the MeteoAlarm/EUMETNET awareness-zone GeoJSON and indexes each zone's
Polygon/MultiPolygon geometry by its EMMA id, so WeatherAlert entities can carry
a `location` GeoProperty for spatial matching against parcels.

The legacy MeteoAlarm Atom feed only provides the EMMA id per alert (no polygon),
so the geometry comes from this separate dataset (see
internal docs 2026-07-27-emma-zone-source.md).
"""

import json
import logging
import os
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# GeoJSON feature property that may hold the EMMA id (first present wins).
_ID_PROPS = ("emma_id", "code", "EMMA_ID", "id")


class EmmaZoneIndex:
    """emma_id -> GeoJSON geometry, loaded once from a file path or URL."""

    def __init__(self, source: str = ""):
        self._by_id: Dict[str, dict] = {}
        if source:
            try:
                self._load(source)
            except Exception as e:  # non-fatal: alerts simply get no location
                logger.warning("EmmaZoneIndex: could not load %s: %s", source, e)

    def _load(self, source: str) -> None:
        if source.startswith("http://") or source.startswith("https://"):
            resp = requests.get(source, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        elif os.path.exists(source):
            with open(source, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            logger.warning("EmmaZoneIndex: source not found: %s", source)
            return

        features = data.get("features", []) if isinstance(data, dict) else []
        for feat in features:
            if not isinstance(feat, dict):
                continue
            props = feat.get("properties") or {}
            geom = feat.get("geometry")
            if not isinstance(geom, dict) or geom.get("type") not in (
                "Polygon",
                "MultiPolygon",
            ):
                continue
            zone_id = next(
                (str(props[k]) for k in _ID_PROPS if props.get(k)), None
            )
            if zone_id:
                self._by_id[zone_id] = geom
        logger.info("EmmaZoneIndex: indexed %d zones", len(self._by_id))

    def geometry_for(self, emma_id: str) -> Optional[dict]:
        if not emma_id:
            return None
        return self._by_id.get(emma_id)
