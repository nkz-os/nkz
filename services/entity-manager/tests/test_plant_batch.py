"""Plant batch validation: ids, codes, GPS tolerance, duplicates and spacing (pure, no network)."""
import base64
import math
import os
import sys

_svc_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _svc_dir not in sys.path:
    sys.path.insert(0, _svc_dir)

from plant_batch import items_from_file, prepare_plants  # noqa: E402

LAT0, LON0 = 42.8, -1.6
PARCEL_URN = "urn:ngsi-ld:AgriParcel:demo-parcel-001"


def _off(east_m, north_m):
    """WGS84 point east_m/north_m metres from the parcel's south-west corner."""
    return (LAT0 + north_m / 110_540.0, LON0 + east_m / (111_320.0 * math.cos(math.radians(LAT0))))


def _ring(size=100):
    pts = [_off(0, 0), _off(size, 0), _off(size, size), _off(0, size), _off(0, 0)]
    return [[lon, lat] for lat, lon in pts]


PARCEL = {"id": PARCEL_URN, "type": "AgriParcel",
          "location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": [_ring()]}}}
CROP = {"id": "urn:ngsi-ld:AgriCrop:olive", "type": "AgriCrop",
        "agroVocConcept": {"type": "Property", "value": "http://aims.fao.org/aos/agrovoc/c_5386"}}


def _item(east, north, **kw):
    lat, lon = _off(east, north)
    return {"lat": lat, "lon": lon, **kw}


def _run(items, existing=None, crop=None):
    return prepare_plants("tenant-a", PARCEL, items, existing or {}, crop)


def test_ids_are_deterministic_per_parcel_and_code():
    out = _run([_item(10, 10, plantCode="T-1")])
    (a,) = out["accepted"]
    assert a["id"] == "urn:ngsi-ld:AgriPlant:tenant-a:demo-parcel-001:T-1"
    e = a["entity"]
    assert e["type"] == "AgriPlant" and e["plantCode"]["value"] == "T-1"
    assert e["hasAgriParcel"] == {"type": "Relationship", "object": PARCEL_URN}
    assert e["location"]["value"]["type"] == "Point"
    assert a["action"] == "create" and "@context" not in e


def test_gps_tolerance_10m():
    out = _run([_item(-6, 50, plantCode="near"), _item(-40, 50, plantCode="far")])
    assert [a["plantCode"] for a in out["accepted"]] == ["near"]
    (w,) = [w for w in out["warnings"] if w["code"] == "near_boundary"]
    assert w["plantCode"] == "near" and 5 < w["distance_m"] < 7
    (r,) = out["rejected"]
    assert r["plantCode"] == "far" and r["reason"] == "outside_parcel"


def test_duplicate_code_in_request_rejected():
    out = _run([_item(10, 10, plantCode="A"), _item(20, 10, plantCode="A")])
    assert len(out["accepted"]) == 1
    assert out["rejected"] == [{"index": 1, "plantCode": "A", "reason": "duplicate_code"}]


def test_too_close_warns():
    out = _run([_item(10, 10, plantCode="A"), _item(10.5, 10, plantCode="B")])
    assert len(out["accepted"]) == 2
    (w,) = [w for w in out["warnings"] if w["code"] == "too_close"]
    assert w["plantCode"] == "B" and w["other"] == "A" and w["distance_m"] < 1


def test_existing_code_is_update():
    existing = {"A": {"id": "urn:ngsi-ld:AgriPlant:tenant-a:demo-parcel-001:A"}}
    out = _run([_item(10, 10, plantCode="A")], existing=existing)
    assert out["accepted"][0]["action"] == "update"
    assert any(w["code"] == "exists" and w["plantCode"] == "A" for w in out["warnings"])


def test_missing_codes_follow_row_position_or_next_free():
    existing = {"7": {"id": "x"}, "R2-1": {"id": "y"}}
    out = _run([_item(10, 10), _item(20, 10, rowNumber=3, positionInRow=4), _item(30, 10)], existing=existing)
    codes = [a["plantCode"] for a in out["accepted"]]
    assert codes == ["8", "3-4", "9"]
    e = out["accepted"][1]["entity"]
    assert e["rowNumber"]["value"] == 3 and e["positionInRow"]["value"] == 4


