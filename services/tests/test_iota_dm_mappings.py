"""IoT Agent multi-entity mappings that write DeviceMeasurement: ordering, conditions, reserved keys."""
import pytest

from common.device_measurement_shape import Channel
from common.iot_agent import build_iota_attributes
from common.iota_dm_mappings import (
    RESERVED_KEYS,
    InvalidChannelKey,
    UnsupportedChannel,
    device_attributes,
    dm_mappings,
)

URN = "urn:ngsi-ld:Device:tenant-a:dev-1"
DM = "urn:ngsi-ld:DeviceMeasurement:tenant-a:dev-1:temperature"
TIME_NAMES = {"TimeInstant", "dateObserved", "timeSource"}


def G(n):
    return (f"({n} != null && ({n}|parsefloat) == {n} && ({n}|parsefloat) < 1000000000000000"
            f" && ({n}|parsefloat) > -1000000000000000)")


def ch(key="t1", kind="number", unit="CEL", expression=None, name="temperature", dm_id=DM, fixed=None):
    return Channel(dm_id=dm_id, name=name, key=key, expression=expression, kind=kind, unit_code=unit,
                   fixed=fixed if fixed is not None else {"controlledProperty": "temperature",
                                                          "measurementType": "instant"})


def by_name(maps):
    return {m["name"]: m for m in maps}


def test_golden_numeric_channel():
    maps = dm_mappings([ch()], device_urn=URN)
    ent = {"entity_name": DM, "entity_type": "DeviceMeasurement"}
    assert maps == [
        {"object_id": "t1", "name": "numValue", "type": "Number",
         "expression": f"{G('numValue')} ? (numValue|parsefloat) : null",
         "metadata": {"unitCode": {"type": "Text", "value": "CEL"}}, **ent},
        {"object_id": "TimeInstant", "name": "TimeInstant", "type": "DateTime",
         "expression": f"{G('t1')} ? TimeInstant : null", **ent},
        {"object_id": "TimeInstant", "name": "dateObserved", "type": "DateTime",
         "expression": f"{G('t1')} ? (dateObserved ? dateObserved : (0|now|toisodate)) : null", **ent},
        {"object_id": "TimeInstant", "name": "timeSource", "type": "Text",
         "expression": f"{G('t1')} ? (timeSource ? 'device' : 'server') : null", **ent},
        {"object_id": "t1", "name": "refDevice", "type": "Relationship",
         "expression": f"{G('refDevice')} ? '{URN}' : null", **ent},
        {"object_id": "t1", "name": "controlledProperty", "type": "Text",
         "expression": f"{G('controlledProperty')} ? 'temperature' : null", **ent},
        {"object_id": "t1", "name": "measurementType", "type": "Text",
         "expression": f"{G('measurementType')} ? 'instant' : null", **ent},
    ]


def test_dm_mappings_before_device_ones_and_shape_rules():
    attrs = device_attributes([{"type": "t1", "sdmAttribute": "temperature", "unit": "°C"}],
                              [ch()], device_urn=URN)
    first_plain = next(i for i, a in enumerate(attrs) if "entity_name" not in a)
    assert all("entity_name" in a for a in attrs[:first_plain])
    assert all("entity_name" not in a for a in attrs[first_plain:])
    for a in attrs[:first_plain]:
        assert a["entity_type"] == "DeviceMeasurement" and a["expression"] and "static_attributes" not in a
        assert (a["object_id"] == "TimeInstant") == (a["name"] in TIME_NAMES)


def test_no_unit_no_metadata_and_none_fixed_skipped():
    maps = dm_mappings([ch(unit=None, fixed={"controlledProperty": "x", "measurementType": None})],
                       device_urn=URN)
    assert "metadata" not in by_name(maps)["numValue"]
    assert "measurementType" not in by_name(maps)


def test_scaling_golden_every_mapping():
    maps = dm_mappings([ch(expression="t1 * 0.1")], device_urn=URN)
    gt1 = ("(t1 != null && (t1|parsefloat) == t1 && (t1|parsefloat) < 1000000000000000"
           " && (t1|parsefloat) > -1000000000000000)")
    term = " && (((t1 * 0.1)|parsefloat) != null)"

    def g(n):
        return gt1.replace("t1", n) + term

    assert [m["expression"] for m in maps] == [
        g("numValue") + " ? ((t1 * 0.1)|parsefloat) : null",
        gt1 + term + " ? TimeInstant : null",
        gt1 + term + " ? (dateObserved ? dateObserved : (0|now|toisodate)) : null",
        gt1 + term + " ? (timeSource ? 'device' : 'server') : null",
        g("refDevice") + " ? '" + URN + "' : null",
        g("controlledProperty") + " ? 'temperature' : null",
        g("measurementType") + " ? 'instant' : null",
    ]
    assert gt1.startswith("(t1 != null") and g("numValue").startswith("(numValue != null && (numValue|parsefloat) == numValue")


