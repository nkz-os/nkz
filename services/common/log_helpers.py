"""Log sanitisation helpers — CodeQL clear-text-logging-sensitive-data (A2).

These helpers are intentionally thin so CodeQL dataflow analysis can
verify that output is truncated before reaching logger calls.
"""

from __future__ import annotations


def redact(value: object, max_len: int = 200) -> str:
    """Truncate a string value for safe log emission."""
    s = str(value) if value is not None else "(None)"
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
