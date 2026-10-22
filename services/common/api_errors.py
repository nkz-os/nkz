#!/usr/bin/env python3
"""Sanitized Flask API error responses (CodeQL stack-trace exposure / A1)."""

from __future__ import annotations

import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def internal_error(
    exc: BaseException,
    context: str,
    *,
    status: int = 500,
    user_message: str = "Internal server error",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
):
    """Log full exception server-side; return a generic body to the client."""
    # Lazy import: this Flask helper lives beside the FastAPI variant, but
    # FastAPI services (e.g. weather-api) import the module without Flask
    # installed. A module-level Flask import crash-loops them on startup.
    from flask import jsonify

    request_id = secrets.token_hex(8)
    logger.error(
        "[%s] request_id=%s: %s: %s",
        context,
        request_id,
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    body: dict[str, Any] = {
        "error": user_message,
        "request_id": request_id,
    }
    if error_code:
        body["error_code"] = error_code
    if extra:
        body.update(extra)
    return jsonify(body), status


def fastapi_internal_error(
    exc: BaseException,
    context: str,
    *,
    status: int = 500,
    user_message: str = "Internal server error",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
):
    """FastAPI variant: log server-side, raise HTTPException with generic body."""
    from fastapi import HTTPException

    request_id = secrets.token_hex(8)
    logger.error(
        "[%s] request_id=%s: %s: %s",
        context,
        request_id,
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    detail: dict[str, Any] = {
        "error": user_message,
        "request_id": request_id,
    }
    if error_code:
        detail["error_code"] = error_code
    if extra:
        detail.update(extra)
    raise HTTPException(status_code=status, detail=detail) from exc
