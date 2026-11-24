"""DeviceMeasurement health lookups and reliabilityStatus PATCH target the Device (refDevice)."""

import asyncio
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from _lookup_fakes import CacheRedis, FakeClient, FakeResponse, health_property
from telemetry_worker import notification_handler as nh
from telemetry_worker.config import Settings
from telemetry_worker.health_checker import HealthChecker
from test_device_measurement_notification import FakeProfileService
from test_device_measurement_replay import FakeSink

TENANT = "tenant-a"
DEVICE = "urn:ngsi-ld:Device:tenant-a:dev-1"
CFG = {"airTemperature": {"minValid": 0, "maxValid": 40}}


def _dm(value=99.0, channel="airTemperature", sdm=True, ref=None, ref_key="refDevice"):
    e = {
        "id": f"urn:ngsi-ld:DeviceMeasurement:tenant-a:dev-1:{channel}",
        "type": "DeviceMeasurement",
        "numValue": {"type": "Property", "value": value},
        "dateObserved": {"type": "Property", "value": "2026-08-29T10:00:00Z"},
        "deviceType": {"type": "Property", "value": "prof-x"},
    }
    if ref is not False:
        e[ref_key] = {"type": "Relationship", "object": DEVICE if ref is None else ref}
    if sdm:
        e["name"] = {"type": "Property", "value": channel}
        e["controlledProperty"] = {"type": "Property", "value": "airTemperature"}
        e["measurementType"] = {"type": "Property", "value": "instant"}
    else:
        e["controlledProperty"] = {"type": "Property", "value": channel}
    return e


def _run(entities_list, client):
    hc = HealthChecker(orion_url="http://orion:1026", redis_url="redis://x/0", context_url="http://localhost/c.json")
    hc._redis, hc._client = CacheRedis(), client
    sink = FakeSink()
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(), event_sink=sink, health_checker=hc,
    )

    async def go():
        for entities in entities_list:
            await nh.process_notification_task({"data": entities}, TENANT)

    try:
        asyncio.run(go())
    finally:
        nh.init_handler(
            settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
            profile_service=FakeProfileService(), event_sink=None,
        )
    return sink


def test_dm_looks_up_the_device_and_never_patches():
    # A reliabilityStatus write on the Device would re-trigger the entity-manager
    # subscription and rebuild every channel of it, so a DM reading only gets its flag.
    client = FakeClient([FakeResponse(200, health_property(CFG))])
    sink = _run([[_dm()]], client)
    assert [g[0] for g in client.gets] == [f"http://orion:1026/ngsi-ld/v1/entities/{DEVICE}?attrs=healthConfig"]
    assert client.patches == []
    assert [e.quality_flag for e in sink.inserted] == ["out_of_bounds"]


def test_dm_nan_reading_never_patches():
    client = FakeClient([FakeResponse(200, health_property(CFG))])
    _run([[_dm(value=None)]], client)
    assert client.patches == []


def test_dm_in_range_has_no_patch_and_valid_flag():
    client = FakeClient([FakeResponse(200, health_property(CFG))])
    sink = _run([[_dm(value=20.0)]], client)
    assert client.patches == []
    assert [e.quality_flag for e in sink.inserted] == ["valid"]


def test_dm_legacy_shape_matches_variable_by_channel():
    client = FakeClient([FakeResponse(200, health_property(CFG))])
    sink = _run([[_dm(sdm=False)]], client)
    assert [e.quality_flag for e in sink.inserted] == ["out_of_bounds"]


def test_dm_sdm_shape_matches_variable_by_channel_not_catalogue_name():
    # channel `airTemperatureMin` (name) differs from controlledProperty (catalogue name):
    # the wizard key is the channel
    cfg = {"airTemperatureMin": {"minValid": 0, "maxValid": 40}}
    client = FakeClient([FakeResponse(200, health_property(cfg))])
    e = _dm(channel="airTemperatureMin")
    e["measurementType"]["value"] = "min"
    sink = _run([[e]], client)
    assert [x.quality_flag for x in sink.inserted] == ["out_of_bounds"]


def test_dm_ref_device_under_alternate_key():
    client = FakeClient([FakeResponse(200, health_property(CFG))])
    _run([[_dm(ref_key="hasDevice")]], client)
    assert client.gets[0][0].startswith(f"http://orion:1026/ngsi-ld/v1/entities/{DEVICE}?")


def test_dm_with_non_urn_ref_device_makes_no_request_and_is_stored():
    client = FakeClient()
    sink = _run([[_dm(ref="dev-1")]], client)
    assert client.gets == [] and client.patches == []
    assert len(sink.inserted) == 1 and sink.inserted[0].quality_flag == "valid"


def test_dm_without_ref_device_is_skipped_before_any_health_request():
    client = FakeClient()
    sink = _run([[_dm(ref=False)]], client)
    assert sink.inserted == []
    assert client.gets == [] and client.patches == []


