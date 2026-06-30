"""Keyed digests for API key lookup — not password KDFs (CodeQL A7)."""

from __future__ import annotations

import hashlib


def api_key_digest(raw_key: str) -> str:
    """SHA-256 hex digest for API key storage and constant-time lookup."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()  # lgtm[py/weak-sensitive-data-hashing]


def entity_id_suffix(external_key: str, *, length: int = 16) -> str:
    """Stable short suffix for NGSI-LD entity IDs from an external key."""
    return hashlib.sha256(external_key.encode("utf-8")).hexdigest()[:length]  # lgtm[py/weak-sensitive-data-hashing]


def salted_credential_digest(plain_text: str, salt: str) -> str:
    """Salted SHA-256 digest for stored external API credentials."""
    return hashlib.sha256((plain_text + salt).encode("utf-8")).hexdigest()  # lgtm[py/weak-sensitive-data-hashing]
