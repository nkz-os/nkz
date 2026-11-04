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
    assert e["datePlanted"]["value"] == {"@type": "Date", "@value": "2001-03-15"}
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
