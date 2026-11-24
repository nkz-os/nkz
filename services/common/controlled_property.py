"""Platform property names mapped to the official Smart Data Models `controlledProperty`.

`Device.controlledProperty` is a closed enum (`ControlledPropertyType` in dataModel.Device).
The table is strict: a name maps only to a value that is exact or strictly broader and
still true of it. Where no such value exists the row is `none` and the attribute is
omitted from the entity; nothing is invented.
"""

from dataclasses import dataclass
from typing import Literal

from common.context_aliases import canonical_attribute
from common.window_statistics import base_attribute

# ControlledPropertyType enum of dataModel.Device (device-schema.json at the pinned commit).
CONTROLLED_PROPERTY_TYPES: frozenset[str] = frozenset({
    "airPollution", "atmosphericPressure", "averageVelocity", "batteryLife", "batterySupply",
    "cdom", "conductance", "conductivity", "depth", "eatingActivity", "electricityConsumption",
    "energy", "fillingLevel", "freeChlorine", "gasConsumption", "gateOpening", "heading",
    "humidity", "light", "location", "milking", "motion", "movementActivity", "noiseLevel",
    "occupancy", "orp", "pH", "power", "precipitation", "pressure", "refractiveIndex",
    "salinity", "smoke", "soilMoisture", "solarRadiation", "speed", "tds", "temperature",
    "trafficFlow", "tss", "turbidity", "uvLampIntensity", "uvOrganicLoad", "waterConsumption",
    "waterFlow", "waterLevel", "waterPollution", "weatherConditions", "weight",
    "windDirection", "windSpeed",
})


@dataclass(frozen=True)
class MappingRow:
    value: str | None
    kind: Literal["exact", "broader", "none"]
    reason: str


def _exact(value: str) -> MappingRow:
    return MappingRow(value, "exact", f"The official value `{value}` names this quantity.")


def _broader(value: str, reason: str) -> MappingRow:
    return MappingRow(value, "broader", reason)


def _none(reason: str) -> MappingRow:
    return MappingRow(None, "none", reason)


_NO_VALUE = "No value of the official enum is exact or strictly broader and true for this quantity."
_AGRIPARCEL = (
    "No value of the official enum fits; the concept is an official attribute of "
    "AgriParcelRecord (dataModel.Agrifood)."
)

MAPPING: dict[str, MappingRow] = {
    # Exact
    **{name: _exact(name) for name in (
        "atmosphericPressure", "precipitation", "soilMoisture", "solarRadiation",
        "windDirection", "windSpeed", "temperature", "pressure", "waterConsumption",
    )},
    "ph": _exact("pH"),
    # Broader: temperature
    **{name: _broader("temperature", "It is a temperature, which the official value covers.")
       for name in ("airTemperature", "leafTemperature", "soilTemperature", "equipmentTemperature")},
    **{name: _broader("temperature", "Dew point is a temperature, which the official value covers.")
       for name in ("dewPoint", "dewPointTemperature")},
    # Broader: others
    "relativeHumidity": _broader("humidity", "Relative humidity is a kind of humidity."),
    **{name: _broader("light", "It measures light reaching a surface, which `light` covers.")
       for name in ("photosyntheticPhotonFluxDensity", "photosyntheticallyActiveRadiation")},
    "reflectedSolarRadiation": _broader("solarRadiation", "It is solar radiation, the reflected part."),
    "precipitationAccumulated": _broader("precipitation", "It is the accumulated amount of precipitation."),
    **{name: _broader("power", "It is a kind of electrical power.")
       for name in ("instantaneousPower", "reactivePower")},
    "cumulativeActivePowerImport": _broader(
        "energy", "It is a cumulative imported energy counter, which `energy` covers."),
    "soilElectricalConductivity": _broader("conductivity", "It is the electrical conductivity of soil."),
    "snowHeight": _broader("depth", "Snow height is the depth of the snow layer."),
    "soilSalinity": _broader("salinity", "A soil salinity reading is a salinity."),
    "drainFlow": _broader("waterFlow", "A drain flow is a flow of water."),
    # None: concept official elsewhere
    **{name: _none(_AGRIPARCEL) for name in ("stemDiameter", "stemDiameterVariation", "leafWetness")},
    # None: no true value
    **{name: _none(_NO_VALUE) for name in (
        "sapFlow", "stemWaterPotential", "vaporPressureDeficit", "ndvi", "leafAreaIndex",
        "chlorophyllContent", "voltage", "current", "powerFactor", "stateOfCharge", "batteryLevel",
        "efficiency", "status", "inclination", "azimuth", "elevation", "solarZenith", "uvIndex",
        "soilingIndex", "shadingCoverage", "soilNitrogen", "soilPhosphorus", "soilPotassium",
        "soilOrganicMatter", "soilCompaction",
    )},
}


def mapped_name(measurement: object) -> str | None:
    """First MAPPING key found for observedProperty, canonical sdmAttribute, then its base name.

    A `none` row is a decision and stops the search. Anything that is not a dict, and
    names that are not strings, are ignored.
    """
    if not isinstance(measurement, dict):
        return None
    observed = measurement.get("observedProperty")
    sdm = measurement.get("sdmAttribute")
    candidates = [observed] if isinstance(observed, str) else []
    if isinstance(sdm, str) and sdm:
        canonical = canonical_attribute(sdm)
        candidates += [canonical, base_attribute(canonical)]
    for name in candidates:
        if name and name in MAPPING:
            return name
    return None


def controlled_properties(measurements: list[dict]) -> list[str]:
    """Sorted unique official values for the measurements; empty list when nothing maps.

    Entries that are not dicts are skipped, so a malformed profile cannot raise here.
    """
    if not isinstance(measurements, (list, tuple)):
        return []
    values = set()
    for measurement in measurements:
        name = mapped_name(measurement)
        if name is not None and MAPPING[name].value is not None:
            values.add(MAPPING[name].value)
    return sorted(values)
