#!/usr/bin/env python3
# =============================================================================
# Orion Writer - Sync WeatherObserved entities to Orion-LD
# =============================================================================

import logging
import os
import sys
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
import requests

# Add common directory to path
sys.path.insert(0, "/app/common")

from common.ngsi_headers import inject_fiware_headers

try:
    from common.log_helpers import redact
except ImportError:
    def redact(v, _m=200): return str(v)[:200]

logger = logging.getLogger(__name__)

# Get Orion URL from environment
ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv("CONTEXT_URL", "")


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — delegates to canonical ngsi_headers."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def find_existing_weather_observed(
    tenant_id: str, latitude: float, longitude: float, radius_km: float = 4.0
) -> Optional[Dict[str, Any]]:
    """
    Find existing WeatherObserved entity within radius (spatial clustering).

    This implements the "Virtual Station" concept: if a WeatherObserved entity
    already exists within 4km, reuse it instead of creating a new one.

    Args:
        tenant_id: Tenant ID
        latitude: Latitude to search from
        longitude: Longitude to search from
        radius_km: Search radius in kilometers (default: 4km for clustering)

    Returns:
        WeatherObserved entity if found, None otherwise
    """
    try:
        # Build geo-query to find WeatherObserved entities near location
        query_params = {
            "type": "WeatherObserved",
            "georel": "near;maxDistance=={}".format(
                int(radius_km * 1000)
            ),  # Convert to meters
            "geometry": "Point",
            "coordinates": f"[{longitude},{latitude}]",
            "options": "count",
        }

        headers = _make_headers(tenant_id)

        url = f"{ORION_URL}/ngsi-ld/v1/entities"
        response = requests.get(url, params=query_params, headers=headers, timeout=10)

        if response.status_code == 200:
            entities = response.json()
            if isinstance(entities, list) and len(entities) > 0:
                # Return the first (closest) entity
                logger.debug(
                    f"Found existing WeatherObserved entity within {radius_km}km of ({latitude}, {longitude})"
                )
                return entities[0]
            elif isinstance(entities, dict):
                # Single entity returned
                logger.debug(
                    f"Found existing WeatherObserved entity within {radius_km}km"
                )
                return entities
            else:
                logger.debug(
                    f"No existing WeatherObserved entities within {radius_km}km"
                )
                return None
        elif response.status_code == 404:
            logger.debug(f"No WeatherObserved entities found within {radius_km}km")
            return None
        else:
            logger.warning(
                f"Error querying for existing WeatherObserved: {response.status_code} - {redact(redact(redact(response.text)))}"
            )
            return None

    except Exception as e:
        logger.warning(f"Error finding existing WeatherObserved: {e}")
        return None


