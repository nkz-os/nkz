"""One reading out of a ``DeviceMeasurement``, whatever shape it was stored in.

Two shapes exist, and a module must not tell them apart by hand:

- **Legacy** (entity-manager flag ``DEVICE_MEASUREMENT_SDM_SHAPE_ENABLED`` off, the
  production default): ``controlledProperty`` is the *channel* (``airTemperatureMin``),
  there is no ``name``, and ``measurementType`` is the sensor-profile type, which means
  nothing to a reader.
- **SDM** (flag on): ``controlledProperty`` is the catalogue *property*
  (``airTemperature``), ``name`` is the *channel* (``airTemperatureMin``) and
  ``measurementType`` is the statistic, from a closed vocabulary.

Key a reading by :attr:`DeviceMeasurementReading.channel`. It is the series key in both
shapes and the same one the platform stores (``telemetry_measurements.attribute_name``).
Keying by ``controlledProperty`` merges the ``...Min``/``...Max``/``...StdDev`` channels
into the base one as soon as the SDM shape is on.

The rule is the telemetry-worker's (``services/telemetry-worker/telemetry_worker/
device_measurement.py``); a parity test there keeps both in step.

Entities may come normalized (``{"type": "Property", "value": ...}``) or as keyValues,
and attributes under their short names or the IRIs they are stored with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

__all__ = ["MEASUREMENT_TYPES", "DeviceMeasurementReading", "read_device_measurement"]

# The closed `measurementType` vocabulary of the SDM shape: `instant` (no window) and the
# window statistics. Same set as `common.window_statistics` (INSTANT + STATISTICS).
MEASUREMENT_TYPES: frozenset[str] = frozenset(
    {"instant", "mean", "min", "max", "stddev", "last", "sum"}
)

_DEVICE = "https://smartdatamodels.org/dataModel.Device/"
# Precedence is the tuple order, short (compacted) name first, as in the worker.
_NAME_KEYS = ("name", "https://schema.org/name", "https://smartdatamodels.org/name")
_REF_DEVICE_KEYS = ("refDevice", "hasDevice", _DEVICE + "hasDevice", _DEVICE + "refDevice")
_CONTROLLED_PROPERTY_KEYS = ("controlledProperty", _DEVICE + "controlledProperty")
_MEASUREMENT_TYPE_KEYS = ("measurementType", _DEVICE + "measurementType")
_NUM_VALUE_KEYS = ("numValue", _DEVICE + "numValue")
_TEXT_VALUE_KEYS = ("textValue", _DEVICE + "textValue")
_DATE_OBSERVED_KEYS = ("dateObserved", "https://smartdatamodels.org/dateObserved")

_MISSING = object()


@dataclass(frozen=True)
class DeviceMeasurementReading:
    """What a ``DeviceMeasurement`` says, independent of its stored shape.

    ``shape``: ``"sdm"`` when ``name`` is present AND ``measurementType`` is in
    :data:`MEASUREMENT_TYPES`; ``"legacy"`` otherwise.
    ``channel``: the series key. ``name`` whenever it is present (also after a flag
    rollback, when a stale ``name`` stays on the entity), else ``controlledProperty``.
    None when it cannot be read; a ``name`` that is present but empty or not a string is
    never replaced by ``controlledProperty``.
    ``observed_property``: the catalogue property (``controlledProperty``), SDM shape only.
    None in the legacy shape: there it depends on the device profile.
    ``statistic``: ``measurementType`` (``instant``, ``mean``, ``min``...), SDM shape only.
    ``value``: ``numValue``, else ``textValue``; ``unit_code`` is ``numValue``'s ``unitCode``.
    ``date_observed``: the ``dateObserved`` value as stored (an ISO-8601 string).
    """

    shape: Literal["sdm", "legacy"]
    device_id: Optional[str]
    channel: Optional[str]
    observed_property: Optional[str]
    statistic: Optional[str]
    value: Any
    unit_code: Optional[str]
    date_observed: Optional[str]


def _first_key(entity: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in entity:
            return entity[key]
    return _MISSING


def _value(attr: Any) -> Any:
    """The value of a normalized Property, or the attribute itself in keyValues."""
    if attr is _MISSING:
        return None
    if isinstance(attr, Mapping):
        return attr.get("value")
    return attr


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _device_id(entity: Mapping[str, Any]) -> Optional[str]:
    for key in _REF_DEVICE_KEYS:
        rel = entity.get(key)
        target = rel.get("object", rel.get("value")) if isinstance(rel, Mapping) else rel
        if isinstance(target, str) and target:
            return target
    return None


def _reading(entity: Mapping[str, Any]) -> tuple[Any, Optional[str]]:
    num = _first_key(entity, _NUM_VALUE_KEYS)
    value = _value(num)
    if value is not None:
        unit = num.get("unitCode") if isinstance(num, Mapping) else None
        return value, _text(unit)
    return _value(_first_key(entity, _TEXT_VALUE_KEYS)), None


def read_device_measurement(entity: Mapping[str, Any]) -> DeviceMeasurementReading:
    """Read one ``DeviceMeasurement`` entity (normalized or keyValues).

    Never raises on a malformed entity: missing or unusable parts come back as None, so
    the caller decides what to skip. Raises ``TypeError`` if ``entity`` is not a mapping.
    """
    if not isinstance(entity, Mapping):
        raise TypeError(f"DeviceMeasurement must be a mapping, got {type(entity).__name__}")

    controlled = _text(_value(_first_key(entity, _CONTROLLED_PROPERTY_KEYS)))
    measurement_type = _value(_first_key(entity, _MEASUREMENT_TYPE_KEYS))
    name_attr = _first_key(entity, _NAME_KEYS)
    value, unit_code = _reading(entity)
    common = {
        "device_id": _device_id(entity),
        "value": value,
        "unit_code": unit_code,
        "date_observed": _text(_value(_first_key(entity, _DATE_OBSERVED_KEYS))),
    }

    if name_attr is _MISSING:
        return DeviceMeasurementReading(
            shape="legacy", channel=controlled, observed_property=None, statistic=None, **common
        )
    channel = _text(_value(name_attr))
    if isinstance(measurement_type, str) and measurement_type in MEASUREMENT_TYPES:
        return DeviceMeasurementReading(
            shape="sdm",
            channel=channel,
            observed_property=controlled,
            statistic=measurement_type,
            **common,
        )
    return DeviceMeasurementReading(
        shape="legacy", channel=channel, observed_property=None, statistic=None, **common
    )
