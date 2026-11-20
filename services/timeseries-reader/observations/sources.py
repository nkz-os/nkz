"""Source planning and data fetch of the `best` observation service (contract 2.1, 2.2, 8.1, 8.4).

Turns a request into `Reading` lists per tier for the Task 12 engine:
  1 direct (deployment observes the feature), 2 contained (inside the area), 3 nearby (radius),
  4 parcel model, 5 regional model (nothing ingested today: always empty).

Fetch-window contract (engine docstring): [buckets[0][0], buckets[-1][1]) plus, for cumulative
properties, the last valid reading of every (device, attribute) before the window (it supplies
the first delta).

Two fetch paths for tiers 1-3 (contract 8.10):
  - SQL-aggregated (default): `series` at PT15M / PT1H / P1D of instant/rate properties gets one
    folded row per source x tier inputs x sub-context x grain (engine module doc); `latest` of
    instant/rate properties gets each series' newest in-range row in the window (8.9).
  - raw rows: everything else (cumulative and amount, resolution=raw). It is also the reference
    of the differential test (`aggregate_in_sql=False`).

Transactions: `observed_tiers` and `model_tiers` run on a `tenant_cursor` (tenant already set).
`geometry_of` may write the geometry cache, so the caller resolves geometries FIRST in their own
short transaction and commits, then opens the read transaction (Task 11 review ruling).
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from common.context_aliases import canonical_attribute
from common.observation_context import measurement_entity_suffix
from urn_resolution import WEATHER_OBSERVED_ENTITY_TYPES

from .engine import Reading, buckets
from .request import BestRequest, RequestError

GREENHOUSE_TYPES = frozenset({"AgriGreenhouse", "Greenhouse"})
# ASSUMPTION: 2 days is long enough to find the last counter reading before the window (loggers
# report at least daily); a longer silence loses the first delta of that source, never fabricates one.
PRIOR_LOOKBACK = timedelta(days=2)
PROFILE_LOOKBACK = timedelta(days=30)    # counterMax: how far back the latest device event is sought
MAX_REGIONAL_RADIUS_M = 50_000.0
MODEL_SOURCE_ID = "parcel_weather"
REGIONAL_SOURCE_ID = "regional_proxy"

# catalogue property -> telemetry_events WeatherObserved payload.measurements key
# (verified against app._WEATHER_COLUMN_TO_MEASUREMENT).
_MODEL_KEYS = {
    "airTemperature": "airTemperature",
    "relativeHumidity": "humidity",
    "precipitation": "precipitation",
    "solarRadiation": "solarRadiation",
}
# Model precipitation is a per-period amount, not a counter. Task 14 must aggregate tiers 4/5 of
# these properties with engine semantics "amount" (see `model_semantics`).
_MODEL_AMOUNT_PROPERTIES = frozenset({"precipitation"})

# Same pattern as app._NUMERIC_TEXT_PATTERN (app cannot be imported from here).
_NUMERIC_TEXT_PATTERN = r"^\s*[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?\s*$"


# ------------------------------------------------------------------ medium

def _short_type(t: Any) -> str:
    return str(t or "").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def resolve_medium(req: BestRequest, prop: dict, feature_entity: Optional[dict]) -> str:
    """explicit -> feature environment -> prop.default_medium; 422 if not in prop.allowed_media.

    Feature environment today: AgriGreenhouse / Greenhouse -> enclosedAir. "Under structure"
    detection is deferred by ruling.
    """
    medium = req.medium
    if not medium and feature_entity and _short_type(feature_entity.get("type")) in GREENHOUSE_TYPES:
        medium = "enclosedAir"
    if not medium:
        medium = prop.get("default_medium")
    allowed = prop.get("allowed_media") or []
    if not medium or medium not in allowed:
        raise RequestError(422, f"medium {medium!r} not allowed for property {prop.get('name')}")
    return medium


# ---------------------------------------------------------------- helpers

def model_semantics(prop: dict) -> Optional[str]:
    """Engine semantics to force on tiers 4/5 ("amount" for precipitation), else None."""
    return "amount" if prop.get("name") in _MODEL_AMOUNT_PROPERTIES else None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _device_id(device_urn: str) -> str:
    return device_urn.rsplit(":", 1)[-1]


def _override_for(overrides: dict, attr: str) -> dict:
    """Channel override of `attr`; keys may be aliases of the canonical name."""
    for key, value in (overrides or {}).items():
        if canonical_attribute(key) == attr and isinstance(value, dict):
            return value
    return {}


def _channel(dep: dict, attr: str) -> Tuple[Optional[str], Optional[str]]:
    """(medium, feature) a deployment gives to `attr` (same rules as the worker's resolution)."""
    ov = _override_for(dep["channel_overrides"], attr)
    ctx = ov.get("observationContext")
    if not isinstance(ctx, dict):
        ctx = dep["observation_context"] or {}
    medium = ctx.get("medium")
    feature = ov.get("observesFeature") or dep["observes_feature"] or None
    if feature is None and medium == "equipment":
        feature = dep["device_urn"]  # same rule as the worker's observation_context
    return medium, feature


def _sub_context(row: dict) -> dict:
    sub = {
        "depthFrom": _num(row.get("depth_from_cm")),
        "depthTo": _num(row.get("depth_to_cm")),
        "heightAboveGround": _num(row.get("height_m")),
        "organ": row.get("organ"),
        "contextRef": row.get("context_ref"),
    }
    return {k: v for k, v in sub.items() if v is not None}


def _matches_request_sub_context(sub: dict, wanted: dict, prop: dict) -> bool:
    tol = prop.get("sub_context_tolerance") or {}
    for key, want in (wanted or {}).items():
        if key == "height":
            have = sub.get("heightAboveGround")
            t = tol.get("heightAboveGround", tol.get("height", 0.0)) or 0.0
            if have is None or abs(have - float(want)) > float(t) + 1e-9:
                return False
        elif sub.get(key) != want:
            return False
    return True


# ------------------------------------------------------------------- SQL

_ATTRS_SQL = """-- obs:attrs
SELECT DISTINCT m->>'sdmAttribute' AS attr
FROM sensor_profiles p, jsonb_array_elements(p.mapping->'measurements') m
WHERE (p.tenant_id IS NULL OR p.tenant_id = %s)
  AND m->>'observedProperty' = %s
  AND m->>'sdmAttribute' IS NOT NULL
"""

# Candidate deployments of the tenant overlapping the window. Spatial facts are computed against
# the area (NULL area -> NULL facts); medium/feature rules are applied per attribute in Python.
_DEPLOYMENTS_SQL = """-- obs:deployments
WITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)
SELECT d.id::text AS id, d.device_urn, d.observes_feature, d.observation_context,
       d.channel_overrides, d.mobility,
       COALESCE(ST_Covers((SELECT g FROM area), d.location), false) AS in_area,
       ST_Distance((SELECT g FROM area), d.location) AS dist_m
FROM device_deployments d
WHERE d.tenant_id = %s
  AND d.valid && tstzrange(%s, %s, '[)')
"""

_COVERED_SQL = """-- obs:covered
SELECT g.entity_urn
FROM observation_geometry_cache g
WHERE g.tenant_id = %s
  AND g.entity_urn = ANY(%s)
  AND ST_Covers(ST_GeomFromGeoJSON(%s::text)::geography, g.geom)
"""

_VALUE_COLUMNS = """m.observed_at, m.device_id, m.attribute_name, m.value,
       m.deployment_id::text AS deployment_id, m.source_kind, m.feature_of_interest,
       m.depth_from_cm, m.depth_to_cm, m.height_m, m.organ, m.context_ref,
       COALESCE((e.payload->'calibration_period_ids') ? m.attribute_name, false) AS calibrated,
       COALESCE(ST_Covers((SELECT g FROM area), m.location), false) AS r_in_area,
       ST_Distance((SELECT g FROM area), m.location) AS r_dist_m"""

# The event join is bounded like the measurements (tenant, device, same instant): it lets chunk
# exclusion work on telemetry_events.
_VALUE_FROM = """FROM telemetry_measurements m
LEFT JOIN telemetry_events e
       ON e.tenant_id = m.tenant_id AND e.observed_at = m.observed_at
      AND e.id = m.source_event_id
      AND e.tenant_id = %s AND e.device_id = ANY(%s)
      AND e.observed_at >= %s AND e.observed_at < %s"""

# tenant_id and attribute_name are compression segmentby keys: always present (contract 8.1).
_VALUE_FILTER = """m.tenant_id = %s
  AND m.attribute_name = ANY(%s)
  AND m.device_id = ANY(%s)
  AND m.observed_property = %s
  AND m.medium = %s
  AND m.deployment_id IS NOT NULL
  AND m.deployment_id = ANY(%s::uuid[])
  AND m.quality_flag = 'valid'
  AND m.observed_at >= %s AND m.observed_at < %s"""

_VALUES_SQL = (
    "-- obs:values\nWITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)\n"
    f"SELECT {_VALUE_COLUMNS}\n{_VALUE_FROM}\nWHERE {_VALUE_FILTER}\nORDER BY m.observed_at"
)

# ------------------------------------------------- SQL-aggregated paths (8.10)
# Named parameters. %(lo)s / %(hi)s are the catalogue valid range (+-Infinity when NULL): the
# engine's in-range rule `not (v < valid_min or v > valid_max)` for finite values.
# The spatial tier inputs are computed per distinct stored location (`loc`, exact bytes), not per
# row: same values as the raw path's per-row ST_Covers / ST_Distance.

# Calibration: only the events that carry calibration ids, and only that key, enter the join.
# `cal` is non-strict (COALESCE), so PostgreSQL must evaluate it below the outer join (a
# placeholder at the events scan): the hash holds the small key, never full payloads (a parallel
# hash of payloads overflows shared memory), and the plan stays parallel (an OFFSET 0 fence
# would not). Same `calibrated` as the raw path: no event / no key / JSON null -> false.
_AGG_EVENT_JOIN = """LEFT JOIN (
        SELECT e.tenant_id, e.observed_at, e.id,
               COALESCE(e.payload->'calibration_period_ids', 'null'::jsonb) AS cal
        FROM telemetry_events e
        WHERE e.tenant_id = %(tenant)s AND e.device_id = ANY(%(devices)s)
          AND e.observed_at >= %(w0)s AND e.observed_at < %(w1)s
          AND e.payload ? 'calibration_period_ids') e
       ON e.tenant_id = m.tenant_id AND e.observed_at = m.observed_at
      AND e.id = m.source_event_id"""

# Segment pruning (contract 8.1 intent): device_id is not a compression segmentby key of
# telemetry_measurements, entity_id is. Every row with observation context is a DeviceMeasurement
# row (worker and recompute job), whose entity_id is `...:{externalId}:{attr}` with device_id
# the last segment of externalId and attribute_name = attr (entity-manager build_measurements,
# worker _extract_device_measurement). So `entity_id LIKE '%:{device_id}:{attr}'` keeps every
# candidate row and lets the compressed scan skip the batches of all other devices.
# The rule is enforced at the single writer: the telemetry-worker attaches observation context
# only when common.observation_context.entity_id_conforms holds (else it logs
# observation_entity_id_nonconforming), so a non-conforming row has no deployment and is
# excluded from every read path, not only from this one. Contract test: telemetry-worker
# tests/test_observation_entity_id_contract.py. An `OR entity_id IS NULL`
# escape is not used: it defeats the pushdown to the compressed scan (measured 1.1 s vs 0.25 s).
_SERIES_PRUNE = "m.entity_id LIKE ANY(%(entity_like)s)"

_AGG_FILTER = """m.tenant_id = %(tenant)s
  AND m.attribute_name = ANY(%(attrs)s)
  AND m.device_id = ANY(%(devices)s)
  AND m.observed_property = %(prop)s
  AND m.medium = %(medium)s
  AND m.deployment_id IS NOT NULL
  AND m.deployment_id = ANY(%(deps)s::uuid[])
  AND m.quality_flag = 'valid'
  AND m.observed_at >= %(w0)s AND m.observed_at < %(w1)s"""

_SUB_COLS = "depth_from_cm, depth_to_cm, height_m, organ, context_ref"

# Grain = the unit that nests in the engine's buckets: the UTC bucket for PT15M / PT1H; for P1D
# the hour counted from the local-midnight bucket start b0 (the engine's coverage hour).
_GRAIN = {
    "PT15M": "time_bucket(interval '15 minutes', m.observed_at)",
    "PT1H": "time_bucket(interval '1 hour', m.observed_at)",
    "P1D": ("b.s[width_bucket(m.observed_at, b.s)] + floor(extract(epoch FROM "
            "m.observed_at - b.s[width_bucket(m.observed_at, b.s)]) / 3600) * interval '1 hour'"),
}

_SERIES_SQL = """-- obs:series
WITH area AS (SELECT ST_GeomFromGeoJSON(%(area)s::text)::geography AS g),
b AS (SELECT %(starts)s::timestamptz[] AS s),
x AS (
    SELECT m.deployment_id, m.device_id, m.source_kind, m.feature_of_interest,
           m.depth_from_cm, m.depth_to_cm, m.height_m, m.organ, m.context_ref,
           m.location::bytea AS loc, m.observed_at, m.value,
           (m.value >= %(lo)s AND m.value <= %(hi)s) AS ok,
           COALESCE(e.cal ? m.attribute_name, false) AS calibrated,
           {grain} AS grain
    FROM telemetry_measurements m
    {event_join}
    CROSS JOIN b
    WHERE {filter}
      AND {prune}
), grp AS (
    SELECT deployment_id::text AS deployment_id, device_id, source_kind, feature_of_interest,
           {sub}, loc, grain,
           count(*) FILTER (WHERE ok) AS n,
           sum(value) FILTER (WHERE ok) AS value_sum,
           bool_and(calibrated) FILTER (WHERE ok) AS calibrated,
           min(observed_at) FILTER (WHERE ok) AS t_first,
           count(*) FILTER (WHERE NOT ok) AS oor_n,
           min(observed_at) FILTER (WHERE NOT ok) AS oor_first,
           max(observed_at) FILTER (WHERE NOT ok) AS oor_last
    FROM x
    GROUP BY deployment_id, device_id, source_kind, feature_of_interest, {sub}, loc, grain
), ser AS (
    -- one row per stored series key; the grains as parallel arrays
    SELECT deployment_id, source_kind, feature_of_interest, {sub}, loc,
           array_agg(n ORDER BY grain) AS n,
           array_agg(value_sum ORDER BY grain) AS value_sum,
           array_agg(calibrated ORDER BY grain) AS calibrated,
           array_agg(t_first ORDER BY grain) AS t_first,
           array_agg(oor_n ORDER BY grain) AS oor_n,
           array_agg(oor_first ORDER BY grain) AS oor_first,
           array_agg(oor_last ORDER BY grain) AS oor_last
    FROM grp
    GROUP BY deployment_id, device_id, source_kind, feature_of_interest, {sub}, loc
)
SELECT ser.deployment_id, ser.source_kind, ser.feature_of_interest, {ser_sub},
       ser.n, ser.value_sum, ser.calibrated, ser.t_first, ser.oor_n, ser.oor_first, ser.oor_last,
       COALESCE(ST_Covers((SELECT g FROM area), ser.loc::geography), false) AS r_in_area,
       ST_Distance((SELECT g FROM area), ser.loc::geography) AS r_dist_m
FROM ser
"""

# Latest (8.9): one pass over the max_age window. Each stored row is classed by its series key
# (deployment, device, attribute, kind, feature, sub-context) and by its location-derived tier
# inputs (inside the area; within the nearby radius), computed once per distinct stored
# location. Per class, the newest in-range row (ties: the higher value, as the engine's duplicate
# rule) and the out-of-range counts. The tier itself is still decided in Python (`_row_tier`)
# from the class columns, which fix it: a mobile device keeps its newest row in every tier, and
# no channel or sub-context is lost. Calibration is looked up for the chosen rows only.
_KEY_COLS = ("deployment_id", "device_id", "attribute_name", "source_kind",
             "feature_of_interest", "depth_from_cm", "depth_to_cm", "height_m", "organ",
             "context_ref")

_LATEST_SQL = """-- obs:latest
WITH area AS (SELECT ST_GeomFromGeoJSON(%(area)s::text)::geography AS g),
w AS (
    SELECT m.tenant_id, m.observed_at, m.source_event_id, m.value, {key_m},
           COALESCE(m.location::bytea, '\\x'::bytea) AS loc,
           (m.value >= %(lo)s AND m.value <= %(hi)s) AS ok
    FROM telemetry_measurements m
    WHERE {filter}
), locs AS (
    SELECT d.loc,
           COALESCE(ST_Covers((SELECT g FROM area), NULLIF(d.loc, '\\x'::bytea)::geography),
                    false) AS r_in_area,
           ST_Distance((SELECT g FROM area), NULLIF(d.loc, '\\x'::bytea)::geography) AS r_dist_m
    FROM (SELECT DISTINCT loc FROM w) d
), c AS (
    SELECT w.*, l.r_in_area, l.r_dist_m,
           COALESCE(%(radius)s > 0 AND l.r_dist_m <= %(radius)s, false) AS r_near
    FROM w JOIN locs l ON l.loc = w.loc
), r AS (
    SELECT c.*,
           row_number() OVER (cls ORDER BY ok DESC, observed_at DESC, value DESC) AS rn,
           count(*) FILTER (WHERE NOT ok) OVER cls AS oor_n,
           min(observed_at) FILTER (WHERE NOT ok) OVER cls AS oor_first,
           max(observed_at) FILTER (WHERE NOT ok) OVER cls AS oor_last
    FROM c
    WINDOW cls AS (PARTITION BY {key_c}, r_in_area, r_near)
)
SELECT r.deployment_id::text AS deployment_id, {key_r_rest}, r.oor_n, r.oor_first, r.oor_last,
       CASE WHEN r.ok THEN r.observed_at END AS observed_at,
       CASE WHEN r.ok THEN r.value END AS value,
       COALESCE(ev.calibrated, false) AS calibrated,
       r.r_in_area, r.r_dist_m
FROM r
LEFT JOIN LATERAL (
    SELECT (e.payload->'calibration_period_ids') ? r.attribute_name AS calibrated
    FROM telemetry_events e
    WHERE r.ok
      AND e.tenant_id = r.tenant_id AND e.observed_at = r.observed_at
      AND e.id = r.source_event_id
      AND e.tenant_id = %(tenant)s AND e.device_id = ANY(%(devices)s)
      AND e.observed_at >= %(w0)s AND e.observed_at < %(w1)s
) ev ON true
WHERE r.rn = 1
"""


def _series_sql(resolution: str) -> str:
    return _SERIES_SQL.format(
        grain=_GRAIN[resolution], event_join=_AGG_EVENT_JOIN, filter=_AGG_FILTER,
        prune=_SERIES_PRUNE, sub=_SUB_COLS,
        ser_sub=", ".join(f"ser.{c}" for c in _SUB_COLS.split(", ")))


# JIT is switched off for the latest statement only: it is a short pass over the max_age window,
# but the PostGIS functions' cost estimates push its plan over the JIT thresholds, and compiling
# took 1.2 s of a 1.4 s run (mobile device, 5400 rows); without JIT the run takes ~0.2 s.
_JIT_OFF_SQL = "-- obs:jit_off\nSET LOCAL jit = off"
_JIT_DEFAULT_SQL = "-- obs:jit_default\nSET LOCAL jit TO DEFAULT"


def _latest_sql() -> str:
    return _LATEST_SQL.format(
        key_m=", ".join(f"m.{c}" for c in _KEY_COLS),
        key_c=", ".join(_KEY_COLS),
        key_r_rest=", ".join(f"r.{c}" for c in _KEY_COLS if c != "deployment_id"),
        filter=_AGG_FILTER)


SQL_SEMANTICS = frozenset({"instant", "rate"})
SQL_SERIES_RESOLUTIONS = frozenset(_GRAIN)


def fetch_plan(req: BestRequest, prop: dict) -> str:
    """'series' | 'latest' (SQL-aggregated, 8.10) or 'raw' (raw rows; cumulative, raw resolution)."""
    sem = prop.get("temporal_semantics") or "instant"
    if sem not in SQL_SEMANTICS:
        return "raw"
    if req.mode == "latest":
        return "latest"
    if req.mode == "series" and req.resolution in SQL_SERIES_RESOLUTIONS:
        return "series"
    return "raw"


def entity_like_patterns(device_ids: List[str], attrs: List[str]) -> List[str]:
    """LIKE patterns matching the DeviceMeasurement entity ids of (device, attribute) pairs:
    the shared rule common.observation_context.measurement_entity_suffix, which the
    telemetry-worker enforces before it writes observation context (entity_id_conforms)."""
    return ["%" + _like_escape(measurement_entity_suffix(d, a)) for d in device_ids for a in attrs]


def _bound(v: Any, default: float) -> float:
    n = _num(v)
    return default if n is None else n


_PRIOR_SQL = (
    "-- obs:prior\nWITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)\n"
    f"SELECT DISTINCT ON (m.device_id, m.attribute_name) {_VALUE_COLUMNS}\n{_VALUE_FROM}\n"
    f"WHERE {_VALUE_FILTER}\n"
    "ORDER BY m.device_id, m.attribute_name, m.observed_at DESC"
)

# Latest event per device -> its profile code -> counterMax of the measurement mapped to the
# property. One query per request. A tenant profile shadows the global one (Python side).
_COUNTER_MAX_SQL = """-- obs:counter_max
WITH latest AS (
    SELECT DISTINCT ON (e.device_id) e.device_id,
           e.payload->'raw'->'profileCode'->>'value' AS code
    FROM telemetry_events e
    WHERE e.tenant_id = %s
      AND e.device_id = ANY(%s)
      AND e.observed_at >= %s AND e.observed_at < %s
      AND e.payload->'raw'->'profileCode'->>'value' IS NOT NULL
    ORDER BY e.device_id, e.observed_at DESC
)
SELECT l.device_id, p.tenant_id AS profile_tenant, m->>'sdmAttribute' AS attr,
       m->>'counterMax' AS counter_max
FROM latest l
JOIN sensor_profiles p ON p.code = l.code AND (p.tenant_id IS NULL OR p.tenant_id = %s)
CROSS JOIN LATERAL jsonb_array_elements(p.mapping->'measurements') m
WHERE m->>'observedProperty' = %s
  AND m->>'counterMax' IS NOT NULL
"""


def _property_attrs(cur, tenant_id: str, prop_name: str) -> List[str]:
    cur.execute(_ATTRS_SQL, (tenant_id, prop_name))
    attrs = {canonical_attribute(r["attr"]) for r in cur.fetchall() if r.get("attr")}
    return sorted(attrs)


# --------------------------------------------------------- tier planning

def _covered_features(cur, tenant_id: str, features: List[str], area_json: str) -> Set[str]:
    """Subset of `features` whose cached geometry is covered by the area."""
    if not features:
        return set()
    cur.execute(_COVERED_SQL, (tenant_id, features, area_json))
    return {r["entity_urn"] for r in cur.fetchall()}


def _candidate_deployments(cur, tenant_id: str, req: BestRequest, prop: dict, medium: str,
                           attrs: List[str], area_geom: Optional[dict], lo, hi
                           ) -> Tuple[Dict[str, str], Set[str], Set[str]]:
    """({deployment id: device URN}, probed features, covered features).

    Preselection only (bounds the device ids of the values query). A deployment is kept when it
    observes the request feature, any of its channel features is covered by the area, its
    location is inside the area or within the radius, or it is mobile. Final tier membership is
    decided per stored row, never per deployment (a datalogger can observe several features).
    """
    area_json = json.dumps(area_geom) if area_geom else None
    cur.execute(_DEPLOYMENTS_SQL, (area_json, tenant_id, lo, hi))
    radius = _num(prop.get("nearby_radius_m")) or 0.0
    cands: List[Tuple[dict, Set[str]]] = []
    for r in cur.fetchall():
        dep = dict(r)
        dep["observation_context"] = dep.get("observation_context") or {}
        dep["channel_overrides"] = dep.get("channel_overrides") or {}
        feats: Set[str] = set()
        matched = False
        for attr in attrs:
            m, f = _channel(dep, attr)
            if m == medium:
                matched = True
                if f:
                    feats.add(f)
        if matched:
            cands.append((dep, feats))

    probed: Set[str] = set()
    covered: Set[str] = set()
    if area_geom is not None:
        probed = {f for _, feats in cands for f in feats if f != req.feature}
        covered = _covered_features(cur, tenant_id, sorted(probed), area_json)

    keep: Dict[str, str] = {}
    for dep, feats in cands:
        if req.feature is not None and req.feature in feats:
            ok = True
        elif area_geom is None:
            ok = False
        else:
            ok = bool(dep["mobility"] == "mobile" or (feats & covered) or dep.get("in_area")
                      or (radius > 0 and dep.get("dist_m") is not None
                          and float(dep["dist_m"]) <= radius))
        if ok:
            keep[dep["id"]] = dep["device_urn"]
    return keep, probed, covered


def _row_tier(req: BestRequest, row: dict, area_geom: Optional[dict], covered: Set[str],
              radius: float) -> Tuple[Optional[int], Optional[float]]:
    """(tier, distance_m) of one stored row: first tier it qualifies for, from its own columns."""
    feature = row.get("feature_of_interest")
    if req.feature is not None and feature == req.feature:
        return 1, None
    if area_geom is None:
        return None, None
    if row.get("r_in_area") or (feature and feature in covered):
        return 2, None
    dist = row.get("r_dist_m")
    if radius > 0 and dist is not None and float(dist) <= radius:
        return 3, float(dist)
    return None, None


def _counter_maxes(cur, tenant_id: str, prop_name: str, device_ids: List[str],
                   w0, w1) -> Dict[Tuple[str, str], float]:
    cur.execute(_COUNTER_MAX_SQL,
                (tenant_id, device_ids, w0 - PROFILE_LOOKBACK, w1, tenant_id, prop_name))
    best: Dict[Tuple[str, str], Tuple[int, float]] = {}
    for r in cur.fetchall():
        cm = _num(r.get("counter_max"))
        if cm is None or not r.get("attr"):
            continue
        key = (r["device_id"], canonical_attribute(r["attr"]))
        rank = 1 if r.get("profile_tenant") is not None else 0  # tenant row shadows global
        if key not in best or rank > best[key][0]:
            best[key] = (rank, cm)
    return {k: v[1] for k, v in best.items()}


# --------------------------------------------------------- public: 1-3

def observed_tiers(cur, tenant_id: str, req: BestRequest, prop: dict, medium: str,
                   area_geom: Optional[dict], *,
                   aggregate_in_sql: bool = True) -> Dict[int, List[Reading]]:
    """Readings of tiers 1 (direct), 2 (contained), 3 (nearby); every key always present.

    `aggregate_in_sql` (default) uses the SQL-aggregated fetch where `fetch_plan` allows it: the
    readings are then folded (series) or one newest reading per stored series (latest; mode
    `latest` with the request window = the max_age window). False forces the raw-row fetch.

    Tier is decided per stored row (resolved columns): 1 iff feature_of_interest == feature;
    2 iff the cached geometry of feature_of_interest is covered by the area or the row's
    location is inside it; 3 iff the row's location is within nearby_radius_m of the area
    (distance_m set). `area_geom` is the GeoJSON of the request feature (geometry_of, resolved
    beforehand) or `req.geometry`; with no area only tier 1 can be populated.
    `cur` is a tenant_cursor.
    """
    out: Dict[int, List[Reading]] = {1: [], 2: [], 3: []}
    bks = buckets(req.start, req.end, req.resolution, req.tz)
    w0, w1 = bks[0][0], bks[-1][1]
    cumulative = prop.get("temporal_semantics") == "cumulative"

    attrs = _property_attrs(cur, tenant_id, req.property)
    if not attrs:
        return out
    prior_lo = w0 - PRIOR_LOOKBACK
    urns, probed, covered = _candidate_deployments(
        cur, tenant_id, req, prop, medium, attrs, area_geom, prior_lo if cumulative else w0, w1)
    if not urns:
        return out
    area_json = json.dumps(area_geom) if area_geom else None
    device_ids = sorted({_device_id(u) for u in urns.values()})
    dep_ids = sorted(urns)

    def fetch(sql: str, lo, hi) -> List[dict]:
        cur.execute(sql, (area_json, tenant_id, device_ids, lo, hi,
                          tenant_id, attrs, device_ids, req.property, medium, dep_ids, lo, hi))
        return [dict(r) for r in cur.fetchall()]

    plan = fetch_plan(req, prop) if aggregate_in_sql else "raw"
    cmax: Dict[Tuple[str, str], float] = {}
    if plan == "raw":
        rows = fetch(_VALUES_SQL, w0, w1)
        if cumulative:
            rows = fetch(_PRIOR_SQL, prior_lo, w0) + rows
            cmax = _counter_maxes(cur, tenant_id, req.property, device_ids, w0, w1)
    else:
        params = {"area": area_json, "tenant": tenant_id, "devices": device_ids, "attrs": attrs,
                  "prop": req.property, "medium": medium, "deps": dep_ids, "w0": w0, "w1": w1,
                  "lo": _bound(prop.get("valid_min"), float("-inf")),
                  "hi": _bound(prop.get("valid_max"), float("inf")),
                  "starts": [b[0] for b in bks] if req.resolution == "P1D" else [],
                  "entity_like": entity_like_patterns(device_ids, attrs),
                  "radius": _num(prop.get("nearby_radius_m")) or 0.0}
        if plan == "series":
            cur.execute(_series_sql(req.resolution), params)
            rows = [dict(r) for r in cur.fetchall()]
        else:
            cur.execute(_JIT_OFF_SQL)
            cur.execute(_latest_sql(), params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(_JIT_DEFAULT_SQL)

    if area_geom is not None:  # row features the deployment preselection did not probe
        extra = sorted({r["feature_of_interest"] for r in rows
                        if r.get("feature_of_interest")
                        and r["feature_of_interest"] not in probed
                        and r["feature_of_interest"] != req.feature})
        covered = covered | _covered_features(cur, tenant_id, extra, area_json)

    radius = _num(prop.get("nearby_radius_m")) or 0.0
    if plan != "raw":
        _append_aggregated(out, plan, rows, urns, req, prop, area_geom, covered, radius)
        return out
    for row in rows:
        urn = urns.get(row["deployment_id"])
        if urn is None:
            continue
        tier, dist = _row_tier(req, row, area_geom, covered, radius)
        if tier is None:
            continue
        sub = _sub_context(row)
        if not _matches_request_sub_context(sub, req.sub_context, prop):
            continue
        out[tier].append(Reading(
            source_id=urn,
            deployment_id=row["deployment_id"],
            t=row["observed_at"],
            value=float(row["value"]),
            sub_context=sub,
            distance_m=dist,
            kind=row.get("source_kind") or "device",
            calibrated=bool(row.get("calibrated")),
            counter_max=cmax.get((row["device_id"], row["attribute_name"])) if cumulative else None,
        ))
    return out


def _append_aggregated(out: Dict[int, List[Reading]], plan: str, rows: List[dict],
                       urns: Dict[str, str], req: BestRequest, prop: dict,
                       area_geom: Optional[dict], covered: Set[str], radius: float) -> None:
    """Folded (series) / newest (latest) readings of the SQL-aggregated rows, per tier.

    Each tier list is ordered by the reading's first in-range instant (the raw path's row order),
    so the engine's "first reading of a source in a bucket" is the same; ties are broken by the
    remaining columns (the raw path leaves them to the database).
    """
    staged: List[Tuple[tuple, int, Reading]] = []
    for row in rows:
        urn = urns.get(row["deployment_id"])
        if urn is None:
            continue
        tier, dist = _row_tier(req, row, area_geom, covered, radius)
        if tier is None:
            continue
        sub = _sub_context(row)
        if not _matches_request_sub_context(sub, req.sub_context, prop):
            continue
        kind = row.get("source_kind") or "device"
        key = (urn, row["deployment_id"], kind, -1.0 if dist is None else dist,
               repr(sorted(sub.items())))
        if plan == "series":  # parallel arrays, one entry per grain
            grains = zip(row["n"], row["value_sum"], row["calibrated"], row["t_first"],
                         row["oor_n"], row["oor_first"], row["oor_last"])
        else:
            n1 = 1 if row.get("observed_at") is not None else 0
            grains = [(n1, None, row.get("calibrated"), row.get("observed_at"),
                       row.get("oor_n"), row.get("oor_first"), row.get("oor_last"))]
        for n, value_sum, calibrated, t_first, oor_n, oor_first, oor_last in grains:
            n, oor_n = int(n or 0), int(oor_n or 0)
            if plan == "series":
                value_sum = float(value_sum) if n else None
                value = value_sum / n if n else 0.0
            else:
                value = float(row["value"]) if n else 0.0
            t = t_first if n else oor_first
            if t is None:  # neither in-range nor out-of-range readings: nothing stored
                continue
            reading = Reading(
                source_id=urn, deployment_id=row["deployment_id"], t=t, value=value,
                sub_context=sub, distance_m=dist, kind=kind,
                calibrated=bool(calibrated) if n else False, n=n, value_sum=value_sum,
                oor_n=oor_n, oor_first=oor_first if oor_n else None,
                oor_last=oor_last if oor_n else None)
            staged.append(((t,) + key + (value,), tier, reading))
    staged.sort(key=lambda x: x[0])
    for _, tier, reading in staged:
        out[tier].append(reading)


# --------------------------------------------------------- public: 4-5

_MODEL_SQL = """-- obs:model
SELECT e.observed_at,
       (CASE WHEN {ref} ~ '{pat}' THEN ({ref})::double precision END) AS value
FROM telemetry_events e
WHERE e.tenant_id = %s
  AND e.entity_type = ANY(%s)
  AND e.entity_id LIKE %s
  AND e.observed_at >= %s AND e.observed_at < %s
ORDER BY e.observed_at
"""

# Nearest non-parcel WeatherObserved station (location of its latest row in the window, as in
# urn_resolution.nearest_weather_entity) within the radius; its rows in the window. %s order:
# tenant, types, w0, w1, lon, lat, lon, lat, radius, tenant, types, w0, w1.
_REGIONAL_SQL = """-- obs:regional
WITH st AS (
    SELECT DISTINCT ON (x.entity_id) x.entity_id,
           ST_SetSRID(ST_MakePoint(
               (x.payload#>>'{{raw,location,value,coordinates,0}}')::double precision,
               (x.payload#>>'{{raw,location,value,coordinates,1}}')::double precision), 4326)::geography AS g
    FROM telemetry_events x
    WHERE x.tenant_id = %s AND x.entity_type = ANY(%s)
      AND x.observed_at >= %s AND x.observed_at < %s
      AND x.entity_id NOT LIKE '%%:parcel-%%' AND x.entity_id NOT LIKE '%%-daily'
      AND x.payload#>>'{{raw,location,value,type}}' = 'Point'
      AND jsonb_typeof(x.payload#>'{{raw,location,value,coordinates,0}}') = 'number'
      AND jsonb_typeof(x.payload#>'{{raw,location,value,coordinates,1}}') = 'number'
      AND {xref} ~ '{pat}'
    ORDER BY x.entity_id, x.observed_at DESC
), near AS (
    SELECT s.entity_id, ST_Distance(s.g, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS d
    FROM st s
    WHERE ST_DWithin(s.g, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
    ORDER BY d, s.entity_id
    LIMIT 1
)
SELECT e.observed_at, near.d AS distance_m,
       (CASE WHEN {ref} ~ '{pat}' THEN ({ref})::double precision END) AS value
FROM near
JOIN telemetry_events e ON e.entity_id = near.entity_id
WHERE e.tenant_id = %s AND e.entity_type = ANY(%s)
  AND e.observed_at >= %s AND e.observed_at < %s
ORDER BY e.observed_at
"""


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parcel_urn_of(feature: Optional[str], feature_entity: Optional[dict]) -> Optional[str]:
    """The AgriParcel behind the request feature, or None (tier 4 is then skipped)."""
    if not feature or not feature_entity:
        return None
    if _short_type(feature_entity.get("type")) == "AgriParcel":
        return feature
    for key in ("hasAgriParcel", "refAgriParcel"):
        rel = feature_entity.get(key)
        if isinstance(rel, dict):
            target = rel.get("object") or rel.get("value")
            if isinstance(target, str) and target:
                return target
    return None


def model_tiers(cur, tenant_id: str, req: BestRequest, prop: dict,
                parcel_urn: Optional[str],
                point: Optional[Tuple[float, float]]) -> Dict[int, List[Reading]]:
    """Tiers 4 (parcel model) and 5 (regional proxy); both keys always present.

    Only when `prop.has_model` and `req.tiers == "all"`. Tier 4 needs `parcel_urn`. Tier 5 needs
    `point` = (lon, lat) and searches the nearest non-parcel WeatherObserved within
    min(nearby_radius_m * 10, 50 km); it is empty until a regional source is ingested. Precip-
    itation values are per-period amounts: aggregate tiers 4/5 with `model_semantics(prop)`.
    """
    out: Dict[int, List[Reading]] = {4: [], 5: []}
    if not prop.get("has_model") or req.tiers != "all":
        return out
    key = _MODEL_KEYS.get(prop.get("name") or req.property)
    if key is None:
        return out
    bks = buckets(req.start, req.end, req.resolution, req.tz)
    w0, w1 = bks[0][0], bks[-1][1]
    ref = f"e.payload->'measurements'->>'{key}'"  # key from the constant map above
    types = list(WEATHER_OBSERVED_ENTITY_TYPES)

    if parcel_urn:
        bare = parcel_urn.rsplit(":", 1)[-1]
        suffix = "-daily" if req.resolution == "P1D" else ""
        pattern = "%:parcel-" + _like_escape(bare) + suffix
        cur.execute(_MODEL_SQL.format(ref=ref, pat=_NUMERIC_TEXT_PATTERN),
                    (tenant_id, types, pattern, w0, w1))
        for r in cur.fetchall():
            if r["value"] is None:
                continue
            out[4].append(Reading(
                source_id=MODEL_SOURCE_ID, deployment_id=None, t=r["observed_at"],
                value=float(r["value"]), sub_context={}, distance_m=None,
                kind="model", calibrated=False,
            ))

    radius = min((_num(prop.get("nearby_radius_m")) or 0.0) * 10, MAX_REGIONAL_RADIUS_M)
    if point is not None and radius > 0:
        lon, lat = float(point[0]), float(point[1])
        cur.execute(_REGIONAL_SQL.format(ref=ref, xref=ref.replace('e.payload', 'x.payload'),
                                         pat=_NUMERIC_TEXT_PATTERN),
                    (tenant_id, types, w0, w1, lon, lat, lon, lat, radius,
                     tenant_id, types, w0, w1))
        for r in cur.fetchall():
            if r["value"] is None:
                continue
            out[5].append(Reading(
                source_id=REGIONAL_SOURCE_ID, deployment_id=None, t=r["observed_at"],
                value=float(r["value"]), sub_context={},
                distance_m=float(r["distance_m"]), kind="model", calibrated=False,
            ))
    return out