def get_parcels_by_location(
    tenant_id: str,
    latitude: float,
    longitude: float,
    radius_km: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Query Orion-LD for AgriParcel entities near a location.

    Searches across ALL tenants because weather is shared infrastructure —
    a weather station near a parcel should feed that parcel regardless of
    which tenant owns it.

    Args:
        tenant_id: Tenant ID (used as fallback if tenant list query fails)
        latitude: Latitude
        longitude: Longitude
        radius_km: Search radius in kilometers (default: 10km)

    Returns:
        List of parcel entities from Orion-LD (may span multiple tenants)
    """
    all_parcels = []

    # Discover tenants that have parcels. We query ALL tenants (not just those
    # with weather locations) because a weather station should create virtual
    # stations for any parcel within range, regardless of tenant.
    try:
        import psycopg2

        # Build connection URL from components (worker doesn't have POSTGRES_URL env)
        pg_url = os.environ.get("POSTGRES_URL") or (
            f"postgresql://{os.environ.get('POSTGRES_USER', 'postgres')}:"
            f"{os.environ.get('POSTGRES_PASSWORD', '')}@"
            f"{os.environ.get('POSTGRES_HOST', 'postgresql-service')}:"
            f"{os.environ.get('POSTGRES_PORT', '5432')}/"
            f"{os.environ.get('POSTGRES_DB', 'nekazari')}"
        )
        conn = psycopg2.connect(pg_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id FROM public.tenants WHERE status = 'active'"
                )
                rows = cur.fetchall()
                if rows:
                    tenant_ids = [r[0] for r in rows]
                else:
                    tenant_ids = [tenant_id]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            f"Could not query public.tenants: {e}, trying tenant_weather_locations"
        )
        try:
            import psycopg2

            pg_url = os.environ.get("POSTGRES_URL") or (
                f"postgresql://{os.environ.get('POSTGRES_USER', 'postgres')}:"
                f"{os.environ.get('POSTGRES_PASSWORD', '')}@"
                f"{os.environ.get('POSTGRES_HOST', 'postgresql-service')}:"
                f"{os.environ.get('POSTGRES_PORT', '5432')}/"
                f"{os.environ.get('POSTGRES_DB', 'nekazari')}"
            )
            conn = psycopg2.connect(pg_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT tenant_id FROM tenant_weather_locations"
                    )
                    rows = cur.fetchall()
                    tenant_ids = [r[0] for r in rows] if rows else [tenant_id]
            finally:
                conn.close()
        except Exception as e2:
            logger.warning(f"Could not query tenant_weather_locations: {e2}")
            tenant_ids = [tenant_id]

    for tid in tenant_ids:
        try:
            query_params = {
                "type": "AgriParcel",
                "georel": "near;maxDistance=={}".format(int(radius_km * 1000)),
                "geometry": "Point",
                "coordinates": f"[{longitude},{latitude}]",
                "options": "count",
            }

            headers = _make_headers(tid)
            if CONTEXT_URL:
                headers["Link"] = (
                    f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context";'
                    f' type="application/ld+json"'
                )

            url = f"{ORION_URL}/ngsi-ld/v1/entities"
            response = requests.get(
                url, params=query_params, headers=headers, timeout=10
            )

            if response.status_code == 200:
                entities = response.json()
                if isinstance(entities, list) and len(entities) > 0:
                    # Tag each parcel with its tenant for correct scoping
                    for e in entities:
                        e["_tenant"] = tid
                    all_parcels.extend(entities)
                    logger.info(
                        f"Found {len(entities)} parcels near ({latitude}, {longitude})"
                        f" for tenant {tid}"
                    )
            elif response.status_code == 404:
                logger.debug(
                    f"No parcels near ({latitude}, {longitude}) for tenant {tid}"
                )
            else:
                logger.warning(
                    f"Error querying parcels for tenant {tid}: {response.status_code}"
                )
        except Exception as e:
            logger.warning(f"Error querying parcels for tenant {tid}: {e}")

    logger.info(
        f"Total parcels found near ({latitude}, {longitude}):"
        f" {len(all_parcels)} across {len(tenant_ids)} tenant(s)"
    )
    return all_parcels


def create_weather_observed_entity(
    parcel_id: str,
    tenant_id: str,
    location: Tuple[float, float],
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    municipality_code: Optional[str] = None,
    parcel_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create or update a WeatherObserved entity in Orion-LD for a parcel.

    Args:
        parcel_id: Parcel entity ID from Orion-LD
        tenant_id: Tenant ID
        location: Tuple of (longitude, latitude)
        weather_data: Weather data dict with keys like temp_avg, humidity_avg, etc.
        observed_at: Observation timestamp (defaults to now)
        municipality_code: Optional INE/AEMET municipality code
        parcel_name: Optional parcel name (used as "virtual {name}")

    Returns:
        Entity ID if successful, None otherwise
    """
    try:
        lon, lat = location

        # Use provided timestamp or current time
        if observed_at is None:
            observed_at = datetime.utcnow()

        # Generate entity ID following NGSI-LD format
        parcel_identifier = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
        entity_id = (
            f"urn:ngsi-ld:WeatherObserved:{tenant_id}:parcel-{parcel_identifier}"
        )

        # Entity name: "virtual {parcel_name}" for discoverability in UI
        display_name = parcel_name or parcel_identifier
        entity_name = f"virtual {display_name}"

        # Build WeatherObserved entity
        entity = {
            "@context": [CONTEXT_URL],
            "id": entity_id,
            "type": "WeatherObserved",
            "name": {
                "type": "Property",
                "value": entity_name,
            },
            "location": {
                "type": "GeoProperty",
                "value": {"type": "Point", "coordinates": [lon, lat]},
            },
            "dateObserved": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": observed_at.isoformat() + "Z"},
            },
            "locatedAt": {"type": "Relationship", "object": parcel_id},
        }

        # Self-describing: carry municipality code for direct timeseries resolution
        if municipality_code:
            entity["municipalityCode"] = {
                "type": "Property",
                "value": municipality_code,
            }

        # Add weather properties (map from weather_observations table format)
        if weather_data.get("temp_avg") is not None:
            entity["temperature"] = {
                "type": "Property",
                "value": float(weather_data["temp_avg"]),
                "unitCode": "CEL",
            }

        if weather_data.get("humidity_avg") is not None:
            entity["relativeHumidity"] = {
                "type": "Property",
                "value": float(weather_data["humidity_avg"]),
                "unitCode": "P1",  # Percentage
            }

        if weather_data.get("wind_speed_ms") is not None:
            entity["windSpeed"] = {
                "type": "Property",
                "value": float(weather_data["wind_speed_ms"]),
                "unitCode": "MTS",  # Meters per second — latest hour mean
            }

        if weather_data.get("wind_speed_max") is not None:
            entity["windSpeedMax"] = {
                "type": "Property",
                "value": float(weather_data["wind_speed_max"]),
                "unitCode": "MTS",
            }

        if weather_data.get("wind_gusts_ms") is not None:
            entity["windGusts"] = {
                "type": "Property",
                "value": float(weather_data["wind_gusts_ms"]),
                "unitCode": "MTS",
            }

        if weather_data.get("wind_direction_deg") is not None:
            entity["windDirection"] = {
                "type": "Property",
                "value": float(weather_data["wind_direction_deg"]),
                "unitCode": "DD",  # Degrees
            }

        if weather_data.get("pressure_hpa") is not None:
            entity["atmosphericPressure"] = {
                "type": "Property",
                "value": float(weather_data["pressure_hpa"]),
                "unitCode": "HPA",  # Hectopascal
            }

        if weather_data.get("precip_mm") is not None:
            entity["precipitation"] = {
                "type": "Property",
                "value": float(weather_data["precip_mm"]),
                "unitCode": "MMT",  # Millimeters
            }

        # Soil moisture (from Open-Meteo daily)
        if weather_data.get("soil_moisture_0_10cm") is not None:
            entity["soilMoistureTop"] = {
                "type": "Property",
                "value": float(weather_data["soil_moisture_0_10cm"]),
                "unitCode": "M3",  # m³/m³
            }

        if weather_data.get("soil_moisture_10_40cm") is not None:
            entity["soilMoistureSub"] = {
                "type": "Property",
                "value": float(weather_data["soil_moisture_10_40cm"]),
                "unitCode": "M3",  # m³/m³
            }

        # Add source information
        source = weather_data.get("source", "OPEN-METEO")
        entity["sourceConfidence"] = {"type": "Property", "value": source}

        # Add agroclimatic metrics if available
        if weather_data.get("eto_mm") is not None:
            entity["et0"] = {
                "type": "Property",
                "value": float(weather_data["eto_mm"]),
                "unitCode": "MMT",
            }

        if weather_data.get("delta_t") is not None:
            entity["deltaT"] = {
                "type": "Property",
                "value": float(weather_data["delta_t"]),
                "unitCode": "CEL",
            }

        if weather_data.get("gdd_accumulated") is not None:
            entity["gddAccumulated"] = {
                "type": "Property",
                "value": float(weather_data["gdd_accumulated"]),
                "unitCode": "DD",  # Degree-days (base 10°C)
            }

        if weather_data.get("temp_current") is not None:
            entity["tempCurrent"] = {
                "type": "Property",
                "value": float(weather_data["temp_current"]),
                "unitCode": "CEL",
            }

        # Prepare headers — no Link header: context is embedded inline in the body
        # (application/ld+json + Link is not allowed by the NGSI-LD spec)
        headers = _make_headers(tenant_id)
        headers["Content-Type"] = "application/ld+json"
        headers.pop("Link", None)  # @context in body — no Link header per NGSI-LD spec

        # Try to create entity
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        response = requests.post(orion_url, json=entity, headers=headers, timeout=10)

        if response.status_code in [201, 204]:
            logger.info(
                f"Created WeatherObserved entity {entity_id} for parcel {parcel_id}"
            )
            return entity_id
        elif response.status_code == 409:
            # Entity already exists, update it
            logger.debug(
                f"WeatherObserved entity {entity_id} already exists, updating..."
            )
            return update_weather_observed_entity(
                entity_id, tenant_id, weather_data, observed_at, headers
            )
        else:
            logger.error(
                f"Failed to create WeatherObserved entity: {response.status_code} - {response.text}"
            )
            return None

    except Exception as e:
        logger.error(f"Error creating WeatherObserved entity: {e}", exc_info=True)
        return None


