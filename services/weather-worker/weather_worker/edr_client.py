"""EdrWarningsClient — pure-HTTP client for MeteoAlarm EDR API.

Fetches warning index pages (GeoJSON FeatureCollection) from the EDR
`/collections/warnings/locations/ALL` endpoint and resolves detail
payloads (CAP-JSON attributes + GeoJSON geometry).

No Orion-LD knowledge; used by MeteoAlertsEngine in a separate module.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# EDR API constants
_DEFAULT_BASE_URL = "https://api.meteoalarm.org/edr/v1"
_DEFAULT_SENT_WINDOW_HOURS = 23
_DEFAULT_ACTIVE_WINDOW_HOURS = 6
_PAGE_SIZE = 100  # EDR fixed page size

# Hosts whose detail links are pre-signed and must NOT carry our Bearer token.
# Adding an Authorization header to a pre-signed S3 / DigitalOcean Spaces URL
# causes a 400 (signature mismatch).
_ARCHIVE_HOSTS = {"meteo.fra1.digitaloceanspaces.com"}


class EdrWarningsClient:
    """HTTP client for the MeteoAlarm EDR warnings API.

    Pure HTTP — no Orion knowledge, no FIWARE headers.  Configured with
    the EDR base URL and a MeteoAlarm API key (Bearer token).  All calls
    are fail-safe: an HTTP error on a page or detail is logged and skipped;
    the caller (MeteoAlertsEngine) never sees an exception from here.
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        sent_window_hours: int = _DEFAULT_SENT_WINDOW_HOURS,
        active_window_hours: int = _DEFAULT_ACTIVE_WINDOW_HOURS,
        session: Optional[requests.Session] = None,
        timeout: int = 30,
    ):
        self._base = base_url or _DEFAULT_BASE_URL
        self._api_key = api_key
        self._sent_window_hours = sent_window_hours
        self._active_window_hours = active_window_hours
        self._session = session or requests.Session()
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def iter_index(self, now: Optional[datetime] = None) -> Generator[Dict[str, Any], None, None]:
        """Yield every *active* warning index feature (GeoJSON Feature dict).

        Pages through `GET /collections/warnings/locations/ALL` with
        ``datetime`` (sent window) + ``active`` (validity window) filters.
        Stops when a page returns fewer than ``_PAGE_SIZE`` features or
        when an HTTP/page-parsing error occurs (logged).

        The ``active`` window is a **closed** interval (``now/now+active``);
        open-ended intervals (``now/..``) cause a 400 from the EDR API.
        """
        dt_str, act_str = self._build_intervals(now or datetime.now(timezone.utc))
        page = 1
        while True:
            features = self._get_page(dt_str, act_str, page)
            if not features:
                break
            yield from features
            if len(features) < _PAGE_SIZE:
                break
            page += 1

    def fetch_detail(
        self, feature: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Return (cap_dict, geometry) for a warning feature, or (None, None).

        Follows two links from the index feature's ``links`` array:
        * ``application/json`` → CAP-JSON payload (attributes).
        * ``application/geo+json`` → GeoJSON Feature with the exact polygon.

        Archive links (pre-signed DigitalOcean Spaces URLs) are fetched
        **without** our Bearer token — adding it would cause a 400.
        API-host links (``api.meteoalarm.org``) carry the Bearer token.
        """
        # Resolve links
        json_href = _find_link(feature, "application/json")
        geo_href = _find_link(feature, "application/geo+json")

        cap = self._get_detail(json_href) if json_href else None
        geo = self._get_detail(geo_href) if geo_href else None
        # geo detail wraps the polygon in a Feature; extract the geometry
        if geo and isinstance(geo, dict):
            geo = geo.get("geometry", geo)

        return cap, geo

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_intervals(self, now: datetime) -> Tuple[str, str]:
        """Return ``(datetime_interval, active_interval)`` as closed ISO8601 strings."""
        sent_start = now - timedelta(hours=self._sent_window_hours)
        active_end = now + timedelta(hours=self._active_window_hours)

        def _fmt(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return f"{_fmt(sent_start)}/{_fmt(now)}", f"{_fmt(now)}/{_fmt(active_end)}"

    def _get_page(
        self, dt_interval: str, act_interval: str, page: int
    ) -> List[Dict[str, Any]]:
        """Fetch one index page. Returns a list of feature dicts (empty on error)."""
        url = f"{self._base}/collections/warnings/locations/ALL"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        params: Dict[str, Any] = {
            "f": "json",
            "datetime": dt_interval,
            "active": act_interval,
            "page": page,
        }
        try:
            resp = self._session.get(
                url, params=params, headers=headers, timeout=self._timeout
            )
        except Exception as e:
            logger.warning("EdrWarningsClient: page %d request failed: %s", page, e)
            return []

        if resp.status_code != 200:
            logger.warning(
                "EdrWarningsClient: page %d HTTP %d — %s",
                page,
                resp.status_code,
                resp.text[:300],
            )
            return []

        try:
            body = resp.json()
        except Exception as e:
            logger.warning("EdrWarningsClient: page %d JSON parse error: %s", page, e)
            return []

        features = body.get("features", []) if isinstance(body, dict) else []
        logger.debug("EdrWarningsClient: page %d returned %d features", page, len(features))
        return features

    def _get_detail(self, href: str) -> Optional[Dict[str, Any]]:
        """GET a single detail URL. Returns parsed JSON or None on any failure."""
        headers = _detail_headers(self._api_key, href)
        try:
            resp = self._session.get(href, headers=headers, timeout=self._timeout)
        except Exception as e:
            logger.debug("EdrWarningsClient: detail fetch failed (%s): %s", href[:120], e)
            return None

        if resp.status_code != 200:
            logger.debug(
                "EdrWarningsClient: detail HTTP %d for %s — %s",
                resp.status_code,
                href[:120],
                resp.text[:200],
            )
            return None

        try:
            return resp.json()
        except Exception as e:
            logger.debug("EdrWarningsClient: detail JSON parse error: %s", e)
            return None


# ---------------------------------------------------------------------------
# Internal helpers (module-level, overridable for testing)
# ---------------------------------------------------------------------------


def _find_link(feature: Dict[str, Any], media_type: str) -> Optional[str]:
    """Return the href of the first link with the given ``type``."""
    for link in feature.get("links", []):
        if isinstance(link, dict) and link.get("type") == media_type:
            return link.get("href")
    return None


def _detail_headers(api_key: str, href: str) -> Dict[str, str]:
    """Headers for a detail fetch: Bearer only if the host is NOT an archive host."""
    from urllib.parse import urlparse

    host = (urlparse(href).hostname or "").lower()
    if host in _ARCHIVE_HOSTS:
        return {}  # pre-signed → no auth
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}
