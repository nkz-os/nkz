"""Pure observation resolution (contract 1.2, 8.3)."""

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker.observations import (
    Deployment,
    PropertyDef,
    quality_flag,
    resolve_observation,
)

DEV = "urn:ngsi-ld:Device:t1:d1"
FARM = "urn:ngsi-ld:AgriParcel:t1:p1"
AIR = PropertyDef("airTemperature", "instant", -40.0, 60.0)
RAIN = PropertyDef("precipitation", "cumulative", 0.0, 100.0)


def dep(feature=None, ctx=None, overrides=None):
    return Deployment(
        id="11111111-1111-1111-1111-111111111111", device_urn=DEV,
        observes_feature=feature,
        observation_context=ctx if ctx is not None else {"medium": "ambientAir", "heightAboveGround": 2},
        channel_overrides=overrides or {}, mobility="fixed", location=(-1.6, 42.8),
    )


def resolve(d, attr="airTemperature", prop=AIR, mdef=None, flag=None, value=20.0):
    return resolve_observation(attr, value, DEV, d, mdef or {"observedProperty": attr}, prop, flag)


def test_feature_from_channel_override_wins():
    d = dep(FARM, overrides={"airTemperature": {"observesFeature": "urn:ngsi-ld:Other:1"}})
    assert resolve(d)["featureOfInterest"] == "urn:ngsi-ld:Other:1"


def test_override_matched_by_canonical_alias():
    d = dep(FARM, overrides={"temperature": {"observesFeature": "urn:ngsi-ld:Other:1"}})
    assert resolve(d)["featureOfInterest"] == "urn:ngsi-ld:Other:1"


def test_feature_from_deployment():
    assert resolve(dep(FARM))["featureOfInterest"] == FARM


def test_equipment_without_feature_uses_device_urn():
    d = dep(None, ctx={"medium": "equipment"})
    assert resolve(d)["featureOfInterest"] == DEV


def test_non_equipment_without_feature_is_none():
    assert resolve(dep(None))["featureOfInterest"] is None


def test_context_override_replaces_whole_context():
    d = dep(FARM, overrides={"airTemperature": {
        "observationContext": {"medium": "soil", "depthFrom": 20, "depthTo": 40}}})
    o = resolve(d)
    assert o["medium"] == "soil"
    assert o["depthFromCm"] == 20.0 and o["depthToCm"] == 40.0
    assert o["heightM"] is None
    assert o["observedProperty"] == "airTemperature"


def test_deployment_context_height():
    o = resolve(dep(FARM))
    assert o["medium"] == "ambientAir" and o["heightM"] == 2.0
    assert o["depthFromCm"] is None and o["organ"] is None and o["contextRef"] is None


def test_structure_and_enclosure_map_to_context_ref():
    s = "urn:ngsi-ld:Structure:t1:s1"
    e = "urn:ngsi-ld:Enclosure:t1:e1"
    d1 = dep(FARM, ctx={"medium": "ambientAirUnderStructure", "structure": s})
    d2 = dep(FARM, ctx={"medium": "enclosedAir", "enclosure": e})
    assert resolve(d1)["contextRef"] == s
    assert resolve(d2)["contextRef"] == e


def test_organ_copied():
    d = dep(FARM, ctx={"medium": "plant", "organ": "leaf"})
    assert resolve(d)["organ"] == "leaf"


def test_resolve_includes_quality_flag_with_counter_max():
    o = resolve(dep(FARM), "precipitation", RAIN, {"observedProperty": "precipitation", "counterMax": 6553.5}, value=7000.0)
    assert o["qualityFlag"] == "out_of_range"
    assert resolve(dep(FARM), value=75.0)["qualityFlag"] == "out_of_range"


def test_quality_out_of_range_instant():
    assert quality_flag(75, AIR, None, None) == "out_of_range"
    assert quality_flag(-41, AIR, None, None) == "out_of_range"


def test_quality_open_bounds():
    p = PropertyDef("x", "instant", None, None)
    assert quality_flag(1e9, p, None, None) == "valid"


def test_quality_cumulative_uses_counter_max_not_valid_range():
    assert quality_flag(900, RAIN, 6553.5, None) == "valid"
    assert quality_flag(7000, RAIN, 6553.5, None) == "out_of_range"
    assert quality_flag(7000, RAIN, None, None) == "valid"


def test_quality_maps_health_flags():
    assert quality_flag(20, AIR, None, "out_of_bounds") == "out_of_range"
    assert quality_flag(20, AIR, None, "nan") == "suspect"
    assert quality_flag(20, AIR, None, "valid") == "valid"
    assert quality_flag(20, AIR, None, None) == "valid"
    assert quality_flag(20, AIR, None, "stale") == "suspect"


def test_quality_nan_value_is_suspect():
    assert quality_flag(float("nan"), AIR, None, None) == "suspect"
