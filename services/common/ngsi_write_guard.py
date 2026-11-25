"""Shape of the NGSI-LD entity writes a service forwards to the broker.

A body is an object, and its inline @context has only known definitions.
No Flask import.
"""

from __future__ import annotations

MAX_CONTEXT_DEPTH = 8
MAX_CONTEXT_TERMS = 2000
MAX_CONTEXT_TEXT = 2048
_TEXT_KEYWORDS = frozenset({"@vocab", "@base", "@language"})


def _is_text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_CONTEXT_TEXT and "\x00" not in value


def _is_url(value: object) -> bool:
    return _is_text(value) and value.startswith(("http://", "https://"))


def _is_definition(value: object) -> bool:
    """A term: its IRI, or an object that names it."""
    if not isinstance(value, dict):
        return _is_text(value)
    if not _is_text(value.get("@id")):
        return False
    for key, item in value.items():
        if key in ("@id", "@type", "@language"):
            if not _is_text(item):
                return False
        elif key == "@container":
            items = item if isinstance(item, list) else [item]
            if not items or not all(_is_text(name) for name in items):
                return False
        elif key == "@protected":
            if not isinstance(item, bool):
                return False
        else:
            return False
    return True


def _is_entry(key: object, value: object) -> bool:
    if not _is_text(key):
        return False
    if not key.startswith("@"):
        return _is_definition(value)
    if key in _TEXT_KEYWORDS:
        return _is_text(value)
    if key == "@version":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return key == "@protected" and isinstance(value, bool)


def valid_context(context: object) -> bool:
    """True for a URL, a term table, or a list of them."""
    pending: list[tuple[object, int]] = [(context, 0)]
    terms = 0
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            terms += len(item)
            if terms > MAX_CONTEXT_TERMS or not all(_is_entry(k, v) for k, v in item.items()):
                return False
        elif isinstance(item, list):
            if depth >= MAX_CONTEXT_DEPTH or len(item) > MAX_CONTEXT_TERMS:
                return False
            pending.extend((entry, depth + 1) for entry in item)
        elif not _is_url(item):
            return False
    return True


def valid_write(body: object) -> bool:
    """True for an object whose inline @context, if any, is known."""
    if not isinstance(body, dict):
        return False
    return "@context" not in body or valid_context(body["@context"])