def test_species_from_crop():
    out = _run([_item(10, 10, plantCode="A")], crop=CROP)
    e = out["accepted"][0]["entity"]
    assert e["agroVocConcept"]["value"] == "http://aims.fao.org/aos/agrovoc/c_5386"
    assert e["hasAgriCrop"] == {"type": "Relationship", "object": CROP["id"]}


def test_optional_attributes_and_invalid_values():
    out = _run([_item(10, 10, plantCode="A", variety="Arbequina", datePlanted="2001-03-15",
                      plantStatus="alive", height=3.2),
                _item(20, 10, plantCode="B", plantStatus="sleeping"),
                _item(30, 10, plantCode="C", datePlanted="15/03/2001")])
    (a,) = out["accepted"]
    e = a["entity"]
    assert e["variety"]["value"] == "Arbequina" and e["height"]["value"] == 3.2
    assert e["datePlanted"]["value"] == "2001-03-15"
    assert {(r["plantCode"], r["reason"], r["field"]) for r in out["rejected"]} == {
        ("B", "invalid_value", "plantStatus"), ("C", "invalid_value", "datePlanted")}


def test_invalid_point_rejected():
    out = _run([{"lat": 600000, "lon": 4700000, "plantCode": "utm"}, {"plantCode": "nolat"}])
    assert [r["reason"] for r in out["rejected"]] == ["invalid_point", "invalid_point"]


def test_items_from_gpx_and_csv():
    lat, lon = _off(10, 10)
    gpx = (f'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
           f'<wpt lat="{lat}" lon="{lon}"><name>T-7</name></wpt></gpx>')
    items, rejected = items_from_file("gpx", base64.b64encode(gpx.encode()).decode())
    assert rejected == [] and items[0]["plantCode"] == "T-7" and abs(items[0]["lat"] - lat) < 1e-9
    csv = (f"plantCode,lat,lon,variety,rowNumber,positionInRow\n"
           f"T-1,{lat},{lon},Arbequina,1,2\n"
           f"T-2,4700000,600000,,,\n")
    items, rejected = items_from_file("csv", csv)
    assert items[0] == {"plantCode": "T-1", "lat": lat, "lon": lon, "variety": "Arbequina",
                        "rowNumber": 1, "positionInRow": 2}
    assert rejected and rejected[0]["reason"] == "invalid_point"


# ---- final review fixes ---------------------------------------------------------

def _parcel(geom):
    return {"id": PARCEL_URN, "type": "AgriParcel", "location": {"type": "GeoProperty", "value": geom}}


def test_crop_read_under_legacy_names_and_uri_only():
    crop = {"id": "urn:ngsi-ld:AgriCrop:x", "eppoCode": {"type": "Property", "value": "http://aims.fao.org/aos/agrovoc/c_5386"}}
    e = prepare_plants("tenant-a", PARCEL, [_item(10, 10, plantCode="A")], {}, crop)["accepted"][0]["entity"]
    assert e["agroVocConcept"]["value"].endswith("c_5386")
    crop_eppo = {"id": "urn:ngsi-ld:AgriCrop:x", "agroVocConcept": {"type": "Property", "value": "OLVEU"}}
    e = prepare_plants("tenant-a", PARCEL, [_item(10, 10, plantCode="A")], {}, crop_eppo)["accepted"][0]["entity"]
    assert "agroVocConcept" not in e and e["hasAgriCrop"]["object"] == "urn:ngsi-ld:AgriCrop:x"


