"""Pure observation resolution for the telemetry-worker (no I/O).

Turns (reading, deployment, profile measurement, catalogue property) into the
`payload.observations[attr]` object (contract 1.2) and the per-measurement
quality flag (contract 8.3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from common.context_aliases import canonical_attribute
from common.unit_codes import DEGRADED_UNITS, to_unit_code


@dataclass(frozen=True)
class Deployment:
    """One device_deployments row."""

    id: str
    device_urn: str
    observes_feature: Optional[str]
    observation_context: Dict[str, Any]
    channel_overrides: Dict[str, Any]
    mobility: str
    location: Optional[Tuple[float, float]]  # (lon, lat)


@dataclass(frozen=True)
class PropertyDef:
    """One observation_properties row (fields used here)."""

    name: str
    temporal_semantics: str
    valid_min: Optional[float]
    valid_max: Optional[float]
    unit: Optional[str] = None


def units_compatible(profile_unit: Optional[str], catalogue_unit: Optional[str]) -> bool:
    """True only when both units are present and denote the same unit (fail-safe).

    Same UN/CEFACT code (so aliases of one unit match). A missing/empty unit, a symbol
    unknown to `to_unit_code`, or a symbol without a real code (code None or the C62
    "degraded" placeholder, which would make e.g. kg/ha equal to umol/m2/s) is
    compatible only with the identical symbol string.
    """
    if not profile_unit or not catalogue_unit:
        return False
    if not isinstance(profile_unit, str) or not isinstance(catalogue_unit, str):
        return False
    try:
        a, b = to_unit_code(profile_unit), to_unit_code(catalogue_unit)
    except ValueError:
        return False
    if a is None or b is None or profile_unit in DEGRADED_UNITS or catalogue_unit in DEGRADED_UNITS:
        return profile_unit == catalogue_unit
    return a == b


def _override_for(dep: Deployment, attr: str) -> Dict[str, Any]:
    """Channel override for `attr`; keys may be aliases of the canonical name."""
    overrides = dep.channel_overrides or {}
    for key, value in overrides.items():
        if canonical_attribute(key) == canonical_attribute(attr) and isinstance(value, dict):
            return value
    return {}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quality_flag(
    value: float,
    prop: PropertyDef,
    counter_max: Optional[float],
    health_flag: Optional[str],
) -> str:
    """Per-measurement quality: valid | out_of_range | suspect (never counter_reset)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "suspect"

    if prop.temporal_semantics == "cumulative":
        # Raw counter: only counterMax applies. valid_min/max are for increments (reader).
        if counter_max is not None and value > counter_max:
            return "out_of_range"
    else:
        if prop.valid_min is not None and value < prop.valid_min:
            return "out_of_range"
        if prop.valid_max is not None and value > prop.valid_max:
            return "out_of_range"

    if health_flag is None or health_flag == "valid":
        return "valid"
    if health_flag == "out_of_bounds":
        return "out_of_range"
    return "suspect"  # nan and any unknown flag: fail-safe, never valid


def observation_context(attr: str, device_urn: str, dep: Deployment) -> Dict[str, Any]:
    """Deployment-derived context of one channel (no property, no quality).

    Shared by ingest (resolve_observation) and the recompute job, so both store
    the same values for the same deployment.
    """
    override = _override_for(dep, attr)

    ctx = override.get("observationContext")
    if not isinstance(ctx, dict):
        ctx = dep.observation_context or {}
    medium = ctx.get("medium")

    feature = override.get("observesFeature") or dep.observes_feature or None
    if feature is None and medium == "equipment":
        feature = device_urn

    return {
        "featureOfInterest": feature,
        "medium": medium,
        "depthFromCm": _num(ctx.get("depthFrom")),
        "depthToCm": _num(ctx.get("depthTo")),
        "heightM": _num(ctx.get("heightAboveGround")),
        "organ": ctx.get("organ"),
        "contextRef": ctx.get("structure") or ctx.get("enclosure") or None,
    }


def reading_point(value: Any) -> Optional[Dict[str, Any]]:
    """A GeoJSON Point value (a reading's own `location`) if well-formed, else None.

    Shared by ingest (payload.location of mobile readings) and the recompute job.
    """
    if not isinstance(value, dict) or value.get("type") != "Point":
        return None
    coords = value.get("coordinates")
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in coords[:2])
    ):
        return {"type": "Point", "coordinates": [coords[0], coords[1]]}
    return None


def counter_max_of(measurement_def: Optional[Dict[str, Any]]) -> Optional[float]:
    """The profile measurement's `counterMax` as a number, else None."""
    return _num((measurement_def or {}).get("counterMax"))


def resolve_observation(
    attr: str,
    value: float,
    device_urn: str,
    dep: Deployment,
    measurement_def: Dict[str, Any],
    prop: PropertyDef,
    health_flag: Optional[str],
) -> Dict[str, Any]:
    """Build the `payload.observations[attr]` object."""
    return {
        "observedProperty": prop.name,
        **observation_context(attr, device_urn, dep),
        "qualityFlag": quality_flag(
            value, prop, counter_max_of(measurement_def), health_flag
        ),
    }
