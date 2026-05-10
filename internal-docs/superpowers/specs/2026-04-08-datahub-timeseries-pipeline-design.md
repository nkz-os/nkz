---
title: "DataHub Timeseries Pipeline — SOTA Redesign"
description: "Self-describing NGSI-LD entities for reliable weather + telemetry data resolution in the DataHub module"
---

# DataHub Timeseries Pipeline — SOTA Redesign

> Design approved 2026-04-08. Addresses 4 interconnected failures preventing the DataHub module from displaying IoT sensor data and simulated weather station data.

## Context

### Current State (broken)

The DataHub module discovers NGSI-LD entities via Orion-LD and fetches their historical timeseries from TimescaleDB via the timeseries-reader v2 API. Four failures break this pipeline:

1. **Weather resolution chain is fragile**: `WeatherObserved → refParcel → AgriParcel → address.addressLocality → catalog_municipalities → weather_observations`. Fails because parcels lack `address` and `cadastral_parcels` is empty.
2. **Attribute name mismatch**: DataHub sends NGSI-LD names (`temperature`) but timeseries-reader only accepts DB column names (`temp_avg`) → 400 error.
3. **Telemetry notification handler stores metadata as measurements**: `name`, `refDeviceProfile` saved instead of actual sensor values. Relationships and GeoProperties incorrectly included.
4. **Subscription port mismatch + single-tenant**: 3/4 subscriptions use port 8080 but K8s Service exposes 80. Only `DEFAULT_TENANT` gets subscriptions.

### Production Evidence (2026-04-08)

- Tenant `asociacinallotarra`: 2 AgriParcel, 2 WeatherObserved, 1 AgriSensor
- `weather_observations`: 1512 rows for 3 municipalities (31012, 01059, 31258) — data exists but unreachable
- `telemetry_events`: 1111 rows. Device `120786a0cf364796` had real measurements but entity deleted. Device `3646669629bf44e5` only stores `{name, refDeviceProfile}` — zero numeric data
- telemetry-worker running 100d but logs show only health checks — zero notifications received

### Design Principle

**Approach A: Self-describing entities.** Each NGSI-LD entity carries the metadata needed to locate its own timeseries data. No fragile resolution chains. The timeseries-reader reads the entity once and knows where to query.

## Component 1: WeatherObserved — Direct Municipality Resolution

### Problem

The weather-worker already knows `municipality_code` when creating WeatherObserved entities (it comes from `tenant_weather_locations`), but does not store it on the entity. The timeseries-reader must reverse-engineer it through a 4-hop chain that fails.

### Changes

#### 1a. `weather-worker/storage/orion_writer.py` — `create_weather_observed_entity()`

Add parameter `municipality_code: str`. Add NGSI-LD Property to the entity:

```json
{
  "municipalityCode": {
    "type": "Property",
    "value": "31012"
  }
}
```

#### 1b. `weather-worker/storage/orion_writer.py` — `sync_weather_to_orion()`

The caller already has `municipality_code` from `tenant_weather_locations`. Pass it through to `create_weather_observed_entity()`. Signature change:

```python
def sync_weather_to_orion(
    tenant_id: str,
    latitude: float,
    longitude: float,
    weather_data: Dict[str, Any],
    observed_at: Optional[datetime] = None,
    radius_km: float = 10.0,
    municipality_code: Optional[str] = None,  # NEW
) -> int:
```

The `WeatherWorker.ingest_weather_data()` method already has `municipality_code` — pass it when calling `sync_weather_to_orion`.

#### 1c. `timeseries-reader/urn_resolution.py` — `_resolve_urn_to_weather_key()`

For `WeatherObserved` entities, check for `municipalityCode` Property first:

```python
if etype_short == "WeatherObserved":
    # Direct resolution: entity carries its own municipality code
    muni_prop = entity.get("municipalityCode")
    if muni_prop:
        muni_val = _get_value(muni_prop)
        if isinstance(muni_val, str) and muni_val.strip():
            return (muni_val.strip(), "municipality")
    # Fallback: legacy chain via refParcel → address
    # (existing code, unchanged)
```

#### 1d. `datahub/entities.py` — `_NGSI_SYSTEM_KEYS`

Add `municipalityCode` to the set so it is not exposed as a timeseries attribute in the entity tree.

### Result

O(1) resolution. One Orion read, one field access. No chain. Legacy entities without `municipalityCode` fall back to existing (broken) chain — they need a one-time Orion PATCH to add the field.

