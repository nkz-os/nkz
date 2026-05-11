---
title: "DataHub Timeseries Pipeline — Implementation Plan"
description: "Step-by-step TDD plan for fixing 4 interconnected failures in the DataHub timeseries pipeline"
---

# DataHub Timeseries Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 4 interconnected failures preventing the DataHub module from displaying IoT sensor data and simulated weather station data.

**Architecture:** Self-describing NGSI-LD entities. Each entity carries the metadata needed to locate its own timeseries data — no fragile resolution chains. The timeseries-reader reads the entity once and knows where to query. Additionally: fix attribute name mapping (NGSI-LD names vs DB columns), clean the telemetry notification handler to store only real measurements, and fix subscription management (port + multi-tenant).

**Tech Stack:** Python (Flask for timeseries-reader, FastAPI for telemetry-worker), psycopg2, asyncpg, requests, NGSI-LD/Orion-LD, TimescaleDB, pytest

**Spec:** `docs/superpowers/specs/2026-04-08-datahub-timeseries-pipeline-design.md`

---

## File Structure

| Service | File | Action | Responsibility |
|---------|------|--------|----------------|
| weather-worker | `weather_worker/storage/orion_writer.py` | Modify | Add `municipality_code` param, add `municipalityCode` Property to entity |
| weather-worker | `weather_worker/storage/orion_writer.py` | Modify | Pass `municipality_code` through `sync_weather_to_orion()` |
| timeseries-reader | `urn_resolution.py` | Modify | Direct `municipalityCode` resolution for WeatherObserved |
| timeseries-reader | `app.py` | Modify | Add `_WEATHER_ATTRIBUTE_MAP`, `_resolve_weather_attribute()`, add `wind_direction_deg` |
| telemetry-worker | `telemetry_worker/notification_handler.py` | Modify | Filter metadata keys, skip Relationship/GeoProperty, only scalar values |
| telemetry-worker | `telemetry_worker/subscription_manager.py` | Modify | Fix port default, add multi-tenant, cleanup broken subs |
| telemetry-worker | `app.py` | Modify | Add periodic subscription check task |
| datahub | `backend/app/api/entities.py` | Modify | Add `municipalityCode` to `_NGSI_SYSTEM_KEYS` |
| k8s | `k8s/core/services/telemetry-worker-deployment.yaml` | Create | New deployment with correct port + image |
| tests | `services/tests/test_weather_municipality.py` | Create | Tests for Component 1 |
| tests | `services/tests/test_weather_attribute_map.py` | Create | Tests for Component 2 |
| tests | `services/tests/test_telemetry_measurements.py` | Create | Tests for Component 3 |
| tests | `services/tests/test_subscription_manager.py` | Create | Tests for Component 4 |

---

## Task 1: WeatherObserved — Direct Municipality Resolution (weather-worker)

**Files:**
- Modify: `nkz/services/weather-worker/weather_worker/storage/orion_writer.py:164-308` (`create_weather_observed_entity`)
- Modify: `nkz/services/weather-worker/weather_worker/storage/orion_writer.py:444-552` (`sync_weather_to_orion`)
- Test: `nkz/services/tests/test_weather_municipality.py`

- [ ] **Step 1: Write the failing test for `create_weather_observed_entity` with `municipality_code`**

Create `nkz/services/tests/test_weather_municipality.py`:

```python
"""Tests for municipality_code on WeatherObserved entities."""
import json
from unittest.mock import patch, MagicMock

import pytest


def test_create_weather_observed_includes_municipality_code():
    """When municipality_code is provided, entity must include municipalityCode Property."""
    from weather_worker.storage.orion_writer import create_weather_observed_entity

    mock_response = MagicMock()
    mock_response.status_code = 201

    with patch("weather_worker.storage.orion_writer.requests.post", return_value=mock_response) as mock_post:
        result = create_weather_observed_entity(
            parcel_id="urn:ngsi-ld:AgriParcel:test:p1",
            tenant_id="test",
            location=(1.0, 42.0),
            weather_data={"temp_avg": 22.5},
            municipality_code="31012",
        )

    assert result is not None
    # Inspect the entity JSON sent to Orion
    call_args = mock_post.call_args
    entity = call_args.kwargs.get("json") or call_args[1].get("json")
    assert "municipalityCode" in entity
    assert entity["municipalityCode"]["type"] == "Property"
    assert entity["municipalityCode"]["value"] == "31012"


def test_create_weather_observed_without_municipality_code():
    """When municipality_code is None, entity must NOT include municipalityCode."""
    from weather_worker.storage.orion_writer import create_weather_observed_entity

    mock_response = MagicMock()
    mock_response.status_code = 201

    with patch("weather_worker.storage.orion_writer.requests.post", return_value=mock_response) as mock_post:
        result = create_weather_observed_entity(
            parcel_id="urn:ngsi-ld:AgriParcel:test:p1",
            tenant_id="test",
            location=(1.0, 42.0),
            weather_data={"temp_avg": 22.5},
            municipality_code=None,
        )

    assert result is not None
    call_args = mock_post.call_args
    entity = call_args.kwargs.get("json") or call_args[1].get("json")
    assert "municipalityCode" not in entity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py -v`

Expected: FAIL — `create_weather_observed_entity()` does not accept `municipality_code` parameter.

- [ ] **Step 3: Implement `municipality_code` parameter in `create_weather_observed_entity`**

Edit `nkz/services/weather-worker/weather_worker/storage/orion_writer.py`. Change the function signature at line 164:

```python
def create_weather_observed_entity(
    parcel_id: str,
    tenant_id: str,
    location: Tuple[float, float],
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    municipality_code: Optional[str] = None,
) -> Optional[str]:
```

