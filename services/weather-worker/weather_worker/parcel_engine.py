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
sys_paths = ["/app/common", "/app/weather-worker"]
for p in sys_paths:
    if p not in sys.path:
        sys.path.insert(0, p)


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

    def _make_headers(self, tenant_id: str) -> dict:
        """Build Orion-LD headers with normalized tenant ID."""
        import re

        n = tenant_id.lower().strip().replace("-", "_").replace(" ", "_")
        n = re.sub(r"[^a-z0-9_]", "", n)
        n = n.strip("_") or tenant_id
        headers = {
            "NGSILD-Tenant": n,
            "Fiware-Service": n,
            "Fiware-ServicePath": "/",
            "Accept": "application/ld+json",
        }
        if self.context_url:
            headers["Link"] = (
                f'<{self.context_url}>; '
                f'rel="http://www.w3.org/ns/json-ld#context"; '
                f'type="application/ld+json"'
            )
        return headers

    def _discover_tenants_from_db(self) -> List[str]:
        """Discover active tenants from the admin platform database.

        Queries tenant_limits for all tenants that have been active.
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
                    "SELECT DISTINCT tenant_id FROM tenant_limits "
                    "WHERE tenant_id IS NOT NULL AND tenant_id != '' "
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
        2. Discover from admin platform DB (tenant_limits table)
        3. Discover from Orion-LD by listing entity types per tenant scope
        4. Fallback to ['default']
        """
        # Priority 1: explicit env var
        env_tenants = os.getenv("PARCEL_ENGINE_TENANTS", "").strip()
        if env_tenants:
            return [t.strip() for t in env_tenants.split(",") if t.strip()]

        # Priority 2: discover from admin platform DB
        db_tenants = self._discover_tenants_from_db()
        if db_tenants:
            return db_tenants

        # Priority 2: discover from Orion-LD by querying without tenant scope
        # (returns entities regardless of tenant)
        try:
            headers = {
                "Accept": "application/ld+json",
            }
            if self.context_url:
                headers["Link"] = (
                    f'<{self.context_url}>; '
                    f'rel="http://www.w3.org/ns/json-ld#context"; '
                    f'type="application/ld+json"'
                )
            # Query AgriParcel across all tenants — extract tenant from entity IDs
            params = {
                "type": "AgriParcel",
                "attrs": "id",
                "limit": 1000,
            }
            url = f"{self.orion_url}/ngsi-ld/v1/entities"
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                entities = resp.json()
                if isinstance(entities, list):
                    tenant_ids: set = set()
                    for entity in entities:
                        eid = entity.get("id", "")
                        # Extract tenant from urn:ngsi-ld:AgriParcel:<tenant>:<id>
                        parts = eid.split(":")
                        if len(parts) >= 4:
                            tenant_ids.add(parts[3])
                    if tenant_ids:
                        return list(tenant_ids)
        except Exception as e:
            logger.warning(f"Could not discover tenants from Orion-LD: {e}")

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
                    f"{elev_service_url}/point",
                    params={"lat": centroid[1], "lon": centroid[0], "purpose": "weather"},
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
                    "wind_speed_10m_max",
                    "wind_gusts_10m_max",
                    "wind_direction_10m_dominant",
                    "et0_fao_evapotranspiration",
                    "shortwave_radiation_sum",
                    "soil_moisture_0_to_7cm_mean",
                    "soil_moisture_7_to_28cm_mean",
                    "surface_pressure_mean",
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
        """Parse Open-Meteo daily response into observation dicts."""
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        if not dates:
            return []

        station_elevation = data.get("elevation", 0.0)

        observations = []
        for i, date_str in enumerate(dates):
            obs = {
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
                # Open-Meteo returns wind_gusts_10m_max in km/h — convert to m/s
                "wind_gusts_ms": self._div_if(
                    self._safe_get(daily, "wind_gusts_10m_max", i), 3.6
                ),
                # Open-Meteo returns wind_speed_10m_max in km/h — convert to m/s
                "wind_speed_ms": self._div_if(
                    self._safe_get(daily, "wind_speed_10m_max", i), 3.6
                ),
                "wind_direction_deg": self._safe_get(
                    daily, "wind_direction_10m_dominant", i
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
            observations.append(obs)

        return observations

    @staticmethod
    def _safe_get(daily: dict, key: str, index: int) -> Optional[float]:
        """Safely get a value from Open-Meteo daily dict."""
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
            "clusters": 0,
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
                station_elevation = raw_data.get("elevation", 0.0)
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

                        # Apply spatial downscaling per parcel
                        corrected_observations = self._downscale_observations(
                            observations=observations,
                            parcel_lat=parcel_lat,
                            parcel_lon=parcel_lon,
                            parcel_altitude_m=parcel_altitude,
                            station_altitude_m=station_elevation,
                            parcel_entity=parcel,
                        )

                        # Write to Orion-LD (WeatherObserved)
                        written = self._write_weather_observed(
                            parcel=parcel,
                            observations=corrected_observations,
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

        logger.info(
            f"ParcelWeatherEngine cycle complete: {stats['parcels_processed']} "
            f"parcels, {stats['weather_observed_created']} created, "
            f"{stats['weather_observed_updated']} updated, {stats['errors']} errors"
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
        station_altitude_m: float,
        parcel_entity: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Apply spatial downscaling to observations for a specific parcel.

        Uses the existing spatial_downscaler module from common/weather_utils.
        """
        try:
            from common.weather_utils.spatial_downscaler import (
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

                result = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=effective_alt,
                    station_altitude_m=station_altitude_m
                    if station_altitude_m > 0
                    else effective_alt,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                result["observed_at"] = obs.get("observed_at")
                result["source"] = "OPEN-METEO"
                result["data_type"] = "FORECAST"
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
            return observations

    # ------------------------------------------------------------------
    # Orion-LD persistence
    # ------------------------------------------------------------------

    def _write_weather_observed(
        self,
        parcel: Dict[str, Any],
        observations: List[Dict[str, Any]],
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
            )

            if entity_id:
                result["created"] = 1

        except ImportError as e:
            logger.debug(f"orion_writer not available: {e}")
        except Exception as e:
            logger.warning(f"Error writing WeatherObserved: {e}")

        return result
