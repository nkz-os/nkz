"""Tests for the EMMA awareness-zone geometry index."""

import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

from unittest.mock import MagicMock, patch

from weather_worker.emma_zones import EmmaZoneIndex, normalize_zone_payload

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "emma_zones_sample.geojson")

_POLY = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}


def test_geometry_for_known_zone():
    idx = EmmaZoneIndex(FIXTURE)
    geom = idx.geometry_for("ES190")
    assert geom is not None
    assert geom["type"] in ("Polygon", "MultiPolygon")
    assert isinstance(geom["coordinates"], list)


def test_geometry_for_multipolygon_zone():
    idx = EmmaZoneIndex(FIXTURE)
    geom = idx.geometry_for("DE311")
    assert geom is not None
    assert geom["type"] == "MultiPolygon"


def test_geometry_for_unknown_zone_returns_none():
    idx = EmmaZoneIndex(FIXTURE)
    assert idx.geometry_for("ZZ999") is None


def test_null_geometry_feature_is_skipped():
    idx = EmmaZoneIndex(FIXTURE)
    assert idx.geometry_for("NOGEOM") is None


def test_empty_emma_id_returns_none():
    idx = EmmaZoneIndex(FIXTURE)
    assert idx.geometry_for("") is None


def test_missing_source_is_empty_not_fatal(tmp_path):
    idx = EmmaZoneIndex(str(tmp_path / "does-not-exist.geojson"))
    assert idx.geometry_for("ES190") is None


def test_no_source_is_empty_not_fatal():
    idx = EmmaZoneIndex("")
    assert idx.geometry_for("ES190") is None


# ── normalize_zone_payload (shared with the prep script) ─────────────────────
def test_normalize_feature_collection():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": "ES190"}, "geometry": _POLY}]}
    out = normalize_zone_payload(fc)
    assert out["features"][0]["properties"]["emma_id"] == "ES190"


def test_normalize_region_list():
    out = normalize_zone_payload([{"emma_id": "DE311", "geometry": _POLY}, {"id": "x"}])
    assert len(out["features"]) == 1
    assert out["features"][0]["properties"]["emma_id"] == "DE311"


def test_normalize_regions_wrapper():
    out = normalize_zone_payload({"regions": [{"code": "FR12", "geom": _POLY}]})
    assert out["features"][0]["properties"]["emma_id"] == "FR12"


def test_normalize_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        normalize_zone_payload({"nope": 1})


# ── Authenticated URL fetch (option B: runtime pull with SealedSecret key) ────
def test_url_source_sends_bearer_and_normalizes():
    payload = {"regions": [{"code": "ES190", "geometry": _POLY}]}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    with patch("weather_worker.emma_zones.requests.get", return_value=resp) as g:
        idx = EmmaZoneIndex("https://api.meteoalarm.org/metadata/v1/regions", api_key="k")
    assert idx.geometry_for("ES190") is not None
    assert g.call_args[1]["headers"]["Authorization"] == "Bearer k"


def test_url_fetch_failure_is_empty_not_fatal():
    with patch("weather_worker.emma_zones.requests.get", side_effect=RuntimeError("down")):
        idx = EmmaZoneIndex("https://api.meteoalarm.org/metadata/v1/regions", api_key="k")
    assert idx.geometry_for("ES190") is None