After the `"refParcel"` line (line 210), add the `municipalityCode` Property:

```python
        "refParcel": {"type": "Relationship", "object": parcel_id},
    }

    # Self-describing: carry municipality code for direct timeseries resolution
    if municipality_code:
        entity["municipalityCode"] = {
            "type": "Property",
            "value": municipality_code,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py -v`

Expected: PASS (both tests)

- [ ] **Step 5: Write the failing test for `sync_weather_to_orion` passing `municipality_code`**

Append to `nkz/services/tests/test_weather_municipality.py`:

```python
def test_sync_weather_to_orion_passes_municipality_code():
    """sync_weather_to_orion must forward municipality_code to create_weather_observed_entity."""
    from weather_worker.storage.orion_writer import sync_weather_to_orion

    fake_parcel = {
        "id": "urn:ngsi-ld:AgriParcel:test:p1",
        "type": "AgriParcel",
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [1.0, 42.0]},
        },
    }

    with patch("weather_worker.storage.orion_writer.get_parcels_by_location", return_value=[fake_parcel]), \
         patch("weather_worker.storage.orion_writer.create_weather_observed_entity", return_value="urn:ok") as mock_create:

        count = sync_weather_to_orion(
            tenant_id="test",
            latitude=42.0,
            longitude=1.0,
            weather_data={"temp_avg": 20.0},
            municipality_code="31012",
        )

    assert count == 1
    # Verify municipality_code was forwarded
    call_kwargs = mock_create.call_args.kwargs if mock_create.call_args.kwargs else {}
    # Could be positional — check both
    if "municipality_code" in call_kwargs:
        assert call_kwargs["municipality_code"] == "31012"
    else:
        # positional args: parcel_id, tenant_id, location, weather_data, observed_at, municipality_code
        assert "31012" in mock_create.call_args.args or any("31012" == str(v) for v in call_kwargs.values())
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py::test_sync_weather_to_orion_passes_municipality_code -v`

Expected: FAIL — `sync_weather_to_orion()` does not accept `municipality_code` parameter.

- [ ] **Step 7: Implement `municipality_code` in `sync_weather_to_orion`**

Edit `nkz/services/weather-worker/weather_worker/storage/orion_writer.py`. Change the function signature at line 444:

```python
def sync_weather_to_orion(
    tenant_id: str,
    latitude: float,
    longitude: float,
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    radius_km: float = 10.0,
    municipality_code: Optional[str] = None,
) -> int:
```

Change the `create_weather_observed_entity` call at line 534:

```python
        entity_id = create_weather_observed_entity(
            parcel_id=parcel_id,
            tenant_id=tenant_id,
            location=parcel_location,
            weather_data=weather_data,
            observed_at=observed_at,
            municipality_code=municipality_code,
        )
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py -v`

Expected: PASS (all 3 tests)

- [ ] **Step 9: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/weather-worker/weather_worker/storage/orion_writer.py services/tests/test_weather_municipality.py
git commit -m "feat(weather-worker): add municipalityCode to WeatherObserved entities

Self-describing NGSI-LD entities carry municipality_code for O(1) timeseries resolution.
Eliminates fragile 4-hop chain (refParcel → parcel → address → weather_observations)."
```

---

## Task 2: WeatherObserved — Pass `municipality_code` from `ingest_weather_data`

**Files:**
- Modify: `nkz/services/weather-worker/main.py:259` (call to `sync_weather_to_orion` inside `run_ingestion_cycle`)
- No separate test needed — the existing `ingest_weather_data` does NOT call `sync_weather_to_orion`; the Orion sync is a separate concern. The `municipality_code` is already available in `ingest_weather_data` but that method writes to TimescaleDB. The Orion sync caller is wherever `sync_weather_to_orion` is invoked.

Looking at the code: `main.py` calls `self.ingest_weather_data(tenant_id, municipality_code, latitude, longitude)` which writes to TimescaleDB. The Orion sync (`sync_weather_to_orion`) must be called separately. Let me check where it's called.

- [ ] **Step 1: Find where `sync_weather_to_orion` is called**

Run: `cd /home/g/Documents/nekazari/nkz && grep -rn "sync_weather_to_orion" services/weather-worker/`

Look for the call site. If it's only called from `ingest_weather_data` or a separate sync step, we need to ensure `municipality_code` flows through.

- [ ] **Step 2: Add Orion sync call with `municipality_code` in `ingest_weather_data`**

If `sync_weather_to_orion` is not already called from `ingest_weather_data`, it needs to be. Based on the spec, the weather-worker `ingest_weather_data()` already has `municipality_code` — it just needs to pass it. Check the actual call site and add the `municipality_code=municipality_code` kwarg.

If the Orion sync is called from a different location (e.g., a scheduler or separate sync script), update that call site instead.

In `main.py`, `ingest_weather_data()` already receives `municipality_code` at line 91. If it calls `sync_weather_to_orion` anywhere, add `municipality_code=municipality_code`.

If it doesn't call `sync_weather_to_orion` (the DB write and Orion sync are separate), find the actual caller and update it.

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/weather-worker/
git commit -m "feat(weather-worker): pass municipality_code through to Orion sync"
```

---

## Task 3: Timeseries Reader — Direct `municipalityCode` Resolution

**Files:**
- Modify: `nkz/services/timeseries-reader/urn_resolution.py:175-221` (`_resolve_urn_to_weather_key`)
- Test: `nkz/services/tests/test_weather_municipality.py` (extend)

- [ ] **Step 1: Write the failing test for direct `municipalityCode` resolution**

