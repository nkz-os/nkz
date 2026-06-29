#!/usr/bin/env python3
"""Sanitized Flask API error responses (CodeQL stack-trace exposure / A1)."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from flask import jsonify

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