def update_weather_observed_entity(
    entity_id: str,
    tenant_id: str,
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    headers: Optional[Dict[str, str]] = None,
    add_parcel_ref: Optional[str] = None,
) -> Optional[str]:
    """
    Update an existing WeatherObserved entity in Orion-LD.

    Args:
        entity_id: WeatherObserved entity ID
        tenant_id: Tenant ID
        weather_data: Weather data dict
        observed_at: Observation timestamp
        headers: Optional headers dict

    Returns:
        Entity ID if successful, None otherwise
    """
    try:
        if observed_at is None:
            observed_at = datetime.utcnow()

        # Build update payload — @context required for application/ld+json
        update_payload = {
            "@context": [CONTEXT_URL] if CONTEXT_URL else [],
            "dateObserved": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": observed_at.isoformat() + "Z"},
            },
        }

        # Add weather properties
        if weather_data.get("temp_avg") is not None:
            update_payload["temperature"] = {
                "type": "Property",
                "value": float(weather_data["temp_avg"]),
                "unitCode": "CEL",
            }

        if weather_data.get("humidity_avg") is not None:
            update_payload["relativeHumidity"] = {
                "type": "Property",
                "value": float(weather_data["humidity_avg"]),
                "unitCode": "P1",
            }

        if weather_data.get("wind_speed_ms") is not None:
            update_payload["windSpeed"] = {
                "type": "Property",
                "value": float(weather_data["wind_speed_ms"]),
                "unitCode": "MTS",
            }

        if weather_data.get("wind_speed_max") is not None:
            update_payload["windSpeedMax"] = {
                "type": "Property",
                "value": float(weather_data["wind_speed_max"]),
                "unitCode": "MTS",
            }

        if weather_data.get("wind_gusts_ms") is not None:
            update_payload["windGusts"] = {
                "type": "Property",
                "value": float(weather_data["wind_gusts_ms"]),
                "unitCode": "MTS",
            }

        if weather_data.get("wind_direction_deg") is not None:
            update_payload["windDirection"] = {
                "type": "Property",
                "value": float(weather_data["wind_direction_deg"]),
                "unitCode": "DD",
            }

        if weather_data.get("pressure_hpa") is not None:
            update_payload["atmosphericPressure"] = {
                "type": "Property",
                "value": float(weather_data["pressure_hpa"]),
                "unitCode": "HPA",
            }

        if weather_data.get("precip_mm") is not None:
            update_payload["precipitation"] = {
                "type": "Property",
                "value": float(weather_data["precip_mm"]),
                "unitCode": "MMT",
            }

        # Soil moisture (from Open-Meteo daily)
        if weather_data.get("soil_moisture_0_10cm") is not None:
            update_payload["soilMoistureTop"] = {
                "type": "Property",
                "value": float(weather_data["soil_moisture_0_10cm"]),
                "unitCode": "M3",
            }

        if weather_data.get("soil_moisture_10_40cm") is not None:
            update_payload["soilMoistureSub"] = {
                "type": "Property",
                "value": float(weather_data["soil_moisture_10_40cm"]),
                "unitCode": "M3",
            }

        source = weather_data.get("source", "OPEN-METEO")
        update_payload["sourceConfidence"] = {"type": "Property", "value": source}

        if weather_data.get("eto_mm") is not None:
            update_payload["et0"] = {
                "type": "Property",
                "value": float(weather_data["eto_mm"]),
                "unitCode": "MMT",
            }

        if weather_data.get("delta_t") is not None:
            update_payload["deltaT"] = {
                "type": "Property",
                "value": float(weather_data["delta_t"]),
                "unitCode": "CEL",
            }

        if weather_data.get("gdd_accumulated") is not None:
            update_payload["gddAccumulated"] = {
                "type": "Property",
                "value": float(weather_data["gdd_accumulated"]),
                "unitCode": "DD",
            }

        if weather_data.get("temp_current") is not None:
            update_payload["tempCurrent"] = {
                "type": "Property",
                "value": float(weather_data["temp_current"]),
                "unitCode": "CEL",
            }

        # If add_parcel_ref is provided, we should add it to locatedAt
        # Note: NGSI-LD relationships can be arrays, but for simplicity we'll just update
        # In a more complex implementation, we'd check if the parcel is already in locatedAt
        # and only add it if not present
        if add_parcel_ref:
            # For now, we'll just log it - in production you might want to merge locatedAt arrays
            logger.debug(
                f"Would add parcel {add_parcel_ref} to locatedAt of {entity_id} (not implemented)"
            )

        # Prepare headers
        if headers is None:
            headers = {}

        if headers is None:
            headers = _make_headers(tenant_id)
        headers["Content-Type"] = "application/ld+json"
        headers.pop("Link", None)  # @context in body — no Link header per NGSI-LD spec

        # Update entity
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs"
        response = requests.patch(
            orion_url, json=update_payload, headers=headers, timeout=10
        )

        if response.status_code in [200, 204]:
            logger.debug(f"Updated WeatherObserved entity {entity_id}")
            return entity_id
        else:
            logger.error(
                f"Failed to update WeatherObserved entity: {response.status_code} - {response.text}"
            )
            return None

    except Exception as e:
        logger.error(f"Error updating WeatherObserved entity: {e}", exc_info=True)
        return None


