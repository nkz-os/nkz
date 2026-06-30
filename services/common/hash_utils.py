"""Keyed digests for API key lookup — not password KDFs (CodeQL A7)."""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes


def _sha256_hex(data: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize().hex()


def api_key_digest(raw_key: str) -> str:
    """SHA-256 hex digest for API key storage and constant-time lookup."""
    return _sha256_hex(raw_key.encode("utf-8"))


def entity_id_suffix(external_key: str, *, length: int = 16) -> str:
    """Stable short suffix for NGSI-LD entity IDs from an external key."""
    return _sha256_hex(external_key.encode("utf-8"))[:length]


def salted_credential_digest(plain_text: str, salt: str) -> str:
    """Salted SHA-256 digest for stored external API credentials."""
    return _sha256_hex((plain_text + salt).encode("utf-8"))
