"""EMMA awareness-zone geometry index.

Indexes each EMMA awareness zone's Polygon/MultiPolygon geometry by its EMMA id,
so WeatherAlert entities can carry a `location` GeoProperty for spatial matching
against parcels. The MeteoAlarm legacy Atom feed only provides the EMMA id per
alert (no polygon), so geometry comes from the MeteoAlarm Metadata API (keyed by
EMMA id) — see internal docs 2026-07-27-emma-zone-source.md.

Two source modes (both keep the API key out of the repo):
  - file path  → a bundled/pre-fetched GeoJSON FeatureCollection.
  - https URL  → the Metadata API, fetched at runtime with `api_key`
    (Authorization: Bearer). The response is normalised via
    `normalize_zone_payload`.
"""

import json
import logging
import os
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# GeoJSON/region property that may hold the EMMA id (first present wins).
_ID_PROPS = ("emma_id", "code", "EMMA_ID", "id")


def _emma_id(props: dict) -> Optional[str]:
    return next((str(props[k]) for k in _ID_PROPS if props.get(k)), None)


def _feature(zone_id: str, geom: dict, name: str = "") -> dict:
    return {
        "type": "Feature",
        "properties": {"emma_id": zone_id, "name": name},
        "geometry": geom,
    }


def normalize_zone_payload(payload) -> dict:
    """Normalise a zones payload into a GeoJSON FeatureCollection keyed by
    `properties.emma_id`. Accepts three shapes (Metadata API responses vary):
      1. an already-normalised GeoJSON FeatureCollection,
      2. a bare list of region objects each carrying a geometry,
      3. a {"regions": [...]} wrapper.
    Raises ValueError if no Polygon/MultiPolygon zone geometries are found.
    Shared by the runtime loader and scripts/fetch_emma_zones.py.
    """
    def _is_area(geom) -> bool:
        return isinstance(geom, dict) and geom.get("type") in ("Polygon", "MultiPolygon")

    # 1. FeatureCollection.
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        feats = []
        for f in payload.get("features", []):
            if not isinstance(f, dict):
                continue
            props = f.get("properties") or {}
            zid, geom = _emma_id(props), f.get("geometry")
            if zid and _is_area(geom):
                feats.append(_feature(zid, geom, props.get("name", "")))
        if feats:
            return {"type": "FeatureCollection", "features": feats}

    # 2/3. list of regions, or {"regions": [...]}.
    regions = payload if isinstance(payload, list) else (
        payload.get("regions") if isinstance(payload, dict) else None
    )
    if isinstance(regions, list):
        feats = []
        for r in regions:
            if not isinstance(r, dict):
                continue
            zid = _emma_id(r)
            geom = r.get("geometry") or r.get("geom")
            if zid and _is_area(geom):
                feats.append(_feature(zid, geom, r.get("name", "")))
        if feats:
            return {"type": "FeatureCollection", "features": feats}

    raise ValueError("Could not locate zone geometries in the payload")


class EmmaZoneIndex:
    """emma_id -> GeoJSON geometry, from a file path or an authenticated URL."""

    def __init__(self, source: str = "", api_key: str = "", refresh_hours: float = 24.0):
        self._source = source
        self._api_key = api_key
        self._refresh_seconds = refresh_hours * 3600
        self._by_id: Dict[str, dict] = {}
        self._loaded_at = 0.0
        if source:
            self._safe_load()

    def _is_url(self) -> bool:
        return self._source.startswith("http://") or self._source.startswith("https://")

    def _safe_load(self) -> None:
        try:
            self._load()
            self._loaded_at = time.monotonic()
        except Exception as e:  # non-fatal: alerts simply get no location
            logger.warning("EmmaZoneIndex: could not load %s: %s", self._source, e)

    def _load(self) -> None:
        if self._is_url():
            headers = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            resp = requests.get(self._source, headers=headers, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        elif os.path.exists(self._source):
            with open(self._source, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        else:
            logger.warning("EmmaZoneIndex: source not found: %s", self._source)
            return

        fc = normalize_zone_payload(payload)
        by_id: Dict[str, dict] = {}
        for feat in fc["features"]:
            zid = feat["properties"].get("emma_id")
            if zid:
                by_id[zid] = feat["geometry"]
        self._by_id = by_id
        logger.info("EmmaZoneIndex: indexed %d zones", len(self._by_id))

    def maybe_refresh(self) -> None:
        """Reload from a URL source once the refresh interval has elapsed.

        No-op for file sources (bundled data never changes at runtime) and when
        the interval has not passed. Failures keep the existing index (fail-safe).
        """
        if not self._source or not self._is_url():
            return
        if time.monotonic() - self._loaded_at < self._refresh_seconds:
            return
        logger.info("EmmaZoneIndex: refreshing zones from %s", self._source)
        self._safe_load()

    def geometry_for(self, emma_id: str) -> Optional[dict]:
        if not emma_id:
            return None
        return self._by_id.get(emma_id)
