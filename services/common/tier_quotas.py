"""Canonical tier -> plan_level and tier -> quota mapping.

Single source of truth for the 4-tier subscription model.
Imported by tenant-webhook, tenant-user-api, and entity-manager.
"""

from decimal import Decimal
from typing import Any, Dict

PLAN_LEVELS: Dict[str, int] = {
    "basic": 0,
    "pro": 1,
    "premium": 2,
    "enterprise": 3,
}

LEVEL_TO_TIER: Dict[int, str] = {v: k for k, v in PLAN_LEVELS.items()}

TIER_QUOTAS: Dict[str, Dict[str, Any]] = {
    "basic": {
        "max_users": 1,
        "max_sensors": 0,
        "max_robots": 0,
        "max_parcels": 0,
        "max_area_hectares": Decimal("0.20"),
        "max_entities_total": 2,
    },
    "pro": {
        "max_users": 5,
        "max_sensors": 10,
        "max_robots": 2,
        "max_parcels": 5,
        "max_area_hectares": Decimal("50.00"),
        "max_entities_total": None,
    },
    "premium": {
        "max_users": 10,
        "max_sensors": 50,
        "max_robots": 5,
        "max_parcels": 20,
        "max_area_hectares": Decimal("500.00"),
        "max_entities_total": None,
    },
    "enterprise": {
        "max_users": None,
        "max_sensors": None,
        "max_robots": None,
        "max_parcels": None,
        "max_area_hectares": None,
        "max_entities_total": None,
    },
}


def plan_level_for(tier: str) -> int:
    """Map tier name -> plan_level int. Raises KeyError on unknown tier."""
    key = (tier or "").strip().lower()
    if key not in PLAN_LEVELS:
        raise KeyError(f"Unknown tier: {tier!r}")
    return PLAN_LEVELS[key]


def quotas_for_tier(tier: str) -> Dict[str, Any]:
    """Return quota defaults for a given tier. Raises KeyError on unknown tier."""
    key = (tier or "").strip().lower()
    if key not in TIER_QUOTAS:
        raise KeyError(f"Unknown tier: {tier!r}")
    # Return a copy so callers can't mutate the canonical dict
    return dict(TIER_QUOTAS[key])


def quotas_for_level(level: int) -> Dict[str, Any]:
    """Return quotas by plan_level int."""
    if level not in LEVEL_TO_TIER:
        raise KeyError(f"Unknown plan_level: {level!r}")
    return quotas_for_tier(LEVEL_TO_TIER[level])
