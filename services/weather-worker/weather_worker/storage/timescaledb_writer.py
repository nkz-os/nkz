"""
TimescaleDB Writer - Write weather data to PostgreSQL/TimescaleDB
Also syncs to Orion-LD for digital twin integration
"""

import logging
import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values, Json
from typing import Dict, Any, List, Optional
from contextlib import contextmanager
from datetime import datetime

# Add common directory to path
sys.path.insert(0, '/app/common')
sys.path.insert(0, '/app/weather-worker')

try:
    from db_helper import set_platform_admin_context
except ImportError:
    # Fallback if db_helper not available
    def set_platform_admin_context(conn):
        admin_tenant = os.getenv('PLATFORM_ADMIN_TENANT', 'platform_admin')
        cursor = conn.cursor()
        try:
            # Use set_config with local=true to persist for entire session
            cursor.execute("SELECT set_config('app.current_tenant', %s, true)", (admin_tenant,))
            # Setting persists for entire connection session
        finally:
            cursor.close()

logger = logging.getLogger(__name__)

# Import Orion writer for dual write
try:
    from weather_worker.storage.orion_writer import sync_weather_to_orion
    ORION_SYNC_ENABLED = os.getenv('WEATHER_ORION_SYNC_ENABLED', 'true').lower() == 'true'
except ImportError as e:
    logger.warning(f"Orion writer not available - Orion-LD sync disabled: {e}")
    ORION_SYNC_ENABLED = False
    def sync_weather_to_orion(*args, **kwargs):
        return 0