Append to `nkz/services/tests/test_weather_municipality.py`:

```python
def test_resolve_urn_weather_key_direct_municipality_code():
    """WeatherObserved with municipalityCode should resolve directly, no chain."""
    from timeseries_reader.urn_resolution import _resolve_urn_to_weather_key

    entity = {
        "id": "urn:ngsi-ld:WeatherObserved:test:parcel-p1",
        "type": "WeatherObserved",
        "municipalityCode": {"type": "Property", "value": "31012"},
        "refParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:test:p1"},
    }

    with patch("timeseries_reader.urn_resolution.fetch_orion_entity", return_value=entity):
        result, source = _resolve_urn_to_weather_key(
            tenant_id="test",
            entity_id="urn:ngsi-ld:WeatherObserved:test:parcel-p1",
            entity=entity,
        )

    assert result == "31012"
    assert source == "municipality"


def test_resolve_urn_weather_key_falls_back_without_municipality_code():
    """WeatherObserved without municipalityCode should fallback to refParcel chain."""
    from timeseries_reader.urn_resolution import _resolve_urn_to_weather_key

    entity = {
        "id": "urn:ngsi-ld:WeatherObserved:test:parcel-p1",
        "type": "WeatherObserved",
        "refParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:test:p1"},
    }

    # Legacy chain will fail (no parcel entity), returning None
    with patch("timeseries_reader.urn_resolution.fetch_orion_entity") as mock_fetch:
        mock_fetch.return_value = entity  # first call returns the WeatherObserved itself
        # Second call (for parcel) returns None — chain fails
        mock_fetch.side_effect = [entity, None]
        result, source = _resolve_urn_to_weather_key(
            tenant_id="test",
            entity_id="urn:ngsi-ld:WeatherObserved:test:parcel-p1",
            entity=entity,
        )

    # Without municipalityCode AND without resolvable parcel → no_location
    assert result is None
    assert source == "no_location"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py::test_resolve_urn_weather_key_direct_municipality_code -v`

Expected: FAIL — `_resolve_urn_to_weather_key` does not check `municipalityCode`.

- [ ] **Step 3: Implement direct `municipalityCode` resolution**

Edit `nkz/services/timeseries-reader/urn_resolution.py`. In `_resolve_urn_to_weather_key` (line 203), add the direct resolution BEFORE the `refParcel` chain:

Replace lines 203-215:

```python
    if etype_short == "WeatherObserved" or etype.endswith("WeatherObserved"):
        # Direct resolution: entity carries its own municipality code
        muni_prop = entity.get("municipalityCode")
        if muni_prop:
            muni_val = muni_prop.get("value") if isinstance(muni_prop, dict) else muni_prop
            if isinstance(muni_val, str) and muni_val.strip():
                return (muni_val.strip(), "municipality")

        # Fallback: legacy chain via refParcel -> parcel -> address
        ref_parcel = entity.get("refParcel")
        if not ref_parcel:
            return None, "no_location"
        parcel_urn = ref_parcel.get("object") if isinstance(ref_parcel, dict) else ref_parcel
        if not parcel_urn:
            return None, "no_location"
        parcel_urn = str(parcel_urn).strip()
        parcel_entity = fetch_orion_entity(tenant_id, parcel_urn)
        if not parcel_entity:
            return None, "no_location"
        res = _parcel_urn_to_municipality_code(tenant_id, parcel_urn, parcel_entity)
        return (None, "no_location") if res is None else res
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_municipality.py -v -k "resolve_urn"`

Expected: PASS (both resolution tests)

- [ ] **Step 5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/timeseries-reader/urn_resolution.py services/tests/test_weather_municipality.py
git commit -m "feat(timeseries-reader): direct municipalityCode resolution for WeatherObserved

O(1) resolution from entity property. Falls back to legacy refParcel chain
for entities not yet migrated."
```

---

## Task 4: Weather Attribute Name Mapping (timeseries-reader)

**Files:**
- Modify: `nkz/services/timeseries-reader/app.py:66-72` (add `wind_direction_deg` to `VALID_ATTRIBUTES`)
- Modify: `nkz/services/timeseries-reader/app.py:66+` (add `_WEATHER_ATTRIBUTE_MAP` and `_resolve_weather_attribute`)
- Modify: `nkz/services/timeseries-reader/app.py:1173-1176` (use resolver instead of direct check)
- Modify: `nkz/services/timeseries-reader/app.py:1031` (`_weather_query_columnar` — use resolved names)
- Test: `nkz/services/tests/test_weather_attribute_map.py`

- [ ] **Step 1: Write failing tests for attribute mapping**

Create `nkz/services/tests/test_weather_attribute_map.py`:

```python
"""Tests for NGSI-LD to DB column weather attribute mapping."""
import pytest


def test_resolve_ngsi_ld_name_to_db_column():
    """NGSI-LD attribute name should resolve to DB column name."""
    from timeseries_reader.app import _resolve_weather_attribute

    assert _resolve_weather_attribute("temperature") == "temp_avg"
    assert _resolve_weather_attribute("relativeHumidity") == "humidity_avg"
    assert _resolve_weather_attribute("windSpeed") == "wind_speed_ms"
    assert _resolve_weather_attribute("windDirection") == "wind_direction_deg"
    assert _resolve_weather_attribute("atmosphericPressure") == "pressure_hpa"
    assert _resolve_weather_attribute("precipitation") == "precip_mm"
    assert _resolve_weather_attribute("et0") == "eto_mm"
    assert _resolve_weather_attribute("solarRadiation") == "solar_rad_w_m2"
    assert _resolve_weather_attribute("soilMoisture") == "soil_moisture_0_10cm"


