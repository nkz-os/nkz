"""Tests for the EMMA awareness-zone geometry index."""

import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

from weather_worker.emma_zones import EmmaZoneIndex

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "emma_zones_sample.geojson")


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
