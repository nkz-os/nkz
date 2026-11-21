"""process_entities: infrastructure failures are reported, and a failed attempt is retry-safe.

The inbox consumer retries an entity when process_entities raises
TransientProcessingError. That only works if the failed attempt left no state that
makes the retry skip the reading: the dedup key and the delta-filter last values are
written only after the sink returned (inserted or dead-lettered), never before.
"""

import asyncio
import os
import sys

import asyncpg
import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker import notification_handler as nh
from telemetry_worker.config import Settings
from telemetry_worker.dedup import KEY_PREFIX, NotificationDedup
from test_device_measurement_notification import FakeProfileService
from test_notification_dedup import FakeRedis

TENANT = "acme"
DEVICE_URN = "urn:ngsi-ld:Device:acme:sensor-001"
DM_ID = "urn:ngsi-ld:DeviceMeasurement:acme:sensor-001:airTemperature"


def _dm(value=21.5, at="2026-08-29T10:00:00Z", entity_id=DM_ID, channel="airTemperature"):
    return {
        "id": entity_id,
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE_URN},
        "name": {"type": "Property", "value": channel},
        "controlledProperty": {"type": "Property", "value": "airTemperature"},
        "measurementType": {"type": "Property", "value": "instant"},
        "numValue": {"type": "Property", "value": value},
        "dateObserved": {"type": "Property", "value": at},
    }


def _sensor(device="s1", value=20.0, at="2026-08-29T10:00:00Z"):
    return {
        "id": f"urn:ngsi-ld:AgriSensor:acme:{device}",
        "type": "AgriSensor",
        "temperature": {"type": "Property", "value": value, "observedAt": at},
    }


class RecordingProfileService(FakeProfileService):
    """Mode "all", but records every delta-filter last-values update."""

    def __init__(self, raise_for=None):
        self.updates = []
        self.raise_for = raise_for or {}

    def get_profile(self, device_type, device_id=None, tenant_id=None):
        if device_id in self.raise_for:
            raise self.raise_for[device_id]
        return super().get_profile(device_type, device_id, tenant_id)

    def update_last_values(self, device_id, measurements):
        self.updates.append((device_id, dict(measurements)))


class Sink:
    """`fail` raises on the next write; `dead_letter` devices are dead-lettered (final)."""

    def __init__(self, fail=None, dead_letter=()):
        self.fail = fail
        self.dead_letter = set(dead_letter)
        self.inserted = []
        self.dlq = []

    def _raise(self):
        if self.fail is not None:
            err, self.fail = self.fail, None
            raise err

    async def write(self, event):
        self._raise()
        if event.device_id in self.dead_letter:
            self.dlq.append(event)
            return False
        self.inserted.append(event)
        return True

    async def write_batch(self, events):
        self._raise()
        stored = []
        for event in events:
            if event.device_id in self.dead_letter:
                self.dlq.append(event)
            else:
                stored.append(event)
        self.inserted.extend(stored)
        return stored


def _dedup():
    d = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    d._redis = FakeRedis()
    return d


def _wire(sink, dedup=None, profiles=None):
    profiles = profiles or RecordingProfileService()
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=profiles,
        event_sink=sink,
        dedup=dedup,
    )
    return profiles


def teardown_function(_fn):
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(), event_sink=None,
    )


def _run(entities):
    asyncio.run(nh.process_entities(entities, TENANT))


def _dedup_keys(dedup):
    return [k for k in dedup._redis.store if k.startswith(KEY_PREFIX)]


@pytest.mark.parametrize("err", [
    asyncpg.PostgresConnectionError("connection failure"),
    asyncpg.exceptions.ConnectionDoesNotExistError("closed in the middle of operation"),
    asyncpg.exceptions.InterfaceError("connection is closed"),
    ConnectionRefusedError("refused"),
    asyncio.TimeoutError(),
], ids=lambda e: type(e).__name__)
def test_batch_connection_error_is_transient(err):
    _wire(Sink(fail=err))
    with pytest.raises(nh.TransientProcessingError) as exc:
        _run([_sensor("s1"), _sensor("s2")])
    assert exc.value.__cause__ is err


def test_single_write_connection_error_is_transient():
    _wire(Sink(fail=ConnectionResetError("reset")))
    with pytest.raises(nh.TransientProcessingError):
        _run([_sensor("s1")])


def test_dead_lettered_record_is_final_and_the_rest_written():
    sink = Sink(dead_letter={"bad"})
    dedup = _dedup()
    profiles = _wire(sink, dedup)
    _run([_sensor("s1"), _sensor("bad"), _sensor("s2")])  # no exception
    assert [e.device_id for e in sink.inserted] == ["s1", "s2"]
    assert [e.device_id for e in sink.dlq] == ["bad"]
    # dead-lettered is final: marked like the inserted ones, so a redelivery is skipped
    assert len(_dedup_keys(dedup)) == 3
    assert [d for d, _ in profiles.updates] == ["s1", "bad", "s2"]


def test_entity_raising_non_infrastructure_error_is_skipped():
    sink = Sink()
    _wire(sink, profiles=RecordingProfileService(raise_for={"boom": ValueError("bad profile")}))
    _run([_sensor("s1"), _sensor("boom"), _sensor("s2")])
    assert [e.device_id for e in sink.inserted] == ["s1", "s2"]


