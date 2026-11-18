"""Mobile sync delivers platform machines with their geometry and plan passes for guidance."""

import json
import importlib.util
import os
import sys
from unittest.mock import patch

_test_dir = os.path.dirname(os.path.abspath(__file__))
_em_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_em_dir, ".."))
_common_dir = os.path.join(_services_dir, "common")
for _p in (_em_dir, _services_dir, _common_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "blueprints.sync", os.path.join(_em_dir, "blueprints", "sync.py")
)
sync = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sync)




def _p(v):
    return {"type": "Property", "value": v}


def test_equipment_types_include_platform_machines():
    assert "ManufacturingMachine" in sync.EQUIPMENT_SYNC_TYPES.split(",")
    assert "AgriEquipment" in sync.EQUIPMENT_SYNC_TYPES.split(",")


def test_machine_role_comes_from_category_including_aliases():
    for category, role in (("tractor", "tractor"), ("Apero", "implement"), ("implement", "implement"),
                           ("vehicle", "tractor")):
        ent = {"id": "urn:m", "type": "ManufacturingMachine", "name": _p("M"), "category": _p(category)}
        assert sync._equipment_record_for_watermelon(ent, 1)["equipment_type"] == role, category


def test_geometry_json_keeps_zero_and_drops_absent_values():
    ent = {"id": "urn:m", "type": "ManufacturingMachine", "name": _p("Drill"), "category": _p("implement"),
           "implementOffsetX": _p(-2.0), "implementOffsetY": _p(0), "mountPosition": _p("rear"),
           "hitchOffsetX": _p(""), "transportWidth": _p(3.0)}
    geom = json.loads(sync._equipment_record_for_watermelon(ent, 1)["geometry_json"])
    assert geom == {"implementOffsetX": -2.0, "implementOffsetY": 0, "mountPosition": "rear"}


def test_plan_operation_carries_its_passes_and_parcel():
    swaths = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
         "properties": {"index": 0, "kind": "work"}}]}
    ent = {"id": "urn:op", "type": "AgriParcelOperation", "status": _p("planned"),
           "hasAgriParcel": {"type": "Relationship", "object": "urn:p1"}, "swaths": _p(swaths)}
    rec = sync._operation_record_for_watermelon(ent, 1)
    assert rec["parcel_id"] == "urn:p1"
    assert json.loads(rec["swaths_geojson"]) == swaths


def test_operation_without_passes_has_empty_swaths():
    rec = sync._operation_record_for_watermelon({"id": "urn:op", "type": "AgriParcelOperation"}, 1)
    assert rec["swaths_geojson"] == ""


def test_cache_key_depends_on_the_app_schema_version():
    assert sync._sync_cache_key("t", "equipment", 0, 5) != sync._sync_cache_key("t", "equipment", 0, 6)


def test_plan_operation_names_its_tractor_and_implement():
    ent = {"id": "urn:op", "type": "AgriParcelOperation",
           "usesTractor": {"type": "Relationship", "object": "urn:t1"},
           "usesImplement": {"type": "Relationship", "object": "urn:i1"}}
    rec = sync._operation_record_for_watermelon(ent, 1)
    assert (rec["tractor_id"], rec["implement_id"]) == ("urn:t1", "urn:i1")
