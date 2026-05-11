"""
Dynamic module routing for api-gateway.

Routes /api/modules/<name>/* to the module's backend service.
Uses feature flags from MODULE_GATEWAY_ENABLED env var to control
which modules go through gateway vs direct ingress.
"""

import os
from typing import Optional

import httpx
from flask import Blueprint, request, Response, g

module_bp = Blueprint("module_routes", __name__, url_prefix="/api/modules")

MODULE_SERVICE_URL = os.getenv(
    "MODULE_SERVICE_URL_PATTERN",
    "http://{module_id}-backend-service.nekazari.svc.cluster.local:8000",
)


def _resolve_module_backend(module_name: str) -> Optional[str]:
    gateway_enabled = os.getenv("MODULE_GATEWAY_ENABLED", "").split(",")
    if module_name not in gateway_enabled:
        return None
    return MODULE_SERVICE_URL.format(module_id=module_name)


@module_bp.route(
    "/<module_name>/",
    defaults={"subpath": ""},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
@module_bp.route(
    "/<module_name>/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def proxy_module_request(module_name: str, subpath: str):
    backend_url = _resolve_module_backend(module_name)

    if backend_url is None:
        return {
            "error": "Module not routed through gateway",
            "module": module_name,
        }, 404

    target_url = f"{backend_url}/{subpath}" if subpath else backend_url

    headers = {
        "X-Tenant-ID": getattr(g, "tenant_id", ""),
        "X-User-ID": getattr(g, "user_id", ""),
        "X-User-Roles": getattr(g, "user_roles", ""),
        "X-Request-ID": request.headers.get("X-Request-ID", ""),
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }
    headers = {k: v for k, v in headers.items() if v}

    try:
        resp = httpx.request(
            method=request.method,
            url=target_url,
            headers=headers,
            params=request.args,
            content=request.get_data(),
            timeout=30.0,
        )
        return Response(
            resp.content, status=resp.status_code, headers=dict(resp.headers)
        )
    except httpx.RequestError as e:
        return {"error": f"Module backend unreachable: {e}"}, 502
