"""Validation lookup: only the parcel's own regions, never a neighbour's."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import json
import re

from app.services.history import validation


def test_inside_region_returns_both_networks_and_periods():
    out = validation.validation_for(42.0, -1.5, "tmax_c", "daily")   # Ebro: AEMET + SIAR
    assert isinstance(out, list) and out
    assert {"AEMET", "SIAR"} <= {e["network"] for e in out}
    assert {e["region"] for e in out} <= {"ebro_upper", "siar_ebro_upper", "meseta_north", "siar_meseta_north"}
    assert len({e["period"] for e in out}) >= 2
    assert all(e["variable"] == "tmax_c" for e in out)


def test_germany_returns_dwd():
    out = validation.validation_for(52.0, 12.5, "tmax_c", "daily")
    assert out != "unvalidated_region"
    assert {e["network"] for e in out} == {"DWD"}


def test_outside_every_region_is_unvalidated():
    for lat, lon in ((48.8, 2.3), (28.1, -15.4), (41.0, -9.0)):
        assert validation.validation_for(lat, lon, "tmax_c", "daily") == "unvalidated_region"


def test_variable_without_rows_in_region_is_unvalidated():
    # no radiation rows exist for the German regions
    assert validation.validation_for(52.0, 12.5, "radiation_mj_m2", "daily") == "unvalidated_region"


def test_resolution_filters():
    assert validation.validation_for(52.0, 12.5, "t_c", "daily") == "unvalidated_region"
    assert validation.validation_for(42.0, -1.5, "t_c", "hourly") == "unvalidated_region"   # Spain: no hourly rows
    out = validation.validation_for(52.0, 12.5, "t_c", "hourly")
    assert out != "unvalidated_region" and {e["network"] for e in out} == {"DWD"}


def test_never_returns_another_regions_entries():
    out = validation.validation_for(52.0, 12.5, "tmax_c", "daily")
    regions = {r["id"]: r["bbox"] for r in validation.load_summary()["regions"]}
    for e in out:
        s, n, w, east = regions[e["region"]]
        assert s <= 52.0 <= n and w <= 12.5 <= east


def test_summary_has_no_station_identifiers():
    raw = open(validation.SUMMARY_PATH, encoding="utf-8").read()
    data = json.loads(raw)
    assert set(data) == {"schema", "generated_from", "regions", "entries"}
    for e in data["entries"]:
        assert "station" not in e and "station_id" not in e and "name" not in e
    assert not re.search(r"https?://", raw)
