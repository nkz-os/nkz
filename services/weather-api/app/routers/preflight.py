"""
OPTIONS /api/weather/{subpath:path} — CORS preflight for all weather routes.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["cors"])


@router.options("/{subpath:path}")
def weather_cors_preflight(subpath: str, request: Request):
    """Explicit OPTIONS handler for all /api/weather/* routes."""
    origin = request.headers.get("Origin")
    requested_method = request.headers.get("Access-Control-Request-Method", "GET")
    requested_headers = request.headers.get("Access-Control-Request-Headers", "")

    logger.debug(
        f"CORS Preflight OPTIONS /api/weather/{subpath}, "
        f"origin={origin}, method={requested_method}, headers={requested_headers}"
    )

    resp = Response(content="{}", status_code=200, media_type="application/json")

    if origin and origin in settings.allowed_origins:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Tenant-ID, x-tenant-id, X-Auth-Signature"
        )
        resp.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        )
        resp.headers["Access-Control-Max-Age"] = "86400"

    return resp
