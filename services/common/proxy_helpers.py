"""Safe upstream proxy responses — CodeQL reflected XSS (A5)."""

from __future__ import annotations

from typing import Any

from flask import Response, jsonify, make_response

_JSON_PREFIXES = ("application/json", "application/ld+json", "application/problem+json")


def _is_json_content_type(content_type: str) -> bool:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if not base:
        return False
    if base in _JSON_PREFIXES:
        return True
    return base.endswith("+json")


def safe_json_proxy_response(
    resp: Any,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Return upstream body only when Content-Type is JSON; strip hop-by-hop headers."""
    content_type = resp.headers.get("Content-Type", "")
    if not _is_json_content_type(content_type):
        return jsonify({"error": "Upstream returned non-JSON response"}), 502

    out_headers: dict[str, str] = {}
    for key, value in resp.headers.items():
        lower = key.lower()
        if lower in {
            "content-encoding",
            "transfer-encoding",
            "set-cookie",
            "content-security-policy",
            "content-length",
        }:
            continue
        out_headers[key] = value
    out_headers["Content-Type"] = content_type.split(";", 1)[0].strip()
    if extra_headers:
        out_headers.update(extra_headers)

    return make_response(resp.content, resp.status_code, out_headers)
