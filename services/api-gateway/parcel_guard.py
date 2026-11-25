"""Which entity ids and payloads belong to an AgriParcel.

Pure rules for the single-writer guard of the gateway; no Flask import.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from entity_type_guard import declared_types

PARCEL_TYPE = "AgriParcel"
MAX_HOPS = 5
# No real entity comes near these bounds; a payload beyond them is refused.
MAX_DECODES = 5
MAX_TYPE_CHARS = 2048
MAX_TYPES = 4
MAX_TABLES = 50
NUL = "\x00"
_URN_TYPE = re.compile(r"urn:ngsi-ld:([A-Za-z][A-Za-z0-9_]*):")


def _decoded(value: str) -> str | None:
    """Percent-decode until stable, or None when it does not settle."""
    for _ in range(MAX_DECODES):
        nxt = unquote(value)
        if nxt == value:
            return value
        value = nxt
    return None


def entity_type_from_id(entity_id: str) -> str | None:
    """The type named by a `urn:ngsi-ld:<Type>:...` id, or None for any other shape."""
    decoded = _decoded(entity_id)
    match = _URN_TYPE.match(decoded) if decoded is not None else None
    return match.group(1) if match else None


def is_parcel_id(entity_id: str) -> bool:
    """True for a parcel id, and for an id of another shape that contains the name."""
    decoded = _decoded(entity_id)
    if decoded is None:
        return True
    named = entity_type_from_id(decoded)
    if named is not None:
        return named == PARCEL_TYPE
    return PARCEL_TYPE in decoded


def local_name(value: str) -> str:
    """The last part of a short name, a compact name or an IRI."""
    return value[max(value.rfind(sep) for sep in "/#:") + 1 :]


def _inline_tables(context: object) -> list[dict]:
    """Every non-empty term table in the payload; a stack, so nesting depth is free."""
    tables: list[dict] = []
    pending = [context]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item:
                tables.append(item)
        elif isinstance(item, list):
            pending.extend(item)
    return tables


def _has_nul(context: object) -> bool:
    """True when any key or text of the context holds a NUL."""
    pending = [context]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if NUL in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _term(table: dict, name: str) -> str | None:
    value = table.get(name)
    if isinstance(value, dict):
        value = value.get("@id")
    return value if isinstance(value, str) else None


def _next_hop(table: dict, name: str) -> str | None:
    value = _term(table, name)
    if value is not None:
        return value
    prefix, sep, rest = name.partition(":")
    base = _term(table, prefix) if sep else None
    return base + rest if base is not None else None


def _names_parcel(declared: str, tables: list[dict]) -> bool:
    if local_name(declared) == PARCEL_TYPE:
        return True
    # Each table on its own, as the broker resolves them.
    for table in tables:
        name: str | None = declared
        for _ in range(MAX_HOPS):
            name = _next_hop(table, name)
            if name is None:
                break
            if len(name) > MAX_TYPE_CHARS or local_name(name) == PARCEL_TYPE:
                return True
    return False


def declares_parcel(body: object) -> bool:
    """True when a payload's type is, or expands in its own inline @context to, a parcel."""
    if not isinstance(body, dict):
        return False
    types = declared_types(body) or []
    tables = _inline_tables(body.get("@context"))
    if len(types) > MAX_TYPES or len(tables) > MAX_TABLES:
        return True
    if any(NUL in key for key in body) or _has_nul(body.get("@context")):
        return True
    if any(len(declared) > MAX_TYPE_CHARS or NUL in declared for declared in types):
        return True
    return any(_names_parcel(declared, tables) for declared in types)


def creates_parcel_id(body: object) -> bool:
    """True when a payload names itself with a parcel id. Body ids are stored literally."""
    if not isinstance(body, dict):
        return False
    for key in ("id", "@id"):
        value = body.get(key)
        if isinstance(value, str):
            match = _URN_TYPE.match(value)
            if match and match.group(1) == PARCEL_TYPE:
                return True
    return False
