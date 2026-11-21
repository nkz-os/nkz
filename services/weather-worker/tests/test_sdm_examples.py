"""Smart Data Models conformance examples of the entities this service builds.

`config/sdm-examples/` holds one entity per file exactly as the builders emit it; CI validates
those files against the official schema (`.github/scripts/check_sdm_conformance.py`). These
tests keep the files equal to the builders' output. To refresh them after a deliberate change:
SDM_EXAMPLES_WRITE=1 python -m pytest tests/test_sdm_examples.py
"""

import json
import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.closed_day as cd  # noqa: E402
from weather_worker.storage import orion_writer  # noqa: E402

EXAMPLES_DIR = os.path.normpath(os.path.join(_ww, "..", "..", "config", "sdm-examples"))
PARCEL = "urn:ngsi-ld:AgriParcel:abc-123"


def _assert_example(file_name, entity):
    entity = {key: value for key, value in entity.items() if key != "@context"}
    path = os.path.join(EXAMPLES_DIR, file_name)
    if os.environ.get("SDM_EXAMPLES_WRITE") == "1":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entity, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == entity, f"{file_name} no longer matches what the builder emits"


def test_closed_day_weather_observed_example():
    values = {"tmin_c": 11.2, "tmax_c": 24.5, "precip_mm": 3.4, "et0_mm": 3.9, "radiation_mj_m2": 15.5,
              "vapour_pressure_kpa": 1.2, "wind2m_ms": 3.7, "frost_hours": 0.0}
    entity = cd.build_closed_day_entity("tenant-a", PARCEL, (-1.6, 42.8), "2026-10-07", values, context_url="")
    _assert_example("WeatherObserved.closed-day.json", entity)


def test_weather_forecast_example():
    daily = {"temp_min": 11.2, "temp_max": 24.5, "humidity_avg": 62.0, "precip_probability": 35.0}
    entity = orion_writer.build_weather_forecast_entity(
        PARCEL, "tenant-a", (-1.6, 42.8), daily, "2026-10-08T00:00:00Z", "2026-10-09T00:00:00Z"
    )
    _assert_example("WeatherForecast.json", entity)
