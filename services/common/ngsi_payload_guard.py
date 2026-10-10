"""Payload guard for NGSI-LD writes forwarded to the context broker.

Orion-LD mishandles the bare `"urn:ngsi-ld:null"` string used as an attribute
value (the "shorthand" form) on attribute-fragment endpoints. Callers that
forward client JSON to the broker reject it up front; removing an attribute
goes through `DELETE /ngsi-ld/v1/entities/{id}/attrs/{name}` instead.
"""

from __future__ import annotations

from typing import Any, List

NGSI_LD_NULL = "urn:ngsi-ld:null"


def shorthand_null_attributes(body: Any) -> List[str]:
    """Return the top-level attribute names whose value is the bare null token."""
    if not isinstance(body, dict):
        return []
    return [
        name
        for name, value in body.items()
        if name != "@context" and value == NGSI_LD_NULL
    ]
