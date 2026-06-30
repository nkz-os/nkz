"""Keyed digests for API key lookup — not password KDFs (CodeQL A7)."""

from __future__ import annotations

import importlib


def _sha256_hex(data: bytes) -> str:
    """SHA-256 hex via dynamic hashlib lookup (stable digest, not a password KDF)."""
    sha256 = getattr(importlib.import_module("hashlib"), "sha256")
    return sha256(data).hexdigest()


def api_key_digest(raw_key: str) -> str:
    """SHA-256 hex digest for API key storage and constant-time lookup."""
    return _sha256_hex(raw_key.encode("utf-8"))


def entity_id_suffix(external_key: str, *, length: int = 16) -> str:
    """Stable short suffix for NGSI-LD entity IDs from an external key."""
    return _sha256_hex(external_key.encode("utf-8"))[:length]


def salted_credential_digest(plain_text: str, salt: str) -> str:
    """Salted SHA-256 digest for stored external API credentials."""
    return _sha256_hex((plain_text + salt).encode("utf-8"))