def test_entity_raising_connection_error_is_transient():
    """Skipping here would lose the reading for good; the inbox retries instead."""
    sink = Sink()
    _wire(sink, profiles=RecordingProfileService(raise_for={"s2": ConnectionResetError("reset")}))
    with pytest.raises(nh.TransientProcessingError):
        _run([_sensor("s1"), _sensor("s2")])
    assert sink.inserted == []


def test_review_focus_3_same_dm_processed_twice_writes_one_row():
    """Consumer killed after processing, before the delete: the row is processed again."""
    sink, dedup = Sink(), _dedup()
    _wire(sink, dedup)
    _run([_dm()])
    _run([_dm()])
    assert len(sink.inserted) == 1


def test_identical_entity_twice_in_one_call_writes_one_row():
    sink = Sink()
    _wire(sink, _dedup())
    _run([_sensor("s1"), _sensor("s1")])
    assert len(sink.inserted) == 1


@pytest.mark.parametrize("entity", [_sensor("s1"), _dm()], ids=["AgriSensor", "DeviceMeasurement"])
def test_failed_attempt_leaves_no_state_and_retry_writes(entity):
    sink, dedup = Sink(fail=asyncpg.PostgresConnectionError("connection failure")), _dedup()
    profiles = _wire(sink, dedup)

    with pytest.raises(nh.TransientProcessingError):
        _run([dict(entity)])
    assert _dedup_keys(dedup) == []
    assert profiles.updates == []
    assert not [k for k in dedup._redis.store if k.startswith("telemetry:dm_last:")]

    _run([dict(entity)])  # retry within the dedup TTL
    assert len(sink.inserted) == 1
    assert len(_dedup_keys(dedup)) == 1
    assert len(profiles.updates) == 1


def test_after_success_the_same_reading_is_skipped_as_duplicate():
    sink, dedup = Sink(), _dedup()
    profiles = _wire(sink, dedup)
    _run([_sensor("s1")])
    _run([_sensor("s1")])
    assert len(sink.inserted) == 1
    assert len(profiles.updates) == 1


def test_last_values_updated_with_the_unfiltered_measurements():
    class Filtering(RecordingProfileService):
        def filter_attributes(self, profile, measurements):
            return {k: v for k, v in measurements.items() if k != "humidity"}

    sink = Sink()
    profiles = _wire(sink, profiles=Filtering())
    e = _sensor("s1")
    e["humidity"] = {"type": "Property", "value": 60.0}
    _run([e])
    assert sink.inserted[0].payload["measurements"] == {"temperature": 20.0}
    assert profiles.updates == [("s1", {"temperature": 20.0, "humidity": 60.0})]


def test_wrapper_still_swallows_failures():
    """process_notification_task is kept for existing callers and never raises."""
    sink = Sink(fail=asyncpg.PostgresConnectionError("connection failure"))
    _wire(sink, _dedup())
    asyncio.run(nh.process_notification_task({"data": [_sensor("s1")]}, TENANT))
    asyncio.run(nh.process_notification_task({"data": [_sensor("s1")]}, TENANT))
    assert len(sink.inserted) == 1


# ── Not ready: a normal return would let the inbox delete the row ───────────


def test_sink_not_wired_is_transient():
    _wire(None, _dedup())
    with pytest.raises(nh.TransientProcessingError):
        _run([_sensor("s1")])


def test_handler_not_initialised_is_transient(monkeypatch):
    _wire(Sink())
    monkeypatch.setattr(nh, "_profile_service", None)
    with pytest.raises(nh.TransientProcessingError):
        _run([_sensor("s1")])
    monkeypatch.setattr(nh, "_settings", None)
    with pytest.raises(nh.TransientProcessingError):
        _run([_sensor("s1")])


def test_prepare_raises_when_not_initialised(monkeypatch):
    """The per-entity guard raises too, so it is never logged and skipped."""
    _wire(Sink())
    monkeypatch.setattr(nh, "_settings", None)
    with pytest.raises(nh.TransientProcessingError):
        asyncio.run(nh._prepare_entity(_sensor("s1"), TENANT))


def test_wrapper_never_raises_when_not_ready(monkeypatch):
    _wire(None)
    asyncio.run(nh.process_notification_task({"data": [_sensor("s1")]}, TENANT))
    _wire(Sink())
    monkeypatch.setattr(nh, "_settings", None)
    asyncio.run(nh.process_notification_task({"data": [_sensor("s1")]}, TENANT))


@pytest.mark.parametrize("err", [
    asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    asyncpg.exceptions.TooManyConnectionsError("too many clients already"),
    asyncpg.exceptions.AdminShutdownError("terminating connection due to administrator command"),
    asyncpg.exceptions.CrashShutdownError("terminating connection due to crash of another process"),
], ids=lambda e: type(e).__name__)
def test_postgres_startup_and_shutdown_errors_are_transient(err):
    _wire(Sink(fail=err))
    with pytest.raises(nh.TransientProcessingError) as exc:
        _run([_sensor("s1")])
    assert exc.value.__cause__ is err
