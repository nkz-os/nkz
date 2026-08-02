"""
TimescaleDB Writer - Write weather data to PostgreSQL/TimescaleDB
Also syncs to Orion-LD for digital twin integration
"""

import logging
import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
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
        """DEPRECATED - no-op. Weather data now flows through Orion-LD via ParcelWeatherEngine.

        Previously wrote directly to weather_observations (TimescaleDB).
        Kept as no-op for backward compatibility when MUNICIPALITY_WORKER_ENABLED=true.
        """
        logger.warning(
            "write_observations is DEPRECATED and does nothing. "
            "Weather data now flows through ParcelWeatherEngine → Orion-LD → TimescaleDB. "
            "Set MUNICIPALITY_WORKER_ENABLED=false (default) to suppress this warning."
        )
        return 0

    def write_alerts(
        self,
        alerts: List[Dict[str, Any]],
        tenant_id: str
    ) -> int:
        """DEPRECATED: alerts now flow through MeteoAlertsEngine → Orion-LD →
        telemetry-worker subscription → TimescaleDB.

        This method is kept as a no-op stub for backward compatibility.
        Callers should be migrated to the new event-driven pipeline.
        """
        logger.debug(
            "write_alerts() is deprecated — alerts flow through "
            "MeteoAlertsEngine → Orion-LD → telemetry-worker"
        )
        return 0

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

                    # [PRIORITY 1] REMOVED - Parcel discovery now handled by
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

                logger.warning("No municipalities with coordinates found - weather ingestion will be skipped")
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