def test_resolve_db_column_passthrough():
    """DB column names should also resolve (backwards compatibility)."""
    from timeseries_reader.app import _resolve_weather_attribute

    assert _resolve_weather_attribute("temp_avg") == "temp_avg"
    assert _resolve_weather_attribute("humidity_avg") == "humidity_avg"
    assert _resolve_weather_attribute("wind_direction_deg") == "wind_direction_deg"


def test_resolve_unknown_attribute_returns_none():
    """Unknown attribute names should return None."""
    from timeseries_reader.app import _resolve_weather_attribute

    assert _resolve_weather_attribute("nonExistent") is None
    assert _resolve_weather_attribute("") is None
    assert _resolve_weather_attribute(None) is None


def test_resolve_strips_whitespace():
    """Attribute names with whitespace should be stripped."""
    from timeseries_reader.app import _resolve_weather_attribute

    assert _resolve_weather_attribute("  temperature  ") == "temp_avg"


def test_wind_direction_in_valid_attributes():
    """wind_direction_deg must be in VALID_ATTRIBUTES."""
    from timeseries_reader.app import VALID_ATTRIBUTES

    assert "wind_direction_deg" in VALID_ATTRIBUTES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_attribute_map.py -v`

Expected: FAIL — `_resolve_weather_attribute` does not exist, `wind_direction_deg` not in `VALID_ATTRIBUTES`.

- [ ] **Step 3: Implement the attribute mapping**

Edit `nkz/services/timeseries-reader/app.py`.

First, add `wind_direction_deg` to `VALID_ATTRIBUTES` (after line 71):

```python
VALID_ATTRIBUTES = frozenset({
    'temp_avg', 'temp_min', 'temp_max',
    'humidity_avg', 'precip_mm',
    'solar_rad_w_m2', 'eto_mm',
    'soil_moisture_0_10cm', 'wind_speed_ms',
    'pressure_hpa', 'wind_direction_deg',
})
```

Then, after `VALID_ATTRIBUTES` (after line 72), add the mapping dict and resolver:

```python
# NGSI-LD (Smart Data Models) attribute name -> weather_observations DB column
_WEATHER_ATTRIBUTE_MAP: Dict[str, str] = {
    "temperature":          "temp_avg",
    "relativeHumidity":     "humidity_avg",
    "windSpeed":            "wind_speed_ms",
    "windDirection":        "wind_direction_deg",
    "atmosphericPressure":  "pressure_hpa",
    "precipitation":        "precip_mm",
    "et0":                  "eto_mm",
    "solarRadiation":       "solar_rad_w_m2",
    "soilMoisture":         "soil_moisture_0_10cm",
}
# DB column names also accepted (passthrough for scripts, direct API)
for _col in VALID_ATTRIBUTES:
    _WEATHER_ATTRIBUTE_MAP.setdefault(_col, _col)


def _resolve_weather_attribute(requested: str) -> Optional[str]:
    """Map NGSI-LD attribute name or DB column name to weather_observations column."""
    r = (requested or "").strip()
    return _WEATHER_ATTRIBUTE_MAP.get(r)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_weather_attribute_map.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Update the v2 endpoint to use the resolver**

Edit `nkz/services/timeseries-reader/app.py`. Replace lines 1173-1176:

```python
    # Before:
    if attrs_list:
        for a in attrs_list:
            if a not in VALID_ATTRIBUTES:
                return jsonify({"error": f'Invalid attribute: {a}'}), 400

    # After:
    if attrs_list:
        resolved_attrs = []
        for a in attrs_list:
            resolved = _resolve_weather_attribute(a)
            if resolved is None:
                return jsonify({"error": f"Unknown weather attribute: {a}"}), 400
            resolved_attrs.append(resolved)
        attrs_list = resolved_attrs
```

- [ ] **Step 6: Update `_weather_query_columnar` to accept resolved names**

Edit `nkz/services/timeseries-reader/app.py`. In `_weather_query_columnar` (line 1031), the `want` line filters against `VALID_ATTRIBUTES`. Since attrs are already resolved to DB column names by the caller, this still works. But we need to also handle the case where `attrs_requested` is `None` (no filter — return all columns). The current line `want = [a for a in (attrs_requested or []) if a in VALID_ATTRIBUTES]` works correctly because resolved names ARE in `VALID_ATTRIBUTES`. No change needed here.

- [ ] **Step 7: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/timeseries-reader/app.py services/tests/test_weather_attribute_map.py
git commit -m "feat(timeseries-reader): map NGSI-LD attribute names to DB columns

DataHub sends 'temperature' -> reader resolves to 'temp_avg' -> SQL works.
DB column names also accepted (backwards compatible). Adds wind_direction_deg."
```

---

## Task 5: Telemetry Notification Handler — Clean Measurements

**Files:**
- Modify: `nkz/services/telemetry-worker/telemetry_worker/notification_handler.py:149-179` (`_extract_measurements`)
- Test: `nkz/services/tests/test_telemetry_measurements.py`

- [ ] **Step 1: Write failing tests for measurement extraction**

Create `nkz/services/tests/test_telemetry_measurements.py`:

```python
"""Tests for telemetry notification handler measurement extraction."""
import pytest


