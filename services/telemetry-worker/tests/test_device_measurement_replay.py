"""DeviceMeasurement replay guard in the notification path (design A.3).

A one-off Orion write to a DM re-notifies its current state. The worker skips a DM
notification whose (entity id, dateObserved, value) equals the last one it inserted;
the record is written only after a successful insert, never expires, and the guard
fails open when Redis is unavailable.
"""

import asyncio
import logging
import os
import sys

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
from test_notification_dedup import BrokenRedis, FakeRedis

TENANT = "acmefarm"
DEVICE_URN = "urn:ngsi-ld:Device:acmefarm:sensor-001"
DM_ID = "urn:ngsi-ld:DeviceMeasurement:acmefarm:sensor-001:airTemperature"
REPLAY = "device_measurement_replay_skipped"


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
        "deviceType": {"type": "Property", "value": "prof-x"},
    }


class FakeSink:
    """Records inserted events; `dead_letter` makes every insert fail (dead-lettered)."""

    def __init__(self, dead_letter=False):
        self.dead_letter = dead_letter
        self.inserted = []

    async def write(self, event):
        if self.dead_letter:
            return False
        self.inserted.append(event)
        return True

    async def write_batch(self, events):
        if self.dead_letter:
            return []
        self.inserted.extend(events)
        return list(events)


class SpyHealthChecker:
    def __init__(self):
        self.calls = 0

    async def get_health_config(self, entity_id, tenant_id):
        self.calls += 1
        return None


def _dedup(redis=None):
    d = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    d._redis = FakeRedis() if redis is None else redis
    return d


def _expire_short_dedup(dedup):
    """Simulate the 5-minute dedup TTL elapsing, so only the replay guard remains."""
    for key in [k for k in dedup._redis.store if k.startswith(KEY_PREFIX)]:
        del dedup._redis.store[key]


def _notify(sink, dedup, *notifications, health=None, between=None):
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(),
        event_sink=sink,
        dedup=dedup,
        health_checker=health,
    )

    async def go():
        for entities in notifications:
            await nh.process_notification_task({"data": entities}, TENANT)
            if between:
                between()

    try:
        asyncio.run(go())
    finally:
        nh.init_handler(
            settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
            profile_service=FakeProfileService(), event_sink=None,
        )


def _values(sink):
    return [(e.observed_at.isoformat(), e.payload["measurements"]) for e in sink.inserted]


def test_identical_replay_is_skipped_and_logged_once(caplog):
    sink, dedup = FakeSink(), _dedup()
    with caplog.at_level(logging.DEBUG):
        _notify(sink, dedup, [_dm()], [_dm()], [_dm()], between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 1
    lines = [r for r in caplog.records if REPLAY in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    msg = lines[0].getMessage()
    for needle in (f"tenant_id={TENANT}", f"entity_id={DM_ID}", "device_id=sensor-001",
                   "date_observed=2026-08-29T10:00:00+00:00", "value=21.5"):
        assert needle in msg, needle


def test_replay_is_skipped_before_calibration_and_health():
    """Health writes back to Orion on degraded readings; a replay must not re-trigger it."""
    sink, dedup, health = FakeSink(), _dedup(), SpyHealthChecker()
    _notify(sink, dedup, [_dm()], [_dm()], health=health,
            between=lambda: _expire_short_dedup(dedup))
    assert health.calls == 1


def test_late_but_different_reading_is_stored():
    sink, dedup = FakeSink(), _dedup()
    _notify(sink, dedup,
            [_dm(value=21.5, at="2026-08-29T10:00:00Z")],
            [_dm(value=19.0, at="2026-08-29T09:00:00Z")],   # late, older instant
            [_dm(value=21.5, at="2026-08-29T10:10:00Z")],   # same value, new instant
            [_dm(value=22.0, at="2026-08-29T10:10:00Z")],   # same instant, corrected value
            between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 4


def test_replay_guard_is_per_entity():
    sink, dedup = FakeSink(), _dedup()
    other = DM_ID + "Min"
    _notify(sink, dedup, [_dm()], [_dm(entity_id=other, channel="airTemperatureMin")],
            between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 2


def test_record_is_written_only_after_a_successful_insert():
    dedup = _dedup()
    _notify(FakeSink(dead_letter=True), dedup, [_dm()])
    assert not [k for k in dedup._redis.store if k.startswith("telemetry:dm_last:")]
    sink = FakeSink()
    _expire_short_dedup(dedup)
    _notify(sink, dedup, [_dm()])
    assert len(sink.inserted) == 1  # the dead-lettered reading was not recorded


def test_batch_records_every_inserted_dm():
    sink, dedup = FakeSink(), _dedup()
    a, b = _dm(), _dm(entity_id=DM_ID + "Max", channel="airTemperatureMax", value=30.0)
    _notify(sink, dedup, [a, b], [a, b], between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 2


def test_redis_down_fails_open_and_logs(caplog):
    sink = FakeSink()
    with caplog.at_level(logging.WARNING):
        _notify(sink, _dedup(BrokenRedis()), [_dm()], [_dm()], [_dm()])
    assert len(sink.inserted) == 3   # fail-open: every notification processed
    lines = [r for r in caplog.records if "replay_guard_unavailable" in r.getMessage()]
    assert len(lines) == 1


def test_redis_never_connected_fails_open():
    sink = FakeSink()
    d = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    _notify(sink, d, [_dm()], [_dm()])
    assert len(sink.inserted) == 2


def test_dm_without_date_observed_bypasses_the_guard():
    """observed_at then falls back to now(); a record keyed on it would be meaningless."""
    sink, dedup = FakeSink(), _dedup()
    e = _dm()
    del e["dateObserved"]
    _notify(sink, dedup, [e], [dict(e)], between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 2
    assert not [k for k in dedup._redis.store if k.startswith("telemetry:dm_last:")]


def test_other_entity_types_are_not_guarded():
    sink, dedup = FakeSink(), _dedup()
    e = {"id": "urn:ngsi-ld:AgriSensor:acmefarm:s1", "type": "AgriSensor",
         "temperature": {"type": "Property", "value": 20.0, "observedAt": "2026-08-29T10:00:00Z"}}
    _notify(sink, dedup, [e], [dict(e)], between=lambda: _expire_short_dedup(dedup))
    assert len(sink.inserted) == 2
    assert not [k for k in dedup._redis.store if k.startswith("telemetry:dm_last:")]
