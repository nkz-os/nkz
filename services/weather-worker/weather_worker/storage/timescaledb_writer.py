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

# NOTE: Orion-LD sync for parcel weather is now handled by ParcelWeatherEngine.
# The old try/except import of sync_weather_to_orion has been removed.
# See: weather_worker/parcel_engine.py


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
        """Write weather observations to TimescaleDB.

        FIWARE COMPLIANCE NOTE: Writes directly to weather_observations table,
        then syncs back to Orion-LD via sync_weather_to_orion().
        The dual-write pattern (DB then Orion) is deliberate: weather data must
        be available for TimescaleDB continuous aggregates immediately, while
        Orion-LD entity updates are asynchronous.

        TODO (FIWARE certification): Reverse the order — write to Orion-LD first,
        use subscription notification to populate TimescaleDB. The subscription
        infrastructure already exists (telemetry-worker subscription_manager.py).

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
                        humidity_avg, precip_mm, precip_probability,
                        solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                        eto_mm,
                        soil_moisture_0_10cm, soil_moisture_10_40cm,
                        wind_speed_ms, wind_gusts_ms, wind_direction_deg, pressure_hpa,
                        gdd_accumulated, delta_t,
                        location,
                        metrics, metadata
                    ) VALUES %s
                    ON CONFLICT (tenant_id, municipality_code, COALESCE(station_id, ''), observed_at)
                    DO UPDATE SET
                        temp_avg = EXCLUDED.temp_avg,
                        temp_min = EXCLUDED.temp_min,
                        temp_max = EXCLUDED.temp_max,
                        humidity_avg = EXCLUDED.humidity_avg,
                        precip_mm = EXCLUDED.precip_mm,
                        precip_probability = EXCLUDED.precip_probability,
                        solar_rad_w_m2 = EXCLUDED.solar_rad_w_m2,
                        solar_rad_ghi_w_m2 = EXCLUDED.solar_rad_ghi_w_m2,
                        solar_rad_dni_w_m2 = EXCLUDED.solar_rad_dni_w_m2,
                        eto_mm = EXCLUDED.eto_mm,
                        soil_moisture_0_10cm = EXCLUDED.soil_moisture_0_10cm,
                        soil_moisture_10_40cm = EXCLUDED.soil_moisture_10_40cm,
                        wind_speed_ms = EXCLUDED.wind_speed_ms,
                        wind_gusts_ms = EXCLUDED.wind_gusts_ms,
                        wind_direction_deg = EXCLUDED.wind_direction_deg,
                        pressure_hpa = EXCLUDED.pressure_hpa,
                        gdd_accumulated = EXCLUDED.gdd_accumulated,
                        delta_t = EXCLUDED.delta_t,
                        location = EXCLUDED.location,
                        metrics = EXCLUDED.metrics,
                        metadata = EXCLUDED.metadata,
                        source = EXCLUDED.source,
                        data_type = EXCLUDED.data_type
                """

                # Resolve location coordinates for this municipality
                location_wkt = None
                first_muni_code = observations[0].get('municipality_code') if observations else None
                if first_muni_code:
                    try:
                        cursor.execute("""
                            SELECT ST_AsText(
                                COALESCE(
                                    cm.geom,
                                    ST_SetSRID(ST_MakePoint(cm.longitude, cm.latitude), 4326)
                                )
                            ) as point_wkt
                            FROM catalog_municipalities cm
                            WHERE cm.ine_code = %s
                              AND (cm.geom IS NOT NULL
                                   OR (cm.latitude IS NOT NULL AND cm.longitude IS NOT NULL))
                            LIMIT 1
                        """, (first_muni_code,))
                        loc_row = cursor.fetchone()
                        if loc_row and loc_row[0]:
                            location_wkt = loc_row[0]
                    except Exception as loc_err:
                        logger.warning(f"Could not resolve location for {first_muni_code}: {loc_err}")

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
                        obs.get('precip_probability'),
                        obs.get('solar_rad_w_m2'),
                        obs.get('solar_rad_ghi_w_m2'),
                        obs.get('solar_rad_dni_w_m2'),
                        obs.get('eto_mm'),
                        obs.get('soil_moisture_0_10cm'),
                        obs.get('soil_moisture_10_40cm'),
                        obs.get('wind_speed_ms'),
                        obs.get('wind_gusts_ms'),
                        obs.get('wind_direction_deg'),
                        obs.get('pressure_hpa'),
                        obs.get('gdd_accumulated'),
                        obs.get('delta_t'),
                        location_wkt,
                        Json(obs.get('metrics', {})),
                        Json(obs.get('metadata', {}))
                    ))
                
                if values:
                    execute_values(cursor, insert_query, values)
                    conn.commit()
                    logger.info(f"Inserted {len(values)} weather observations for tenant {tenant_id}")

                    # NOTE: Orion-LD sync is now handled by ParcelWeatherEngine
                    # (parcel-driven, per-parcel downscaling). The municipality
                    # worker no longer writes WeatherObserved entities.
                    # See: weather_worker/parcel_engine.py

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
                            ST_Y(wo.location) as latitude,
                            ST_X(wo.location) as longitude,
                            twl.station_id,
                            twl.label
                        FROM tenant_weather_locations twl
                        JOIN weather_observations wo ON wo.municipality_code = twl.municipality_code
                            AND wo.tenant_id = twl.tenant_id
                        WHERE wo.location IS NOT NULL
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

                    # [PRIORITY 1] REMOVED — Parcel discovery now handled by
                    # ParcelWeatherEngine. Municipality worker no longer
                    # writes to tenant_weather_locations.

                    # [PRIORITY 2] Reuse municipalities from previous weather ingestions
                    if not discovered:
                        try:
                            cursor.execute("""
                                SELECT
                                    %s as tenant_id,
                                    wo.municipality_code,
                                    ST_Y(wo.location) as latitude,
                                    ST_X(wo.location) as longitude,
                                    NULL as station_id,
                                    cm.name as label
                                FROM weather_observations wo
                                LEFT JOIN catalog_municipalities cm ON cm.ine_code = wo.municipality_code
                                WHERE wo.tenant_id = %s
                                  AND wo.location IS NOT NULL
                                GROUP BY wo.municipality_code, wo.location, cm.name
                                ORDER BY MAX(wo.observed_at) DESC
                                LIMIT 1
                            """, (tenant_id, tenant_id))
                            discovered = cursor.fetchone()
                        except Exception:
                            conn.rollback()

                    # [PRIORITY 3] Pick the most recently observed municipality
                    if not discovered:
                        try:
                            cursor.execute("""
                                SELECT
                                    %s as tenant_id,
                                    wo.municipality_code,
                                    ST_Y(wo.location) as latitude,
                                    ST_X(wo.location) as longitude,
                                    NULL as station_id,
                                    cm.name as label
                                FROM weather_observations wo
                                LEFT JOIN catalog_municipalities cm ON cm.ine_code = wo.municipality_code
                                WHERE wo.location IS NOT NULL
                                ORDER BY wo.observed_at DESC
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

