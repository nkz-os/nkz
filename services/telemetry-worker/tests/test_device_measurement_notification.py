"""Task 3: telemetry-worker reads `DeviceMeasurement` notifications correctly.

`DeviceMeasurement` inverts the shape every other entity type uses in
notification_handler.py: the device is not the last segment of the entity's own id (that
is the controlledProperty name), the reading's name is the VALUE of `controlledProperty`
rather than an attribute key, and the value/instant live in `numValue`/`textValue` and
`dateObserved` instead of a per-attribute Property + `observedAt` metadata. Covers:

  - device_id comes from `refDevice`, not `entity_id.split(":")[-1]` (which would yield
    the property name — the exact bug this branch exists to avoid)
  - the measurement is named by `controlledProperty`
  - `observed_at` comes from `dateObserved`, not `utcnow()`
  - `measurementType`, `outlier` and `dateObserved` never appear as measurements
  - `AgriSensor` notifications are unaffected (regression guard — the generic path must
    stay byte-for-byte the same)
  - `SUBSCRIPTIONS` gained `DeviceMeasurement` without losing `AgriSensor`/`AgriDevice`

Async coroutines are driven with `asyncio.run` in sync test functions (mirrors
test_notification_dedup.py — no pytest-asyncio plugin in this suite).
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.subscription_manager as sm
from telemetry_worker import notification_handler as nh
from telemetry_worker.config import Settings
from telemetry_worker.profiles import DEFAULT_PROFILE_CONFIG, ProcessingProfile


class FakeProfileService:
    """Stand-in for ProfileService — no Redis/DB, matches the interface
    `notification_handler._process_entity` actually calls (mode "all": persist
    everything, no filtering — same as the real DEFAULT_PROFILE_CONFIG fallback)."""

    def get_profile(self, device_type, device_id=None, tenant_id=None):
        return ProcessingProfile(
            device_type=device_type,
            device_id=device_id,
            tenant_id=tenant_id,
            config=DEFAULT_PROFILE_CONFIG.copy(),
        )

    def should_persist(self, profile, device_id, measurements):
        return True

    def filter_attributes(self, profile, measurements):
        return dict(measurements)

    def update_last_values(self, device_id, measurements):
        pass


def setup_module(module):
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(),
        event_sink=None,
    )


DEVICE_ID_URN = "urn:ngsi-ld:Device:montiko:sensor-001"
DEVICE_MEASUREMENT_ID = "urn:ngsi-ld:DeviceMeasurement:montiko:sensor-001:soilMoisture"


def _device_measurement_entity(**overrides):
    entity = {
        "id": DEVICE_MEASUREMENT_ID,
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE_ID_URN},
        "controlledProperty": {"type": "Property", "value": "soilMoisture"},
        "measurementType": {"type": "Property", "value": "soilMoisture"},
        "numValue": {"type": "Property", "value": 42.0, "unitCode": "P1"},
        "dateObserved": {"type": "Property", "value": "2026-08-29T10:00:00Z"},
    }
    entity.update(overrides)
    return entity


class TestDeviceMeasurementDeviceId:
    def test_device_id_comes_from_ref_device_not_the_property_name(self):
        event = asyncio.run(nh._process_entity(_device_measurement_entity(), "montiko"))
        assert event is not None
        assert event.device_id == "sensor-001"
        assert event.device_id != "soilMoisture"

    def test_missing_ref_device_is_dropped_not_persisted_with_a_guessed_device(self):
        entity = _device_measurement_entity()
        del entity["refDevice"]
        event = asyncio.run(nh._process_entity(entity, "montiko"))
        assert event is None


class TestDeviceMeasurementName:
    def test_measurement_is_named_by_controlled_property(self):
        event = asyncio.run(nh._process_entity(_device_measurement_entity(), "montiko"))
        assert event is not None
        assert event.payload["measurements"] == {"soilMoisture": 42.0}

    def test_text_value_used_when_no_num_value(self):
        entity = _device_measurement_entity(
            controlledProperty={"type": "Property", "value": "operationalStatus"},
        )
        del entity["numValue"]
        entity["textValue"] = {"type": "Property", "value": "ok"}
        event = asyncio.run(nh._process_entity(entity, "montiko"))
        assert event is not None
        assert event.payload["measurements"] == {"operationalStatus": "ok"}


class TestDeviceMeasurementObservedAt:
    def test_observed_at_comes_from_date_observed_not_utcnow(self):
        event = asyncio.run(nh._process_entity(_device_measurement_entity(), "montiko"))
        assert event is not None
        assert event.observed_at == datetime(2026, 8, 29, 10, 0, 0, tzinfo=timezone.utc)


class TestDeviceMeasurementExcludedKeys:
    def test_measurement_type_outlier_date_observed_never_appear_as_measurements(self):
        entity = _device_measurement_entity(outlier={"type": "Property", "value": False})
        event = asyncio.run(nh._process_entity(entity, "montiko"))
        assert event is not None
        measurements = event.payload["measurements"]
        assert set(measurements.keys()) == {"soilMoisture"}
        for excluded in ("measurementType", "outlier", "dateObserved", "numValue", "refDevice"):
            assert excluded not in measurements


class TestAgriSensorRegression:
    """Every other entity type must behave exactly as it did before this task."""

    def test_agrisensor_device_id_is_still_the_last_urn_segment(self):
        entity = {
            "id": "urn:ngsi-ld:AgriSensor:montiko:sensor-042",
            "type": "AgriSensor",
            "temperature": {
                "type": "Property",
                "value": 21.5,
                "observedAt": "2026-08-29T09:00:00Z",
            },
        }
        event = asyncio.run(nh._process_entity(entity, "montiko"))
        assert event is not None
        assert event.device_id == "sensor-042"
        assert event.payload["measurements"] == {"temperature": 21.5}
        assert event.observed_at == datetime(2026, 8, 29, 9, 0, 0, tzinfo=timezone.utc)

    def test_agrisensor_observed_at_still_falls_back_to_utcnow_when_absent(self):
        entity = {
            "id": "urn:ngsi-ld:AgriSensor:montiko:sensor-043",
            "type": "AgriSensor",
            "temperature": {"type": "Property", "value": 19.0},
        }
        before = datetime.utcnow()
        event = asyncio.run(nh._process_entity(entity, "montiko"))
        after = datetime.utcnow()
        assert event is not None
        assert before <= event.observed_at <= after


class TestSubscriptionListGainedDeviceMeasurement:
    def _types(self):
        types = set()
        for sub in sm.SUBSCRIPTIONS:
            for e in sub.get("entities", []):
                if e.get("type"):
                    types.add(e["type"])
        return types

    def test_device_measurement_is_subscribed(self):
        assert "DeviceMeasurement" in self._types()

    def test_agrisensor_and_agridevice_are_still_subscribed(self):
        types = self._types()
        assert "AgriSensor" in types
        assert "AgriDevice" in types
