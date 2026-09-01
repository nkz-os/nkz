"""
Parcel Weather Engine — parcel-driven weather ingestion.

Iterates over AgriParcel entities from Orion-LD, downloads weather for each
parcel's exact coordinates from Open-Meteo, applies spatial downscaling
(altitude, aspect, slope), and writes WeatherObserved entities 1:1 per parcel.

This is the AGRONOMIC ENGINE — the heart of the platform. It does NOT read
tenant_weather_locations. It does NOT write to PostgreSQL UI tables.
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Path hacks for in-pod execution
sys_paths = ["/app", "/app/weather-worker"]
for p in sys_paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from common.ngsi_headers import inject_fiware_headers


def _observation_day(observed_at: Any) -> Optional[str]:
    """Return the YYYY-MM-DD day of an observation, or None if it has none.

    Open-Meteo dates the daily block as "2026-09-01"; anything that does not parse
    as a calendar day is unusable, and no day is substituted for it.
    """
    if hasattr(observed_at, "strftime"):
        return observed_at.strftime("%Y-%m-%d")
    if not isinstance(observed_at, str):
        return None
    try:
        return datetime.strptime(observed_at[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _elevation_request_headers() -> dict[str, str]:
    secret = os.getenv("INTERNAL_SERVICE_SECRET", "").strip()
    if secret:
        return {"X-Internal-Service-Secret": secret}
    return {}


class ParcelWeatherEngine:
    """Parcel-driven weather ingestion engine.

    Discovers AgriParcel entities from Orion-LD (source of truth),
    downloads weather for each parcel's exact location, applies
    spatial downscaling, and persists WeatherObserved entities.
    """

    def __init__(
        self,
        orion_url: str = "",
        openmeteo_url: str = "",
        postgres_url: str = "",
        forecast_days: int = 14,
        cluster_radius_km: float = 2.0,
        max_parcels: int = 500,
        context_url: str = "",
    ):
        self.orion_url = orion_url or os.getenv(
            "ORION_URL", "http://orion-ld-service:1026"
        )
        self.openmeteo_url = openmeteo_url or os.getenv(
            "OPENMETEO_API_URL", "https://api.open-meteo.com/v1"
        )
        self.postgres_url = postgres_url or os.getenv("POSTGRES_URL", "")
        self.forecast_days = forecast_days
        self.cluster_radius_km = cluster_radius_km
        self.max_parcels = max_parcels
        self.context_url = context_url or os.getenv("CONTEXT_URL", "")

    # ------------------------------------------------------------------
    # Orion-LD helpers
    # ------------------------------------------------------------------

    def _make_headers(self, tenant_id: str, body: object = None) -> dict:
        """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated).

        ``body`` selects the @context delivery mode from what is actually posted:
        a payload carrying @context goes as application/ld+json with NO Link header,
        anything else as application/json + Link. Sending both is a 400 on every
        request, so the mode is derived from the payload and cannot drift from it.
        """
        if body is not None:
            return inject_fiware_headers({}, tenant=tenant_id, body=body)
        return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)

    def _discover_tenants_from_db(self) -> List[str]:
        """Discover active tenants from the admin platform database.

        Queries tenant_installed_modules for all tenants that have the weather module.
        Uses POSTGRES_URL or individual POSTGRES_* env vars.
        """
        try:
            postgres_url = os.getenv("POSTGRES_URL", "").strip()
            if not postgres_url:
                host = os.getenv("POSTGRES_HOST", "postgresql-service")
                port = os.getenv("POSTGRES_PORT", "5432")
                db = os.getenv("POSTGRES_DB", "nekazari")
                user = os.getenv("POSTGRES_USER", "postgres")
                password = os.getenv("POSTGRES_PASSWORD", "")
                if not password:
                    logger.debug("POSTGRES_PASSWORD not set, skipping DB tenant discovery")
                    return []
                postgres_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
            import psycopg2
            conn = psycopg2.connect(postgres_url)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT DISTINCT tenant_id FROM tenant_installed_modules "
                    "WHERE module_id = 'weather' AND is_enabled = true "
                    "AND tenant_id IS NOT NULL AND tenant_id != '' "
                    "AND tenant_id != 'platform' "
                    "ORDER BY tenant_id"
                )
                tenants = [row[0] for row in cur.fetchall()]
                cur.close()
                if tenants:
                    logger.info(
                        f"Discovered {len(tenants)} tenants from DB: {tenants}"
                    )
                return tenants
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Could not discover tenants from DB: {e}")
            return []

    def _get_active_tenants(self) -> List[str]:
        """Get all active tenant IDs.

        Priority:
        1. PARCEL_ENGINE_TENANTS env var (comma-separated)
        2. Discover from admin platform DB (tenant_installed_modules with weather module)
        3. Fallback to ['default']

        Note: there is no "parse tenant from entity id" path. Uniform entity writes
        use ``urn:ngsi-ld:AgriParcel:<uuid4>`` ids that carry no tenant segment;
        tenant discovery is DB-backed + per-tenant ``NGSILD-Tenant`` queries.
        """
        # Priority 1: explicit env var
        env_tenants = os.getenv("PARCEL_ENGINE_TENANTS", "").strip()
        if env_tenants:
            return [t.strip() for t in env_tenants.split(",") if t.strip()]

        # Priority 2: discover from admin platform DB
        db_tenants = self._discover_tenants_from_db()
        if db_tenants:
            return db_tenants

        # Priority 3: fallback
        return ["default"]

    def _fetch_all_parcels(self) -> List[Dict[str, Any]]:
        """Fetch all AgriParcel entities across all active tenants.

        Returns list of parcel dicts, each tagged with `_tenant`.
        """
        all_parcels: List[Dict[str, Any]] = []
        tenant_ids = self._get_active_tenants()

        for tid in tenant_ids:
            try:
                headers = self._make_headers(tid)
                params = {
                    "type": "AgriParcel",
                    "attrs": "location,name,elevation,terrainAspect,terrainSlope,cropStatus",
                    "limit": min(self.max_parcels, 1000),
                }
                url = f"{self.orion_url}/ngsi-ld/v1/entities"
                resp = requests.get(
                    url, params=params, headers=headers, timeout=15
                )

                if resp.status_code == 200:
                    entities = resp.json()
                    if isinstance(entities, list):
                        for entity in entities:
                            entity["_tenant"] = tid
                        all_parcels.extend(entities)
                        logger.info(
                            f"Found {len(entities)} parcels for tenant {tid}"
                        )
                elif resp.status_code == 404:
                    logger.debug(f"No parcels for tenant {tid}")
                else:
                    logger.warning(
                        f"Orion-LD returned {resp.status_code} for tenant {tid}"
                    )
            except Exception as e:
                logger.warning(f"Error fetching parcels for tenant {tid}: {e}")

        logger.info(f"Total parcels discovered: {len(all_parcels)}")
        return all_parcels

    def _prune_orphan_weather_observed(self, tenant_id: str) -> int:
        """Delete WeatherObserved entities whose parcel no longer exists.

        Safe by construction: each WeatherObserved is 1:1 with a parcel
        (id ``...:parcel-<id>``, ``locatedAt`` -> parcel), so deleting an orphan
        never affects a live parcel. False-zero guard: never prune when
        ``CONTEXT_URL`` is unset or the live-parcel query is not HTTP 200 — a
        context-less or failed AgriParcel query returns a FALSE empty set, which
        would otherwise delete every WeatherObserved in the tenant.
        """
        if not self.context_url:
            logger.warning(
                "Prune skipped for %s: CONTEXT_URL unset (a context-less "
                "AgriParcel query false-zeros -> would delete everything).",
                tenant_id,
            )
            return 0

        headers = self._make_headers(tenant_id)
        base = f"{self.orion_url}/ngsi-ld/v1/entities"

        # 1. Live parcel ids — skip prune on ANY non-200 (false-zero guard)
        try:
            rp = requests.get(
                base, params={"type": "AgriParcel", "options": "keyValues", "limit": 1000},
                headers=headers, timeout=15,
            )
        except Exception as e:
            logger.warning("Prune %s: parcel query failed (%s) — skip", tenant_id, e)
            return 0
        if rp.status_code != 200 or not isinstance(rp.json(), list):
            logger.warning("Prune %s: parcel query %s — skip", tenant_id, rp.status_code)
            return 0
        live = {p["id"] for p in rp.json() if p.get("id")}

        # 2. WeatherObserved for the tenant
        try:
            rw = requests.get(
                base, params={"type": "WeatherObserved", "options": "keyValues", "limit": 1000},
                headers=headers, timeout=15,
            )
        except Exception as e:
            logger.warning("Prune %s: WeatherObserved query failed (%s) — skip", tenant_id, e)
            return 0
        if rw.status_code != 200 or not isinstance(rw.json(), list):
            logger.warning("Prune %s: WeatherObserved query %s — skip", tenant_id, rw.status_code)
            return 0

        # 3. Orphans: locatedAt parcel not in the live set
        orphans = []
        for wo in rw.json():
            loc = wo.get("locatedAt")
            ref = loc.get("object") if isinstance(loc, dict) else loc
            if ref and ref not in live and wo.get("id"):
                orphans.append(wo["id"])
        if not orphans:
            return 0

        # 4. Batch delete (NGSI-LD entityOperations/delete)
        del_headers = dict(headers)
        del_headers["Content-Type"] = "application/json"
        deleted = 0
        for i in range(0, len(orphans), 100):
            chunk = orphans[i:i + 100]
            try:
                resp = requests.post(
                    f"{self.orion_url}/ngsi-ld/v1/entityOperations/delete",
                    json=chunk, headers=del_headers, timeout=15,
                )
                if resp.status_code in (200, 204, 207):
                    deleted += len(chunk)
                    logger.info(
                        "Prune %s: deleted %d orphan WeatherObserved", tenant_id, len(chunk)
                    )
                else:
                    logger.warning("Prune %s: delete -> %s", tenant_id, resp.status_code)
            except Exception as e:
                logger.warning("Prune %s: delete failed (%s)", tenant_id, e)
        return deleted

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _extract_centroid_and_altitude(
        self, parcel: Dict[str, Any]
    ) -> Tuple[Optional[Tuple[float, float]], float]:
        """Extract (centroid, altitude) from an AgriParcel entity.

        Handles Point and Polygon geometries via Shapely centroid.
        Falls back to elevation service if no altitude stored in entity.
        Returns (None, 0.0) if location is unresolvable.
        """
        # Altitude from entity (already stored during parcel creation from
        # IGN layers or user input). No fallback to external elevation APIs
        # — the eu-elevation module is optional and should not be required
        # for basic weather ingestion.
        altitude = 0.0
        has_explicit_elevation = False
        elev = parcel.get("elevation", {})
        elev_value = elev.get("value", 0) if isinstance(elev, dict) else 0
        if elev_value:
            try:
                altitude = float(elev_value)
                has_explicit_elevation = True
            except (ValueError, TypeError):
                altitude = 0.0

        # Location
        loc_attr = parcel.get("location", {})
        if not isinstance(loc_attr, dict):
            return None, altitude

        loc_value = loc_attr.get("value", loc_attr)
        if not isinstance(loc_value, dict):
            return None, altitude

        geom_type = loc_value.get("type", "")
        coords = loc_value.get("coordinates", [])

        centroid = None

        if geom_type == "Point" and len(coords) >= 2:
            centroid = (float(coords[0]), float(coords[1]))
        elif geom_type in ("Polygon", "MultiPolygon") and coords:
            try:
                from shapely.geometry import shape  # lazy import
                shapely_geom = shape(loc_value)
                c = shapely_geom.centroid
                centroid = (c.x, c.y)
            except Exception as e:
                logger.warning(f"Error computing centroid: {e}")

        # Optional: query elevation service only if explicitly configured.
        # Core parcels should store elevation in the entity (from IGN, user
        # input, or terrain layers). Open-Meteo works fine without altitude.
        elev_service_url = os.getenv("ELEVATION_SERVICE_URL", "").strip()
        if not has_explicit_elevation and elev_service_url and centroid is not None:
            try:
                resp = requests.get(
                    f"{elev_service_url}/api/elevation/point",
                    params={"lat": centroid[1], "lon": centroid[0], "purpose": "weather"},
                    headers=_elevation_request_headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    altitude = float(data.get("elevation_m", 0))
                    logger.info(
                        "Fetched elevation %.1fm for parcel %s from elevation service",
                        altitude, parcel.get("id", "unknown")
                    )
            except Exception as e:
                logger.warning("Failed to fetch elevation for parcel %s: %s",
                               parcel.get("id", "unknown"), e)

        return centroid, altitude

    # ------------------------------------------------------------------
    # Terrain attributes (aspect, slope) from elevation service
    # ------------------------------------------------------------------

    def _compute_terrain_attributes(
        self,
        centroid: Tuple[float, float],
    ) -> Tuple[float, float]:
        """Compute slope (deg) and aspect (deg) from elevation-api 5-point query."""
        import math

        elev_url = os.getenv("ELEVATION_SERVICE_URL", "").strip()
        if not elev_url:
            return 0.0, 0.0

        cx, cy = centroid  # lon, lat
        d_lat = 30.0 / 111320.0
        d_lon = 30.0 / (111320.0 * 0.766)  # cos(40°)

        points = {
            "center": (cy, cx),
            "north":  (cy + d_lat, cx),
            "south":  (cy - d_lat, cx),
            "east":   (cy, cx + d_lon),
            "west":   (cy, cx - d_lon),
        }

        elevations: dict = {}
        for name, (lat, lon) in points.items():
            try:
                resp = requests.get(
                    f"{elev_url}/api/elevation/point",
                    params={"lat": round(lat, 6), "lon": round(lon, 6), "purpose": "weather"},
                    headers=_elevation_request_headers(),
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    elevations[name] = float(data.get("elevation_m", 0))
            except Exception:
                pass

        if len(elevations) < 3:
            return 0.0, 0.0

        center_z = elevations.get("center", 0.0)
        n = elevations.get("north", center_z)
        s = elevations.get("south", center_z)
        e = elevations.get("east", center_z)
        w = elevations.get("west", center_z)

        cell_size_m = 30.0
        dz_dx = (e - w) / (2.0 * cell_size_m)
        dz_dy = (n - s) / (2.0 * cell_size_m)

        slope_rad = math.atan(math.sqrt(dz_dx * dz_dx + dz_dy * dz_dy))
        slope_deg = round(math.degrees(slope_rad), 1)

        if slope_deg < 0.5:
            aspect_deg = 0.0
        else:
            aspect_rad = math.atan2(-dz_dx, -dz_dy)
            aspect_deg = round((math.degrees(aspect_rad) + 360) % 360, 1)

        return aspect_deg, slope_deg

    def _persist_terrain_attributes(
        self,
        parcel: Dict[str, Any],
        aspect_deg: float,
        slope_deg: float,
        altitude: float,
    ):
        """Persist terrainAspect, terrainSlope, elevation to AgriParcel.

        Uses upsert with merger: fetches existing entity to preserve all
        attributes (name, location, category, etc.) while adding terrain.
        """
        parcel_id = parcel.get("id", "")
        tenant_id = parcel.get("_tenant", "default")
        if not parcel_id:
            return

        try:
            hdrs = self._make_headers(tenant_id)
            hdrs["Content-Type"] = "application/ld+json"
            hdrs.pop("Link", None)

            # Fetch existing entity to merge with terrain attrs
            existing = {}
            try:
                fetch_hdrs = {k: v for k, v in hdrs.items()
                              if k != "Content-Type"}
                fetch_hdrs["Accept"] = "application/ld+json"
                r = requests.get(
                    f"{self.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
                    headers=fetch_hdrs, timeout=5,
                )
                if r.status_code == 200:
                    raw = r.json()
                    existing = {
                        k: v for k, v in raw.items()
                        if k not in ("@context", "id", "type")
                        and isinstance(v, dict) and "type" in v
                    }
            except Exception:
                pass

            body = existing.copy()
            body["@context"] = [self.context_url] if self.context_url else []
            body["id"] = parcel_id
            body["type"] = "AgriParcel"

            if altitude > 0:
                body["elevation"] = {
                    "type": "Property", "value": round(altitude, 1), "unitCode": "MTR"
                }
            if slope_deg >= 0:
                body["terrainSlope"] = {
                    "type": "Property", "value": slope_deg, "unitCode": "DD"
                }
            if aspect_deg >= 0:
                body["terrainAspect"] = {
                    "type": "Property", "value": aspect_deg, "unitCode": "DD"
                }

            resp = requests.post(
                f"{self.orion_url}/ngsi-ld/v1/entityOperations/upsert",
                headers=hdrs, json=[body], timeout=5,
            )
            if resp.status_code not in (200, 201, 204):
                logger.debug(f"Terrain persist: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Terrain persist failed: {e}")

    def _haversine_km(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Haversine distance in km."""
        import math

        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # ------------------------------------------------------------------
    # Spatial clustering
    # ------------------------------------------------------------------

    def _cluster_parcels(
        self, parcels: List[Dict[str, Any]], radius_km: float = 2.0
    ) -> List[List[Dict[str, Any]]]:
        """Group parcels into spatial clusters to minimize API calls.

        Simple greedy clustering: for each unassigned parcel, create a cluster
        and add all nearby parcels within radius_km.
        """
        if not parcels:
            return []

        # Enrich with centroid if not already present
        enriched = []
        for p in parcels:
            if "_centroid" not in p or "_altitude" not in p:
                centroid, altitude = self._extract_centroid_and_altitude(p)
                p["_centroid"] = centroid
                p["_altitude"] = altitude
            enriched.append(p)

        # Only cluster parcels with valid centroids
        valid = [p for p in enriched if p["_centroid"] is not None]
        if not valid:
            return []

        assigned: set = set()
        clusters: List[List[Dict[str, Any]]] = []

        for i, parcel in enumerate(valid):
            if i in assigned:
                continue

            cluster = [parcel]
            assigned.add(i)
            lon1, lat1 = parcel["_centroid"]

            for j, other in enumerate(valid):
                if j in assigned:
                    continue
                lon2, lat2 = other["_centroid"]
                dist = self._haversine_km(lat1, lon1, lat2, lon2)
                if dist <= radius_km:
                    cluster.append(other)
                    assigned.add(j)

            clusters.append(cluster)

        logger.info(
            f"Clustered {len(valid)} parcels into {len(clusters)} groups "
            f"(radius={radius_km}km)"
        )
        return clusters

    # ------------------------------------------------------------------
    # Open-Meteo data fetching
    # ------------------------------------------------------------------

    def _fetch_openmeteo(
        self, latitude: float, longitude: float
    ) -> Optional[Dict[str, Any]]:
        """Fetch current + forecast weather from Open-Meteo for a point.

        Returns a dict with the full Open-Meteo API response.
        """
        try:
            url = f"{self.openmeteo_url}/forecast"
            today = datetime.utcnow().strftime("%Y-%m-%d")
            end = (datetime.utcnow() + timedelta(days=self.forecast_days)).strftime(
                "%Y-%m-%d"
            )

            params = {
                "latitude": latitude,
                "longitude": longitude,
                "start_date": today,
                "end_date": end,
                "daily": [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "temperature_2m_mean",
                    "relative_humidity_2m_mean",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "et0_fao_evapotranspiration",
                    "shortwave_radiation_sum",
                    "soil_moisture_0_to_7cm_mean",
                    "soil_moisture_7_to_28cm_mean",
                    "surface_pressure_mean",
                ],
                # Hourly data — for accurate agronomic metrics (GDD, Delta-T,
                # disease pressure, frost hours) plus wind mean/max/gusts.
                "hourly": [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "precipitation",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                    "wind_direction_10m",
                ],
                "timezone": "Europe/Madrid",
            }

            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning(
                    f"Open-Meteo returned {resp.status_code}: {resp.text[:200]}"
                )
                return None

            data = resp.json()
            return data

        except Exception as e:
            logger.warning(f"Error fetching Open-Meteo: {e}")
            return None

    def _parse_openmeteo_response(
        self,
        data: Dict[str, Any],
        latitude: float,
        longitude: float,
    ) -> List[Dict[str, Any]]:
        """Parse Open-Meteo response into observation dicts.

        Uses daily for temperature/humidity/precip/ET0/pressure/soil.
        Uses hourly for wind — computes per-day aggregates from hourly data:
          - wind_speed_ms:     latest available hour mean (for current conditions)
          - wind_speed_max:    max of today's hourly means (for context)
          - wind_gusts_ms:     max of today's hourly gusts
          - wind_direction_deg: latest available hour direction
        """
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        if not dates:
            return []

        # Sin default: un 0.0 aquí declara nivel del mar y se propaga hasta
        # location[2]. Ausente debe seguir ausente.
        station_elevation = data.get("elevation")
        hourly = data.get("hourly", {})
        hourly_times = hourly.get("time", [])

        # Pre-group hourly data by date for per-day aggregation
        hourly_by_date: dict = {}
        if hourly_times:
            temps = hourly.get("temperature_2m", [])
            hums = hourly.get("relative_humidity_2m", [])
            precips = hourly.get("precipitation", [])
            wind_speeds = hourly.get("wind_speed_10m", [])
            wind_gusts = hourly.get("wind_gusts_10m", [])
            wind_dirs = hourly.get("wind_direction_10m", [])
            for hi, ht in enumerate(hourly_times):
                day = ht[:10]  # "2026-06-08"
                if day not in hourly_by_date:
                    hourly_by_date[day] = {
                        "temps": [],
                        "hums": [],
                        "precips": [],
                        "speeds": [],
                        "gusts": [],
                        "dirs": [],
                        "last_dir": None,
                    }
                hd = hourly_by_date[day]
                if hi < len(temps) and temps[hi] is not None:
                    hd["temps"].append(float(temps[hi]))
                if hi < len(hums) and hums[hi] is not None:
                    hd["hums"].append(float(hums[hi]))
                if hi < len(precips) and precips[hi] is not None:
                    hd["precips"].append(float(precips[hi]))
                if hi < len(wind_speeds) and wind_speeds[hi] is not None:
                    hd["speeds"].append(float(wind_speeds[hi]))
                if hi < len(wind_gusts) and wind_gusts[hi] is not None:
                    hd["gusts"].append(float(wind_gusts[hi]))
                if hi < len(wind_dirs) and wind_dirs[hi] is not None:
                    hd["dirs"].append(float(wind_dirs[hi]))
                    hd["last_dir"] = float(wind_dirs[hi])

        observations = []
        for i, date_str in enumerate(dates):
            # ── Daily (non-wind) parameters from daily endpoint ──
            obs: dict = {
                "observed_at": date_str,
                "temp_min": self._safe_get(daily, "temperature_2m_min", i),
                "temp_max": self._safe_get(daily, "temperature_2m_max", i),
                "temp_avg": self._safe_get(daily, "temperature_2m_mean", i),
                "humidity_avg": self._safe_get(
                    daily, "relative_humidity_2m_mean", i
                ),
                "precip_mm": self._safe_get(daily, "precipitation_sum", i),
                "precip_probability": self._safe_get(
                    daily, "precipitation_probability_max", i
                ),
                "pressure_hpa": self._safe_get(
                    daily, "surface_pressure_mean", i
                ),
                "eto_mm": self._safe_get(
                    daily, "et0_fao_evapotranspiration", i
                ),
                "solar_rad_w_m2": self._div_if(
                    self._safe_get(daily, "shortwave_radiation_sum", i),
                    0.0864,  # MJ/m2/day → W/m2
                ),
                "soil_moisture_0_10cm": self._safe_get(
                    daily, "soil_moisture_0_to_7cm_mean", i
                ),
                "soil_moisture_10_40cm": self._safe_get(
                    daily, "soil_moisture_7_to_28cm_mean", i
                ),
                "station_elevation_m": station_elevation,
            }

            # ── Hourly wind → per-day aggregates ──
            hday = hourly_by_date.get(date_str, {})
            speeds = hday.get("speeds", [])
            gusts = hday.get("gusts", [])

            if speeds:
                # Open-Meteo hourly wind is in km/h — convert to m/s
                obs["wind_speed_ms"] = round(speeds[-1] / 3.6, 1)      # latest hour (current)
                obs["wind_speed_max"] = round(max(speeds) / 3.6, 1)    # max hourly mean today
                obs["wind_direction_deg"] = hday.get("last_dir")
            if gusts:
                obs["wind_gusts_ms"] = round(max(gusts) / 3.6, 1)

            # ── Hourly agronomic metrics (for crop-health / GDD / disease models) ──
            htemps = hday.get("temps", [])
            hhums = hday.get("hums", [])
            hprecips = hday.get("precips", [])

            if htemps:
                # Store current (latest hour) for Delta-T / spraying
                obs["temp_current"] = round(htemps[-1], 1)
                obs["humidity_current"] = round(hhums[-1], 1) if hhums else None
                # Growing Degree Days (base 10°C) from hourly integration
                gdd = sum(max(t - 10.0, 0.0) for t in htemps) / 24.0
                obs["gdd_accumulated"] = round(gdd, 1)
                # Delta-T from latest hour (wet-bulb depression)
                if hhums:
                    try:
                        from weather_utils.psychrometrics import calculate_delta_t
                        obs["delta_t"] = calculate_delta_t(htemps[-1], hhums[-1])
                    except ImportError:
                        logger.debug("psychrometrics not available, skipping delta_t")

            if hprecips:
                # Hourly precipitation sum (should match daily, but self-consistent)
                obs["precip_mm"] = round(sum(hprecips), 1)

            observations.append(obs)

        return observations

    @staticmethod
    def _safe_get(daily: dict, key: str, index: int) -> Optional[float]:
        """Safely get a value from Open-Meteo daily dict by index."""
        arr = daily.get(key, [])
        if arr and index < len(arr) and arr[index] is not None:
            return float(arr[index])
        return None

    @staticmethod
    def _div_if(value: Optional[float], divisor: float) -> Optional[float]:
        """Divide value by divisor if value is not None."""
        if value is None:
            return None
        return round(value / divisor, 2)

    # ------------------------------------------------------------------
    # Main engine loop
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """Execute one complete ingestion cycle — parcel-driven.

        Returns:
            Dict with counts: parcels_processed, observations_written,
            weather_observed_created, weather_observed_updated.
        """
        stats = {
            "parcels_discovered": 0,
            "parcels_processed": 0,
            "observations_written": 0,
            "weather_observed_created": 0,
            "weather_observed_updated": 0,
            "weather_forecast_written": 0,
            "clusters": 0,
            "weather_observed_pruned": 0,
            "errors": 0,
        }

        # Step 1: Discover parcels from Orion-LD
        logger.info("ParcelWeatherEngine: discovering AgriParcel entities...")
        raw_parcels = self._fetch_all_parcels()
        stats["parcels_discovered"] = len(raw_parcels)

        if not raw_parcels:
            logger.info("No parcels found — nothing to do")
            return stats

        # Step 2: Enrich with centroids and cluster
        enriched = []
        for p in raw_parcels:
            centroid, altitude = self._extract_centroid_and_altitude(p)
            if centroid is not None:
                p["_centroid"] = centroid
                p["_altitude"] = altitude
                enriched.append(p)
            else:
                logger.debug(
                    f"Skipping parcel {p.get('id')}: no resolvable location"
                )

        clusters = self._cluster_parcels(enriched, self.cluster_radius_km)
        stats["clusters"] = len(clusters)

        # Step 3: For each cluster, download weather and apply downscaling
        for ci, cluster in enumerate(clusters):
            try:
                # Use cluster centroid for Open-Meteo query
                cluster_lat = sum(
                    p["_centroid"][1] for p in cluster
                ) / len(cluster)
                cluster_lon = sum(
                    p["_centroid"][0] for p in cluster
                ) / len(cluster)

                logger.debug(
                    f"Cluster {ci + 1}/{len(clusters)}: "
                    f"{len(cluster)} parcels at ({cluster_lat:.4f}, {cluster_lon:.4f})"
                )

                # Fetch weather from Open-Meteo
                raw_data = self._fetch_openmeteo(cluster_lat, cluster_lon)
                if raw_data is None:
                    stats["errors"] += 1
                    continue

                # Parse into observation dicts
                station_elevation = raw_data.get("elevation")
                observations = self._parse_openmeteo_response(
                    raw_data, cluster_lat, cluster_lon
                )

                if not observations:
                    continue

                # For each parcel in cluster, apply downscaling and write
                for parcel in cluster:
                    try:
                        parcel_lon, parcel_lat = parcel["_centroid"]
                        parcel_altitude = parcel.get("_altitude", 0.0)

                        # Compute terrain aspect/slope from elevation service
                        # (best-effort, cached in AgriParcel for future cycles)
                        aspect, slope = self._compute_terrain_attributes(
                            (parcel_lon, parcel_lat)
                        )
                        if aspect > 0 or slope > 0:
                            self._persist_terrain_attributes(
                                parcel, aspect, slope, parcel_altitude
                            )
                        # Inject into parcel dict so downscaling can use them
                        if "elevation" not in parcel:
                            parcel["elevation"] = {"type": "Property", "value": parcel_altitude}
                        if slope > 0:
                            parcel["terrainSlope"] = {"type": "Property", "value": slope}
                            parcel["terrainAspect"] = {"type": "Property", "value": aspect}

                        # Apply spatial downscaling per parcel
                        corrected_observations = self._downscale_observations(
                            observations=observations,
                            parcel_lat=parcel_lat,
                            parcel_lon=parcel_lon,
                            parcel_altitude_m=parcel_altitude,
                            station_altitude_m=station_elevation,
                            parcel_entity=parcel,
                        )

                        if not corrected_observations:
                            logger.warning(
                                "Parcel %s: downscaling produced no observations "
                                "— nothing published this cycle",
                                parcel.get("id"),
                            )
                            stats["errors"] += 1
                            continue

                        # Altitud de referencia de los valores ya corregidos.
                        # No es la de la celda de Open-Meteo: publicar aquella
                        # haría que el consumidor volviera a aplicar una
                        # corrección ya aplicada.
                        reference_altitude_m = corrected_observations[0].get(
                            "reference_elevation_m"
                        )

                        # Write to Orion-LD (WeatherObserved)
                        written = self._write_weather_observed(
                            parcel=parcel,
                            observations=corrected_observations,
                            reference_altitude_m=reference_altitude_m,
                        )

                        stats["weather_observed_created"] += written.get(
                            "created", 0
                        )
                        stats["weather_observed_updated"] += written.get(
                            "updated", 0
                        )
                        stats["observations_written"] += len(
                            corrected_observations
                        )

                        # Agregados del día: van en WeatherForecast porque el SDM no los
                        # define en WeatherObserved.
                        latest = corrected_observations[0]
                        tenant_id = parcel.get("_tenant", "default")
                        parcel_id = parcel.get("id", "")
                        _loc = (
                            (parcel_lon, parcel_lat)
                            if reference_altitude_m is None
                            else (parcel_lon, parcel_lat, float(reference_altitude_m))
                        )
                        if self._write_weather_forecast(
                            tenant_id=tenant_id,
                            parcel_id=parcel_id,
                            location=_loc,
                            daily=latest,
                        ):
                            stats["weather_forecast_written"] += 1

                        stats["parcels_processed"] += 1

                    except Exception as e:
                        logger.warning(
                            f"Error processing parcel {parcel.get('id')}: {e}"
                        )
                        stats["errors"] += 1

                # Rate limiting between clusters
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error processing cluster {ci}: {e}")
                stats["errors"] += 1

        # Step 4: Prune orphan WeatherObserved (parcels deleted since last cycle).
        # The engine already provisions per-parcel stations by discovery; this
        # closes the loop on teardown. Per-tenant, false-zero-guarded inside.
        for tid in self._get_active_tenants():
            try:
                stats["weather_observed_pruned"] += self._prune_orphan_weather_observed(tid)
            except Exception as e:
                logger.warning(f"Prune error for tenant {tid}: {e}")

        logger.info(
            f"ParcelWeatherEngine cycle complete: {stats['parcels_processed']} "
            f"parcels, {stats['weather_observed_created']} created, "
            f"{stats['weather_observed_updated']} updated, "
            f"{stats['weather_forecast_written']} forecasts, "
            f"{stats['weather_observed_pruned']} pruned, {stats['errors']} errors"
        )
        return stats

    # ------------------------------------------------------------------
    # Downscaling
    # ------------------------------------------------------------------

    def _downscale_observations(
        self,
        observations: List[Dict[str, Any]],
        parcel_lat: float,
        parcel_lon: float,
        parcel_altitude_m: float,
        station_altitude_m: Optional[float],
        parcel_entity: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Apply spatial downscaling to observations for a specific parcel.

        Uses the existing spatial_downscaler module from weather_utils.
        """
        try:
            from weather_utils.spatial_downscaler import (
                downscale_for_parcel,
                extract_parcel_terrain,
            )

            parcel_alt, parcel_aspect, parcel_slope = extract_parcel_terrain(
                parcel_entity
            )
            effective_alt = (
                parcel_alt if parcel_alt > 0 else parcel_altitude_m
            )

            corrected = []
            for obs in observations:
                obs_dt_str = obs.get("observed_at")
                doy = None
                if obs_dt_str and isinstance(obs_dt_str, str):
                    try:
                        dt = datetime.fromisoformat(obs_dt_str)
                        doy = dt.timetuple().tm_yday
                    except (ValueError, TypeError):
                        pass

                # La corrección por aspecto es para el valor puntual de la parcela.
                # WeatherObserved.solarRadiation debe llevar la GLOBAL HORIZONTAL sin
                # corregir: el consumidor por píxel corrige con SU aspecto. Publicar la
                # corregida la aplicaría dos veces.
                horizontal = obs.get("solar_rad_w_m2")

                result = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=effective_alt,
                    station_altitude_m=station_altitude_m
                    if (station_altitude_m or 0) > 0
                    else effective_alt,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                result["observed_at"] = obs.get("observed_at")
                result["source"] = "OPEN-METEO"
                result["data_type"] = "FORECAST"
                # Altitud a la que corresponden ESTOS valores una vez corregidos.
                # Es lo que location[2] debe declarar: la base DESDE la que el
                # consumidor corrige. Desconocida => None, nunca 0.0.
                result["reference_elevation_m"] = (
                    effective_alt if effective_alt > 0 else None
                )
                if horizontal is not None:
                    result["solar_rad_w_m2_horizontal"] = horizontal
                corrected.append(result)

            return corrected

        except ImportError:
            logger.debug("Spatial downscaler not available — using raw data")
            # Deep copy to avoid mutating shared observations across parcels in a cluster
            import copy
            observations = copy.deepcopy(observations)
            for obs in observations:
                obs["source"] = "OPEN-METEO"
                obs["data_type"] = "FORECAST"
                # Sin corrección por aspecto, el valor crudo YA es la global
                # horizontal, que es justo lo que el consumidor necesita. No
                # publicarlo dejaba a weather-map sin radiación y sin ET0.
                if obs.get("solar_rad_w_m2") is not None:
                    obs["solar_rad_w_m2_horizontal"] = obs["solar_rad_w_m2"]
                # Sin downscaling los valores siguen siendo los del grid: su
                # altitud de referencia es la de la celda, no la de la parcela.
                _station = obs.get("station_elevation_m")
                obs["reference_elevation_m"] = _station if _station else None
            return observations

    # ------------------------------------------------------------------
    # Orion-LD persistence
    # ------------------------------------------------------------------

    def _write_weather_observed(
        self,
        parcel: Dict[str, Any],
        observations: List[Dict[str, Any]],
        reference_altitude_m: Optional[float] = None,
    ) -> Dict[str, int]:
        """Create or update WeatherObserved entities in Orion-LD for a parcel.

        Uses the latest observation to populate the entity.
        Returns {'created': N, 'updated': N}.
        """
        if not observations:
            return {"created": 0, "updated": 0}

        result = {"created": 0, "updated": 0}
        tenant_id = parcel.get("_tenant", "default")
        parcel_id = parcel.get("id", "")
        centroid = parcel.get("_centroid", (0.0, 0.0))

        # Use latest observation for the WeatherObserved entity
        latest = observations[0]

        try:
            from weather_worker.storage.orion_writer import (
                create_weather_observed_entity,
            )

            # Extract display name
            name_attr = parcel.get("name", {})
            parcel_name = (
                name_attr.get("value", "")
                if isinstance(name_attr, dict)
                else ""
            )

            entity_id = create_weather_observed_entity(
                parcel_id=parcel_id,
                tenant_id=tenant_id,
                location=centroid,
                weather_data=latest,
                observed_at=datetime.utcnow(),
                parcel_name=parcel_name,
                reference_altitude_m=reference_altitude_m,
            )

            if entity_id:
                result["created"] = 1

        except ImportError as e:
            logger.debug(f"orion_writer not available: {e}")
        except Exception as e:
            logger.warning(f"Error writing WeatherObserved: {e}")

        return result

    def _write_weather_forecast(
        self,
        tenant_id: str,
        parcel_id: str,
        location: tuple,
        daily: Dict[str, Any],
    ) -> bool:
        """Publica el WeatherForecast del día para una parcela.

        Best-effort e independiente de la observación: si falla, se registra y el
        ciclo sigue. Nunca sustituye un dato ausente por un valor por defecto.
        """
        try:
            from weather_worker.storage.orion_writer import (
                build_weather_forecast_entity,
            )

            # La ventana la fija el DATO, no el reloj: la observación es el día 0
            # de Open-Meteo en Europe/Madrid, así que entre las 22:00 UTC y
            # medianoche utcnow() nombra el día siguiente al del payload.
            day = _observation_day(daily.get("observed_at"))
            if day is None:
                logger.warning(
                    "WeatherForecast for %s skipped: unusable observed_at (%r); "
                    "a validity window that names another day is worse than none",
                    parcel_id,
                    daily.get("observed_at"),
                )
                return False
            entity = build_weather_forecast_entity(
                parcel_id=parcel_id,
                tenant_id=tenant_id,
                location=location,
                daily=daily,
                valid_from=f"{day}T00:00:00Z",
                valid_to=f"{day}T23:59:59Z",
            )
            payload = [entity]
            resp = requests.post(
                f"{self.orion_url}/ngsi-ld/v1/entityOperations/upsert",
                json=payload,
                headers=self._make_headers(tenant_id, body=payload),
                timeout=15,
            )
            if resp.status_code in (200, 201, 204):
                return True
            logger.warning(
                "WeatherForecast upsert for %s returned %s", parcel_id, resp.status_code
            )
            return False
        except Exception as e:
            logger.warning("Error writing WeatherForecast for %s: %s", parcel_id, e)
            return False
