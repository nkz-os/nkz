"""The DeviceMeasurement subscription carries no throttling; the others keep theirs.

Orion applies throttling per subscription, and this one covers every device in a
tenant, so any window would drop readings from the other devices.
"""

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.subscription_manager as sm


def _sub(entity_type):
    return next(s for s in sm.SUBSCRIPTIONS if s["entities"][0]["type"] == entity_type)


def test_device_measurement_body_has_no_throttling():
    assert "throttling" not in sm._subscription_body(_sub("DeviceMeasurement"), "tenant-a")


def test_other_subscriptions_keep_their_throttling():
    assert _sub("AgriDevice")["throttling"] == 30
    assert _sub("AgriParcel")["throttling"] == 30
    assert _sub("VegetationIndex")["throttling"] == 5


def test_zone_subscription_shape_and_no_throttling():
    zones = [s for s in sm.SUBSCRIPTIONS if s["entities"] == [{"type": "AgriParcelZone"}]]
    assert len(zones) == 1
    z = zones[0]
    assert z["watchedAttributes"] == ["tMin", "tMax", "eto"]
    assert z["notification"]["attributes"] == [
        "tMin", "tMax", "eto", "solarRadiationWm2", "frostRisk",
        "zoneId", "zoneVersion", "zoneType",
    ]
    assert z.get("throttling") is None
    assert "throttling" not in sm._subscription_body(z, "tenant-a")
