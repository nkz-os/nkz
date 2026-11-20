from common import window_statistics as ws

T = {"type": "temp", "sdmAttribute": "temperature", "unit": "°C", "observedProperty": "airTemperature"}


def test_without_statistics_passes_through_unchanged():
    assert ws.expand_measurements([dict(T)]) == [dict(T)]
    assert ws.gateway_statistics(T) is None


def test_statistics_expand_to_canonical_derived_attributes_and_keys():
    out = ws.expand_measurements([{**T, "statistics": ["mean", "min", "max", "stddev"]}])
    assert [(m["sdmAttribute"], m.get("incomingKey", m["type"]), m["statistic"]) for m in out] == [
        ("airTemperature", "temp", "mean"), ("airTemperatureMin", "tempMin", "min"),
        ("airTemperatureMax", "tempMax", "max"), ("airTemperatureStdDev", "tempStdDev", "stddev")]
    assert all(m["unit"] == "°C" and m["baseAttribute"] == "airTemperature" and "statistics" not in m for m in out)


def test_derived_entries_drop_expression_and_counter():
    out = ws.expand_measurements([{**T, "incomingKey": "t", "expression": "t*1", "statistics": ["mean", "max"]}])
    assert out[0]["expression"] == "t*1" and "expression" not in out[1] and out[1]["incomingKey"] == "tMax"


def test_derived_entries_drop_counter_max():
    m = {"type": "rain", "sdmAttribute": "precipitationAccumulated", "counterMax": 100, "statistics": ["last", "max"]}
    out = ws.expand_measurements([m])
    assert out[0]["counterMax"] == 100 and "counterMax" not in out[1]


def test_only_derived_statistics_emit_no_base_entry():
    out = ws.expand_measurements([{**T, "statistics": ["max", "min"]}])
    assert [(m["sdmAttribute"], m["statistic"], m["baseAttribute"]) for m in out] == [
        ("airTemperatureMin", "min", "airTemperature"), ("airTemperatureMax", "max", "airTemperature")]
    assert ws.gateway_statistics({**T, "statistics": ["max", "min"]}) == {"max": "tempMax", "min": "tempMin"}


def test_output_order_is_base_min_max_stddev_regardless_of_input_order():
    out = ws.expand_measurements([{**T, "statistics": ["stddev", "max", "mean", "min"]}])
    assert [m["statistic"] for m in out] == ["mean", "min", "max", "stddev"]


def test_input_is_not_mutated():
    src = {**T, "statistics": ["mean", "min"]}
    ws.expand_measurements([src])
    assert src == {**T, "statistics": ["mean", "min"]}


def test_gateway_statistics_maps_each_statistic_to_its_key():
    assert ws.gateway_statistics({**T, "statistics": ["mean", "min"]}) == {"mean": "temp", "min": "tempMin"}
    assert ws.gateway_statistics({"type": "rain", "sdmAttribute": "precipitationAccumulated", "statistics": ["last"]}) == {"last": "rain"}


def test_base_attribute():
    assert ws.base_attribute("airTemperatureMin") == "airTemperature"
    assert ws.base_attribute("equipmentTemperatureStdDev") == "equipmentTemperature"
    assert ws.base_attribute("airTemperature") == "airTemperature"
    assert ws.base_attribute("Max") == "Max"
