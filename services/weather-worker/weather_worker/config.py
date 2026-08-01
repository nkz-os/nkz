"""
Configuration for Weather Worker
"""

import os
from typing import Optional


class WeatherWorkerConfig:
    """Configuration for Weather Worker service"""
    
    # PostgreSQL
    POSTGRES_URL: Optional[str] = os.getenv('POSTGRES_URL')
    POSTGRES_USER: Optional[str] = os.getenv('POSTGRES_USER', 'nekazari')
    POSTGRES_PASSWORD: Optional[str] = os.getenv('POSTGRES_PASSWORD')
    POSTGRES_HOST: Optional[str] = os.getenv('POSTGRES_HOST', 'postgresql-service')
    POSTGRES_PORT: Optional[str] = os.getenv('POSTGRES_PORT', '5432')
    POSTGRES_DB: Optional[str] = os.getenv('POSTGRES_DB', 'nekazari')
    
    # Open-Meteo API (Primary source)
    OPENMETEO_API_URL: str = os.getenv('OPENMETEO_API_URL', 'https://api.open-meteo.com/v1')
    
    # AEMET API (Secondary source - alerts only)
    AEMET_API_KEY: Optional[str] = os.getenv('AEMET_API_KEY')
    AEMET_API_URL: str = os.getenv('AEMET_API_URL', 'https://opendata.aemet.es/opendata/api')
    
    # Ingestion settings
    WEATHER_INGESTION_INTERVAL_HOURS: int = int(os.getenv('WEATHER_INGESTION_INTERVAL_HOURS', '1'))
    FORECAST_DAYS: int = int(os.getenv('WEATHER_FORECAST_DAYS', '14'))

    # Parcel Engine settings (parcel-driven weather ingestion)
    PARCEL_ENGINE_ENABLED: bool = os.getenv('PARCEL_ENGINE_ENABLED', 'true').lower() == 'true'
    PARCEL_ENGINE_INTERVAL_HOURS: int = int(os.getenv('PARCEL_ENGINE_INTERVAL_HOURS', '2'))
    PARCEL_ENGINE_CLUSTER_RADIUS_KM: float = float(os.getenv('PARCEL_ENGINE_CLUSTER_RADIUS_KM', '2.0'))
    PARCEL_ENGINE_MAX_PARCELS: int = int(os.getenv('PARCEL_ENGINE_MAX_PARCELS', '500'))

    # Municipality worker (DEPRECATED — disabled by default, use ParcelEngine)
    MUNICIPALITY_WORKER_ENABLED: bool = os.getenv('MUNICIPALITY_WORKER_ENABLED', 'false').lower() == 'true'

    # MeteoAlarm Alerts Engine (EU-wide, EDR API — supersedes legacy Atom/EMMA)
    METEOALARM_ENABLED: bool = os.getenv('METEOALARM_ENABLED', 'true').lower() == 'true'
    AEMET_ALERTS_ENABLED: bool = os.getenv('AEMET_ALERTS_ENABLED', 'true').lower() == 'true'  # backward compat
    AEMET_ALERTS_INTERVAL_HOURS: int = int(os.getenv('AEMET_ALERTS_INTERVAL_HOURS', '1'))

    # MeteoAlarm EDR API (replaces legacy Atom feeds + EMMA zones)
    EDR_BASE_URL: str = os.getenv('EDR_BASE_URL', 'https://api.meteoalarm.org/edr/v1')
    EDR_SENT_WINDOW_HOURS: int = int(os.getenv('EDR_SENT_WINDOW_HOURS', '23'))
    EDR_ACTIVE_WINDOW_HOURS: int = int(os.getenv('EDR_ACTIVE_WINDOW_HOURS', '6'))
    METEOALARM_API_KEY: str = os.getenv('METEOALARM_API_KEY', '')
    
    # Metrics
    METRICS_HOST: str = os.getenv('METRICS_HOST', '0.0.0.0')
    METRICS_PORT: int = int(os.getenv('METRICS_PORT', '9106'))
    
    # Logging
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    @classmethod
    def build_postgres_url(cls) -> Optional[str]:
        """Build PostgreSQL URL from components if password is available"""
        if cls.POSTGRES_PASSWORD:
            return f"postgresql://{cls.POSTGRES_USER}:{cls.POSTGRES_PASSWORD}@{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}"
        return cls.POSTGRES_URL