def test_multipolygon_and_hole_distances():
    ring = PARCEL["location"]["value"]["coordinates"][0]
    multi = _parcel({"type": "MultiPolygon", "coordinates": [[ring]]})
    out = prepare_plants("tenant-a", multi, [_item(-6, 50, plantCode="n"), _item(-40, 50, plantCode="f")], {}, None)
    assert [a["plantCode"] for a in out["accepted"]] == ["n"]
    hole = [[lon, lat] for lat, lon in (_off(30, 30), _off(70, 30), _off(70, 70), _off(30, 70), _off(30, 30))]
    holed = _parcel({"type": "Polygon", "coordinates": [ring, hole]})
    out = prepare_plants("tenant-a", holed, [_item(50, 50, plantCode="h")], {}, None)
    # distance to the hole's edge (20 m), not to the outer ring
    assert out["rejected"][0]["reason"] == "outside_parcel" and out["rejected"][0]["distance_m"] == 20


def test_parcel_without_area_raises_parcel_error():
    import pytest
    from plant_batch import ParcelGeometryError
    for geom in ({"type": "Point", "coordinates": [-1.6, 42.8]}, None):
        with pytest.raises(ParcelGeometryError):
            prepare_plants("tenant-a", _parcel(geom), [_item(10, 10)], {}, None)


def test_code_prefix_for_auto_codes():
    out = prepare_plants("tenant-a", PARCEL, [_item(10, 10, rowNumber=1, positionInRow=2), _item(20, 10)], {}, None,
                         code_prefix="B")
    assert [a["plantCode"] for a in out["accepted"]] == ["B1-2", "B1"]


def test_update_that_moves_a_plant_warns():
    lat, lon = _off(10, 10)
    existing = {"A": {"id": "x", "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [lon, lat]}}}}
    out = _run([_item(20, 10, plantCode="A")], existing=existing)
    assert any(w["code"] == "moved" and 9 < w["distance_m"] < 11 for w in out["warnings"])


def test_auto_coded_point_on_an_existing_plant_is_rejected():
    lat, lon = _off(10, 10)
    existing = {"5": {"id": "x", "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [lon, lat]}}}}
    out = _run([_item(10.2, 10), _item(30, 10)], existing=existing)
    assert out["rejected"] == [{"index": 0, "reason": "duplicate_location", "other": "5"}]
    assert [a["plantCode"] for a in out["accepted"]] == ["6"]
    coded = _run([_item(10.5, 10, plantCode="Z")], existing=existing)
    assert any(w["code"] == "too_close" and w["other"] == "5" for w in coded["warnings"])


def test_auto_flag_marks_codes_assigned_here():
    out = _run([_item(10, 10), _item(20, 10, plantCode="A")])
    assert [a["auto"] for a in out["accepted"]] == [True, False]


def test_too_many_plants_raises():
    import pytest
    from plant_batch import MAX_PLANTS, TooManyPlants
    with pytest.raises(TooManyPlants):
        _run([_item(10, 10)] * (MAX_PLANTS + 1))


def test_file_names_are_slugified_into_codes():
    lat, lon = _off(10, 10)
    gpx = (f'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
           f'<wpt lat="{lat}" lon="{lon}"><name>Olivo 2 (viejo)</name></wpt>'
           f'<wpt lat="{lat}" lon="{lon}"><name>Árbol ñ</name></wpt></gpx>')
    items, _ = items_from_file("gpx", base64.b64encode(gpx.encode()).decode())
    assert [i["plantCode"] for i in items] == ["Olivo-2-viejo", "Arbol-n"]


def test_spanish_excel_csv():
    lat, lon = _off(10, 10)
    csv = f"plantCode;lat;lon;height\nT-1;{str(lat).replace('.', ',')};{str(lon).replace('.', ',')};3,5\n"
    items, rejected = items_from_file("csv", csv)
    assert rejected == [] and abs(items[0]["lat"] - lat) < 1e-9 and items[0]["height"] == 3.5


def test_csv_requires_plant_code():
    import pytest
    from plant_batch import FileError
    lat, lon = _off(10, 10)
    with pytest.raises(FileError):
        items_from_file("csv", f"lat,lon\n{lat},{lon}\n")


def test_date_planted_is_a_plain_date():
    e = _run([_item(10, 10, plantCode="A", datePlanted="2001-03-15")])["accepted"][0]["entity"]
    assert e["datePlanted"]["value"] == "2001-03-15"
