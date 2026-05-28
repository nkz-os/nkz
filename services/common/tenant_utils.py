# services/common/tenant_utils.py
"""Canonical tenant ID normalization.

This module is the single source of truth for tenant ID format across the
entire Nekazari platform. Every service that handles tenant_id values
(api-gateway, tenant-webhook, entity-manager, risk-worker, etc.) MUST use
`normalize_tenant_id()` from here and MUST NOT implement its own variant.

Format (K8s-native, MongoDB-safe, Keycloak-compatible):
  - canonical regex: ^[a-z0-9]+(?:-[a-z0-9]+)*$ (see TENANT_ID_PATTERN)
  - lowercase letters, digits, hyphens only — no leading/trailing/consecutive hyphens
  - NFD-transliterated for accents (á->a, ñ->n, ç->c, ...)
  - whitespace and any non-alphanumeric collapse to a single '-'
  - 3..47 chars (K8s ns max 63 minus 'nekazari-tenant-' prefix)
  - idempotent
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# K8s namespace RFC 1123 max length is 63. We reserve `nekazari-tenant-` (16 chars)
# so the bare tenant_id can be up to 47 chars.
MIN_TENANT_ID_LENGTH = 3
MAX_TENANT_ID_LENGTH = 47

TENANT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_tenant_id(tenant_id: str) -> str:
    """Normalize an arbitrary string into a canonical tenant ID.

    Raises ValueError when the input is empty or normalizes to something
    outside the canonical bounds. Callers must be ready to surface the
    ValueError as a 400 to the client.
    """
    if tenant_id is None or not isinstance(tenant_id, str):
        raise ValueError("Tenant ID cannot be empty")

    raw = tenant_id.strip()
    if not raw:
        raise ValueError("Tenant ID cannot be empty")

    # Decompose unicode and drop combining marks (accents).
    nfd = unicodedata.normalize("NFD", raw)
    ascii_only = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    ascii_only = ascii_only.lower()

    # Collapse anything that is not [a-z0-9] into a hyphen.
    collapsed = re.sub(r"[^a-z0-9]+", "-", ascii_only)

    # Strip leading/trailing hyphens (consecutive ones already collapsed).
    normalized = collapsed.strip("-")

    if not normalized:
        raise ValueError(
            f"Tenant ID is empty after normalization (from {tenant_id!r})"
        )

    if len(normalized) < MIN_TENANT_ID_LENGTH:
        raise ValueError(
            f"Tenant ID must be at least {MIN_TENANT_ID_LENGTH} characters "
            f"after normalization. Got: {normalized!r} (from {tenant_id!r})"
        )

    if len(normalized) > MAX_TENANT_ID_LENGTH:
        raise ValueError(
            f"Tenant ID must be at most {MAX_TENANT_ID_LENGTH} characters "
            f"after normalization. Got: {normalized!r} (from {tenant_id!r})"
        )

    if not TENANT_ID_PATTERN.match(normalized):
        # Defensive: should be unreachable because of the collapse above.
        raise ValueError(
            f"Tenant ID contains invalid characters after normalization. "
            f"Got: {normalized!r} (from {tenant_id!r})"
        )

    return normalized


def validate_tenant_id(tenant_id: str) -> tuple[bool, str]:
    """Validate a tenant ID without modifying it.

    Returns (is_valid, error_message). Error messages are in English and
    use stable keywords ("empty", "length", "character", "leading",
    "trailing", "consecutive") so they can be mapped to i18n keys on the
    frontend.
    """
    if not tenant_id or not isinstance(tenant_id, str):
        return False, "Tenant ID cannot be empty"

    if len(tenant_id) < MIN_TENANT_ID_LENGTH or len(tenant_id) > MAX_TENANT_ID_LENGTH:
        return False, (
            f"Tenant ID length must be between {MIN_TENANT_ID_LENGTH} and "
            f"{MAX_TENANT_ID_LENGTH} characters."
        )

    if tenant_id.startswith("-"):
        return False, "Tenant ID must not have a leading hyphen."
    if tenant_id.endswith("-"):
        return False, "Tenant ID must not have a trailing hyphen."
    if "--" in tenant_id:
        return False, "Tenant ID must not contain consecutive hyphens."
    if not TENANT_ID_PATTERN.match(tenant_id):
        return False, (
            "Tenant ID may only contain lowercase letters, digits and hyphens "
            "(no spaces, underscores, accents or other characters)."
        )

    return True, ""


def get_tenant_id_validation_rules() -> dict:
    """Return a JSON-serializable description of the rules for frontend display."""
    return {
        "min_length": MIN_TENANT_ID_LENGTH,
        "max_length": MAX_TENANT_ID_LENGTH,
        "pattern": TENANT_ID_PATTERN.pattern,
        "allowed_chars": "lowercase letters (a-z), digits (0-9), hyphen (-)",
        "description": (
            f"Tenant ID must be {MIN_TENANT_ID_LENGTH}-{MAX_TENANT_ID_LENGTH} "
            "characters. Only lowercase letters, digits and hyphens are allowed. "
            "It must not start or end with a hyphen and must not contain "
            "consecutive hyphens."
        ),
    }