def test_extract_measurements_only_scalar_properties():
    """Only Property type with scalar values should be extracted."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "@context": ["https://example.com/context.jsonld"],
        "temperature": {"type": "Property", "value": 23.5},
        "humidity": {"type": "Property", "value": 65},
        "batteryLevel": {"type": "Property", "value": 88.2},
    }

    measurements = _extract_measurements(entity)
    assert measurements == {
        "temperature": 23.5,
        "humidity": 65,
        "batteryLevel": 88.2,
    }


def test_extract_measurements_skips_relationships():
    """Relationships (refDeviceProfile, refDevice, etc.) must be excluded."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "temperature": {"type": "Property", "value": 23.5},
        "refDeviceProfile": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:DeviceProfile:test:soil-sensor",
        },
        "refDevice": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:Device:test:device1",
        },
    }

    measurements = _extract_measurements(entity)
    assert "refDeviceProfile" not in measurements
    assert "refDevice" not in measurements
    assert measurements == {"temperature": 23.5}


def test_extract_measurements_skips_geoproperties():
    """GeoProperties (location) must be excluded."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "temperature": {"type": "Property", "value": 23.5},
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [1.0, 42.0]},
        },
    }

    measurements = _extract_measurements(entity)
    assert "location" not in measurements
    assert measurements == {"temperature": 23.5}


def test_extract_measurements_skips_metadata_keys():
    """Known metadata keys (name, description, controlledProperty, etc.) must be excluded."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "name": {"type": "Property", "value": "Soil Sensor 1"},
        "description": {"type": "Property", "value": "Installed in parcel P1"},
        "controlledProperty": {"type": "Property", "value": ["temperature", "humidity"]},
        "category": {"type": "Property", "value": "sensor"},
        "temperature": {"type": "Property", "value": 23.5},
    }

    measurements = _extract_measurements(entity)
    assert "name" not in measurements
    assert "description" not in measurements
    assert "controlledProperty" not in measurements
    assert "category" not in measurements
    assert measurements == {"temperature": 23.5}


def test_extract_measurements_skips_non_scalar_values():
    """Properties with dict or list values (nested metadata) must be excluded."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "temperature": {"type": "Property", "value": 23.5},
        "address": {"type": "Property", "value": {"streetAddress": "Calle 1", "addressLocality": "Pamplona"}},
        "controlledProperty": {"type": "Property", "value": ["temperature"]},
    }

    measurements = _extract_measurements(entity)
    assert "address" not in measurements
    assert "controlledProperty" not in measurements
    assert measurements == {"temperature": 23.5}


def test_extract_measurements_empty_entity():
    """Entity with only system keys should return empty dict."""
    from telemetry_worker.notification_handler import _extract_measurements

    entity = {
        "id": "urn:ngsi-ld:AgriSensor:test:device1",
        "type": "AgriSensor",
        "name": {"type": "Property", "value": "Empty Sensor"},
        "refDeviceProfile": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:DeviceProfile:test:basic",
        },
    }

    measurements = _extract_measurements(entity)
    assert measurements == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_telemetry_measurements.py -v`

Expected: FAIL — current `_extract_measurements` includes Relationships, GeoProperties, and metadata keys.

- [ ] **Step 3: Implement the cleaned measurement extraction**

Edit `nkz/services/telemetry-worker/telemetry_worker/notification_handler.py`. Replace lines 149-179:

```python
_ENTITY_METADATA_KEYS = frozenset({
    "id", "type", "@context", "location",
    # NGSI-LD system / metadata
    "name", "description", "dateCreated", "dateModified", "observedAt",
    "controlledProperty", "category", "source", "provider",
    "seeAlso", "ownedBy", "address",
    # Relationships (not measurements)
    "refDeviceProfile", "refDevice", "refAgriParcel", "refParcel",
    "refWeatherStation",
})


def _extract_measurements(entity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract measurement values from NGSI-LD entity attributes.

    Only extracts Property-type attributes with scalar values.
    Skips Relationships, GeoProperties, metadata keys, and non-scalar values.
    """
    measurements = {}

    for key, attr in entity.items():
        if key in _ENTITY_METADATA_KEYS:
            continue

        if isinstance(attr, dict):
            attr_type = attr.get("type")
            if attr_type == "Property":
                val = attr.get("value")
                if val is not None and not isinstance(val, (dict, list)):
                    measurements[key] = val
            # GeoProperty and Relationship: skip (not measurements)

    return measurements
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_telemetry_measurements.py -v`

Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/telemetry-worker/telemetry_worker/notification_handler.py services/tests/test_telemetry_measurements.py
git commit -m "fix(telemetry-worker): only extract scalar Property measurements

Skip Relationships (refDeviceProfile, refDevice), GeoProperties (location),
metadata keys (name, description), and non-scalar values (dicts, lists).
Fixes telemetry_events storing metadata instead of sensor readings."
```

---

## Task 6: Subscription Manager — Fix Port Default

**Files:**
- Modify: `nkz/services/telemetry-worker/telemetry_worker/subscription_manager.py:11`
- Test: `nkz/services/tests/test_subscription_manager.py`

- [ ] **Step 1: Write failing test for port default**

Create `nkz/services/tests/test_subscription_manager.py`:

```python
"""Tests for telemetry-worker subscription manager."""
import os
from unittest.mock import patch, MagicMock

import pytest


def test_default_notification_url_uses_port_80():
    """Default notification URL must use port 80 (K8s Service port), not 8080."""
    # Unset SERVICE_PORT to test the default
    env = {k: v for k, v in os.environ.items() if k != "SERVICE_PORT"}
    with patch.dict(os.environ, env, clear=True):
        # Force reimport to pick up new default
        import importlib
        import telemetry_worker.subscription_manager as sm
        importlib.reload(sm)
        assert ":80/" in sm.NOTIFICATION_URL
        assert ":8080" not in sm.NOTIFICATION_URL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_subscription_manager.py::test_default_notification_url_uses_port_80 -v`

Expected: FAIL — default is "8080".

- [ ] **Step 3: Fix port default**

