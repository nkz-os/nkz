"""Source planning and data fetch of the `best` observation service (contract 2.1, 2.2, 8.1, 8.4).

Turns a request into `Reading` lists per tier for the Task 12 engine:
  1 direct (deployment observes the feature), 2 contained (inside the area), 3 nearby (radius),
  4 parcel model, 5 regional model (nothing ingested today: always empty).

Fetch-window contract (engine docstring): [buckets[0][0], buckets[-1][1]) plus, for cumulative
properties, the last valid reading of every (device, attribute) before the window (it supplies
the first delta).

Transactions: `observed_tiers` and `model_tiers` run on a `tenant_cursor` (tenant already set).
`geometry_of` may write the geometry cache, so the caller resolves geometries FIRST in their own
short transaction and commits, then opens the read transaction (Task 11 review ruling).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from common.context_aliases import canonical_attribute
from urn_resolution import WEATHER_OBSERVED_ENTITY_TYPES

from .engine import Reading, buckets
from .request import BestRequest, RequestError

logger = logging.getLogger(__name__)

GREENHOUSE_TYPES = frozenset({"AgriGreenhouse", "Greenhouse"})
PRIOR_LOOKBACK = timedelta(days=2)       # cumulative: how far back the pre-window reading is sought
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
    feature = ov.get("observesFeature") or dep["observes_feature"] or None
    return ctx.get("medium"), feature


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
       m.deployment_id::text AS deployment_id, m.source_kind,
       m.depth_from_cm, m.depth_to_cm, m.height_m, m.organ, m.context_ref,
       COALESCE((e.payload->'calibration_period_ids') ? m.attribute_name, false) AS calibrated,
       COALESCE(ST_Covers((SELECT g FROM area), m.location), false) AS r_in_area,
       ST_Distance((SELECT g FROM area), m.location) AS r_dist_m"""

_VALUE_FROM = """FROM telemetry_measurements m
LEFT JOIN telemetry_events e
       ON e.tenant_id = m.tenant_id AND e.observed_at = m.observed_at
      AND e.id = m.source_event_id"""

# tenant_id and attribute_name are compression segmentby keys: always present (contract 8.1).
_VALUE_FILTER = """m.tenant_id = %s
  AND m.attribute_name = ANY(%s)
  AND m.device_id = ANY(%s)
  AND m.observed_property = %s
  AND m.medium = %s
  AND m.deployment_id IS NOT NULL
  AND m.deployment_id = ANY(%s::uuid[])
  AND m.quality_flag = 'valid'"""

_VALUES_SQL = (
    "-- obs:values\nWITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)\n"
    f"SELECT {_VALUE_COLUMNS}\n{_VALUE_FROM}\nWHERE {_VALUE_FILTER}\n"
    "  AND m.observed_at >= %s AND m.observed_at < %s\nORDER BY m.observed_at"
)

