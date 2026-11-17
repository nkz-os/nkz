"""Frost-risk Tmin: unit tests of the margin rule and API tests on /history and /daily."""
import copy
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import require_auth
import app.routers.parcel_daily as pd
import app.routers.parcel_history as ph
from app.services import frost_risk as fr
from app.services.history.method_version import TMIN_METHOD_VERSION
import test_parcel_history as th

H, D = TMIN_METHOD_VERSION["history"], TMIN_METHOD_VERSION["daily_closed"]
EBRO = (42.25, -1.5)             # ebro_upper: history 0 degC field validated, -2 degC field failed (FAR)
DE_SOUTH = (49.0, 11.0)          # germany_south: both fields validated
EAST_COAST = (38.7, -0.65)       # east_coast: both failed
CANARY = (28.1, -15.4)
BALEARIC = (39.6, 2.9)


# ---------------------------------------------------------------- unit: the rule

def _m(region, field, source="history"):
    return fr.load_margins()["regions"][region][source][field]


def test_cold_night_gets_tmin_minus_margin_and_warm_night_is_untouched():
    e = _m("ebro_upper", "tmin_frost_risk_c")
    assert (e["T_c"], e["m"]) == (3.0, 2.3)
    v, p = fr.frost_risk_tmin(-0.5, "ebro_upper", "history", H)
    assert v == pytest.approx(-0.5 - 2.3)
    assert p["status"] == "validado" and p["threshold_c"] == 0.0 and p["label"] == "validado con estaciones AEMET"
    assert fr.frost_risk_tmin(3.0, "ebro_upper", "history", H)[0] == pytest.approx(3.0 - 2.3)   # <= T_c is cold
    assert fr.frost_risk_tmin(3.01, "ebro_upper", "history", H)[0] == 3.01                       # exactly tmin_c
    assert fr.frost_risk_tmin(11.7, "ebro_upper", "history", H)[0] == 11.7


def test_hard_frost_field_uses_its_own_margin_and_provenance():
    e = _m("germany_south", "tmin_hard_frost_risk_c")
    v, p = fr.frost_risk_tmin(-1.0, "germany_south", "history", H, "tmin_hard_frost_risk_c")
    assert v == pytest.approx(-1.0 - e["m"])
    assert p["field"] == "tmin_hard_frost_risk_c" and p["threshold_c"] == -2.0


def test_missing_tmin_gives_null_value_but_keeps_provenance():
    v, p = fr.frost_risk_tmin(None, "ebro_upper", "history", H)
    assert v is None and p["status"] == "validado"


def test_failed_region_is_null_with_the_gate_reason():
    v, p = fr.frost_risk_tmin(-1.0, "east_coast", "history", H)
    assert v is None and p["status"] == "failed" and "FAR" in p["reason"] and p["region"] == "east_coast"
    v, p = fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H, "tmin_hard_frost_risk_c")
    assert v is None and "FAR" in p["reason"]


def test_no_region_island_and_coastal_reasons():
    assert fr.frost_risk_tmin(-1.0, None, "history", H)[1]["reason"] == fr.REASON_REGION
    assert fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H, off_mainland_box="canary_islands")[1]["reason"] \
        == "sin validar (isla/fuera de península)"
    v, p = fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H, coastal=True)
    assert v is None and p["reason"] == "sin validar (celda costera)"
    assert fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H, coastal=None)[0] is None   # cell undetermined


def test_method_mismatch_is_null_method_changed():
    v, p = fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H + " changed")
    assert v is None and p["reason"] == "method_changed"
    assert fr.frost_risk_tmin(-1.0, "ebro_upper", "history", None)[1]["reason"] == "method_changed"


def test_daily_closed_is_null_everywhere_today():
    for region in fr.load_margins()["regions"]:
        for f in fr.FIELDS:
            v, p = fr.frost_risk_tmin(-1.0, region, "daily_closed", D, f)
            assert v is None and p["reason"] == fr.REASON_NONE_VALIDATED


