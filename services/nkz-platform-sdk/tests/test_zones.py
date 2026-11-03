"""Tests for zone membership helpers (zonings, default zoning, point-in-polygon)."""

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.gis.zones import (
    Zoning,
    ZoneRef,
    default_zoning,
    fetch_zonings,
    group_zonings,
    is_automatic,
    point_in_polygon,
    zone_of,
)
from nkz_platform_sdk.orion import OrionClient

ORION = "http://orion-ld-service:1026"
PARCEL = "urn:ngsi-ld:AgriParcel:demo-parcel-001"
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
WITH_HOLE = {"type": "Polygon", "coordinates": [
    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
]}
NS = "https://nkz-os.org/ns/"


def _zone(zid, run, method=None, rank=None, label=None, date=None, geom=SQUARE, full_iri=False):
    def key(k):
        return NS + k if full_iri else k
    e = {"id": zid, "type": "AgriManagementZone",
         "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
         "location": {"type": "GeoProperty", "value": geom}}
    if run:
        e["zoningRunId"] = {"type": "Property", "value": run}
    if method:
        e[key("zoningMethod")] = {"type": "Property", "value": method}
    if rank is not None:
        e[key("zoneRank")] = {"type": "Property", "value": rank}
    if label:
        e[key("zoneLabel")] = {"type": "Property", "value": label}
    if date:
        e[key("dateZoned")] = {"type": "Property", "value": {"@type": "DateTime", "@value": date}}
    return e


def test_point_inside_square():
    assert point_in_polygon(5, 5, SQUARE)


def test_point_outside():
    assert not point_in_polygon(15, 5, SQUARE)


def test_point_in_hole_is_outside():
    assert not point_in_polygon(5, 5, WITH_HOLE)
    assert point_in_polygon(1, 1, WITH_HOLE)


def test_multipolygon_second_part():
    multi = {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]],
    ]}
    assert point_in_polygon(25, 25, multi)


def test_border_point_lowest_rank_wins():
    left = {"type": "Polygon", "coordinates": [[[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]]]}
    right = {"type": "Polygon", "coordinates": [[[5, 0], [10, 0], [10, 10], [5, 10], [5, 0]]]}
    zoning = Zoning(run_id="r", method="manual", date=None, zones=(
        ZoneRef(id="b", label="B", rank=2, geometry=right),
        ZoneRef(id="a", label="A", rank=1, geometry=left),
    ))
    assert zone_of(5, 5, zoning).id == "a"


def test_group_zonings_by_run_and_legacy():
    zonings = group_zonings([
        _zone("z1", "run-1", "manual", 1, date="2026-03-01T10:00:00Z"),
        _zone("z2", "run-1", "manual", 2, date="2026-03-01T10:00:00Z"),
        _zone("z3", None, None, 1),
    ])
    by_run = {z.run_id: z for z in zonings}
    assert len(by_run["run-1"].zones) == 2
    assert "legacy:none" in by_run


def test_full_iri_attribute_names_are_read():
    (zoning,) = group_zonings([_zone("z1", "run-1", "manual", 3, "T1", full_iri=True)])
    assert zoning.method == "manual"
    assert zoning.zones[0].rank == 3 and zoning.zones[0].label == "T1"


def test_manual_zoning_is_never_default():
    zonings = group_zonings([
        _zone("m1", "manual-1", "manual", 1, date="2026-06-01T00:00:00Z"),
        _zone("v1", "veg-1", "vegetationIndex", 1, date="2026-01-01T00:00:00Z"),
    ])
    assert default_zoning(zonings).run_id == "veg-1"
    assert not is_automatic("manual") and is_automatic(None) and is_automatic("terrain")


def test_default_is_latest_automatic():
    zonings = group_zonings([
        _zone("t1", "ter-1", "terrain", 1, date="2026-01-01T00:00:00Z"),
        _zone("v1", "veg-1", "vegetationIndex", 1, date="2026-05-01T00:00:00Z"),
    ])
    assert default_zoning(zonings).run_id == "veg-1"
    assert default_zoning([]) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_zonings_pages_until_short_page():
    first = [_zone(f"z{i}", "run-1", "manual", i + 1) for i in range(100)]
    second = [_zone(f"y{i}", "run-1", "manual", 101 + i) for i in range(3)]
    route = respx.get(f"{ORION}/ngsi-ld/v1/entities").mock(
        side_effect=[Response(200, json=first), Response(200, json=second)]
    )
    client = OrionClient("acme", base_url=ORION)
    zonings = await fetch_zonings(client, PARCEL)
    await client.close()
    assert route.call_count == 2
    assert sum(len(z.zones) for z in zonings) == 103
    params = route.calls[0].request.url.params
    assert params["type"] == "AgriManagementZone" and params["q"] == f'hasAgriParcel=="{PARCEL}"'
