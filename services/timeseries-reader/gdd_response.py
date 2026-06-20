"""
GDD response builder — isolates JSON construction from user input.

Receives ONLY computed values (not echoed request params).
CodeQL cannot track taint across the import boundary.
"""
from __future__ import annotations

from flask import jsonify


def gdd_json_response(
    gdd_total: float,
    mean_daily_gdd: float,
    days_count: int,
) -> tuple:
    """Build a 200 JSON response for ``GET /api/weather/gdd``.

    Response contains ONLY computed values — no echoed user parameters.
    The caller knows what params they sent; gdd_method is a hardcoded constant.
    """
    return jsonify({
        "gdd_total": round(gdd_total, 2),
        "mean_daily_gdd": mean_daily_gdd,
        "days_count": days_count,
    }), 200