def test_daily_closed_serves_when_a_margin_validates(monkeypatch):
    doc = copy.deepcopy(fr.load_margins())
    doc["regions"]["ebro_upper"]["daily_closed"]["tmin_frost_risk_c"].update(
        status="validated", T_c=4.0, m=2.7, reason=None)
    monkeypatch.setattr(fr, "load_margins", lambda: doc)
    assert fr.frost_risk_tmin(1.0, "ebro_upper", "daily_closed", D)[0] == pytest.approx(1.0 - 2.7)
    assert fr.frost_risk_tmin(1.0, "meseta_north", "daily_closed", D)[0] is None


def test_missing_or_bad_margins_file_gives_null_not_a_crash(monkeypatch, tmp_path, caplog):
    for content in (None, "{not json", json.dumps({"schema_version": 1})):
        path = tmp_path / "m.json"
        if content is not None:
            path.write_text(content)
        monkeypatch.setattr(fr, "MARGINS_PATH", str(tmp_path / ("m.json" if content is not None else "absent.json")))
        fr.load_margins.cache_clear()
        try:
            v, p = fr.frost_risk_tmin(-1.0, "ebro_upper", "history", H)
            assert v is None and p["reason"] == fr.REASON_NO_MARGINS
        finally:
            fr.load_margins.cache_clear()


def test_pinned_method_versions_match_the_margins_file():
    margins = fr.load_margins()
    assert margins["schema_version"] == fr.SCHEMA_VERSION == 2
    assert set(TMIN_METHOD_VERSION) == set(fr.SOURCES)
    assert TMIN_METHOD_VERSION == margins["method_version"]


def test_every_margin_region_has_a_centroid():
    margins = fr.load_margins()
    assert set(margins["regions"]) == set(fr._CENTROIDS)
    assert set(fr.FIELDS) == set(margins["fields"])


def test_method_version_pins_the_calibration_commit():
    for v in TMIN_METHOD_VERSION.values():
        assert "8c7050a" in v
    for v in fr.load_margins()["method_version"].values():
        assert v.startswith("nkz@8c7050a")
    assert TMIN_METHOD_VERSION == fr.load_margins()["method_version"]


@pytest.mark.parametrize("pt,region", [
    (EBRO, "ebro_upper"), (DE_SOUTH, "germany_south"), (EAST_COAST, "east_coast"),
    ((52.0, 13.0), "germany_east"), ((41.0, -5.0), "meseta_north"), ((38.5, -4.0), "interior_south"),
    ((42.0, -3.0), "ebro_upper"), ((50.3, 12.0), "germany_south"),
    ((42.9, -8.0), "meseta_north"),                 # Galicia: outside any validation bbox, nearest zone
    ((43.3, -5.8), "meseta_north"),                 # Asturias
    ((37.4, -6.0), "interior_south"),               # Seville
    ((54.3, 10.1), "germany_east"),                 # Kiel
])
def test_margin_region_is_the_nearest_centroid_of_the_country(pt, region):
    assert fr.margin_region(*pt) == region


@pytest.mark.parametrize("pt", [(10.0, 100.0), (38.7, -9.1), (43.6, 1.45), (51.1, 17.0), (52.4, 4.9),
                                (46.2, 6.1), (28.1, -15.4)])
def test_margin_region_none_outside_spain_and_germany(pt):
    assert fr.margin_region(*pt) is None            # Lisbon, Toulouse, Wroclaw, Amsterdam, Geneva, Canary


def test_a_galicia_like_point_gets_a_value_when_its_zone_validated(env):
    c, h = env
    h["entity"] = th._entity(lat=42.9, lon=-8.0)
    h["shift"] = -10.5
    b = _hist(c, vars="tmin_frost_risk_c").json()
    assert b["days"][0]["tmin_frost_risk_c"] == pytest.approx(-0.5 - _m("meseta_north", "tmin_frost_risk_c")["m"])
    assert b["provenance"]["tmin_frost_risk_c"]["region"] == "meseta_north"


