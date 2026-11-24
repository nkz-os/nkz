"""Contract for `common.device_measurement_shape` (pure, shared by the EM and the IoTA mapping generator)."""
import dataclasses

import pytest

from common.context_aliases import CANONICAL, canonical_attribute
from common.device_measurement_shape import SHAPES, Channel, channels_by_name, measurement_channels

KW = dict(tenant="acme", external_id="dev-1", device_urn="urn:ngsi-ld:Device:acme:dev-1", profile_code="PROF")
ALIAS = next(a for a in CANONICAL if canonical_attribute(a) != a)
CANON = canonical_attribute(ALIAS)


def chans(measurements, **over):
    return measurement_channels(measurements, **{**KW, "shape": "legacy", **over})


def by_name(channels):
    return {c.name: c for c in channels}


STATS = [{"type": "temp", "unit": "°C", "sdmAttribute": "soilTemp", "statistics": ["mean", "min", "max"]}]


def test_shapes_constant():
    assert SHAPES == ("legacy", "sdm")


def test_channel_is_frozen():
    c = chans([{"type": "t", "unit": "°C", "sdmAttribute": "x"}])[0]
    assert isinstance(c, Channel)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.name = "y"


def test_sdm_stats_profile():
    cs = chans(STATS, shape="sdm")
    assert [c.name for c in cs] == ["soilTemp", "soilTempMin", "soilTempMax"]
    m = by_name(cs)
    assert m["soilTemp"].key == "temp"
    assert m["soilTempMin"].key == "tempMin"
    assert m["soilTempMax"].key == "tempMax"
    assert m["soilTemp"].dm_id == "urn:ngsi-ld:DeviceMeasurement:acme:dev-1:soilTemp"
    for name, stat in (("soilTemp", "mean"), ("soilTempMin", "min"), ("soilTempMax", "max")):
        assert m[name].fixed == {
            "controlledProperty": "soilTemp", "measurementType": stat,
            "name": name, "deviceType": "PROF",
        }
        assert m[name].unit_code == "CEL"
        assert m[name].kind == "number"


def test_sdm_observed_property_wins_and_instant():
    cs = chans([{"type": "t", "unit": "°C", "sdmAttribute": "x", "observedProperty": "catalogName"}], shape="sdm")
    assert cs[0].fixed["controlledProperty"] == "catalogName"
    assert cs[0].fixed["measurementType"] == "instant"
    assert cs[0].fixed["name"] == "x"


def test_legacy_shape():
    m = by_name(chans(STATS))
    assert m["soilTempMin"].fixed == {
        "controlledProperty": "soilTempMin", "measurementType": "temp", "deviceType": "PROF",
    }
    assert "name" not in m["soilTemp"].fixed


def test_no_profile_code_no_device_type():
    cs = chans([{"type": "t", "unit": "°C", "sdmAttribute": "x"}], profile_code=None)
    assert "deviceType" not in cs[0].fixed


def test_observed_property_never_in_fixed():
    for shape in SHAPES:
        cs = chans([{"type": "t", "unit": "°C", "sdmAttribute": "x", "observedProperty": "o"}], shape=shape)
        assert "observedProperty" not in cs[0].fixed
        assert "timeSource" not in cs[0].fixed


def test_alias_resolved_to_canonical():
    cs = chans([{"type": "t", "unit": "°C", "sdmAttribute": ALIAS}], shape="sdm")
    assert cs[0].name == CANON
    assert cs[0].dm_id.endswith(f":{CANON}")


def test_alias_and_canonical_share_id_canonical_last():
    cs = chans([
        {"type": "c", "unit": "°C", "sdmAttribute": CANON, "incomingKey": "kc"},
        {"type": "a", "unit": "°C", "sdmAttribute": ALIAS, "incomingKey": "ka"},
    ])
    assert [c.key for c in cs] == ["ka", "kc"]
    assert cs[0].dm_id == cs[1].dm_id
    assert {c.name for c in cs} == {CANON}


def test_unknown_unit_raises():
    with pytest.raises(ValueError):
        chans([{"type": "t", "unit": "not-a-unit", "sdmAttribute": "x"}])


def test_unknown_shape_raises():
    with pytest.raises(ValueError):
        chans(STATS, shape="other")


@pytest.mark.parametrize("m,kind", [
    ({"unit": "°C"}, "number"),
    ({"valueType": "Number"}, "number"),
    ({"valueType": "Text"}, "text"),
    ({"valueType": "Text", "unit": "°C"}, "text"),
    ({"valueType": "Number", "unit": ""}, "number"),
    ({}, "runtime"),
    ({"unit": ""}, "runtime"),
])
def test_kinds(m, kind):
    cs = chans([{"type": "t", "sdmAttribute": "x", **m}])
    assert cs[0].kind == kind


def test_unit_code_none_without_unit():
    assert chans([{"type": "t", "sdmAttribute": "x"}])[0].unit_code is None


def test_incoming_key_rule_and_expression():
    cs = chans([{"type": "t", "sdmAttribute": "x", "incomingKey": "raw", "expression": "raw * 10"},
                {"type": "u", "sdmAttribute": "y"}])
    assert (cs[0].key, cs[0].expression) == ("raw", "raw * 10")
    assert (cs[1].key, cs[1].expression) == ("u", None)


def test_external_id_with_colon():
    cs = chans([{"type": "t", "sdmAttribute": "x"}], external_id="a:b")
    assert cs[0].dm_id == "urn:ngsi-ld:DeviceMeasurement:acme:a:b:x"


def test_same_key_and_name_collapses_last_wins():
    cs = chans([{"type": "t", "sdmAttribute": "x", "unit": "°C"},
                {"type": "t", "sdmAttribute": "x", "unit": "%"}])
    assert len(cs) == 1 and cs[0].unit_code == "P1"


def test_channels_by_name_last_in_profile_order_and_lazy_units():
    ms = [{"type": "c", "unit": "°C", "sdmAttribute": CANON},
          {"type": "a", "unit": "%", "sdmAttribute": ALIAS},
          {"type": "bad", "unit": "nope", "sdmAttribute": "absent"}]
    kw = {**KW, "shape": "legacy"}
    got = channels_by_name(ms, names={CANON}, **kw)
    assert set(got) == {CANON}
    assert got[CANON].key == "a"  # last definition in profile order, as the EM always did
    with pytest.raises(ValueError):
        channels_by_name(ms, names=None, **kw)


def test_missing_type_and_incoming_key_raises_in_channels_not_by_name():
    ms = [{"sdmAttribute": "x", "unit": "°C"}]
    with pytest.raises(KeyError):
        chans(ms)
    assert set(channels_by_name(ms, names={"x"}, **{**KW, "shape": "legacy"})) == {"x"}


def test_fixed_is_read_only():
    c = chans([{"type": "t", "sdmAttribute": "x"}])[0]
    with pytest.raises(TypeError):
        c.fixed["x"] = 1
