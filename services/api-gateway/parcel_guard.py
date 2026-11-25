"""Which entity ids belong to an AgriParcel.

Pure rules for the single-writer guard of the gateway; no Flask import.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

PARCEL_TYPE = "AgriParcel"
_URN_TYPE = re.compile(r"urn:ngsi-ld:([A-Za-z][A-Za-z0-9_]*):")


def _decoded(value: str) -> str:
    """Percent-decode until stable; every pass that changes the text shortens it."""
    while True:
        nxt = unquote(value)
        if nxt == value:
            return value
        value = nxt


def entity_type_from_id(entity_id: str) -> str | None:
    """The type named by a `urn:ngsi-ld:<Type>:...` id, or None for any other shape."""
    match = _URN_TYPE.match(_decoded(entity_id))
    return match.group(1) if match else None


def is_parcel_id(entity_id: str) -> bool:
    """True for a parcel id, and for an id of another shape that contains the name."""
    named = entity_type_from_id(entity_id)
    if named is not None:
        return named == PARCEL_TYPE
    return PARCEL_TYPE in _decoded(entity_id)
