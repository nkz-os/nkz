#!/usr/bin/env python3
# =============================================================================
# Weather Worker - Agroclimatic Intelligence Module
# =============================================================================

import os
import sys
import logging
import signal
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List

# Add paths for imports
sys.path.insert(0, '/app/common')
sys.path.insert(0, '/app/weather-worker')

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from weather_worker.config import WeatherWorkerConfig
from weather_worker.providers import OpenMeteoProvider, AEMETProvider
from weather_worker.processors import MetricsCalculator, DataTransformer
from weather_worker.storage import TimescaleDBWriter

# Configure logging
logging.basicConfig(
    level=getattr(logging, WeatherWorkerConfig.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
WEATHER_OBSERVATIONS_TOTAL = Counter(
    'weather_observations_total',
    'Total weather observations ingested',
    ['source', 'data_type', 'status']
)

WEATHER_ALERTS_TOTAL = Counter(
    'weather_alerts_total',
    'Total weather alerts ingested',
    ['alert_type', 'status']
)

WEATHER_INGESTION_DURATION = Histogram(
    'weather_ingestion_duration_seconds',
    'Duration of weather data ingestion',
    ['source']
)

WEATHER_INGESTION_IN_PROGRESS = Gauge(
    'weather_ingestion_in_progress',
    'Weather ingestion currently in progress'
)


class WeatherWorker:
    """Weather data ingestion worker"""
    
    def __init__(self):
        """Initialize weather worker"""
        self.config = WeatherWorkerConfig
        self.postgres_url = self.config.build_postgres_url()
        
        if not self.postgres_url:
            raise ValueError("POSTGRES_URL not configured")
        
        # Initialize providers
        self.openmeteo = OpenMeteoProvider(api_url=self.config.OPENMETEO_API_URL)
        self.aemet = None
        if self.config.AEMET_API_KEY:
            self.aemet = AEMETProvider(
                api_key=self.config.AEMET_API_KEY,
                api_url=self.config.AEMET_API_URL
            )
        else:
            logger.warning("AEMET_API_KEY not configured - alerts will not be fetched")
        
        # Initialize processors
        self.metrics_calculator = MetricsCalculator()
        self.data_transformer = DataTransformer()
        
        # Initialize storage
        self.storage = TimescaleDBWriter(self.postgres_url)
        
        logger.info("Weather Worker initialized")
    
    def ingest_weather_data(
        self,
        tenant_id: str,
        municipality_code: str,
        latitude: float,
        longitude: float
    ) -> Dict[str, Any]:
        """
        Ingest weather data for a specific location
        
        Args:
            tenant_id: Tenant ID
            municipality_code: Municipality INE code
            latitude: Latitude
            longitude: Longitude
        
        Returns:
            Ingestion result dictionary
        """
        result = {
            'tenant_id': tenant_id,
            'municipality_code': municipality_code,
            'observations_history': 0,
            'observations_forecast': 0,
            'alerts': 0,
            'errors': []
        }
        
        try:
            # 1. Ingest historical data (yesterday to close real data)
            logger.info(f"Ingesting historical weather for {municipality_code}")
            with WEATHER_INGESTION_DURATION.labels(source='OPEN-METEO').time():
                yesterday = datetime.now() - timedelta(days=1)
                today = datetime.now()
                
                historical_data = self.openmeteo.get_historical_weather(
                    latitude=latitude,
                    longitude=longitude,
                    start_date=yesterday,
                    end_date=today
                )
                
                if historical_data:
                    # Enrich with metrics
                    enriched = []
                    for obs in historical_data:
                        enriched_obs = self.metrics_calculator.enrich_observation(obs)
                        transformed = self.data_transformer.transform_observation(
                            enriched_obs,
                            tenant_id=tenant_id,
                            municipality_code=municipality_code,
                            source='OPEN-METEO',
                            data_type='HISTORY'
                        )
                        if transformed:
                            enriched.append(transformed)
                    
                    if enriched:
                        count = self.storage.write_observations(enriched, tenant_id)
                        result['observations_history'] = count
                        WEATHER_OBSERVATIONS_TOTAL.labels(
                            source='OPEN-METEO',
                            data_type='HISTORY',
                            status='success'
                        ).inc(count)
            
            # 2. Ingest forecast data (7-14 days for simulations)
            logger.info(f"Ingesting forecast weather for {municipality_code}")
            with WEATHER_INGESTION_DURATION.labels(source='OPEN-METEO').time():
                forecast_data = self.openmeteo.get_forecast(
                    latitude=latitude,
                    longitude=longitude,
                    days=self.config.FORECAST_DAYS
                )
                
                if forecast_data:
                    # Enrich with metrics
                    enriched = []
                    for obs in forecast_data:
                        enriched_obs = self.metrics_calculator.enrich_observation(obs)
                        transformed = self.data_transformer.transform_observation(
                            enriched_obs,
                            tenant_id=tenant_id,
                            municipality_code=municipality_code,
                            source='OPEN-METEO',
                            data_type='FORECAST'
                        )
                        if transformed:
                            enriched.append(transformed)
                    
                    if enriched:
                        count = self.storage.write_observations(enriched, tenant_id)
                        result['observations_forecast'] = count
                        WEATHER_OBSERVATIONS_TOTAL.labels(
                            source='OPEN-METEO',
                            data_type='FORECAST',
                            status='success'
                        ).inc(count)
            
            # 3. Weather alerts are now handled by MeteoAlertsEngine
            #    (independent engine → Orion-LD → subscription → TimescaleDB).
            #    The municipality worker no longer writes alerts directly.
            logger.debug(
                f"Weather alerts for {municipality_code} handled by MeteoAlertsEngine"
            )
            
            logger.info(f"Completed ingestion for {municipality_code}: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error ingesting weather data: {e}")
            result['errors'].append(str(e))
            WEATHER_OBSERVATIONS_TOTAL.labels(
                source='OPEN-METEO',
                data_type='HISTORY',
                status='error'
            ).inc()
            return result
    
    def run_ingestion_cycle(self):
        """Run one ingestion cycle for all tenant weather locations"""
        logger.info("Starting weather ingestion cycle")
        WEATHER_INGESTION_IN_PROGRESS.set(1)
        
        try:
            # Get all tenant weather locations
            locations = self.storage.get_tenant_weather_locations()
            
            if not locations:
                logger.warning("No tenant weather locations found")
                return
            
            logger.info(f"Found {len(locations)} weather locations to process")
            
            total_observations = 0
            total_alerts = 0
            
            # Group by tenant to process efficiently
            tenant_locations = {}
            for loc in locations:
                tenant_id = loc['tenant_id']
                if tenant_id not in tenant_locations:
                    tenant_locations[tenant_id] = []
                tenant_locations[tenant_id].append(loc)
            
            # Process each tenant
            for tenant_id, locs in tenant_locations.items():
                logger.info(f"Processing {len(locs)} locations for tenant {tenant_id}")
                
                for loc in locs:
                    try:
                        result = self.ingest_weather_data(
                            tenant_id=tenant_id,
                            municipality_code=loc['municipality_code'],
                            latitude=loc['latitude'],
                            longitude=loc['longitude']
                        )
                        
                        total_observations += result['observations_history'] + result['observations_forecast']
                        total_alerts += result['alerts']
                        
                        # Small delay to avoid rate limiting
                        time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"Error processing location {loc['municipality_code']}: {e}")
                        continue
            
            logger.info(f"Ingestion cycle completed: {total_observations} observations, {total_alerts} alerts")
            
        finally:
            WEATHER_INGESTION_IN_PROGRESS.set(0)
    
    def _run_parcel_engine_loop(self):
        """Run parcel-driven weather engine in a background thread."""
        if not self.config.PARCEL_ENGINE_ENABLED:
            logger.info("ParcelWeatherEngine disabled via PARCEL_ENGINE_ENABLED=false")
            return

        logger.info(
            f"ParcelWeatherEngine starting: interval={self.config.PARCEL_ENGINE_INTERVAL_HOURS}h, "
            f"cluster_radius={self.config.PARCEL_ENGINE_CLUSTER_RADIUS_KM}km"
        )

        # Wait for initial DB connection and first municipality cycle
        time.sleep(10)

        from weather_worker.parcel_engine import ParcelWeatherEngine

        parcel_engine = ParcelWeatherEngine(
            orion_url=os.getenv("ORION_URL", "http://orion-ld-service:1026"),
            openmeteo_url=self.config.OPENMETEO_API_URL,
            postgres_url=self.postgres_url,
            forecast_days=self.config.FORECAST_DAYS,
            cluster_radius_km=self.config.PARCEL_ENGINE_CLUSTER_RADIUS_KM,
            max_parcels=self.config.PARCEL_ENGINE_MAX_PARCELS,
        )

        interval_seconds = self.config.PARCEL_ENGINE_INTERVAL_HOURS * 3600

        while True:
            try:
                logger.info("ParcelWeatherEngine: starting cycle")
                stats = parcel_engine.run_once()
                logger.info(f"ParcelWeatherEngine cycle stats: {stats}")
            except Exception as e:
                logger.error(f"ParcelWeatherEngine cycle failed: {e}", exc_info=True)

            time.sleep(interval_seconds)

    def _run_meteoalerts_loop(self):
        """Run MeteoAlarm alerts engine in a background thread.

        MQTT (WIS 2.0) is the primary ingestion path (push, near real-time).
        EDR polling runs hourly IF EDR_ENABLED=true; otherwise only the
        expired-alert prune fires each cycle.
        """
        enabled = (
            self.config.METEOALARM_ENABLED
            or self.config.AEMET_ALERTS_ENABLED  # backward compat
        )
        if not enabled:
            logger.info(
                "MeteoAlertsEngine disabled (METEOALARM_ENABLED=false, "
                "AEMET_ALERTS_ENABLED=false)"
            )
            return

        logger.info(
            f"MeteoAlertsEngine starting: interval={self.config.AEMET_ALERTS_INTERVAL_HOURS}h, "
            f"edr_enabled={self.config.EDR_ENABLED}, "
            f"mqtt_enabled={self.config.METEOALARM_MQTT_ENABLED}"
        )

        # Small delay to let DB connection settle
        time.sleep(5)

        from weather_worker.meteo_alerts_engine import MeteoAlertsEngine

        engine = MeteoAlertsEngine(
            orion_url=os.getenv("ORION_URL", "http://orion-ld-service:1026"),
            interval_hours=self.config.AEMET_ALERTS_INTERVAL_HOURS,
        )

        # Start the MQTT push listener if enabled (primary ingestion path).
        if self.config.METEOALARM_MQTT_ENABLED and self.config.METEOALARM_API_KEY:
            from weather_worker.mqtt_alerts import MqttWarningsListener

            self._mqtt_listener = MqttWarningsListener(
                host=self.config.METEOALARM_MQTT_HOST,
                port=self.config.METEOALARM_MQTT_PORT,
                topic=self.config.METEOALARM_MQTT_TOPIC,
                api_key=self.config.METEOALARM_API_KEY,
                on_notification=engine.handle_notification,
            )
            self._mqtt_listener.start()
            logger.info("MeteoAlarms MQTT listener started")

        # Hourly loop: EDR poll or prune-only.
        interval_seconds = self.config.AEMET_ALERTS_INTERVAL_HOURS * 3600
        while True:
            try:
                if self.config.EDR_ENABLED:
                    stats = engine.run_once()
                    logger.info("MeteoAlertsEngine EDR cycle: %s", stats)
                else:
                    engine.prune_once()
            except Exception as e:
                logger.error(
                    "MeteoAlertsEngine cycle failed: %s", e, exc_info=True
                )
            time.sleep(interval_seconds)

    def run(self):
        """Run worker in continuous mode.

        ParcelWeatherEngine always runs (handles the agronomic heart).
        MeteoAlertsEngine always runs (handles official weather alerts).
        Municipality worker is OFF by default — forecast data is ephemeral.
        """
        logger.info("Weather Worker starting in continuous mode")

        # Start parcel engine in background thread (always on)
        if self.config.PARCEL_ENGINE_ENABLED:
            self.storage.connect()
            parcel_thread = threading.Thread(
                target=self._run_parcel_engine_loop,
                daemon=True,
                name="parcel-engine",
            )
            parcel_thread.start()
            logger.info("ParcelWeatherEngine thread started")

        # Start MeteoAlarm alerts engine in background thread (always on)
        if self.config.METEOALARM_ENABLED or self.config.AEMET_ALERTS_ENABLED:
            alerts_thread = threading.Thread(
                target=self._run_meteoalerts_loop,
                daemon=True,
                name="meteoalarm-engine",
            )
            alerts_thread.start()
            logger.info("MeteoAlertsEngine thread started")

        # Municipality worker is OFF by default
        if self.config.MUNICIPALITY_WORKER_ENABLED:
            logger.info("Municipality worker enabled (legacy mode)")
            self.run_ingestion_cycle()
            interval_seconds = self.config.WEATHER_INGESTION_INTERVAL_HOURS * 3600
            logger.info(
                f"Scheduling municipality ingestion every "
                f"{self.config.WEATHER_INGESTION_INTERVAL_HOURS} hours"
            )
            try:
                while True:
                    time.sleep(interval_seconds)
                    self.run_ingestion_cycle()
            except KeyboardInterrupt:
                logger.info("Weather Worker stopped by user")
            finally:
                self.storage.close()
        else:
            logger.info(
                "Municipality worker disabled — parcel engine only. "
                "Municipality forecasts are served statelessly by weather-api."
            )
            try:
                while True:
                    time.sleep(60)  # keep main thread alive
            except KeyboardInterrupt:
                logger.info("Weather Worker stopped by user")


def main():
    """Main entry point"""
    # Start Prometheus metrics server
    try:
        start_http_server(
            WeatherWorkerConfig.METRICS_PORT,
            WeatherWorkerConfig.METRICS_HOST
        )
        logger.info(f"Prometheus metrics server started on {WeatherWorkerConfig.METRICS_HOST}:{WeatherWorkerConfig.METRICS_PORT}")
    except Exception as e:
        logger.warning(f"Failed to start metrics server: {e}")
    
    # Initialize and run worker
    worker = WeatherWorker()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        worker.storage.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run worker
    worker.run()


if __name__ == '__main__':
    main()