def test_one_lookup_per_device_across_channels():
    client = FakeClient([FakeResponse(404)])
    sink = _run([[_dm(value=1.0, channel="airTemperature"), _dm(value=2.0, channel="airHumidity")]], client)
    assert len(client.gets) == 1
    assert len(sink.inserted) == 2


def test_debug_line_only_when_health_checker_is_on(caplog):
    import logging
    with caplog.at_level(logging.DEBUG, logger=nh.logger.name):
        _run([[_dm(ref="dev-1")]], FakeClient())
        assert any("No usable refDevice" in r.getMessage() for r in caplog.records)
        caplog.clear()
        nh.init_handler(
            settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
            profile_service=FakeProfileService(), event_sink=FakeSink(),
        )
        try:
            asyncio.run(nh.process_notification_task({"data": [_dm(ref="dev-1")]}, TENANT))
        finally:
            nh.init_handler(
                settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
                profile_service=FakeProfileService(), event_sink=None,
            )
        assert not any("No usable refDevice" in r.getMessage() for r in caplog.records)


def test_ref_device_is_quoted_in_the_url():
    client = FakeClient([FakeResponse(404)])
    _run([[_dm(ref="urn:ngsi-ld:Device:tenant-a:a/b?c d")]], client)
    assert client.gets[0][0] == (
        "http://orion:1026/ngsi-ld/v1/entities/urn:ngsi-ld:Device:tenant-a:a%2Fb%3Fc%20d?attrs=healthConfig"
    )


# --- variable matching (DeviceMeasurement only) ---

def test_alias_config_key_matches_canonical_channel():
    cfg = {"temperature": {"minValid": 0, "maxValid": 40}}
    sink = _run([[_dm()]], FakeClient([FakeResponse(200, health_property(cfg))]))
    assert [e.quality_flag for e in sink.inserted] == ["out_of_bounds"]


def test_exact_key_wins_over_alias_key():
    cfg = {"airTemperature": {"minValid": 0, "maxValid": 500}, "temperature": {"minValid": 0, "maxValid": 40}}
    sink = _run([[_dm()]], FakeClient([FakeResponse(200, health_property(cfg))]))
    assert [e.quality_flag for e in sink.inserted] == ["valid"]


def test_alias_not_applied_to_non_dm_entities():
    # non-DM variables are attribute keys as stored; `airTemperature` != config key `temperature`
    entity = {"id": "urn:ngsi-ld:AgriSensor:tenant-a:s1", "type": "AgriSensor",
              "airTemperature": {"type": "Property", "value": 99}}
    cfg = {"temperature": {"maxValid": 40}}
    sink = _run([[entity]], FakeClient([FakeResponse(200, health_property(cfg))]))
    assert [e.quality_flag for e in sink.inserted] == ["valid"]


# --- fail-safe on malformed configuration ---

import pytest


@pytest.mark.parametrize("cfg", [
    ["not", "a", "dict"],
    {"airTemperature": "oops"},
    {"airTemperature": ["x"]},
    {"airTemperature": {"maxValid": "40"}},
    {"airTemperature": {"minValid": True}},
    {"airTemperature": {"maxValid": float("nan")}},
])
def test_malformed_config_is_valid_and_row_is_stored(cfg, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        sink = _run([[_dm()]], FakeClient([FakeResponse(200, health_property(cfg))]))
    assert [e.quality_flag for e in sink.inserted] == ["valid"]
    assert any("health" in r.getMessage().lower() and r.levelno == logging.WARNING for r in caplog.records)


def test_malformed_config_warning_is_throttled_per_device(caplog):
    import logging
    cfg = {"airTemperature": "oops"}
    entities = [[_dm(value=float(i))] for i in range(5)]
    # dedup is off in this harness; distinct values avoid any replay skip
    with caplog.at_level(logging.WARNING):
        _run(entities, FakeClient([FakeResponse(200, health_property(cfg))]))
    assert len([r for r in caplog.records if "health" in r.getMessage().lower()]) == 1


def test_non_dm_entity_is_unchanged_and_targets_its_own_id():
    sensor_id = "urn:ngsi-ld:AgriSensor:tenant-a:s1"
    client = FakeClient([FakeResponse(200, health_property({"temperature": {"maxValid": 40}}))])
    entity = {"id": sensor_id, "type": "AgriSensor", "temperature": {"type": "Property", "value": 99}}
    sink = _run([[entity]], client)
    assert client.gets[0][0].startswith(f"http://orion:1026/ngsi-ld/v1/entities/{sensor_id}?")
    assert client.patches[0][0].endswith(f"/entities/{sensor_id}/attrs")
    assert [e.quality_flag for e in sink.inserted] == ["out_of_bounds"]
