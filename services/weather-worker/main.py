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
from typing import List

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
        # Failures here must NOT kill this thread (the hourly prune depends on it).
        if self.config.METEOALARM_MQTT_ENABLED and self.config.METEOALARM_API_KEY:
            try:
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
            except Exception:
                logger.exception(
                    "Failed to start MQTT listener — continuing with prune-only loop"
                )

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

        # Municipality worker legacy path removed (2026-08-03): its writes are
        # a no-op since the Orion-LD migration (TimescaleDBWriter.write_observations
        # deprecated stub). validate_startup_config() refuses to start if
        # MUNICIPALITY_WORKER_ENABLED=true, so this branch is unreachable by
        # the time run() executes — only the parcel-engine-only path remains.
        logger.info(
            "Municipality worker disabled — parcel engine only. "
            "Municipality forecasts are served statelessly by weather-api."
        )
        try:
            while True:
                time.sleep(60)  # keep main thread alive
        except KeyboardInterrupt:
            logger.info("Weather Worker stopped by user")


def validate_startup_config(config):
    """Fail fast on startup config that would silently discard data.

    MUNICIPALITY_WORKER_ENABLED enables a legacy ingestion path whose writes
    are a no-op since the Orion-LD migration (TimescaleDBWriter.write_observations
    is a deprecated stub returning 0). Running it would silently discard
    weather data instead of erroring, so refuse to start (fail-safe).
    """
    if config.MUNICIPALITY_WORKER_ENABLED:
        raise SystemExit(
            "MUNICIPALITY_WORKER_ENABLED=true is no longer supported: "
            "weather data flows via ParcelWeatherEngine -> Orion-LD. Unset the flag."
        )


def main():
    """Main entry point"""
    validate_startup_config(WeatherWorkerConfig)

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
        listener = getattr(worker, "_mqtt_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.exception("Error stopping MQTT listener")
        worker.storage.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run worker
    worker.run()


if __name__ == '__main__':
    main()