@pytest.mark.parametrize("pt,box", [(CANARY, "canary_islands"), (BALEARIC, "balearic_islands"),
                                    ((35.89, -5.32), "ceuta"), ((35.29, -2.94), "melilla"), (EBRO, None)])
def test_off_mainland_boxes(pt, box):
    assert fr.off_mainland(*pt) == box


# ---------------------------------------------------------------- /history

@pytest.fixture
def env(monkeypatch):
    app.dependency_overrides[require_auth] = lambda: "tenant-a"
    ph._LATEST_CACHE.clear()
    clock = [0.0]
    holder = {"arch": th.Archive(clock=clock), "entity": th._entity(lat=EBRO[0], lon=EBRO[1]), "shift": 0.0}

    def year(*a, **k):
        doc = holder["arch"](*a, **k)
        doc = copy.deepcopy(doc)
        doc["t"] = [t + holder["shift"] for t in doc["t"]]
        return doc

    monkeypatch.setattr(ph, "history_year", year)
    monkeypatch.setattr(ph, "_fetch_parcel_entity", lambda urn, tid: holder["entity"])
    monkeypatch.setattr(ph, "_today", lambda: th.TODAY)
    monkeypatch.setattr(ph, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(ph, "_retry_promise", lambda: True)
    yield TestClient(app), holder
    app.dependency_overrides.pop(require_auth, None)


def _hist(c, **q):
    return th._get(c, **q)


def test_history_cold_and_warm_days_and_hard_field_null_for_failed_region(env):
    c, h = env
    h["shift"] = -10.5                                    # daily tmin -0.5
    b = _hist(c, vars="tmin_c,tmin_frost_risk_c,tmin_hard_frost_risk_c").json()
    day = b["days"][0]
    assert day["tmin_c"] == pytest.approx(-0.5)
    assert day["tmin_frost_risk_c"] == pytest.approx(-0.5 - 2.3)
    assert day["tmin_hard_frost_risk_c"] is None
    p = b["provenance"]
    assert p["tmin_frost_risk_c"]["status"] == "validado" and p["tmin_frost_risk_c"]["m"] == 2.3
    assert p["tmin_hard_frost_risk_c"]["status"] == "failed" and "FAR" in p["tmin_hard_frost_risk_c"]["reason"]
    assert set(b["validation"]) == {"tmin_c"}
    assert b["units"]["tmin_frost_risk_c"] == "degC"
    h["shift"] = 0.0                                      # daily tmin 10
    day = _hist(c, vars="tmin_c,tmin_frost_risk_c").json()["days"][0]
    assert day["tmin_frost_risk_c"] == day["tmin_c"] == pytest.approx(10.0)


def test_history_both_fields_served_in_a_validated_german_zone(env):
    c, h = env
    h["entity"] = th._entity(lat=DE_SOUTH[0], lon=DE_SOUTH[1])
    h["shift"] = -10.5
    b = _hist(c, vars="tmin_frost_risk_c,tmin_hard_frost_risk_c").json()
    day = b["days"][0]
    assert set(day) == {"date", "tmin_frost_risk_c", "tmin_hard_frost_risk_c"}        # tmin_c need not be requested
    assert day["tmin_frost_risk_c"] == pytest.approx(-0.5 - _m("germany_south", "tmin_frost_risk_c")["m"])
    assert day["tmin_hard_frost_risk_c"] == pytest.approx(-0.5 - _m("germany_south", "tmin_hard_frost_risk_c")["m"])
    assert b["provenance"]["tmin_hard_frost_risk_c"]["threshold_c"] == -2.0
    assert b["provenance"]["tmin_frost_risk_c"]["networks"] == ["DWD"]


def test_tmin_c_and_other_fields_are_identical_with_and_without_the_new_fields(env):
    c, h = env
    h["shift"] = -10.5
    old = _hist(c, vars="tmin_c,tmax_c,tmean_c,precip_mm").json()
    new = _hist(c).json()
    for i, day in enumerate(old["days"]):
        assert {k: new["days"][i][k] for k in day} == day
    for v in ("tmin_c", "tmax_c", "tmean_c", "precip_mm"):
        assert new["provenance"][v] == old["provenance"][v] and new["validation"][v] == old["validation"][v]


@pytest.mark.parametrize("pt,reason", [
    ((10.0, 100.0), "sin validar (región no calibrada)"),
    ((38.7, -9.1), "sin validar (región no calibrada)"),
    (CANARY, "sin validar (isla/fuera de península)"),
    (BALEARIC, "sin validar (isla/fuera de península)"),
])
def test_history_unvalidated_places_are_null_with_reason(env, pt, reason):
    c, h = env
    h["entity"] = th._entity(lat=pt[0], lon=pt[1])
    h["shift"] = -10.5
    b = _hist(c, vars="tmin_frost_risk_c,tmin_hard_frost_risk_c").json()
    assert all(d["tmin_frost_risk_c"] is None and d["tmin_hard_frost_risk_c"] is None for d in b["days"])
    assert b["provenance"]["tmin_frost_risk_c"]["reason"] == reason


def test_history_coastal_parcel_is_null_with_reason(env):
    c, h = env
    h["arch"] = th.Archive(coastal=True)
    h["shift"] = -10.5
    b = _hist(c, vars="tmin_frost_risk_c").json()
    assert b["days"][0]["tmin_frost_risk_c"] is None
    assert b["provenance"]["tmin_frost_risk_c"]["reason"] == "sin validar (celda costera)"


def test_history_method_change_is_null(env, monkeypatch):
    c, h = env
    h["shift"] = -10.5
    monkeypatch.setattr(ph, "TMIN_METHOD_VERSION", {**TMIN_METHOD_VERSION, "history": "other method"})
    b = _hist(c, vars="tmin_c,tmin_frost_risk_c").json()
    assert b["days"][0]["tmin_frost_risk_c"] is None and b["days"][0]["tmin_c"] == pytest.approx(-0.5)
    assert b["provenance"]["tmin_frost_risk_c"]["reason"] == "method_changed"


def test_history_missing_margins_file_serves_null_not_500(env, monkeypatch, tmp_path):
    c, h = env
    monkeypatch.setattr(fr, "MARGINS_PATH", str(tmp_path / "absent.json"))
    fr.load_margins.cache_clear()
    try:
        r = _hist(c, vars="tmin_c,tmin_frost_risk_c")
        assert r.status_code == 200
        assert r.json()["days"][0]["tmin_frost_risk_c"] is None
        assert r.json()["provenance"]["tmin_frost_risk_c"]["reason"] == fr.REASON_NO_MARGINS
    finally:
        fr.load_margins.cache_clear()


# ---------------------------------------------------------------- /daily

def _row(day, **m):
    y, mo, d = map(int, day.split("-"))
    return {"observed_at": datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc), "measurements": m}


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self, **kw):
        cur = MagicMock()
        cur.fetchall.return_value = self.rows
        return cur

    def close(self):
        pass


