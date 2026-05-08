"""Canonical NGSI-LD header injection — single source of truth for FIWARE compliance.

ETSI NGSI-LD spec rule (mutual exclusivity):
  - @context in body  → Content-Type: application/ld+json, NO Link header
  - @context NOT in body → Content-Type: application/json, Link header with @context URL

NEVER set both Content-Type: application/ld+json AND a Link header simultaneously.
"""

import os
import re
from typing import Dict, Optional


def inject_fiware_headers(
    headers: Dict[str, str],
    tenant: Optional[str] = None,
    has_context_in_body: bool = False,
) -> Dict[str, str]:
    """Inject NGSI-LD + FIWARE tenant headers for Orion-LD multitenancy.

    Args:
        headers: Existing headers dict. Modified in-place AND returned.
        tenant: Raw tenant ID (will be normalized).
        has_context_in_body: True if the JSON body already contains an @context key.
            Determines Content-Type and whether Link header is added.

    Returns:
        The same dict (modified in-place) for chaining convenience.
    """
    # ── Tenant headers (both NGSI-LD standard + legacy FIWARE v2) ──
    if tenant:
        headers["NGSILD-Tenant"] = _normalize_tenant(tenant)
        headers["Fiware-Service"] = _normalize_tenant(tenant)
        headers["Fiware-ServicePath"] = "/"

    # ── @context delivery (MUTUALLY EXCLUSIVE per ETSI spec) ──
    context_url = os.getenv("CONTEXT_URL", "")

    if has_context_in_body:
        headers["Content-Type"] = "application/ld+json"
    else:
        headers["Content-Type"] = "application/json"
        if context_url:
            headers["Link"] = (
                f"<{context_url}>; "
                f'rel="http://www.w3.org/ns/json-ld#context"; '
                f'type="application/ld+json"'
            )

    headers.setdefault("Accept", "application/ld+json")
    return headers


def _normalize_tenant(tenant: str) -> str:
    """Normalize tenant ID: lowercase, hyphens→underscores, alphanum+underscore only."""
    normalized = tenant.lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return normalized.strip("_") or tenant
