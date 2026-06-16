import pytest
from parcel_geometry import validate_parcel_geometry, GeometryError

def _square():
    return {"type": "Polygon", "coordinates": [[[0,0],[0,0.001],[0.001,0.001],[0.001,0],[0,0]]]}

def test_valid_polygon_passes():
    validate_parcel_geometry(_square())  # must not raise

def test_missing_type_rejected():
    with pytest.raises(GeometryError):
        validate_parcel_geometry({"coordinates": [[[0,0],[0,1],[1,1],[0,0]]]})

def test_wrong_type_rejected():
    with pytest.raises(GeometryError):
        validate_parcel_geometry({"type": "Point", "coordinates": [0,0]})

def test_unclosed_ring_rejected():
    with pytest.raises(GeometryError):
        validate_parcel_geometry({"type": "Polygon", "coordinates": [[[0,0],[0,1],[1,1]]]})

def test_too_few_points_rejected():
    with pytest.raises(GeometryError):
        validate_parcel_geometry({"type": "Polygon", "coordinates": [[[0,0],[0,1],[0,0]]]})

def test_multipolygon_valid():
    validate_parcel_geometry({"type": "MultiPolygon", "coordinates": [[_square()["coordinates"][0]]]})
