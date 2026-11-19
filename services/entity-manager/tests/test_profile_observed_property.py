"""Profile measurements may carry `observedProperty` (catalogue-checked) and `counterMax`."""
from unittest.mock import patch

import pytest

import dataloggers
import observation_catalogue
from blueprints.sensors import _validated_profile

TENANT = "tenant-a"
PROPS = {
    "airTemperature": {"name": "airTemperature", "temporal_semantics": "instant"},
    "precipitation": {"name": "precipitation", "temporal_semantics": "cumulative"},
}


def _body(**extra):
    m = {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C", **extra}
    return {"name": "P", "mapping": {"measurements": [m]}}


def _validate(**extra):
    with patch.object(observation_catalogue, "get_property",
                      lambda tenant, name: PROPS.get(name)):
        return _validated_profile(_body(**extra), TENANT)


def test_observed_property_must_exist_in_catalogue():
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(observedProperty="x")
    assert e.value.status == 400
    assert e.value.message == 'Unknown observedProperty "x"'


def test_counter_max_only_for_cumulative():
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(observedProperty="airTemperature", counterMax=100)
    assert e.value.status == 400


def test_counter_max_requires_observed_property():
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(counterMax=100)
    assert e.value.status == 400


@pytest.mark.parametrize("bad", [0, -1, "10", True, float("inf"), float("nan"), None])
def test_counter_max_must_be_positive_number(bad):
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(observedProperty="precipitation", counterMax=bad)
    assert e.value.status == 400


def test_observed_property_must_be_text():
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(observedProperty=5)
    assert e.value.status == 400


def test_clean_measurement_keeps_observed_property_and_counter_max():
    _, _, mapping = _validate(sdmAttribute="rain", observedProperty="precipitation",
                              counterMax=255.5, unit="mm")
    m = mapping["measurements"][0]
    assert m["observedProperty"] == "precipitation"
    assert m["counterMax"] == 255.5


def test_observed_property_is_optional_in_phase1():
    _, _, mapping = _validate()
    m = mapping["measurements"][0]
    assert "observedProperty" not in m and "counterMax" not in m


def test_get_property_prefers_tenant_row_and_skips_deprecated():
    captured = {}

    class Cur:
        def execute(self, sql, params):
            captured["sql"], captured["params"] = " ".join(sql.split()), params

        def fetchone(self):
            return {"name": "airTemperature"}

        def close(self):
            pass

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self, **kw):
            return Cur()

    with patch.object(observation_catalogue, "get_db_connection_with_tenant",
                      lambda t: Conn()):
        row = observation_catalogue.get_property(TENANT, "airTemperature")
    assert row == {"name": "airTemperature"}
    assert "deprecated_at IS NULL" in captured["sql"]
    assert "ORDER BY tenant_id NULLS LAST" in captured["sql"]
    assert captured["params"] == ("airTemperature", TENANT)


def test_get_property_db_error_is_502():
    def boom(t):
        raise RuntimeError("db down")

    with patch.object(observation_catalogue, "get_db_connection_with_tenant", boom):
        with pytest.raises(dataloggers.ApiError) as e:
            observation_catalogue.get_property(TENANT, "x")
    assert e.value.status == 502