class TimescaleDBWriter:
    """Write weather observations and alerts to TimescaleDB"""
    
    def __init__(self, postgres_url: str):
        """
        Initialize TimescaleDB writer
        
        Args:
            postgres_url: PostgreSQL connection URL
        """
        self.postgres_url = postgres_url
        self.conn = None
    
    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(
                self.postgres_url,
                cursor_factory=RealDictCursor
            )
            self.conn.autocommit = False
            logger.info("PostgreSQL connected")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            raise
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def _ensure_connection(self):
        """Ensure database connection is active, reconnect if needed"""
        if not self.conn or self.conn.closed:
            logger.info("Connection closed or not available, reconnecting...")
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass
            self.connect()
    
    @contextmanager
    def get_connection(self):
        """Get database connection context manager with automatic reconnection"""
        try:
            self._ensure_connection()
            yield self.conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning(f"Connection error detected: {e}. Attempting reconnection...")
            # Close existing connection if it exists
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass
            self.conn = None
            # Retry once with new connection
            self._ensure_connection()
            yield self.conn
    
    def write_observations(
        self,
        observations: List[Dict[str, Any]],
        tenant_id: str
    ) -> int:
        """
        Write weather observations to database
        
        Args:
            observations: List of observation dictionaries
            tenant_id: Tenant ID
        
        Returns:
            Number of observations written
        """
        if not observations:
            return 0
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Set tenant context for RLS (with error handling)
                try:
                    cursor.execute("SELECT set_current_tenant(%s)", (tenant_id,))
                except Exception as tenant_error:
                    logger.warning(f"Error setting tenant context (may not exist): {tenant_error}")
                    # Try fallback method
                    try:
                        cursor.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
                    except Exception as fallback_error:
                        logger.warning(f"Fallback tenant context also failed: {fallback_error}")
                        conn.rollback()
                        # Continue anyway - RLS may not be enabled for this table
                
                # Prepare batch insert
                insert_query = """
                    INSERT INTO weather_observations (
                        tenant_id, observed_at, municipality_code, station_id,
                        source, data_type,
                        temp_avg, temp_min, temp_max,
                        humidity_avg, precip_mm,
                        solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                        eto_mm,
                        soil_moisture_0_10cm, soil_moisture_10_40cm,
                        wind_speed_ms, wind_direction_deg, pressure_hpa,
                        gdd_accumulated, delta_t,
                        metrics, metadata
                    ) VALUES %s
                    ON CONFLICT (tenant_id, municipality_code, COALESCE(station_id, ''), observed_at)
                    DO UPDATE SET
                        temp_avg = EXCLUDED.temp_avg,
                        temp_min = EXCLUDED.temp_min,
                        temp_max = EXCLUDED.temp_max,
                        humidity_avg = EXCLUDED.humidity_avg,
                        precip_mm = EXCLUDED.precip_mm,
                        solar_rad_w_m2 = EXCLUDED.solar_rad_w_m2,
                        solar_rad_ghi_w_m2 = EXCLUDED.solar_rad_ghi_w_m2,
                        solar_rad_dni_w_m2 = EXCLUDED.solar_rad_dni_w_m2,
                        eto_mm = EXCLUDED.eto_mm,
                        soil_moisture_0_10cm = EXCLUDED.soil_moisture_0_10cm,
                        soil_moisture_10_40cm = EXCLUDED.soil_moisture_10_40cm,
                        wind_speed_ms = EXCLUDED.wind_speed_ms,
                        wind_direction_deg = EXCLUDED.wind_direction_deg,
                        pressure_hpa = EXCLUDED.pressure_hpa,
                        gdd_accumulated = EXCLUDED.gdd_accumulated,
                        delta_t = EXCLUDED.delta_t,
                        metrics = EXCLUDED.metrics,
                        metadata = EXCLUDED.metadata,
                        source = EXCLUDED.source,
                        data_type = EXCLUDED.data_type
                """
                
                values = []
                for obs in observations:
                    if not obs:
                        continue
                    
                    values.append((
                        obs['tenant_id'],
                        obs['observed_at'],
                        obs['municipality_code'],
                        obs.get('station_id'),
                        obs['source'],
                        obs['data_type'],
                        obs.get('temp_avg'),
                        obs.get('temp_min'),
                        obs.get('temp_max'),
                        obs.get('humidity_avg'),
                        obs.get('precip_mm'),
                        obs.get('solar_rad_w_m2'),
                        obs.get('solar_rad_ghi_w_m2'),
                        obs.get('solar_rad_dni_w_m2'),
                        obs.get('eto_mm'),
                        obs.get('soil_moisture_0_10cm'),
                        obs.get('soil_moisture_10_40cm'),
                        obs.get('wind_speed_ms'),
                        obs.get('wind_direction_deg'),
                        obs.get('pressure_hpa'),
                        obs.get('gdd_accumulated'),
                        obs.get('delta_t'),
                        Json(obs.get('metrics', {})),
                        Json(obs.get('metadata', {}))
                    ))
                
                if values:
                    execute_values(cursor, insert_query, values)
                    conn.commit()
                    logger.info(f"Inserted {len(values)} weather observations for tenant {tenant_id}")
                    
                    # Sync to Orion-LD (dual write)
                    if ORION_SYNC_ENABLED:
                        try:
                            # Get location from first observation (all should be same location for batch)
                            first_obs = observations[0] if observations else None
                            if first_obs:
                                # Get location from catalog_municipalities or use default
                                cursor.execute("""
                                    SELECT latitude, longitude 
                                    FROM catalog_municipalities 
                                    WHERE ine_code = %s
                                    LIMIT 1
                                """, (first_obs.get('municipality_code'),))
                                loc_row = cursor.fetchone()
                                
                                if loc_row and loc_row.get('latitude') and loc_row.get('longitude'):
                                    lat = float(loc_row['latitude'])
                                    lon = float(loc_row['longitude'])
                                    
                                    # Sync each observation to Orion-LD
                                    # Group by observed_at to avoid duplicate syncs
                                    synced_timestamps = set()
                                    for obs in observations:
                                        obs_timestamp = obs.get('observed_at')
                                        if obs_timestamp and obs_timestamp not in synced_timestamps:
                                            synced_timestamps.add(obs_timestamp)
                                            
                                            # Convert datetime if needed
                                            if isinstance(obs_timestamp, str):
                                                obs_timestamp = datetime.fromisoformat(obs_timestamp.replace('Z', '+00:00'))
                                            
                                            # Sync to Orion-LD with spatial downscaling
                                            station_alt = float(
                                                (obs.get('metadata') or {})
                                                .get('station_elevation_m', 0) or 0
                                            )
                                            synced = sync_weather_to_orion(
                                                tenant_id=tenant_id,
                                                latitude=lat,
                                                longitude=lon,
                                                weather_data=obs,
                                                observed_at=obs_timestamp if isinstance(obs_timestamp, datetime) else None,
                                                radius_km=10.0,
                                                station_altitude_m=station_alt,
                                            )
                                            if synced > 0:
                                                logger.debug(f"Synced {synced} WeatherObserved entities to Orion-LD for timestamp {obs_timestamp}")
                                else:
                                    logger.warning(f"Could not find location for municipality {first_obs.get('municipality_code')} - skipping Orion-LD sync")
                        except Exception as orion_error:
                            # Don't fail the entire write if Orion sync fails
                            logger.error(f"Error syncing to Orion-LD (non-fatal): {orion_error}", exc_info=True)
                    
                    return len(values)
                else:
                    logger.warning("No valid observations to insert")
                    return 0
                    
        except Exception as e:
            logger.error(f"Error writing observations: {e}")
            if conn:
                conn.rollback()
            raise
    
    def write_alerts(
        self,
        alerts: List[Dict[str, Any]],
        tenant_id: str
    ) -> int:
        """
        Write weather alerts to database
        
        Args:
            alerts: List of alert dictionaries
            tenant_id: Tenant ID
        
        Returns:
            Number of alerts written
        """
        if not alerts:
            return 0
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Set tenant context for RLS (with error handling)
                try:
                    cursor.execute("SELECT set_current_tenant(%s)", (tenant_id,))
                except Exception as tenant_error:
                    logger.warning(f"Error setting tenant context (may not exist): {tenant_error}")
                    # Try fallback method
                    try:
                        cursor.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
                    except Exception as fallback_error:
                        logger.warning(f"Fallback tenant context also failed: {fallback_error}")
                        conn.rollback()
                        # Continue anyway - RLS may not be enabled for this table
                
                insert_query = """
                    INSERT INTO weather_alerts (
                        tenant_id, municipality_code,
                        alert_type, alert_category,
                        effective_from, effective_to,
                        description, aemet_alert_id, aemet_zone_id,
                        metadata
                    ) VALUES %s
                    ON CONFLICT (tenant_id, municipality_code, aemet_alert_id, effective_from)
                    DO UPDATE SET
                        alert_type = EXCLUDED.alert_type,
                        alert_category = EXCLUDED.alert_category,
                        effective_to = EXCLUDED.effective_to,
                        description = EXCLUDED.description,
                        metadata = EXCLUDED.metadata
                """
                
                values = []
                for alert in alerts:
                    if not alert:
                        continue
                    
                    values.append((
                        tenant_id,
                        alert['municipality_code'],
                        alert['alert_type'],
                        alert['alert_category'],
                        alert['effective_from'],
                        alert['effective_to'],
                        alert.get('description'),
                        alert.get('aemet_alert_id'),
                        alert.get('aemet_zone_id'),
                        Json(alert.get('metadata', {}))
                    ))
                
                if values:
                    execute_values(cursor, insert_query, values)
                    conn.commit()
                    logger.info(f"Inserted {len(values)} weather alerts for tenant {tenant_id}")

                    # Emit webhook event for automation (N8N, Zulip)
                    self._emit_alert_webhook(tenant_id, alerts)
                    return len(values)
                else:
                    logger.warning("No valid alerts to insert")
                    return 0

        except Exception as e:
            logger.error(f"Error writing alerts: {e}")
            if conn:
                conn.rollback()

    def _emit_alert_webhook(
        self, tenant_id: str, alerts: List[Dict[str, Any]]
    ) -> None:
        """
        Emit webhook events for weather alerts so automation tools
        (N8N, Zulip) can pick them up.

        The platform uses N8N as the automation hub — configure a webhook
        trigger in N8N pointing at any URL, then set WEATHER_ALERT_WEBHOOK_URL
        to that endpoint. From N8N you can route to Zulip channels, email,
        SMS, or any other action.

        Without a configured webhook URL, alerts are still visible in the
        app UI (WeatherWidget alert banners) and via GET /api/weather/alerts.
        """
        webhook_url = os.getenv('WEATHER_ALERT_WEBHOOK_URL', '').strip()
        if not webhook_url:
            return

        high_severity = [a for a in alerts if a.get('alert_type') in ('ORANGE', 'RED')]
        if not high_severity:
            return

        try:
            import requests as req

            payload = {
                'event': 'weather_alert',
                'tenant_id': tenant_id,
                'alerts': [
                    {
                        'alert_type': a.get('alert_type'),
                        'alert_category': a.get('alert_category'),
                        'municipality_code': a.get('municipality_code'),
                        'effective_from': str(a.get('effective_from', '')),
                        'effective_to': str(a.get('effective_to', '')),
                        'description': a.get('description', ''),
                    }
                    for a in high_severity
                ],
            }
            resp = req.post(webhook_url, json=payload, timeout=10)
            logger.info(
                f'Weather alert webhook dispatched: {len(high_severity)} alerts, '
                f'status={resp.status_code}'
            )
        except Exception as exc:
            logger.debug(f'Weather alert webhook failed (non-fatal): {exc}')
            raise
    
    def _discover_locations_from_parcels(
        self, conn, cursor, tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Query Orion-LD for AgriParcel entities belonging to this tenant,
        resolve each parcel's municipality via PostGIS nearest-neighbor,
        and auto-insert into tenant_weather_locations so the system learns
        where the tenant actually farms.

        Returns a single discovered location dict or None.
        """
        ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
        try:
            import requests

            headers = {
                "Fiware-Service": tenant_id,
                "Fiware-ServicePath": "/",
                "Accept": "application/ld+json",
            }
            params = {
                "type": "AgriParcel",
                "attrs": "location",
                "limit": 50,
            }
            resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                params=params,
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug(
                    f"Orion-LD returned {resp.status_code} for tenant {tenant_id} — skipping parcel discovery"
                )
                return None

            entities = resp.json()
            if not isinstance(entities, list) or len(entities) == 0:
                return None

            for entity in entities:
                loc = entity.get("location", {})
                if isinstance(loc, dict):
                    loc_val = loc.get("value", {})
                else:
                    loc_val = loc

                coords = None
                if isinstance(loc_val, dict):
                    if loc_val.get("type") == "Point":
                        coords = loc_val.get("coordinates", [])
                    elif loc_val.get("type") in ("Polygon", "MultiPolygon"):
                        # Use centroid via PostGIS
                        try:
                            from weather_worker.geo_utils import calculate_centroid

                            centroid = calculate_centroid(loc_val)
                            if centroid:
                                coords = [centroid[0], centroid[1]]
                        except ImportError:
                            pass

                if not coords or len(coords) < 2:
                    continue

                lon, lat = float(coords[0]), float(coords[1])

                # Find municipality that contains or is nearest to this parcel
                try:
                    cursor.execute("""
                        SELECT
                            %s as tenant_id,
                            cm.ine_code as municipality_code,
                            cm.latitude,
                            cm.longitude,
                            NULL as station_id,
                            cm.name as label
                        FROM catalog_municipalities cm
                        WHERE cm.latitude IS NOT NULL
                          AND cm.longitude IS NOT NULL
                          AND cm.geom IS NOT NULL
                        ORDER BY cm.geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                        LIMIT 1
                    """, (tenant_id, lon, lat))
                    row = cursor.fetchone()
                    if row:
                        discovered = dict(row)
                        # Auto-insert so we don't query Orion-LD every cycle
                        try:
                            cursor.execute("""
                                INSERT INTO tenant_weather_locations
                                    (tenant_id, municipality_code, label, is_primary)
                                VALUES (%s, %s, %s, true)
                                ON CONFLICT (tenant_id, municipality_code) DO NOTHING
                            """, (tenant_id, discovered['municipality_code'],
                                  discovered.get('label', '')))
                            conn.commit()
                        except Exception:
                            conn.rollback()
                        logger.info(
                            f"Auto-discovered weather location for tenant {tenant_id} "
                            f"from parcel at ({lat:.4f}, {lon:.4f}): "
                            f"{discovered['municipality_code']} ({discovered.get('label')})"
                        )
                        return discovered
                except Exception as point_err:
                    logger.debug(f"PostGIS lookup failed for point ({lat},{lon}): {point_err}")
                    conn.rollback()
                    continue

        except ImportError:
            logger.debug("requests not available in weather-worker — skipping Orion-LD discovery")
        except Exception as exc:
            logger.warning(f"Parcel discovery error for tenant {tenant_id}: {exc}")

        return None

    def get_tenant_weather_locations(self) -> List[Dict[str, Any]]:
        """
        Get all active tenant weather locations
        Falls back to catalog_municipalities if no tenant_weather_locations are configured
        
        Returns:
            List of weather location dictionaries
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Use platform admin context to query all tenants
                set_platform_admin_context(conn)
                
                # First, try to get configured tenant weather locations
                try:
                    query = """
                        SELECT DISTINCT
                            twl.tenant_id,
                            twl.municipality_code,
                            cm.latitude,
                            cm.longitude,
                            twl.station_id,
                            twl.label
                        FROM tenant_weather_locations twl
                        JOIN catalog_municipalities cm ON cm.ine_code = twl.municipality_code
                        WHERE cm.latitude IS NOT NULL 
                          AND cm.longitude IS NOT NULL
                        ORDER BY twl.tenant_id, twl.municipality_code
                    """
                    
                    cursor.execute(query)
                    locations = cursor.fetchall()
                    
                    if locations:
                        logger.info(f"Found {len(locations)} configured tenant weather locations")
                        return [dict(loc) for loc in locations]
                except Exception as e:
                    logger.warning(f"Error fetching tenant_weather_locations (will try fallback): {e}")
                    # Rollback to clear the aborted transaction
                    conn.rollback()
                
                # Fallback: If no tenant_weather_locations with coordinates, discover
                # municipalities from Orion-LD parcels, previous data, or catalog.
                logger.info("No tenant_weather_locations with coordinates found, discovering locations from data")

                # Get all tenants
                try:
                    cursor.execute("SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL")
                    tenant_rows = cursor.fetchall()
                    tenants = [row['tenant_id'] for row in tenant_rows] if tenant_rows else []
                except Exception as tenant_error:
                    logger.warning(f"Error fetching tenants: {tenant_error}")
                    conn.rollback()
                    tenants = []

                if not tenants:
                    logger.warning("No tenants found in database, using default tenant")
                    tenants = ['default']

                all_locations = []
                for tenant_id in tenants:
                    discovered = None

                    # [PRIORITY 1] Discover from Orion-LD: find AgriParcel entities
                    # for this tenant and resolve their municipalities via PostGIS.
                    try:
                        discovered = self._discover_locations_from_parcels(
                            conn, cursor, tenant_id
                        )
                    except Exception as parcel_err:
                        logger.warning(
                            f"Parcel discovery failed for tenant {tenant_id}: {parcel_err}"
                        )
                        conn.rollback()

                    # [PRIORITY 2] Reuse municipalities from previous weather ingestions
                    if not discovered:
                        try:
                            cursor.execute("""
                                SELECT
                                    %s as tenant_id,
                                    wo.municipality_code,
                                    cm.latitude,
                                    cm.longitude,
                                    NULL as station_id,
                                    cm.name as label
                                FROM weather_observations wo
                                JOIN catalog_municipalities cm ON cm.ine_code = wo.municipality_code
                                WHERE wo.tenant_id = %s
                                  AND cm.latitude IS NOT NULL
                                  AND cm.longitude IS NOT NULL
                                GROUP BY wo.municipality_code, cm.latitude, cm.longitude, cm.name
                                ORDER BY MAX(wo.observed_at) DESC
                                LIMIT 1
                            """, (tenant_id, tenant_id))
                            discovered = cursor.fetchone()
                        except Exception:
                            conn.rollback()

                    # [PRIORITY 3] Pick the nearest municipality from catalog
                    if not discovered:
                        try:
                            cursor.execute("""
                                SELECT
                                    %s as tenant_id,
                                    cm.ine_code as municipality_code,
                                    cm.latitude,
                                    cm.longitude,
                                    NULL as station_id,
                                    cm.name as label
                                FROM catalog_municipalities cm
                                WHERE cm.latitude IS NOT NULL
                                  AND cm.longitude IS NOT NULL
                                ORDER BY cm.name
                                LIMIT 1
                            """, (tenant_id,))
                            discovered = cursor.fetchone()
                        except Exception:
                            conn.rollback()

                    if discovered:
                        all_locations.append(dict(discovered))
                        logger.info(f"Discovered weather location for tenant {tenant_id}: "
                                    f"{discovered.get('municipality_code')} ({discovered.get('label')})")

                if all_locations:
                    logger.info(f"Discovered {len(all_locations)} fallback weather locations")
                    return all_locations

                logger.warning("No municipalities with coordinates found — weather ingestion will be skipped")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching tenant weather locations: {e}", exc_info=True)
            # Ensure connection is in a good state
            try:
                if conn:
                    conn.rollback()
            except:
                pass
            return []

