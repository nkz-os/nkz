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
        "max_satellite_computations": 0,
    },
    "pro": {
        "max_users": 5,
        "max_sensors": 10,
        "max_robots": 2,
        "max_parcels": 5,
        "max_area_hectares": Decimal("50.00"),
        "max_entities_total": None,
        "max_satellite_computations": 100,
    },
    "premium": {
        "max_users": 10,
        "max_sensors": 50,
        "max_robots": 5,
        "max_parcels": 20,
        "max_area_hectares": Decimal("500.00"),
        "max_entities_total": None,
        "max_satellite_computations": 500,
    },
    "enterprise": {
        "max_users": None,
        "max_sensors": None,
        "max_robots": None,
        "max_parcels": None,
        "max_area_hectares": None,
        "max_entities_total": None,
        "max_satellite_computations": None,
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


def limits_columns_for_tier(tier: str) -> Dict[str, Any]:
    """Return default tenant limit columns for a plan tier."""
    q = quotas_for_tier(tier)
    area = q["max_area_hectares"]
    return {
        "max_users": q["max_users"],
        "max_robots": q["max_robots"],
        "max_sensors": q["max_sensors"],
        "max_area_hectares": float(area) if area is not None else None,
        "max_parcels": q["max_parcels"],
        "max_entities_total": q["max_entities_total"],
        "max_satellite_computations": q["max_satellite_computations"],
    }


def limits_camel_for_tier(tier: str) -> Dict[str, Any]:
    """Return default tenant limits with camelCase keys for API payloads."""
    defaults = limits_columns_for_tier(tier)
    return {
        "maxUsers": defaults["max_users"],
        "maxRobots": defaults["max_robots"],
        "maxSensors": defaults["max_sensors"],
        "maxAreaHectares": defaults["max_area_hectares"],
        "maxParcels": defaults["max_parcels"],
        "maxEntitiesTotal": defaults["max_entities_total"],
        "maxSatelliteComputations": defaults["max_satellite_computations"],
    }
