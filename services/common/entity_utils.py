"""
Canonical entity utility functions for the Nekazari platform.

Provides:
- generate_entity_id(): UUID-based NGSI-LD entity ID generation
- generate_entity_id_deterministic(): Stable IDs for externally-keyed entities
- get_entity_display_name(): Extract human-readable name from NGSI-LD entity
"""

import uuid
import hashlib


def generate_entity_id(entity_type: str) -> str:
    """Generate a canonical NGSI-LD entity ID using UUID4.

    Format: urn:ngsi-ld:{Type}:{uuid4_hex_16}
    Example: urn:ngsi-ld:AgriParcel:a1b2c3d4e5f67890
    """
    return f"urn:ngsi-ld:{entity_type}:{uuid.uuid4().hex[:16]}"


def generate_entity_id_deterministic(entity_type: str, external_key: str) -> str:
    """Generate a stable NGSI-LD entity ID from an external key.

    Useful for cadastral parcels or other externally-keyed entities
    where the same external_key must always produce the same entity ID.

    Format: urn:ngsi-ld:{Type}:{sha256_hex_16}
    """
    stable = hashlib.sha256(external_key.encode()).hexdigest()[:16]
    return f"urn:ngsi-ld:{entity_type}:{stable}"


def get_entity_display_name(entity: dict) -> str:
    """Extract human-readable display name from an NGSI-LD entity.

    Handles both normalized format (name as string) and
    NGSI-LD Property format (name as {value: "..."}).
    Falls back to entity ID if no name is available.
    """
    name = entity.get("name")
    if isinstance(name, dict) and "value" in name:
        return str(name["value"])
    if isinstance(name, str):
        return name
    return entity.get("id", "")
