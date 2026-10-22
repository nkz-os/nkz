"""Shared constants and enums for the Nekazari platform.

Provides canonical fidelity tiers and other cross-module vocabulary
to avoid string-typo drift across the 27-module ecosystem.
"""

from __future__ import annotations

from enum import Enum


class SensorFidelity(str, Enum):
    """Canonical data-fidelity tiers — from best (onsite calibrated) to none.

    Every module that assigns a ``dataFidelity`` / ``data_fidelity`` string
    to an NGSI-LD Property or a Pydantic schema SHOULD import this enum
    and use its ``.value``, not a bare string literal.

    Order is deliberate: higher ordinal = lower confidence.
    """

    ONSITE_CALIBRATED = "onsite_calibrated"
    ONSITE_UNCALIBRATED = "onsite_uncalibrated"
    SATELLITE_DERIVED = "satellite_derived"
    SATELLITE_PROXY = "satellite_proxy"
    REGIONAL_PROXY = "regional_proxy"
    MODELED_OPENDATA = "modeled_opendata"
    UNAVAILABLE = "unavailable"

    def __ge__(self, other: SensorFidelity) -> bool:
        """Allow ordinal comparison (higher ordinal = lower fidelity)."""
        if isinstance(other, SensorFidelity):
            return self._rank >= other._rank
        return NotImplemented

    def __gt__(self, other: SensorFidelity) -> bool:
        if isinstance(other, SensorFidelity):
            return self._rank > other._rank
        return NotImplemented

    @property
    def _rank(self) -> int:
        return _FIDELITY_RANK[self]


_FIDELITY_RANK: dict[SensorFidelity, int] = {
    SensorFidelity.ONSITE_CALIBRATED: 0,
    SensorFidelity.ONSITE_UNCALIBRATED: 1,
    SensorFidelity.SATELLITE_DERIVED: 2,
    SensorFidelity.SATELLITE_PROXY: 3,
    SensorFidelity.REGIONAL_PROXY: 4,
    SensorFidelity.MODELED_OPENDATA: 5,
    SensorFidelity.UNAVAILABLE: 6,
}