_PRIOR_SQL = (
    "-- obs:prior\nWITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)\n"
    f"SELECT DISTINCT ON (m.device_id, m.attribute_name) {_VALUE_COLUMNS}\n{_VALUE_FROM}\n"
    f"WHERE {_VALUE_FILTER}\n"
    "  AND m.observed_at >= %s AND m.observed_at < %s\n"
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

class _Plan:
    """Tier assignment of one deployment: fixed tier, or per-reading ('mobile')."""

    __slots__ = ("dep", "urn", "tier", "distance_m")

    def __init__(self, dep: dict, tier: Optional[int], distance_m: Optional[float] = None):
        self.dep = dep
        self.urn = dep["device_urn"]
        self.tier = tier  # None = mobile, classified per reading
        self.distance_m = distance_m


def _plan_deployments(cur, tenant_id: str, req: BestRequest, prop: dict, medium: str,
                      attrs: List[str], area_geom: Optional[dict], w0, w1) -> Dict[str, _Plan]:
    area_json = json.dumps(area_geom) if area_geom else None
    cur.execute(_DEPLOYMENTS_SQL, (area_json, tenant_id, w0, w1))
    deps = [dict(r) for r in cur.fetchall()]

    radius = _num(prop.get("nearby_radius_m")) or 0.0
    cand: List[Tuple[dict, Set[str]]] = []
    for dep in deps:
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
            cand.append((dep, feats))

    covered: Set[str] = set()
    if area_geom is not None:
        probe = sorted({f for _, feats in cand for f in feats if f != req.feature})
        if probe:
            cur.execute(_COVERED_SQL, (tenant_id, probe, area_json))
            covered = {r["entity_urn"] for r in cur.fetchall()}

    plans: Dict[str, _Plan] = {}
    for dep, feats in cand:
        if req.feature is not None and req.feature in feats:
            plans[dep["id"]] = _Plan(dep, 1)
        elif area_geom is None:
            continue
        elif dep["mobility"] == "mobile":
            plans[dep["id"]] = _Plan(dep, None)
        elif dep.get("in_area") or (feats & covered):
            plans[dep["id"]] = _Plan(dep, 2)
        elif radius > 0 and dep.get("dist_m") is not None and float(dep["dist_m"]) <= radius:
            plans[dep["id"]] = _Plan(dep, 3, float(dep["dist_m"]))
    return plans


def _reading_tier(plan: _Plan, row: dict, radius: float) -> Tuple[Optional[int], Optional[float]]:
    """(tier, distance_m) of one reading; mobile deployments use the reading's own location."""
    if plan.tier is not None:
        return plan.tier, plan.distance_m
    if row.get("r_in_area"):
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
                   area_geom: Optional[dict]) -> Dict[int, List[Reading]]:
    """Readings of tiers 1 (direct), 2 (contained), 3 (nearby); every key always present.

    `area_geom` is the GeoJSON of the request feature (geometry_of, resolved beforehand) or
    `req.geometry`. With no area only tier 1 can be populated. `cur` is a tenant_cursor.
    """
    out: Dict[int, List[Reading]] = {1: [], 2: [], 3: []}
    bks = buckets(req.start, req.end, req.resolution, req.tz)
    w0, w1 = bks[0][0], bks[-1][1]

    attrs = _property_attrs(cur, tenant_id, req.property)
    if not attrs:
        return out
    plans = _plan_deployments(cur, tenant_id, req, prop, medium, attrs, area_geom, w0, w1)
    if not plans:
        return out

    area_json = json.dumps(area_geom) if area_geom else None
    device_ids = sorted({_device_id(p.urn) for p in plans.values()})
    dep_ids = sorted(plans)
    base = (tenant_id, attrs, device_ids, req.property, medium, dep_ids)

    cur.execute(_VALUES_SQL, (area_json,) + base + (w0, w1))
    rows = [dict(r) for r in cur.fetchall()]
    cumulative = prop.get("temporal_semantics") == "cumulative"
    cmax: Dict[Tuple[str, str], float] = {}
    if cumulative:
        cur.execute(_PRIOR_SQL, (area_json,) + base + (w0 - PRIOR_LOOKBACK, w0))
        rows = [dict(r) for r in cur.fetchall()] + rows
        cmax = _counter_maxes(cur, tenant_id, req.property, device_ids, w0, w1)

    radius = _num(prop.get("nearby_radius_m")) or 0.0
    for row in rows:
        plan = plans.get(row["deployment_id"])
        if plan is None:
            continue
        tier, dist = _reading_tier(plan, row, radius)
        if tier is None:
            continue
        sub = _sub_context(row)
        if not _matches_request_sub_context(sub, req.sub_context, prop):
            continue
        out[tier].append(Reading(
            source_id=plan.urn,
            deployment_id=row["deployment_id"],
            t=row["observed_at"],
            value=float(row["value"]),
            sub_context=sub,
            distance_m=dist if tier == 3 else None,
            kind=row.get("source_kind") or "device",
            calibrated=bool(row.get("calibrated")),
            counter_max=cmax.get((row["device_id"], row["attribute_name"])) if cumulative else None,
        ))
    return out


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

    Only when `prop.has_model` and `req.tiers == "all"`. Tier 5 is empty today (no regional
    WeatherObserved source is ingested; contract 8.1). Precipitation values are per-period
    amounts: aggregate tiers 4/5 with `model_semantics(prop)`.
    """
    out: Dict[int, List[Reading]] = {4: [], 5: []}
    if not prop.get("has_model") or req.tiers != "all":
        return out
    key = _MODEL_KEYS.get(prop.get("name") or req.property)
    if key is None or not parcel_urn:
        return out
    bks = buckets(req.start, req.end, req.resolution, req.tz)
    w0, w1 = bks[0][0], bks[-1][1]

    bare = parcel_urn.rsplit(":", 1)[-1]
    suffix = "-daily" if req.resolution == "P1D" else ""
    pattern = "%:parcel-" + _like_escape(bare) + suffix
    ref = f"e.payload->'measurements'->>'{key}'"  # key from the constant map above
    cur.execute(_MODEL_SQL.format(ref=ref, pat=_NUMERIC_TEXT_PATTERN),
                (tenant_id, list(WEATHER_OBSERVED_ENTITY_TYPES), pattern, w0, w1))
    for r in cur.fetchall():
        if r["value"] is None:
            continue
        out[4].append(Reading(
            source_id=MODEL_SOURCE_ID, deployment_id=None, t=r["observed_at"],
            value=float(r["value"]), sub_context={}, distance_m=None,
            kind="model", calibrated=False,
        ))
    return out