def test_text_channel_golden():
    maps = dm_mappings([ch(kind="text", unit=None)], device_urn=URN)
    assert [(m["object_id"], m["name"], m["type"], m["expression"]) for m in maps] == [
        ("t1", "textValue", "Text", "textValue != null ? textValue : null"),
        ("TimeInstant", "TimeInstant", "DateTime", "t1 != null ? TimeInstant : null"),
        ("TimeInstant", "dateObserved", "DateTime",
         "t1 != null ? (dateObserved ? dateObserved : (0|now|toisodate)) : null"),
        ("TimeInstant", "timeSource", "Text", "t1 != null ? (timeSource ? 'device' : 'server') : null"),
        ("t1", "refDevice", "Relationship", "refDevice != null ? '" + URN + "' : null"),
        ("t1", "controlledProperty", "Text", "controlledProperty != null ? 'temperature' : null"),
        ("t1", "measurementType", "Text", "measurementType != null ? 'instant' : null"),
    ]


def test_runtime_channel_golden():
    maps = dm_mappings([ch(kind="runtime", unit=None)], device_urn=URN)
    assert [(m["object_id"], m["name"], m["type"], m["expression"]) for m in maps] == [
        ("t1", "numValue", "Number", "((numValue|typeof) == 'number') ? numValue : null"),
        ("t1", "textValue", "Text", "((textValue|typeof) == 'string') ? textValue : null"),
        ("TimeInstant", "TimeInstant", "DateTime",
         "(((t1|typeof) == 'number') || ((t1|typeof) == 'string')) ? TimeInstant : null"),
        ("TimeInstant", "dateObserved", "DateTime",
         "(((t1|typeof) == 'number') || ((t1|typeof) == 'string')) ? "
         "(dateObserved ? dateObserved : (0|now|toisodate)) : null"),
        ("TimeInstant", "timeSource", "Text",
         "(((t1|typeof) == 'number') || ((t1|typeof) == 'string')) ? "
         "(timeSource ? 'device' : 'server') : null"),
        ("t1", "refDevice", "Relationship",
         "(((refDevice|typeof) == 'number') || ((refDevice|typeof) == 'string')) ? '" + URN + "' : null"),
        ("t1", "controlledProperty", "Text",
         "(((controlledProperty|typeof) == 'number') || ((controlledProperty|typeof) == 'string'))"
         " ? 'temperature' : null"),
        ("t1", "measurementType", "Text",
         "(((measurementType|typeof) == 'number') || ((measurementType|typeof) == 'string'))"
         " ? 'instant' : null"),
    ]


def test_unit_metadata_on_numvalue_only():
    text = dm_mappings([ch(kind="text", unit="CEL")], device_urn=URN)
    assert all("metadata" not in m for m in text)
    runtime = dm_mappings([ch(kind="runtime", unit="CEL")], device_urn=URN)
    assert [m["name"] for m in runtime if "metadata" in m] == ["numValue"]
    assert by_name(runtime)["numValue"]["metadata"] == {"unitCode": {"type": "Text", "value": "CEL"}}


@pytest.mark.parametrize("kind", ["text", "runtime"])
def test_scaling_on_text_or_runtime_is_unsupported(kind):
    with pytest.raises(UnsupportedChannel) as e:
        dm_mappings([ch(kind=kind, unit=None, expression="a - b")], device_urn=URN)
    assert isinstance(e.value, ValueError)


def test_literals_are_escaped():
    maps = dm_mappings([ch(fixed={"controlledProperty": "a'b\\c", "measurementType": "instant"})],
                       device_urn="urn:x'y")
    exprs = {m["name"]: m["expression"] for m in maps}
    assert exprs["controlledProperty"] == (
        "(controlledProperty != null && (controlledProperty|parsefloat) == controlledProperty"
        " && (controlledProperty|parsefloat) < 1000000000000000"
        " && (controlledProperty|parsefloat) > -1000000000000000) ? 'a\\'b\\\\c' : null")
    assert exprs["refDevice"] == (
        "(refDevice != null && (refDevice|parsefloat) == refDevice"
        " && (refDevice|parsefloat) < 1000000000000000"
        " && (refDevice|parsefloat) > -1000000000000000) ? 'urn:x\\'y' : null")


def _g(n):
    return (f"({n} != null && ({n}|parsefloat) == {n} && ({n}|parsefloat) < 1000000000000000"
            f" && ({n}|parsefloat) > -1000000000000000)")