def sync_weather_to_orion(
    tenant_id: str,
    latitude: float,
    longitude: float,
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    radius_km: float = 10.0,
    municipality_code: Optional[str] = None,
    station_altitude_m: float = 0.0,
) -> int:
    """
    Sync weather data to Orion-LD for all parcels near a location.

    This function:
    1. Queries Orion-LD for parcels near the location
    2. Applies spatial downscaling (altitude, aspect, slope) per parcel
    3. Creates/updates WeatherObserved entities for each parcel

    Args:
        tenant_id: Tenant ID
        latitude: Latitude of weather observation
        longitude: Longitude of weather observation
        weather_data: Weather data dict (from weather_observations table format)
        observed_at: Observation timestamp
        radius_km: Search radius for parcels (default: 10km)
        municipality_code: Optional INE/AEMET municipality code forwarded to each entity
        station_altitude_m: Altitude of the weather station/municipality in meters

    Returns:
        Number of WeatherObserved entities synced
    """
    try:
        # Get parcels near this location
        parcels = get_parcels_by_location(tenant_id, latitude, longitude, radius_km)

        if not parcels:
            logger.debug(
                f"No parcels found near ({latitude}, {longitude}) for tenant {tenant_id}"
            )
            return 0

        synced_count = 0

        for parcel in parcels:
            parcel_id = parcel.get("id")
            if not parcel_id:
                continue

            # Extract parcel location (centroid) for WeatherObserved
            parcel_location = None
            location_attr = parcel.get("location")
            if location_attr:
                location_value = (
                    location_attr.get("value")
                    if isinstance(location_attr, dict)
                    else location_attr
                )
                if isinstance(location_value, dict):
                    geom_type = location_value.get("type")
                    if geom_type == "Point":
                        coords = location_value.get("coordinates", [])
                        if len(coords) >= 2:
                            parcel_location = (coords[0], coords[1])  # (lon, lat)
                    elif geom_type in ["Polygon", "MultiPolygon"]:
                        # Try to calculate centroid using geo_utils if available
                        try:
                            # Import geo_utils for centroid calculation
                            from weather_worker.geo_utils import calculate_centroid

                            centroid = calculate_centroid(location_value)
                            if centroid:
                                parcel_location = centroid  # (lon, lat)
                                logger.debug(
                                    f"Calculated centroid for parcel {parcel_id}: {centroid}"
                                )
                            else:
                                # Fallback to weather location
                                parcel_location = (longitude, latitude)
                        except ImportError:
                            # geo_utils not available, use weather location as fallback
                            logger.debug(
                                f"geo_utils not available, using weather location for parcel {parcel_id}"
                            )
                            parcel_location = (longitude, latitude)
                        except Exception as e:
                            logger.warning(
                                f"Error calculating centroid: {e}, using weather location"
                            )
                            parcel_location = (longitude, latitude)

            # If no location found, use weather observation location
            if not parcel_location:
                parcel_location = (longitude, latitude)

            # Apply spatial downscaling for this specific parcel
            parcel_weather = weather_data
            try:
                from weather_utils.spatial_downscaler import (
                    downscale_for_parcel,
                    extract_parcel_terrain,
                )

                parcel_alt, parcel_aspect, parcel_slope = extract_parcel_terrain(parcel)
                if parcel_alt > 0 or station_altitude_m > 0:
                    doy = observed_at.timetuple().tm_yday if observed_at else None
                    parcel_weather = downscale_for_parcel(
                        weather_data=weather_data,
                        parcel_lat=parcel_location[1],
                        parcel_lon=parcel_location[0],
                        parcel_altitude_m=parcel_alt
                        if parcel_alt > 0
                        else station_altitude_m,
                        station_altitude_m=station_altitude_m,
                        parcel_aspect_deg=parcel_aspect,
                        parcel_slope_deg=parcel_slope,
                        doy=doy,
                    )
            except ImportError:
                pass  # downscaler not available, use uncorrected data
            except Exception as exc:
                logger.debug(
                    f"Spatial downscaling skipped for parcel {parcel_id}: {exc}"
                )

            # Extract parcel name for virtual station naming
            parcel_name_attr = parcel.get("name", {})
            parcel_display_name = None
            if isinstance(parcel_name_attr, dict):
                parcel_display_name = parcel_name_attr.get("value", "")

            # Resolve the tenant the parcel belongs to (tagged during fetch).
            # Falls back to the ingesting tenant for backward compatibility.
            parcel_tenant = parcel.get("_tenant", tenant_id)

            # Create/update WeatherObserved entity with corrected weather
            entity_id = create_weather_observed_entity(
                parcel_id=parcel_id,
                tenant_id=parcel_tenant,
                location=parcel_location,
                weather_data=parcel_weather,
                observed_at=observed_at,
                municipality_code=municipality_code,
                parcel_name=parcel_display_name,
            )

            if entity_id:
                synced_count += 1

        logger.info(
            f"Synced {synced_count}/{len(parcels)} WeatherObserved entities to Orion-LD for tenant {tenant_id}"
        )
        return synced_count

    except Exception as e:
        logger.error(f"Error syncing weather to Orion-LD: {e}", exc_info=True)
        return 0