### Migration

Run a one-time script that:
1. For each tenant, queries `tenant_weather_locations` to get `(tenant_id, municipality_code, latitude, longitude)`
2. For each weather location, queries Orion for `WeatherObserved` entities near those coordinates (geo-query, same approach `sync_weather_to_orion` uses)
3. PATCHes `municipalityCode` onto each matched entity

This avoids the broken resolution chain — it uses the same source of truth (`tenant_weather_locations`) that the weather-worker uses when creating entities. New entities get the field at creation time.

---

## Component 2: Weather Attribute Name Mapping

### Problem

The datahub discovers NGSI-LD attribute names from Orion (`temperature`, `relativeHumidity`, `windSpeed`). The timeseries-reader validates against `VALID_ATTRIBUTES` which contains DB column names (`temp_avg`, `humidity_avg`, `wind_speed_ms`). Result: 400 "Invalid attribute".

### Changes

#### 2a. `timeseries-reader/app.py` — New mapping dict

```python
_WEATHER_ATTRIBUTE_MAP: Dict[str, str] = {
    # NGSI-LD (SDM) name        → DB column (weather_observations)
    "temperature":               "temp_avg",
    "relativeHumidity":          "humidity_avg",
    "windSpeed":                 "wind_speed_ms",
    "windDirection":             "wind_direction_deg",
    "atmosphericPressure":       "pressure_hpa",
    "precipitation":             "precip_mm",
    "et0":                       "eto_mm",
    "solarRadiation":            "solar_rad_w_m2",
    "soilMoisture":              "soil_moisture_0_10cm",
}

# DB column names also accepted (passthrough for scripts, PAT API)
for _col in VALID_ATTRIBUTES:
    _WEATHER_ATTRIBUTE_MAP.setdefault(_col, _col)
```

#### 2b. `timeseries-reader/app.py` — New resolver function

```python
def _resolve_weather_attribute(requested: str) -> Optional[str]:
    """Map NGSI-LD attribute name or DB column name to weather_observations column."""
    r = (requested or "").strip()
    return _WEATHER_ATTRIBUTE_MAP.get(r)
```

#### 2c. `timeseries-reader/app.py` — `get_v2_entity_timeseries()` weather path

Replace direct `VALID_ATTRIBUTES` check with `_resolve_weather_attribute()`:

```python
# Before:
if a not in VALID_ATTRIBUTES:
    return jsonify({"error": f"Invalid attribute: {a}"}), 400

# After:
resolved = _resolve_weather_attribute(a)
if resolved is None:
    return jsonify({"error": f"Unknown weather attribute: {a}"}), 400
```

Use resolved column name for the SQL query.

#### 2d. Add `wind_direction_deg` to `VALID_ATTRIBUTES`

The weather-worker writes this column and the entity exposes `windDirection`, but the reader currently rejects it.

### Result

DataHub sends `temperature` → reader resolves to `temp_avg` → SQL works. Direct DB column names also work (backwards compatible). Same pattern as existing telemetry alias resolution.

---

## Component 3: Telemetry Notification Handler — Clean Measurements

### Problem

`_extract_measurements()` in `notification_handler.py` captures every Property, GeoProperty, and Relationship from the entity notification. This fills `telemetry_events.payload.measurements` with metadata (`name`, `refDeviceProfile`) instead of actual sensor readings.

### Changes

#### 3a. `notification_handler.py` — `_extract_measurements()`

Expand `skip_keys` to exclude known metadata attributes. Exclude Relationships and GeoProperties:

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

Key changes:
- Explicit metadata key exclusion
- Only `Property` type (skip `Relationship`, `GeoProperty`)
- Only scalar values (skip dicts, lists — complex nested values are metadata)

#### 3b. No IoT Agent provisioning changes needed

The fact that device `3646669629bf44e5` has no measurement attributes in Orion is a **data/configuration issue**: the device profile needs its measurements defined in `sensor_profiles`. This is tenant configuration, not a code bug. The code change in 3a ensures the handler is robust regardless.

### Result

`telemetry_events` only contains numeric sensor measurements. Entities with no real measurements produce empty `measurements={}` → handler skips persistence (existing guard at line 126).

---

## Component 4: Subscription Management — Port Fix + Multi-Tenant

### Problem

3/4 subscriptions in the `platform` tenant use `telemetry-worker-service:8080/notify` but K8s Service exposes port 80 (→ targetPort 8080). Subscriptions only created for `DEFAULT_TENANT`.

