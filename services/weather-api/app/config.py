"""Environment configuration for weather-api."""

import os


class Settings:
    """Configuration from environment variables with sensible defaults."""

    def __init__(self):
        self.cors_origins = os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
        )
        self.orion_url = os.getenv("ORION_URL", "http://orion-ld-service:1026").rstrip(
            "/"
        )
        self.context_url = os.getenv("CONTEXT_URL", "")
        self.postgres_url = os.getenv("POSTGRES_URL", "")
        # Base URL ending in /v1; callers append /forecast
        self.openmeteo_api_url = os.getenv(
            "OPENMETEO_API_URL", "https://api.open-meteo.com/v1"
        ).rstrip("/")
        # Comma-separated model list; empty = do not send `models`
        self.openmeteo_models = os.getenv("OPENMETEO_MODELS", "").strip()
        # Archive base URL ending in /v1 (callers append /archive); empty = archive
        # fill disabled. Comma-separated archive models; empty = do not send.
        self.openmeteo_archive_url = os.getenv("OPENMETEO_ARCHIVE_URL", "").rstrip("/")
        self.openmeteo_archive_models = os.getenv("OPENMETEO_ARCHIVE_MODELS", "").strip()
        self.aemet_api_key = os.getenv("AEMET_API_KEY", "")
        self.soil_api_url = os.getenv("SOIL_API_URL", "http://soil-module-service:8000")
        self.weather_map_url = os.getenv(
            "WEATHER_MAP_URL", "http://weather-map-backend:8080"
        ).rstrip("/")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

        # Auth — not used directly by this service (api-gateway validates),
        # but provided for completeness
        self.jwt_secret = os.getenv("JWT_SECRET", "")
        self.keycloak_url = os.getenv("KEYCLOAK_URL", "")
        self.keycloak_realm = os.getenv("KEYCLOAK_REALM", "nekazari")

    @property
    def allowed_origins(self) -> set[str]:
        return {o.strip() for o in self.cors_origins.split(",") if o.strip()}


settings = Settings()


def with_models(params: dict) -> dict:
    """Return Open-Meteo params with `models` added only when configured."""
    if settings.openmeteo_models:
        return {**params, "models": settings.openmeteo_models}
    return params
