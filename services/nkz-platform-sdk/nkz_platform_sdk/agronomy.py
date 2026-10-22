"""Agronomic value contract — uniform {value, source, confidence, fidelity, notes}.

Single source of truth shared by crop-health and bioorchestrator so every
agronomic number reaches the farmer with its provenance and how much to
trust it. Confidence follows the weakest-link rule: a value is only as
trustworthy as its flimsiest input (fail-safe = honesty).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
Fidelity = Literal["iot_sensor", "parcel_weather", "regional_proxy", "unavailable"]

_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_BY_RANK: dict[int, Confidence] = {0: "low", 1: "medium", 2: "high"}


class Source(BaseModel):
    short: str
    doi: str | None = None
    institution: str | None = None


class AgronomicValue(BaseModel):
    value: float | str | None
    source: Source
    confidence: Confidence
    fidelity: Fidelity | None = None
    notes: list[str] = Field(default_factory=list)


def combine_confidence(levels: list[str]) -> Confidence:
    """Weakest-link: the minimum confidence across all inputs.

    Empty list → 'low' (no evidence is not high confidence). This is the
    canonical primitive for any value fused from multiple labelled inputs.
    """
    if not levels:
        return "low"
    return _BY_RANK[min(_RANK.get(lv, 0) for lv in levels)]


def confidence_from_match(match_level: str, is_default: bool) -> Confidence:
    """Reference-parameter confidence from bioorch match_level + is_default."""
    if is_default:
        return "low"
    if match_level == "exact":
        return "high"
    if match_level in ("management", "generic"):
        return "medium"
    return "low"  # species_only, none, unknown


# Both the meteo vocab (iot_sensor/parcel_weather/regional_proxy/unavailable)
# and the crop-health engine vocab (onsite_calibrated/local_proxy/
# regional_proxy/modeled_opendata) map onto the same three confidence tiers.
_FIDELITY_CONF: dict[str, Confidence] = {
    "iot_sensor": "high",
    "onsite_calibrated": "high",
    "parcel_weather": "medium",
    "local_proxy": "medium",
    "regional_proxy": "low",
    "modeled_opendata": "low",
    "unavailable": "low",
}


def confidence_from_fidelity(fidelity: str) -> Confidence:
    """Measured/derived-value confidence from its input fidelity. Unknown → low."""
    return _FIDELITY_CONF.get(fidelity, "low")
