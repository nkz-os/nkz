"""
Weather API — standalone FastAPI service for all weather endpoints.
Extracted from entity-manager (blueprints/weather.py).

Routes:
  GET  /api/weather/municipalities/search
  GET  /api/weather/locations
  POST /api/weather/locations
  GET  /api/weather/municipality/near
  GET  /api/weather/observations/latest
  GET  /api/weather/observations
  GET  /api/weather/parcel/{parcel_id}
  GET  /api/weather/parcel/{parcel_id}/agro-status
  GET  /api/weather/alerts
  OPTIONS /api/weather/{subpath:path}
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import (
    alerts,
    locations,
    municipalities,
    observations,
    parcels,
    preflight,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Weather API",
    description="Standalone weather service for Nekazari platform",
    version="1.0.0",
)

# CORS — preflight handled explicitly via OPTIONS router
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins) or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(municipalities.router)
app.include_router(locations.router)
app.include_router(observations.router)
app.include_router(parcels.router)
app.include_router(alerts.router)
app.include_router(preflight.router)


@app.get("/health")
def health():
    """Liveness probe."""
    return {"status": "healthy", "service": "weather-api"}


@app.get("/health/deep")
def health_deep():
    """Readiness probe — checks DB and Orion-LD connectivity."""
    checks: dict = {}
    overall_ok = True

    # DB check
    try:
        import psycopg2
        conn = psycopg2.connect(settings.postgres_url)
        conn.close()
        checks["database"] = {"status": "up"}
    except Exception as exc:
        checks["database"] = {"status": "down", "error": str(exc)}
        overall_ok = False

    # Orion-LD check
    try:
        import httpx
        r = httpx.get(f"{settings.orion_url}/version", timeout=2.0)
        checks["orion_ld"] = {
            "status": "up" if r.status_code < 500 else "down",
            "http_status": r.status_code,
        }
        if r.status_code >= 500:
            overall_ok = False
    except Exception as exc:
        checks["orion_ld"] = {"status": "unreachable", "error": str(exc)}
        overall_ok = False

    return JSONResponse(
        content={"status": "ok" if overall_ok else "degraded", "checks": checks},
        status_code=200 if overall_ok else 503,
    )