def test_alias_and_canonical_share_one_time_set():
    maps = dm_mappings([ch(key="alias"), ch(key="temperature")], device_urn=URN)
    assert [(m["object_id"], m["name"]) for m in maps] == [
        ("alias", "numValue"), ("alias", "refDevice"), ("alias", "controlledProperty"),
        ("alias", "measurementType"),
        ("temperature", "numValue"), ("TimeInstant", "TimeInstant"), ("TimeInstant", "dateObserved"),
        ("TimeInstant", "timeSource"), ("temperature", "refDevice"),
        ("temperature", "controlledProperty"), ("temperature", "measurementType"),
    ]
    cond = (
        "(((alias != null && (alias|parsefloat) == alias && (alias|parsefloat) < 1000000000000000"
        " && (alias|parsefloat) > -1000000000000000)) || "
        "((temperature != null && (temperature|parsefloat) == temperature"
        " && (temperature|parsefloat) < 1000000000000000"
        " && (temperature|parsefloat) > -1000000000000000)))")
    exprs = {m["name"]: m["expression"] for m in maps if m["object_id"] == "TimeInstant"}
    assert exprs == {
        "TimeInstant": cond + " ? TimeInstant : null",
        "dateObserved": cond + " ? (dateObserved ? dateObserved : (0|now|toisodate)) : null",
        "timeSource": cond + " ? (timeSource ? 'device' : 'server') : null",
    }


def test_shared_time_set_scaling_term_stays_on_its_own_side():
    maps = dm_mappings([ch(key="alias", expression="alias * 10"), ch(key="temperature")], device_urn=URN)
    t = [m for m in maps if m["name"] == "TimeInstant"]
    assert len(t) == 1
    assert t[0]["expression"] == (
        "(((alias != null && (alias|parsefloat) == alias && (alias|parsefloat) < 1000000000000000"
        " && (alias|parsefloat) > -1000000000000000) && (((alias * 10)|parsefloat) != null)) || "
        "((temperature != null && (temperature|parsefloat) == temperature"
        " && (temperature|parsefloat) < 1000000000000000"
        " && (temperature|parsefloat) > -1000000000000000))) ? TimeInstant : null")


def test_three_channels_on_one_dm_id_and_mixed_kinds():
    maps = dm_mappings([ch(key="a"), ch(key="b", kind="text", unit=None), ch(key="c")], device_urn=URN)
    for name in ("TimeInstant", "dateObserved", "timeSource"):
        assert len([m for m in maps if m["name"] == name]) == 1
    t = by_name(maps)["timeSource"]["expression"]
    assert t == (
        "(((a != null && (a|parsefloat) == a && (a|parsefloat) < 1000000000000000"
        " && (a|parsefloat) > -1000000000000000)) || (b != null) || "
        "((c != null && (c|parsefloat) == c && (c|parsefloat) < 1000000000000000"
        " && (c|parsefloat) > -1000000000000000))) ? (timeSource ? 'device' : 'server') : null")
    assert [m["object_id"] for m in maps if m["name"] == "refDevice"] == ["a", "b", "c"]


def test_distinct_dm_ids_keep_their_own_time_sets():
    other = ch(key="h1", dm_id=DM + "2", name="humidity")
    maps = dm_mappings([ch(), other], device_urn=URN)
    assert len([m for m in maps if m["name"] == "TimeInstant"]) == 2


@pytest.mark.parametrize("key", ["r-3", "name", "TimeInstant", "numValue", "1a", "", "a b", "t1\n", "a\n"])
def test_invalid_keys(key):
    with pytest.raises(InvalidChannelKey) as e:
        dm_mappings([ch(key=key)], device_urn=URN)
    assert isinstance(e.value, ValueError)


@pytest.mark.parametrize("key", ["in", "null", "true", "false", "undefined", "id", "type", "service",
                                 "subservice", "entity_name", "measure_id", "measure_type"])
def test_expression_words_and_agent_context_names_are_refused_as_keys(key):
    with pytest.raises(InvalidChannelKey):
        dm_mappings([ch(key=key)], device_urn=URN)


@pytest.mark.parametrize("key", ["In", "NULL", "True", "FALSE", "Undefined", "ID", "Type", "Service",
                                 "types", "idx", "in_", "nullable"])
def test_reserved_words_are_matched_exactly(key):
    assert dm_mappings([ch(key=key)], device_urn=URN)


def test_reserved_keys_cover_every_dm_name():
    assert {"numValue", "textValue", "refDevice", "controlledProperty", "measurementType", "deviceType",
            "name", "dateObserved", "timeSource", "TimeInstant"} <= set(RESERVED_KEYS)


def test_channels_none_is_exactly_iota_attributes():
    ms = [{"type": "t1", "sdmAttribute": "temperature", "unit": "°C"}]
    assert device_attributes(ms) == build_iota_attributes(ms)
    assert device_attributes(ms, None, device_urn=URN) == build_iota_attributes(ms)


def test_channels_without_device_urn_rejected():
    with pytest.raises(ValueError):
        device_attributes([], [ch()])