Edit `nkz/services/telemetry-worker/telemetry_worker/subscription_manager.py` line 11:

```python
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_subscription_manager.py::test_default_notification_url_uses_port_80 -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/telemetry-worker/telemetry_worker/subscription_manager.py services/tests/test_subscription_manager.py
git commit -m "fix(telemetry-worker): default SERVICE_PORT to 80 (K8s Service port)

K8s Service exposes port 80 -> targetPort 8080. Subscriptions were using
8080 directly, causing notification delivery failures."
```

---

## Task 7: Subscription Manager — Multi-Tenant + Cleanup + Periodic Check

**Files:**
- Modify: `nkz/services/telemetry-worker/telemetry_worker/subscription_manager.py`
- Modify: `nkz/services/telemetry-worker/app.py`
- Test: `nkz/services/tests/test_subscription_manager.py` (extend)

- [ ] **Step 1: Write failing tests for multi-tenant and cleanup**

Append to `nkz/services/tests/test_subscription_manager.py`:

```python
def test_cleanup_broken_subscriptions_deletes_port_8080():
    """Subscriptions with :8080 in URI should be deleted on startup."""
    import importlib
    import telemetry_worker.subscription_manager as sm
    importlib.reload(sm)

    existing_subs = [
        {
            "id": "urn:ngsi-ld:Subscription:broken1",
            "notification": {
                "endpoint": {"uri": "http://telemetry-worker-service:8080/notify"}
            },
        },
        {
            "id": "urn:ngsi-ld:Subscription:good1",
            "notification": {
                "endpoint": {"uri": "http://telemetry-worker-service:80/notify"}
            },
        },
    ]

    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.json.return_value = existing_subs

    mock_delete = MagicMock()
    mock_delete.status_code = 204

    with patch("telemetry_worker.subscription_manager.requests.get", return_value=mock_get), \
         patch("telemetry_worker.subscription_manager.requests.delete", return_value=mock_delete) as mock_del:
        sm._cleanup_broken_subscriptions("test-tenant")

    # Should only delete the broken subscription
    mock_del.assert_called_once()
    call_url = mock_del.call_args[0][0]
    assert "broken1" in call_url


def test_get_active_tenants_queries_postgres():
    """_get_active_tenants should query PostgreSQL for distinct tenant_ids."""
    import importlib
    import telemetry_worker.subscription_manager as sm
    importlib.reload(sm)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [("tenant-a",), ("tenant-b",)]

    with patch("telemetry_worker.subscription_manager.psycopg2.connect", return_value=mock_conn):
        tenants = sm._get_active_tenants()

    assert tenants == ["tenant-a", "tenant-b"]
    mock_cursor.execute.assert_called_once()


def test_ensure_subscriptions_for_all_tenants():
    """Should create subscriptions for every active tenant."""
    import importlib
    import telemetry_worker.subscription_manager as sm
    importlib.reload(sm)

    with patch.object(sm, "_get_active_tenants", return_value=["tenant-a", "tenant-b"]), \
         patch.object(sm, "_cleanup_broken_subscriptions") as mock_cleanup, \
         patch.object(sm, "_ensure_tenant_subscriptions") as mock_ensure:
        sm.ensure_subscriptions_for_all_tenants()

    assert mock_cleanup.call_count == 2
    assert mock_ensure.call_count == 2
    mock_ensure.assert_any_call("tenant-a")
    mock_ensure.assert_any_call("tenant-b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_subscription_manager.py -v`

Expected: FAIL — `_cleanup_broken_subscriptions`, `_get_active_tenants`, `ensure_subscriptions_for_all_tenants` don't exist.

- [ ] **Step 3: Implement multi-tenant subscription manager**

Edit `nkz/services/telemetry-worker/telemetry_worker/subscription_manager.py`. Replace the full file:

```python
import os
import logging
import requests
import psycopg2

from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv("SERVICE_HOST", "telemetry-worker-service")
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
CONTEXT_URL = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")
POSTGRES_URL = os.getenv("POSTGRES_URL", "")
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "platform")

# NGSI-LD subscriptions — no watchedAttributes = trigger on ANY attribute change
SUBSCRIPTIONS = [
    {
        "description": "Telemetry Worker - AgriSensor updates",
        "type": "Subscription",
        "entities": [{"type": "AgriSensor"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
    {
        "description": "Telemetry Worker - Device updates",
        "type": "Subscription",
        "entities": [{"type": "Device"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
    {
        "description": "Telemetry Worker - AgriParcel updates",
        "type": "Subscription",
        "entities": [{"type": "AgriParcel"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
    {
        "description": "Telemetry Worker - VegetationIndex analysis results",
        "type": "Subscription",
        "entities": [{"type": "VegetationIndex"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 5,
        "isActive": True
    },
]


def _get_headers(tenant: str) -> dict:
    """Standard NGSI-LD headers with tenant and @context Link."""
    return {
        "Content-Type": "application/json",
        "NGSILD-Tenant": tenant,
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
    }


def _get_active_tenants() -> list:
    """Query PostgreSQL for all active tenant IDs."""
    if not POSTGRES_URL:
        logger.warning("POSTGRES_URL not set, cannot query tenants")
        return []
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL")
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error querying active tenants: {e}")
        return []


def _cleanup_broken_subscriptions(tenant_id: str):
    """Delete subscriptions with wrong port (legacy bug)."""
    headers = _get_headers(tenant_id)
    try:
        r = requests.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions", headers=headers)
        if r.status_code != 200:
            return
        for sub in r.json():
            uri = sub.get("notification", {}).get("endpoint", {}).get("uri", "")
            if ":8080" in uri and "telemetry-worker" in uri:
                sub_id = sub.get("id")
                requests.delete(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
                    headers=headers,
                )
                logger.info(f"Deleted broken subscription {sub_id} (port 8080)")
    except Exception as e:
        logger.warning(f"Error cleaning broken subscriptions for {tenant_id}: {e}")


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _get_headers(tenant_id)
    try:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
        )
        response.raise_for_status()
        existing_subs = response.json()
        existing_descriptions = [
            sub.get("description") for sub in existing_subs
        ] if existing_subs else []

        for sub in SUBSCRIPTIONS:
            if sub["description"] in existing_descriptions:
                logger.debug(f"Subscription '{sub['description']}' exists for tenant {tenant_id}")
            else:
                logger.info(f"Creating subscription '{sub['description']}' for tenant {tenant_id}")
                res = requests.post(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                    json=sub,
                    headers=headers,
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Created: {sub['description']} for {tenant_id}")
                else:
                    logger.error(
                        f"Failed: {sub['description']} for {tenant_id}: "
                        f"{res.status_code} {res.text}"
                    )
    except Exception as e:
        logger.error(f"Error managing subscriptions for {tenant_id}: {e}")


@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = _get_active_tenants()
    if not tenants:
        tenants = [DEFAULT_TENANT]
        logger.info(f"No tenants from DB, using default: {DEFAULT_TENANT}")

    logger.info(f"Ensuring subscriptions for {len(tenants)} tenants: {tenants}")

    for tenant_id in tenants:
        _cleanup_broken_subscriptions(tenant_id)
        _ensure_tenant_subscriptions(tenant_id)


# Keep backwards compat alias for app.py import
check_or_create_subscription = ensure_subscriptions_for_all_tenants
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_subscription_manager.py -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Add periodic subscription check to `app.py`**

Edit `nkz/services/telemetry-worker/app.py`. Update the import (line 15):

```python
from telemetry_worker.subscription_manager import ensure_subscriptions_for_all_tenants
```

Replace the lifespan function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Telemetry Worker starting up...")

    settings = Settings()

    # Initialize async connection pool (shared between sink and profiles)
    sink = PostgreSQLSink(
        dsn=settings.postgres_url,
        min_pool=5,
        max_pool=20,
    )
    await sink.start()

    # ProfileService gets the same pool for async DB queries
    profile_service = ProfileService(settings, pool=sink._pool)

    # Wire dependencies into notification handler
    init_handler(settings, profile_service, sink)

    # Check/create NGSI-LD subscriptions for all tenants (sync, run in executor)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, ensure_subscriptions_for_all_tenants)
    except Exception as e:
        logger.warning(f"Auto-subscription failed (non-fatal): {e}")

    # Periodic subscription self-healing (every 60 minutes)
    async def _periodic_subscription_check():
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, ensure_subscriptions_for_all_tenants
                )
                logger.info("Periodic subscription check completed")
            except Exception as e:
                logger.warning(f"Periodic subscription check failed: {e}")

    periodic_task = asyncio.create_task(_periodic_subscription_check())

    yield

    # Shutdown: cancel periodic task and close pool
    periodic_task.cancel()
    await sink.stop()
    logger.info("Telemetry Worker shut down.")
```

- [ ] **Step 6: Run all subscription tests**

Run: `cd /home/g/Documents/nekazari/nkz && python -m pytest services/tests/test_subscription_manager.py -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add services/telemetry-worker/telemetry_worker/subscription_manager.py services/telemetry-worker/app.py services/tests/test_subscription_manager.py
git commit -m "feat(telemetry-worker): multi-tenant subscriptions + cleanup + periodic check

- Query PostgreSQL for active tenants (fallback to DEFAULT_TENANT)
- Delete broken subscriptions with port 8080 on startup
- Periodic self-healing check every 60 minutes
- Backwards compatible: check_or_create_subscription alias preserved"
```

---

## Task 8: DataHub — Add `municipalityCode` to System Keys

**Files:**
- Modify: `nkz-module-datahub/backend/app/api/entities.py:41-45`

- [ ] **Step 1: Add `municipalityCode` to `_NGSI_SYSTEM_KEYS`**

Edit `nkz-module-datahub/backend/app/api/entities.py` line 41-45. Add `"municipalityCode"` to the frozenset:

```python
_NGSI_SYSTEM_KEYS = frozenset({
    "id", "type", "@context", "location", "name", "description",
    "address", "source", "provider", "dateCreated", "dateModified",
    "refAgriParcel", "refDevice", "refWeatherStation",
    "municipalityCode",
})
```