@pytest.fixture
def dclient(monkeypatch):
    app.dependency_overrides[require_auth] = lambda: "tenant-a"
    h = {"entity": th._entity(lat=EBRO[0], lon=EBRO[1]), "coastal": False, "fetches": 0, "probes": 0}
    monkeypatch.setattr(pd, "_parcel_devices", lambda urn, tid: [])

    def fetch(urn, tid):
        h["fetches"] += 1
        return h["entity"]

    def probe(lat, lon, elev, *a, **k):
        h["probes"] += 1
        if h["coastal"] is None:
            raise RuntimeError("probe failed")
        return SimpleNamespace(coastal=h["coastal"])

    monkeypatch.setattr(pd, "_fetch_parcel_entity", fetch)
    monkeypatch.setattr(pd, "resolve_cell", probe)
    monkeypatch.setattr(pd, "_today", lambda: datetime(2026, 11, 20, tzinfo=timezone.utc).date())
    monkeypatch.setattr(pd, "get_db_connection",
                        lambda tid: _Conn([_row("2026-11-01", tempMin=-0.5), _row("2026-11-02", tempMin=8.0)]))
    yield TestClient(app), h
    app.dependency_overrides.pop(require_auth, None)


def _validate_daily(monkeypatch):
    doc = copy.deepcopy(fr.load_margins())
    doc["regions"]["ebro_upper"]["daily_closed"]["tmin_frost_risk_c"].update(
        status="validated", T_c=4.0, m=2.7, reason=None, networks=["AEMET"])
    monkeypatch.setattr(fr, "load_margins", lambda: doc)


