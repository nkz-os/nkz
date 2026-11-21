"""Observable-property catalogue access (global rows + tenant rows)."""
from __future__ import annotations

from typing import Any, Dict, List

from .request import RequestError

_SELECT = """
SELECT id, tenant_id, name, domain, unit, default_medium, allowed_media,
       temporal_semantics, valid_min, valid_max, max_rate_per_hour, fusion,
       nearby_radius_m, max_age_s, min_coverage, sub_context_tolerance,
       has_model, deprecated_at, created_at
FROM observation_properties
WHERE (tenant_id IS NULL OR tenant_id = %s)
"""


def _shadowed(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One row per name; a tenant row shadows the global one (even if deprecated)."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        cur = by_name.get(row["name"])
        if cur is None or (cur.get("tenant_id") is None and row.get("tenant_id") is not None):
            by_name[row["name"]] = row
    return by_name


def visible_properties(cur, tenant_id: str) -> List[Dict[str, Any]]:
    """Global + tenant properties, tenant shadows global, deprecated excluded."""
    cur.execute(_SELECT + " ORDER BY name", (tenant_id,))
    rows = [dict(r) for r in cur.fetchall()]
    return [r for r in _shadowed(rows).values() if r.get("deprecated_at") is None]


def get_property(cur, tenant_id: str, name: str) -> Dict[str, Any]:
    """The visible property ``name``; RequestError(422) when unknown or deprecated."""
    cur.execute(_SELECT + " AND name = %s", (tenant_id, name))
    rows = [dict(r) for r in cur.fetchall()]
    row = _shadowed(rows).get(name)
    if row is None or row.get("deprecated_at") is not None:
        raise RequestError(422, f"unknown or deprecated property: {name}")
    return row
