"""The DeviceMeasurement example files tie to `build_measurements` and to the IoT Agent mappings.

Each file is the output of `build_measurements` for a synthetic Device and, attribute by
attribute, what the IoT Agent mappings of the same channel write (fixed attributes, unit,
relationship and value kind come from the generator's own mapping list, no JEXL evaluated).
The pre-existing `DeviceMeasurement.{legacy,sdm}.json` are pinned by another test.
"""
import json
import pathlib
import re

import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401,I001  (stubs first)

from blueprints.measurements import SDM_SHAPE_FLAG, build_measurements  # noqa: E402
from common.device_measurement_shape import measurement_channels  # noqa: E402
from common.iota_dm_mappings import dm_mappings  # noqa: E402

EXAMPLES = pathlib.Path(__file__).resolve().parents[3] / "config" / "sdm-examples"
DEVICE = "urn:ngsi-ld:Device:acme:sensor-001"
OBSERVED = "2026-08-29T10:00:00Z"
CODE = "bench-x"

TEMP = {"type": "t", "incomingKey": "t", "sdmAttribute": "airTemperature", "unit": "°C"}
# case -> (profile measurements, channel (canonical name), value)
CASES = {
    "numeric-unit": ([TEMP], "airTemperature", 21.5),
    "text": ([{"type": "stat", "sdmAttribute": "status", "valueType": "Text"}], "status", "ok"),
    "no-unit": ([{"type": "rt", "sdmAttribute": "windSpeed"}], "windSpeed", 3.2),
    "window-statistic": ([{**TEMP, "statistics": ["mean", "max"]}], "airTemperatureMax", 28.4),
}
SHAPES = ("legacy", "sdm")


def example_name(case, shape):
    return f"DeviceMeasurement.{shape}.{case}.json"


def render(case, shape, monkeypatch):
    measurements, name, value = CASES[case]
    monkeypatch.setenv(SDM_SHAPE_FLAG, "true" if shape == "sdm" else "false")
    device = {"id": DEVICE, "type": "Device", "profileCode": {"type": "Property", "value": CODE},
              name: {"type": "Property", "value": value, "observedAt": OBSERVED}}
    built = build_measurements(device, {"mapping": {"measurements": measurements}})
    [dm] = [e for e in built if e["id"].endswith(f":{name}")]
    return dm


def mapping_view(case, shape):
    """What the agent mappings of the channel write, read off the generated list."""
    measurements, name, _ = CASES[case]
    channels = measurement_channels(measurements, tenant="acme", external_id="sensor-001",
                                    device_urn=DEVICE, profile_code=CODE, shape=shape)
    [channel] = [c for c in channels if c.name == name]
    mine = [m for m in dm_mappings([channel], device_urn=DEVICE) if m["entity_name"] == channel.dm_id]
    literal = re.compile(r"\? '((?:[^'\\]|\\.)*)' : null$")
    consts = {}
    for m in mine:
        found = literal.search(m.get("expression", ""))
        if found and m["name"] not in ("timeSource",):
            consts[m["name"]] = re.sub(r"\\(.)", r"\1", found.group(1))
    units = {m["metadata"]["unitCode"]["value"] for m in mine if m.get("metadata")}
    return channel, {m["name"] for m in mine}, consts, units


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("case", CASES)
def test_file_is_the_output_of_build_measurements(case, shape, monkeypatch):
    path = EXAMPLES / example_name(case, shape)
    assert path.exists(), f"missing example {path.name}"
    assert json.loads(path.read_text()) == render(case, shape, monkeypatch)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("case", CASES)
def test_file_equals_what_the_mappings_write(case, shape, monkeypatch):
    channel, names, consts, units = mapping_view(case, shape)
    dm = json.loads((EXAMPLES / example_name(case, shape)).read_text())
    assert dm["id"] == channel.dm_id and dm["type"] == "DeviceMeasurement"
    value_names = {"numValue", "textValue"} & names
    present = {k for k in dm if k not in ("id", "type")}
    assert present - value_names == names - {"numValue", "textValue", "TimeInstant"}
    assert len(present & value_names) == 1 and (present & value_names) <= value_names
    # relationship and fixed attributes carry exactly the mapping's constants
    assert dm["refDevice"]["object"] == consts["refDevice"]
    for fixed, expected in channel.fixed.items():
        assert dm[fixed]["value"] == consts[fixed] == expected
    # the unit, and only when the mappings declare one
    value_key = (present & value_names).pop()
    if units:
        assert units == {dm[value_key]["unitCode"]}
    else:
        assert "unitCode" not in dm[value_key]
    assert (value_key == "textValue") == (channel.kind == "text" or isinstance(CASES[case][2], str))
    assert not any("observedAt" in v for v in dm.values() if isinstance(v, dict))


def test_every_example_file_is_covered():
    extra = {p.name for p in EXAMPLES.glob("DeviceMeasurement.*.*.json")}
    assert extra == {example_name(c, s) for c in CASES for s in SHAPES}