URL = "/api/weather/parcel/" + th.PID + "/daily?start=2026-11-01&end=2026-11-03"


def test_daily_is_null_with_reason_today_and_does_not_look_the_parcel_up(dclient):
    c, h = dclient
    b = c.get(URL).json()
    assert all(d["tmin_frost_risk_c"] is None and d["tmin_hard_frost_risk_c"] is None for d in b["days"])
    assert b["days"][0]["tmin_c"] == -0.5                                  # tmin_c untouched
    assert b["provenance"]["tmin_frost_risk_c"]["reason"] == fr.REASON_NONE_VALIDATED
    assert b["units"]["tmin_frost_risk_c"] == "degC" and h["fetches"] == 0


def test_daily_closed_days_get_the_margin_when_validated(dclient, monkeypatch):
    c, h = dclient
    _validate_daily(monkeypatch)
    b = c.get(URL).json()
    d1, d2, d3 = b["days"]
    assert d1["tmin_frost_risk_c"] == pytest.approx(-0.5 - 2.7)
    assert d2["tmin_frost_risk_c"] == 8.0                                  # warm: exactly tmin_c
    assert d3["tmin_frost_risk_c"] is None and d3["tmin_c"] is None        # missing day
    assert d1["tmin_hard_frost_risk_c"] is None                            # not validated for daily_closed
    assert b["provenance"]["tmin_frost_risk_c"]["status"] == "validado"
    assert d1["tmin_c"] == -0.5


def test_daily_coastal_or_undetermined_cell_is_null(dclient, monkeypatch):
    c, h = dclient
    _validate_daily(monkeypatch)
    for coastal in (True, None):
        h["coastal"] = coastal
        b = c.get(URL).json()
        assert b["days"][0]["tmin_frost_risk_c"] is None
        assert b["provenance"]["tmin_frost_risk_c"]["reason"] == fr.REASON_COASTAL


def test_daily_forecast_and_running_days_are_null(dclient, monkeypatch):
    c, h = dclient
    _validate_daily(monkeypatch)
    fake = {"local_today": "2026-11-03", "local_now": "2026-11-03T10:00", "issued_at": "x",
            "days": {"2026-11-03": {"tmin_c": -3.0, "kind": "running"}, "2026-11-04": {"tmin_c": -4.0, "kind": "forecast"}}}
    monkeypatch.setattr(pd, "fetch_open_meteo_forecast", lambda *a, **k: fake)
    b = c.get(URL + "&forecast_days=1").json()
    by = {d["date"]: d for d in b["days"]}
    assert by["2026-11-01"]["tmin_frost_risk_c"] == pytest.approx(-3.2)
    for k in ("2026-11-03", "2026-11-04"):
        assert by[k]["tmin_frost_risk_c"] is None and by[k]["tmin_hard_frost_risk_c"] is None


def test_daily_filled_days_are_null(dclient, monkeypatch):
    c, h = dclient
    _validate_daily(monkeypatch)
    monkeypatch.setattr(pd, "fetch_open_meteo_daily",
                        lambda kind, *a, **k: {"2026-11-03": {"tmin_c": -2.0}})
    b = c.get(URL + "&fill=open_meteo").json()
    d3 = b["days"][2]
    assert d3["tmin_c"] == -2.0 and d3["source"] != "parcel_weather" and d3["tmin_frost_risk_c"] is None
