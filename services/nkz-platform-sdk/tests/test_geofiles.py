"""Tests for the geo-file reader (KML/KMZ/GPX/GeoJSON)."""

import io
import json
import zipfile

import pytest

from nkz_platform_sdk.gis.geofiles import GeoFileError, read_geofile

GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="42.6123" lon="-1.8456"><name>T-001</name></wpt>
  <wpt lat="42.6124" lon="-1.8457"><name>T-002</name></wpt>
</gpx>"""

GPX_TRACK = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="42.6" lon="-1.8"><name>T-001</name></wpt>
  <trk><name>walk</name><trkseg><trkpt lat="42.6" lon="-1.8"/></trkseg></trk>
</gpx>"""

KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>T-001</name><Point><coordinates>-1.8456,42.6123,0</coordinates></Point></Placemark>
  <Placemark><name>Z1</name>
    <ExtendedData><Data name="treatment"><value>compost</value></Data></ExtendedData>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -1.85,42.61 -1.84,42.61 -1.84,42.62 -1.85,42.62 -1.85,42.61
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark>
</Document></kml>"""

BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>&lol2;</name></Placemark></kml>"""


def test_gpx_waypoints_become_points_with_name():
    result = read_geofile(GPX, "trees.gpx")
    assert [f["properties"]["name"] for f in result.features] == ["T-001", "T-002"]
    assert result.features[0]["geometry"] == {"type": "Point", "coordinates": [-1.8456, 42.6123]}
    assert result.warnings == []


def test_gpx_track_is_skipped_with_warning():
    result = read_geofile(GPX_TRACK, "trees.gpx")
    assert len(result.features) == 1
    assert len(result.warnings) == 1 and "track" in result.warnings[0].lower()


def test_kml_placemark_point_and_polygon():
    result = read_geofile(KML, "zones.kml")
    point, polygon = result.features
    assert point["geometry"] == {"type": "Point", "coordinates": [-1.8456, 42.6123]}
    assert polygon["geometry"]["type"] == "Polygon"
    assert polygon["geometry"]["coordinates"][0][0] == [-1.85, 42.61]
    assert polygon["properties"]["name"] == "Z1"


def test_kml_extended_data_to_properties():
    result = read_geofile(KML, "zones.kml")
    assert result.features[1]["properties"]["treatment"] == "compost"


def test_kmz_reads_inner_kml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", KML)
    result = read_geofile(buf.getvalue(), "zones.kmz")
    assert len(result.features) == 2


def test_geojson_feature_collection_passthrough():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1.8, 42.6]},
         "properties": {"plantCode": "T-001"}}]}
    result = read_geofile(json.dumps(fc).encode(), "trees.geojson")
    assert result.features[0]["properties"] == {"plantCode": "T-001"}


def test_unknown_extension_raises():
    with pytest.raises(GeoFileError):
        read_geofile(b"x", "trees.shp")


def test_kml_entity_expansion_is_rejected():
    with pytest.raises(GeoFileError):
        read_geofile(BOMB, "bomb.kml")


def test_out_of_range_coordinates_are_skipped():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [600000, 4700000]},
         "properties": {"name": "T-009"}}]}
    result = read_geofile(json.dumps(fc).encode(), "utm.geojson")
    assert result.features == []
    assert len(result.warnings) == 1 and "T-009" in result.warnings[0]


def test_feature_limit_raises(monkeypatch):
    import nkz_platform_sdk.gis.geofiles as gf

    monkeypatch.setattr(gf, "MAX_FEATURES", 1)
    with pytest.raises(GeoFileError):
        read_geofile(GPX, "trees.gpx")
