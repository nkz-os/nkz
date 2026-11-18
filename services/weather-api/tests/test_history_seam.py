"""Seam block in /history: measured seam from the bundled public summary (additive, no correction)."""
import hashlib
import json
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from app.services.history import seam
from app.services.history.validation import validation_for
from test_parcel_history import env, _get, _entity  # noqa: F401  (env is a fixture)

# sha256 of the harness export (validation/seam/seam_public.json, local); regenerate both together.
SEAM_PUBLIC_SHA256 = "ddf13449da54fc9fd78008c0b0075ad85ba7e217ecfc125b61d7a043c7728faa"


def test_data_file_sha256_is_pinned():
    raw = open(seam.SEAM_PATH, "rb").read()
    assert hashlib.sha256(raw).hexdigest() == SEAM_PUBLIC_SHA256


def test_data_file_is_public_safe():
    raw = open(seam.SEAM_PATH, encoding="utf-8").read()
    doc = json.loads(raw)
    assert set(doc) == {"method", "not_judged_regions", "regions", "schema_version", "window"}
    assert "tenant" not in raw.lower() and "parcel" not in raw.lower()


def _matching(lat, lon):
    return {r["id"] for r in seam.load_summary()["regions"]
            if r["bbox"][0] <= lat <= r["bbox"][1] and r["bbox"][2] <= lon <= r["bbox"][3]}


def test_ebro_overlap_serves_every_matching_region_like_validation():
    out = seam.seam_for(42.0, -1.5, "tmax_c", "daily")
    assert set(out) == {"status", "reason", "region", "season_cells", "method", "window"}
    assert out["status"] == "medido" and out["reason"] is None
    assert set(out["region"]) == _matching(42.0, -1.5) == {e["region"] for e in validation_for(42.0, -1.5, "tmax_c", "daily")}
    assert set(out["season_cells"]) == set(out["region"])
    for cells in out["season_cells"].values():
        assert set(cells) == {"DJF", "MAM", "JJA", "SON", "annual"}


def test_tmin_is_served_exactly_as_the_file_says():
    out = seam.seam_for(42.0, -1.5, "tmin_c", "daily")
    doc = json.load(open(seam.SEAM_PATH, encoding="utf-8"))
    for region, cells in out["season_cells"].items():
        assert cells["annual"] == doc["regions"][region]["tmin_c"]["all"]
        assert cells["annual"]["bias"] is None and cells["annual"]["reason"] == "metodo_tmin_en_cambio"


def _null(status, reason):
    return {"status": status, "reason": reason, "region": None, "season_cells": None, "method": None, "window": None}


def test_germany_is_sin_juzgar_region():
    assert seam.seam_for(52.0, 12.5, "tmax_c", "daily") == _null("sin_juzgar", "sin_juzgar_region")


def test_outside_every_region_is_not_available():
    assert seam.seam_for(48.8, 2.3, "tmax_c", "daily") == _null("no_disponible", "region_no_validada")


def test_hourly_and_non_seam_variables_are_not_available():
    assert seam.seam_for(42.0, -1.5, "t_c", "hourly") == _null("no_disponible", "solo_diario")
    assert seam.seam_for(42.0, -1.5, "rh_mean_pct", "daily") == _null("no_disponible", "variable_sin_medida")


def test_missing_file_gives_null_and_no_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(seam, "SEAM_PATH", str(tmp_path / "absent.json"))
    seam.load_seam.cache_clear()
    try:
        assert seam.load_seam() is None
        assert seam.seam_for(42.0, -1.5, "tmax_c", "daily") == _null("no_disponible", "datos_no_disponibles")
    finally:
        seam.load_seam.cache_clear()


def test_history_missing_file_keeps_endpoint_working(env, monkeypatch, tmp_path):
    c, _ = env
    monkeypatch.setattr(seam, "SEAM_PATH", str(tmp_path / "absent.json"))
    seam.load_seam.cache_clear()
    try:
        r = _get(c, vars="tmax_c,precip_mm")
        assert r.status_code == 200
        b = r.json()
        assert b["seam"] == {"tmax_c": _null("no_disponible", "datos_no_disponibles"),
                             "precip_mm": _null("no_disponible", "datos_no_disponibles")}
    finally:
        seam.load_seam.cache_clear()


def test_history_block_present_and_null_for_unvalidated(env):
    c, h = env
    b = _get(c, vars="tmax_c,et0_mm").json()
    assert set(b["seam"]) == {"tmax_c", "et0_mm"}
    assert b["seam"]["tmax_c"]["status"] == "medido" and b["seam"]["tmax_c"]["season_cells"]
    assert "seam_reasons" not in b
    h["entity"] = _entity(lon=100.0, lat=10.0)
    assert _get(c, vars="tmax_c").json()["seam"] == {"tmax_c": _null("no_disponible", "region_no_validada")}
    h["entity"] = _entity(lon=12.5, lat=52.0)
    assert _get(c, vars="tmax_c").json()["seam"] == {"tmax_c": _null("sin_juzgar", "sin_juzgar_region")}


def test_frost_fields_have_no_seam_entry(env):
    c, _ = env
    b = _get(c, vars="tmin_frost_risk_c,tmax_c").json()
    assert set(b["seam"]) == {"tmax_c"}


def test_existing_fields_are_byte_identical_to_pre_seam_snapshot(env):
    c, _ = env
    snap = json.load(open(os.path.join(_TEST_DIR, "data", "history_pre_seam_snapshot.json"), encoding="utf-8"))
    cases = {
        "daily_default": _get(c),
        "daily_tmax_precip": _get(c, vars="tmax_c,precip_mm"),
        "hourly": _get(c, resolution="hourly", start="2020-03-01", end="2020-03-01", vars="t_c,precip_mm"),
    }
    for name, r in cases.items():
        body = r.json()
        body.pop("seam")
        assert json.dumps(body, sort_keys=True) == json.dumps(snap[name], sort_keys=True), name


def test_single_region_is_a_one_element_list_keyed_by_id(monkeypatch):
    real = seam.load_summary()
    only = {**real, "regions": [r for r in real["regions"] if r["id"] == "siar_interior_south"]}
    monkeypatch.setattr(seam, "load_summary", lambda: only)
    out = seam.seam_for(38.0, -4.0, "precip_mm", "daily")
    assert out["status"] == "medido"
    assert out["region"] == ["siar_interior_south"]
    assert list(out["season_cells"]) == ["siar_interior_south"]
    assert set(out["season_cells"]["siar_interior_south"]) == {"DJF", "MAM", "JJA", "SON", "annual"}


def test_overlap_is_a_multi_element_list_keyed_by_id():
    out = seam.seam_for(38.0, -4.0, "precip_mm", "daily")       # AEMET + SIAR, same bbox
    assert sorted(out["region"]) == ["interior_south", "siar_interior_south"]
    assert sorted(out["season_cells"]) == sorted(out["region"])
