"""Expansion of per-measurement window statistics into published attributes.

A sensor-profile measurement may declare `statistics` (mean/min/max/stddev/last/sum).
mean/last/sum are published under the base attribute name; min/max/stddev get their own
attributes (`<S>Min`, `<S>Max`, `<S>StdDev`) and gateway keys (`<K>Min`, ...).
"""
from __future__ import annotations

from common.context_aliases import canonical_attribute

STATISTICS: tuple[str, ...] = ("mean", "min", "max", "stddev", "last", "sum")
BASE_NAME_STATISTICS: frozenset[str] = frozenset({"mean", "last", "sum"})
SUFFIXES: dict[str, str] = {"min": "Min", "max": "Max", "stddev": "StdDev"}

# Fixed emission order for derived entries.
_DERIVED_ORDER: tuple[str, ...] = ("min", "max", "stddev")


def base_attribute(name: str) -> str:
    """Strip a trailing Min/Max/StdDev suffix when something remains; else return `name`."""
    for suffix in SUFFIXES.values():
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _key(measurement: dict) -> str:
    return measurement.get("incomingKey") or measurement["type"]


def gateway_statistics(measurement: dict) -> dict[str, str] | None:
    """`{statistic: mqtt_key}` in profile order, or None without `statistics`."""
    statistics = measurement.get("statistics")
    if not statistics:
        return None
    key = _key(measurement)
    return {s: key + SUFFIXES.get(s, "") for s in statistics}


def expand_measurements(measurements: list[dict]) -> list[dict]:
    """One dict per published attribute; inputs are never mutated."""
    out: list[dict] = []
    for m in measurements:
        statistics = m.get("statistics")
        if not statistics:
            out.append(dict(m))
            continue
        canonical = canonical_attribute(m["sdmAttribute"])
        key = _key(m)
        base = {k: v for k, v in m.items() if k != "statistics"}

        base_stat = next((s for s in statistics if s in BASE_NAME_STATISTICS), None)
        if base_stat is not None:
            out.append({**base, "sdmAttribute": canonical, "statistic": base_stat,
                        "baseAttribute": canonical})

        for stat in _DERIVED_ORDER:
            if stat not in statistics:
                continue
            suffix = SUFFIXES[stat]
            derived = {k: v for k, v in base.items() if k not in ("expression", "counterMax")}
            derived.update({"sdmAttribute": canonical + suffix, "incomingKey": key + suffix,
                            "statistic": stat, "baseAttribute": canonical})
            out.append(derived)
    return out