This prevents `municipalityCode` from appearing as a selectable timeseries attribute in the DataHub entity tree (it's metadata, not a measurement).

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz-module-datahub
git add backend/app/api/entities.py
git commit -m "fix(datahub): hide municipalityCode from entity attribute list

municipalityCode is self-describing metadata for timeseries resolution,
not a user-facing measurement attribute."
```

---

## Task 9: K8s Deployment — New telemetry-worker Manifest

**Files:**
- Create: `nkz/k8s/core/services/telemetry-worker-deployment.yaml`

- [ ] **Step 1: Create the new deployment manifest**

Create `nkz/k8s/core/services/telemetry-worker-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: telemetry-worker
  namespace: nekazari
  labels:
    app: telemetry-worker
    layer: backend
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: telemetry-worker
  template:
    metadata:
      labels:
        app: telemetry-worker
        layer: backend
    spec:
      containers:
      - name: telemetry-worker
        image: ghcr.io/nkz-os/nkz/telemetry-worker:latest
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8080
        env:
        - name: ORION_URL
          value: "http://orion-ld-service:1026"
        - name: SERVICE_HOST
          value: "telemetry-worker-service"
        - name: SERVICE_PORT
          value: "80"
        - name: CONTEXT_URL
          value: "http://api-gateway-service:5000/ngsi-ld-context.json"
        - name: POSTGRES_URL
          valueFrom:
            secretKeyRef:
              name: timescale-secret
              key: connection-string
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: telemetry-worker-service
  namespace: nekazari
  labels:
    app: telemetry-worker
spec:
  selector:
    app: telemetry-worker
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
```

Key differences from archived version:
- `image`: `ghcr.io/nkz-os/nkz/telemetry-worker:latest` (new org)
- NO `imagePullSecrets` (public GHCR)
- `SERVICE_PORT: "80"` (fixed)
- `CONTEXT_URL` added (was missing)
- `optional: true` removed from `POSTGRES_URL` secretKeyRef (it's required)

- [ ] **Step 2: Dry-run validation**

Run: `cd /home/g/Documents/nekazari/nkz && sudo kubectl apply -f k8s/core/services/telemetry-worker-deployment.yaml --dry-run=client`

Expected: `deployment.apps/telemetry-worker configured (dry run)` and `service/telemetry-worker-service configured (dry run)`

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add k8s/core/services/telemetry-worker-deployment.yaml
git commit -m "feat(k8s): add telemetry-worker deployment to core services

Replaces archived deployment. Fixes: correct GHCR org (nkz-os),
no imagePullSecrets (public), SERVICE_PORT=80, CONTEXT_URL added."
```

---

## Task 10: Integration Verification

After deploying all changes, verify the full pipeline end-to-end.

- [ ] **Step 1: Rebuild and push Docker images**

```bash
# telemetry-worker
cd /home/g/Documents/nekazari/nkz
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/telemetry-worker:latest services/telemetry-worker/
sudo docker push ghcr.io/nkz-os/nkz/telemetry-worker:latest

# weather-worker
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/weather-worker:latest services/weather-worker/
sudo docker push ghcr.io/nkz-os/nkz/weather-worker:latest

# timeseries-reader
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/timeseries-reader:latest services/timeseries-reader/
sudo docker push ghcr.io/nkz-os/nkz/timeseries-reader:latest
```

- [ ] **Step 2: Deploy telemetry-worker**

```bash
sudo kubectl apply -f k8s/core/services/telemetry-worker-deployment.yaml
sudo kubectl rollout status deployment/telemetry-worker -n nekazari
```

- [ ] **Step 3: Restart weather-worker and timeseries-reader**

```bash
sudo kubectl rollout restart deployment/weather-worker -n nekazari
sudo kubectl rollout restart deployment/timeseries-reader -n nekazari
```

- [ ] **Step 4: Verify subscriptions**

```bash
# Check telemetry-worker logs for subscription creation
sudo kubectl logs deployment/telemetry-worker -n nekazari --tail=50 | grep -i subscription

# Verify subscriptions exist in Orion for tenant asociacinallotarra
sudo kubectl exec deployment/telemetry-worker -n nekazari -- python3 -c "
import requests
headers = {'NGSILD-Tenant': 'asociacinallotarra'}
r = requests.get('http://orion-ld-service:1026/ngsi-ld/v1/subscriptions', headers=headers)
for s in r.json():
    print(s.get('description'), '->', s.get('notification',{}).get('endpoint',{}).get('uri'))
"
```

Expected: Subscriptions using port 80 for all entity types.

- [ ] **Step 5: Run migration — PATCH `municipalityCode` onto existing WeatherObserved entities**

```bash
# One-time script: uses tenant_weather_locations as source of truth
sudo kubectl exec deployment/weather-worker -n nekazari -- python3 -c "
import os, requests, psycopg2
ORION = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
PG = os.getenv('POSTGRES_URL', '')
conn = psycopg2.connect(PG)
cur = conn.cursor()
cur.execute('SELECT tenant_id, municipality_code, latitude, longitude FROM tenant_weather_locations')
for tenant_id, muni_code, lat, lon in cur.fetchall():
    headers = {
        'NGSILD-Tenant': tenant_id,
        'Content-Type': 'application/json',
        'Link': '<http://api-gateway-service:5000/ngsi-ld-context.json>; rel=\"http://www.w3.org/ns/json-ld#context\"; type=\"application/ld+json\"',
    }
    r = requests.get(
        f'{ORION}/ngsi-ld/v1/entities?type=WeatherObserved&limit=100',
        headers=headers,
    )
    if r.status_code != 200:
        print(f'Skip {tenant_id}: {r.status_code}')
        continue
    for ent in r.json():
        if ent.get('municipalityCode'):
            continue
        eid = ent['id']
        patch = {'municipalityCode': {'type': 'Property', 'value': muni_code}}
        pr = requests.patch(
            f'{ORION}/ngsi-ld/v1/entities/{eid}/attrs',
            json=patch,
            headers=headers,
        )
        print(f'PATCH {eid} municipalityCode={muni_code}: {pr.status_code}')
cur.close()
conn.close()
"
```

- [ ] **Step 6: Verify weather data in DataHub**

Open DataHub module in browser. Select a WeatherObserved entity. Verify timeseries charts display temperature, humidity, wind speed data.

- [ ] **Step 7: Verify telemetry data in DataHub**

Check telemetry-worker logs for notification processing:

```bash
sudo kubectl logs deployment/telemetry-worker -n nekazari --tail=20 | grep -i "measurement\|notification\|processing"
```

If an MQTT device sends data, verify `telemetry_events` now contains numeric measurements (not metadata).