### Changes

#### 4a. `subscription_manager.py` — Fix port default

```python
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
```

#### 4b. `subscription_manager.py` — Multi-tenant subscription creation

Replace `check_or_create_subscription()` with `ensure_subscriptions_for_all_tenants()`:

```python
def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = _get_active_tenants()  # Query PostgreSQL
    if not tenants:
        tenants = [DEFAULT_TENANT]

    for tenant_id in tenants:
        _ensure_tenant_subscriptions(tenant_id)
```

`_get_active_tenants()` queries `SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL` from PostgreSQL (using `POSTGRES_URL` env var already available).

`_ensure_tenant_subscriptions(tenant_id)` is the existing `check_or_create_subscription()` logic but parameterized by tenant.

#### 4c. Periodic self-healing check

In `app.py` lifespan, after initial subscription setup, schedule a periodic check (every 60 minutes via `asyncio` task):

```python
async def _periodic_subscription_check():
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, ensure_subscriptions_for_all_tenants
            )
        except Exception as e:
            logger.warning(f"Periodic subscription check failed: {e}")
```

This detects new tenants provisioned after worker startup and creates their subscriptions. Self-healing: if a subscription is accidentally deleted, it gets recreated.

#### 4d. Startup cleanup of broken subscriptions

On startup, before creating new subscriptions, scan for subscriptions whose `endpoint.uri` contains `:8080` and delete them:

```python
def _cleanup_broken_subscriptions(tenant_id: str):
    """Delete subscriptions with wrong port (legacy bug)."""
    headers = _get_headers(tenant_id)
    r = requests.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions", headers=headers)
    if r.status_code != 200:
        return
    for sub in r.json():
        uri = sub.get("notification", {}).get("endpoint", {}).get("uri", "")
        if ":8080" in uri and "telemetry-worker" in uri:
            sub_id = sub.get("id")
            requests.delete(
                f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
                headers=headers
            )
            logger.info(f"Deleted broken subscription {sub_id} (port 8080)")
```

#### 4e. Deployment YAML update

Update the telemetry-worker deployment (create new one in `k8s/core/services/`):
- Fix `SERVICE_PORT: "80"`
- Update image to `ghcr.io/nkz-os/nkz/telemetry-worker:latest`
- Remove `imagePullSecrets` (public GHCR)

### Result

Every tenant gets subscriptions automatically. Self-healing every hour. Broken subscriptions cleaned on startup. No coupling with tenant provisioning services.

---

## Files Modified (Summary)

| Service | File | Change |
|---------|------|--------|
| weather-worker | `storage/orion_writer.py` | Add `municipality_code` param + `municipalityCode` Property |
| weather-worker | `main.py` | Pass `municipality_code` through call chain |
| timeseries-reader | `urn_resolution.py` | Read `municipalityCode` from entity (direct resolution) |
| timeseries-reader | `app.py` | Add `_WEATHER_ATTRIBUTE_MAP`, `_resolve_weather_attribute()`, add `wind_direction_deg` to VALID_ATTRIBUTES |
| telemetry-worker | `notification_handler.py` | Filter metadata keys, skip Relationship/GeoProperty |
| telemetry-worker | `subscription_manager.py` | Fix port, multi-tenant, periodic check, cleanup |
| telemetry-worker | `app.py` | Periodic subscription task |
| datahub | `entities.py` | Add `municipalityCode` to `_NGSI_SYSTEM_KEYS` |
| k8s | `telemetry-worker-deployment.yaml` | New deployment in `k8s/core/services/` |

## Files NOT Modified

- IoT Agent configuration (device profiles are tenant data, not code)
- api-gateway (already injects Link header correctly)
- datahub frontend (attribute names come from Orion, no hardcoding needed)
- datahub backend timeseries proxy (passthrough, no mapping responsibility)

## Migration Steps (One-Time)

1. PATCH existing WeatherObserved entities to add `municipalityCode`
2. Delete broken subscriptions with port 8080 (handled automatically by 4d)
3. Rebuild + push telemetry-worker Docker image
4. Rebuild + push weather-worker Docker image (if not using in-cluster build)

## Out of Scope

- Device profile configuration for individual sensors (tenant responsibility)
- Dynamic ingress routing for modules (tracked separately)
- Kafka/event-streaming replacement for notification subscriptions (future scale concern)
