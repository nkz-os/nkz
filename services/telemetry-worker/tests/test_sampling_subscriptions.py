"""Plant and soil samples reach TimescaleDB through the generic telemetry path.

- both sample types are subscribed, normalized, to the worker's /notify;
- no throttling: Orion applies it per subscription, and a lab import writes
  hundreds of samples at once, so any window would drop most of them;
- the stored instant is the sampling date carried in `observedAt`, not the
  import time (a report imported in October for a July sample plots in July).
"""

import os
import sys
from datetime import datetime, timezone

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.subscription_manager as sm
from telemetry_worker import notification_handler as nh

SAMPLE_TYPES = ("AgriPlantSample", "AgriSoilSample")


def _entry(entity_type):
    return next(s for s in sm.SUBSCRIPTIONS if s["entities"] == [{"type": entity_type}])


def test_sample_types_are_subscribed():
    for t in SAMPLE_TYPES:
        e = _entry(t)
        assert e["notification"]["format"] == "normalized"
        assert e["notification"]["endpoint"]["uri"] == sm.NOTIFICATION_URL
        assert e["isActive"] is True


def test_sample_subscriptions_have_no_throttling():
    for t in SAMPLE_TYPES:
        assert "throttling" not in _entry(t)


def test_sample_observed_at_comes_from_sampling_date():
    entity = {
        "id": "urn:ngsi-ld:AgriPlantSample:acme:abc",
        "type": "AgriPlantSample",
        "hasAgriPlant": {"type": "Relationship", "object": "urn:ngsi-ld:AgriPlant:acme:p:T-001"},
        "nitrogen": {"type": "Property", "value": 0.016, "observedAt": "2026-07-15T12:00:00Z"},
    }
    assert nh._extract_observed_at(entity) == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
