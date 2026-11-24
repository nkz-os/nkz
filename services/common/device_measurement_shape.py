"""One description of the `DeviceMeasurement` channels of a sensor profile.

Pure: no environment, network, database or clock. The entity-manager builds its
`DeviceMeasurement` entities from these channels, and the IoT Agent mapping generator reads
the same list, so both agree on the entity id, the fixed attributes, the unit and the incoming
key of every channel.

Shapes:
  * `legacy`: `controlledProperty` = channel, `measurementType` = profile `type`, no `name`.
  * `sdm`: `controlledProperty` = catalogue name (`observedProperty`) or the base attribute of
    the channel, `measurementType` = window statistic or `instant`, `name` = channel.
`deviceType` is present in both shapes when a profile code is given; `observedProperty` never.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Mapping, Optional, Tuple

from common.context_aliases import canonical_attribute
from common.unit_codes import to_unit_code
from common.window_statistics import INSTANT, expand_measurements

SHAPES = ("legacy", "sdm")


@dataclass(frozen=True)
class Channel:
    """One DeviceMeasurement channel.

    `kind` is for the mapping generator: `number`, `text`, or `runtime` (decided by the JSON
    type of the value). `fixed` holds the constant attribute values; in the `legacy` shape
    `measurementType` is the profile `type` and may be None.
    """

    dm_id: str
    name: str
    key: str
    expression: Optional[str]
    kind: str
    unit_code: Optional[str]
    fixed: Mapping[str, Any]


def _check_shape(shape: str) -> None:
    if shape not in SHAPES:
        raise ValueError(f"Unknown DeviceMeasurement shape {shape!r}; expected one of {SHAPES}")


def _entries(measurements: List[Dict[str, Any]]) -> List[Tuple[str, bool, Dict[str, Any]]]:
    """`(canonical name, declared as canonical, expanded definition)` in profile order."""
    out = []
    for m in expand_measurements([m for m in measurements if isinstance(m, dict)]):
        raw = m.get("sdmAttribute")
        if not raw:
            continue
        name = canonical_attribute(raw)
        out.append((name, raw == name, m))
    return out


def _kind(m: Mapping[str, Any]) -> str:
    value_type = m.get("valueType")
    if value_type == "Number":
        return "number"
    if value_type == "Text":
        return "text"
    if not value_type and m.get("unit"):
        return "number"
    return "runtime"


def _channel(name: str, m: Mapping[str, Any], *, tenant: str, external_id: str,
             profile_code: Optional[str], shape: str) -> Channel:
    # Always resolved, whatever the kind: an unknown unit must raise, never pass through.
    unit_code = to_unit_code(m.get("unit", "") or "")
    fixed: Dict[str, Any] = {}
    if shape == "sdm":
        fixed["controlledProperty"] = m.get("observedProperty") or m.get("baseAttribute") or name
        fixed["measurementType"] = m.get("statistic") or INSTANT
        fixed["name"] = name
    else:
        fixed["controlledProperty"] = name
        fixed["measurementType"] = m.get("type")
    if profile_code is not None:
        fixed["deviceType"] = profile_code
    return Channel(
        dm_id=f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{external_id}:{name}",
        name=name,
        # Same rule as the IoT Agent attribute list; the last fallback only avoids a KeyError
        # on a definition with neither `incomingKey` nor `type`.
        key=m.get("incomingKey") or m.get("type") or name,
        expression=m.get("expression") or None,
        kind=_kind(m),
        unit_code=unit_code,
        fixed=fixed,
    )


def measurement_channels(
    measurements: List[Dict[str, Any]],
    *,
    tenant: str,
    external_id: str,
    device_urn: str,
    profile_code: Optional[str],
    shape: str,
) -> List[Channel]:
    """Every channel of a profile's `mapping.measurements`, statistic channels included.

    One channel per (incoming key, canonical name); a later duplicate replaces an earlier one.
    Channels of one canonical name are adjacent and the one declared under the canonical name
    comes last. `device_urn` is accepted for the mapping generator's symmetry and not used
    here (the channel carries no relationship). Raises `ValueError` on an unknown shape or an
    unknown unit anywhere in the profile.
    """
    _check_shape(shape)
    unique: Dict[Tuple[str, str], Tuple[str, bool, Dict[str, Any]]] = {}
    for name, is_canonical, m in _entries(measurements):
        key = m.get("incomingKey") or m.get("type") or name
        unique[(key, name)] = (name, is_canonical, m)
    first_seen: Dict[str, int] = {}
    for i, (name, _, _) in enumerate(unique.values()):
        first_seen.setdefault(name, i)
    ordered = sorted(
        unique.values(),
        key=lambda e: (first_seen[e[0]], e[1]),  # stable: canonical-declared last per name
    )
    return [
        _channel(name, m, tenant=tenant, external_id=external_id,
                 profile_code=profile_code, shape=shape)
        for name, _, m in ordered
    ]


def channels_by_name(
    measurements: List[Dict[str, Any]],
    *,
    names: Optional[Collection[str]],
    tenant: str,
    external_id: str,
    device_urn: str,
    profile_code: Optional[str],
    shape: str,
) -> Dict[str, Channel]:
    """The channel the entity-manager reads for each canonical name: the LAST definition in
    profile order. Only `names` are resolved (None = all), so an unknown unit of a channel
    that is not read does not raise.
    """
    _check_shape(shape)
    chosen: Dict[str, Dict[str, Any]] = {}
    for name, _, m in _entries(measurements):
        if names is None or name in names:
            chosen[name] = m
    return {
        name: _channel(name, m, tenant=tenant, external_id=external_id,
                       profile_code=profile_code, shape=shape)
        for name, m in chosen.items()
    }
