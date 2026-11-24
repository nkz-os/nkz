"""The official `DeviceModel` built from a `sensor_profiles` row, and the Device attributes that point to it.

A registered `Device` names its profile with `refDeviceModel`, a Relationship to the
`DeviceModel` whose id carries the tenant and the profile code:
`urn:ngsi-ld:DeviceModel:{tenant}:{code}`. Devices registered before that shape name the
profile with the non-standard `profileCode`; `device_profile_code` reads both.

Everything here is pure: dicts in, dicts out. Entities carry no `@context`; the caller adds it.
"""

import logging
import re
from typing import Any, Literal, Mapping, NamedTuple

from common.controlled_property import controlled_properties, mapped_name

logger = logging.getLogger(__name__)

GENERIC = "generic"
OPTIONAL_PROFILE_ATTRS = ("controlledProperty",)

DEVICE_IRI = "https://smartdatamodels.org/dataModel.Device/"
DEFAULT_CONTEXT_IRI = "https://uri.etsi.org/ngsi-ld/default-context/"
_REF_DEVICE_MODEL_KEYS = ("refDeviceModel", DEVICE_IRI + "refDeviceModel")
_PROFILE_CODE_KEYS = ("profileCode", DEFAULT_CONTEXT_IRI + "profileCode")

_MODEL_PREFIX = "urn:ngsi-ld:DeviceModel:"
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TENANT = re.compile(r"[^\s:]+")

# A profile that measures one of these is a meter; every other profile is a sensor.
_METER_PROPERTIES = frozenset({"waterConsumption", "cumulativeActivePowerImport"})


def device_model_id(tenant: str, code: str) -> str:
    """`urn:ngsi-ld:DeviceModel:{tenant}:{code}`; refuses parts that `profile_code_from_model` would reject."""
    if not isinstance(tenant, str) or not _TENANT.fullmatch(tenant):
        raise ValueError(f"tenant cannot be part of a DeviceModel id: {tenant!r}")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ValueError(f"profile code cannot be part of a DeviceModel id: {code!r}")
    return f"{_MODEL_PREFIX}{tenant}:{code}"


def _check_model_urn(tenant: str, urn: object) -> tuple[str | None, str | None]:
    """(code, None) for a valid DeviceModel id of this tenant, else (None, reason)."""
    if not isinstance(urn, str):
        return None, "malformed: not a string"
    parts = urn.split(":")
    if len(parts) != 5 or ":".join(parts[:3]) != _MODEL_PREFIX.rstrip(":"):
        return None, "malformed: not a DeviceModel id of the form urn:ngsi-ld:DeviceModel:<tenant>:<code>"
    if not tenant or parts[3] != tenant:
        return None, "foreign tenant"
    if not _CODE.fullmatch(parts[4]):
        return None, "malformed: invalid profile code"
    return parts[4], None


def profile_code_from_model(tenant: str, urn: object) -> str | None:
    """The profile code of a DeviceModel id of this tenant; None for anything else."""
    return _check_model_urn(tenant, urn)[0]


def _attribute(entity: Mapping, keys: tuple[str, ...], member: str) -> Any:
    """Raw content of the first present attribute: `member` of a normalized one, or the keyValues value."""
    for key in keys:
        if key in entity:
            raw = entity[key]
            return raw.get(member) if isinstance(raw, Mapping) else raw
    return None


class ProfileCodes(NamedTuple):
    """The codes a Device carries; a code is None when it is absent or unusable."""

    model: str | None
    model_error: str | None  # why the `refDeviceModel` it carries is not usable
    legacy: str | None
    present: bool  # it carries `refDeviceModel` or `profileCode`, usable or not


def profile_codes(tenant: str, entity: Mapping) -> ProfileCodes:
    """Both profile codes of a Device: the one of `refDeviceModel` and the one of `profileCode`."""
    if not isinstance(entity, Mapping):
        return ProfileCodes(None, None, None, False)
    model = model_error = None
    has_model = any(key in entity for key in _REF_DEVICE_MODEL_KEYS)
    if has_model:
        model, model_error = _check_model_urn(
            tenant, _attribute(entity, _REF_DEVICE_MODEL_KEYS, "object"))
    legacy = _attribute(entity, _PROFILE_CODE_KEYS, "value")
    return ProfileCodes(model, model_error, legacy if isinstance(legacy, str) and legacy else None,
                        has_model or any(key in entity for key in _PROFILE_CODE_KEYS))


def device_profile_code(
    tenant: str, entity: Mapping
) -> tuple[str | None, Literal["model", "legacy", "none"]]:
    """The profile code a Device names, and where it came from.

    `refDeviceModel` wins. A malformed or foreign one is ignored with a warning and the lookup
    falls through to `profileCode`.
    """
    codes = profile_codes(tenant, entity)
    if codes.model is not None:
        return codes.model, "model"
    if codes.model_error:
        logger.warning(
            "refDeviceModel ignored: tenant=%s entity_id=%s reason=%s",
            tenant, entity.get("id"), codes.model_error,
        )
    if codes.legacy is not None:
        return codes.legacy, "legacy"
    return None, "none"


def _declared(value: object) -> str | None:
    """The text of a column that declares something; empty, blank and non-text count as not declared."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _measurements(profile: Mapping) -> list:
    mapping = profile.get("mapping")
    if not isinstance(mapping, Mapping):
        return []
    measurements = mapping.get("measurements")
    return measurements if isinstance(measurements, list) else []


def _category(profile: Mapping) -> list[str]:
    for measurement in _measurements(profile):
        if mapped_name(measurement) in _METER_PROPERTIES:
            return ["meter"]
    return ["sensor"]


def _property(value: Any) -> dict:
    return {"type": "Property", "value": value}


def build_device_model(tenant: str, profile: Mapping) -> dict:
    """The `DeviceModel` entity (id, type, attributes; no `@context`) for a `sensor_profiles` row.

    `profile` keys: code, name, mapping, manufacturer_name, brand_name, model_name.
    """
    code = profile.get("code")
    entity: dict = {"id": device_model_id(tenant, code), "type": "DeviceModel"}
    name = _declared(profile.get("name"))
    if name is not None:
        entity["name"] = _property(name)
    entity["manufacturerName"] = _property(_declared(profile.get("manufacturer_name")) or GENERIC)
    entity["brandName"] = _property(_declared(profile.get("brand_name")) or GENERIC)
    entity["modelName"] = _property(_declared(profile.get("model_name")) or code)
    entity["category"] = _property(_category(profile))
    controlled = controlled_properties(_measurements(profile))
    if controlled:
        entity["controlledProperty"] = _property(controlled)
    return entity


def device_profile_attrs(tenant: str, profile: Mapping) -> dict:
    """The Device attributes a profile contributes; `controlledProperty` only when not empty."""
    attrs: dict = {
        "refDeviceModel": {
            "type": "Relationship",
            "object": device_model_id(tenant, profile.get("code")),
        },
        "deviceCategory": _property(_category(profile)),
    }
    controlled = controlled_properties(_measurements(profile))
    if controlled:
        attrs["controlledProperty"] = _property(controlled)
    return attrs
