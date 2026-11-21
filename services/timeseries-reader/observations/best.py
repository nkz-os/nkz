"""`best` observation service (contract 2.1, 2.2, 8.4): tier fall-through per sub-context group.

Transactions (Task 11 review ruling): FIRST a short transaction resolves the property, the feature
entity (Orion) and its geometry (`geometry_of` may write the cache) and COMMITS; THEN a read
transaction on a fresh `tenant_cursor` runs every source query and is rolled back. The caller owns
and closes the connection. Client errors are raised as RequestError.

Internal tier keys fed to `fall_through` encode both the tier and where it came from:
  1-3  observed tiers in the resolved medium
  11-13 observed tiers in the fallback medium (ambientAir, allowMediumFallback only)
  24-25 model tiers 4/5 (open-air sources)
tier = key % 10; a point is `mediumFallback` for keys 11-13, and for model keys when the resolved
medium is not ambientAir (then they were only included because of allowMediumFallback).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from common.unit_codes import to_unit_code

from .catalogue import get_property
from .db import tenant_cursor
from .engine import (BucketPoint, Reading, aggregate_tier, buckets, fall_through,
                     group_by_sub_context, latest_point)
from .geometry import fetch_orion_entity_status, geometry_of
from .request import BestRequest, RequestError, iso_z
from .sources import model_semantics, model_tiers, observed_tiers, parcel_urn_of, resolve_medium

logger = logging.getLogger(__name__)

MAX_POINTS_PER_GROUP = 10_000
FALLBACK_MEDIUM = "ambientAir"
# ASSUMPTION: catalogue max_age_s is NULL-able; 3 h (the weather-api freshness default) applies.
DEFAULT_MAX_AGE_S = 10_800
_MANUAL_KINDS = frozenset({"manual", "import"})
_CONFIDENCE = {
    "iot_sensor": "high", "manual_observation": "high",
    "parcel_weather": "medium", "regional_proxy": "low", "unavailable": "low",
}
_FALLBACK_OFFSET = 10
_MODEL_OFFSET = 20


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # connection already broken: the caller closes it
        logger.warning("rollback failed", exc_info=True)


def _point_of(geo: Optional[dict]) -> Optional[Tuple[float, float]]:
    """(lon, lat) = centre of the bounding box of the geometry (input of tier 5).

    ASSUMPTION: the bbox centre represents the area for the regional-station search (the contract
    gives no reference point; a parcel is far smaller than the 50 km search radius).
    """
    if not geo:
        return None
    xs: List[float] = []
    ys: List[float] = []

    def walk(c: Any) -> None:
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    walk(geo.get("coordinates"))
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _fidelity(key: int, sources: List[dict]) -> str:
    tier = key % 10
    if tier == 4:
        return "parcel_weather"
    if tier == 5:
        return "regional_proxy"
    kinds = {s.get("kind") for s in sources}
    # ASSUMPTION: a bucket fusing device and manual/import sources is reported as iot_sensor
    # (contract 2.2 does not rank mixed buckets); only all-manual/import buckets are manual_observation.
    if kinds and kinds <= _MANUAL_KINDS:
        return "manual_observation"
    return "iot_sensor"


def _source_item(s: dict, key: int) -> dict:
    if key % 10 in (4, 5):
        return {"kind": "model", "id": s["source_id"]}
    return {"kind": s["kind"], "id": s["source_id"], "deploymentId": s["deployment_id"],
            "distanceM": s["distance_m"], "n": s["n_readings"]}


def _point_json(key: Optional[int], bp: BucketPoint, medium: str) -> dict:
    if key is None:
        return {"t": iso_z(bp.t), "value": None, "tier": None, "fidelity": "unavailable",
                "confidence": _CONFIDENCE["unavailable"], "calibrated": False,
                "mediumFallback": False, "coverage": 0.0, "sources": []}
    fid = _fidelity(key, bp.sources)
    tier = key % 10
    fallback = (_FALLBACK_OFFSET < key < _MODEL_OFFSET) or (
        key >= _MODEL_OFFSET and medium != FALLBACK_MEDIUM)
    return {"t": iso_z(bp.t), "value": bp.value, "tier": tier, "fidelity": fid,
            "confidence": _CONFIDENCE[fid],
            "calibrated": bool(bp.sources) and all(s.get("calibrated") for s in bp.sources),
            "mediumFallback": fallback, "coverage": round(bp.coverage, 4),
            "sources": [_source_item(s, key) for s in bp.sources]}


def _sub_context_json(sc: dict) -> dict:
    out = {}
    for k, v in sc.items():
        out["heightM" if k == "heightAboveGround" else k] = v  # contract 2.1 example key
    return out


def _resolve_feature(conn, tenant_id: str, req: BestRequest, fetch) -> Tuple[dict, Optional[dict], Optional[dict]]:
    """Transaction 1 (committed): (property, feature entity, area geometry)."""
    entity: Optional[dict] = None
    area = req.geometry
    status = "ok"
    if req.feature:  # Orion first: transaction 1 stays short (no network inside it)
        status, entity = fetch(tenant_id, req.feature)
    try:
        with tenant_cursor(conn, tenant_id) as cur:
            prop = get_property(cur, tenant_id, req.property)
            if req.feature:
                # the same (single) Orion answer feeds the geometry cache, incl. its 404 delete
                area = geometry_of(cur, tenant_id, req.feature,
                                   fetch=lambda _t, _u: (status, entity))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    if status == "not_found":
        raise RequestError(404, "feature not found")
    if status != "ok":
        logger.warning("Orion unavailable for feature: tenant_id=%s feature=%s", tenant_id, req.feature)
        raise RequestError(503, "feature lookup unavailable")
    return prop, entity, area


def best(conn, tenant_id: str, req: BestRequest, *, now: Optional[datetime] = None,
         fetch: Optional[Any] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_orion_entity_status
    prop, entity, area = _resolve_feature(conn, tenant_id, req, fetch)
    medium = resolve_medium(req, prop, entity)

    latest = req.mode == "latest"
    q = req
    if latest:
        max_age = int(prop.get("max_age_s") or DEFAULT_MAX_AGE_S)
        q = replace(req, start=now - timedelta(seconds=max_age), end=now, resolution="raw")
    bks = buckets(q.start, q.end, q.resolution, q.tz)
    if len(bks) > MAX_POINTS_PER_GROUP:
        raise RequestError(413, "too many points: lower the resolution or narrow the period")

    use_fallback = (req.allow_medium_fallback and medium != FALLBACK_MEDIUM
                    and FALLBACK_MEDIUM in (prop.get("allowed_media") or []))
    use_model = (bool(prop.get("has_model")) and req.tiers == "all"
                 and (medium == FALLBACK_MEDIUM or req.allow_medium_fallback))

    tier_readings: Dict[int, List[Reading]] = {}
    try:
        with tenant_cursor(conn, tenant_id) as cur:
            for t, rs in observed_tiers(cur, tenant_id, q, prop, medium, area).items():
                tier_readings[t] = list(rs)
            if use_fallback:
                for t, rs in observed_tiers(cur, tenant_id, q, prop, FALLBACK_MEDIUM, area).items():
                    tier_readings[_FALLBACK_OFFSET + t] = list(rs)
            if use_model:
                mt = model_tiers(cur, tenant_id, q, prop, parcel_urn_of(req.feature, entity),
                                 _point_of(area))
                for t, rs in mt.items():
                    tier_readings[_MODEL_OFFSET + t] = list(rs)
    finally:
        _rollback(conn)  # read-only transaction

    warnings: List[str] = []
    try:
        unit_code: Optional[str] = to_unit_code(prop["unit"])
    except ValueError:
        unit_code = None
        warnings.append(f"unknown_unit:{prop.get('unit')}")

    observed_keys = sorted(k for k in tier_readings if k < _MODEL_OFFSET)
    model_keys = sorted(k for k in tier_readings if k >= _MODEL_OFFSET)
    tolerance = prop.get("sub_context_tolerance") or {}
    # Groups come from the primary-medium readings only; fallback-medium and model tiers are merged
    # into EACH group's fall-through, so the fallback fills only that group's empty buckets.
    primary = [r for k in observed_keys if k < _FALLBACK_OFFSET for r in tier_readings[k]]
    groups = group_by_sub_context(primary, tolerance)
    fallback_grouped = False
    if not groups:
        fallback_grouped = True  # no primary data: the fallback readings define the groups
        groups = group_by_sub_context(
            [r for k in observed_keys for r in tier_readings[k]], tolerance)
    if not groups:
        groups = [({}, [])]

    sem = prop.get("temporal_semantics") or "instant"
    msem = model_semantics(prop) or sem
    out_groups: List[dict] = []
    for sc, members in groups:
        ids = {id(r) for r in members}
        tier_points: List[Tuple[int, List[BucketPoint]]] = []
        latest_points: List[Tuple[int, BucketPoint]] = []
        for k in observed_keys + model_keys:
            rs = tier_readings[k]
            if k < _FALLBACK_OFFSET or (k < _MODEL_OFFSET and fallback_grouped):
                rs = [r for r in rs if id(r) in ids]
            if not rs:
                continue
            if latest:  # contract 8.9: each source's newest valid value, fused
                lp = latest_point(rs, sem if k < _MODEL_OFFSET else msem,
                                  prop.get("fusion") or "median", prop.get("valid_min"),
                                  prop.get("valid_max"),
                                  max_rate_per_hour=prop.get("max_rate_per_hour"),
                                  warnings=warnings)
                if lp is not None:
                    latest_points.append((k, lp))
                continue
            tier_points.append((k, aggregate_tier(
                rs, bks, sem if k < _MODEL_OFFSET else msem, prop.get("fusion") or "median",
                prop.get("valid_min"), prop.get("valid_max"), q.resolution,
                max_rate_per_hour=prop.get("max_rate_per_hour"), warnings=warnings, now=now,
                # P1D tier 4 reads the one-row-per-day twin: a present row is a full day
                pre_aggregated=(k == _MODEL_OFFSET + 4 and q.resolution == "P1D"))))
        if latest:  # the first tier, in fall-through order, with any valid value
            chosen = sorted(latest_points, key=lambda x: x[0])[:1]
        else:
            chosen = fall_through(tier_points, prop.get("min_coverage"), q.resolution)
            if q.resolution != "raw" and not chosen:
                chosen = [(None, BucketPoint(t=b0, value=None, n_sources=0, coverage=0.0,
                                             sources=[]))
                          for b0, _ in bks]
        points = [_point_json(k, p, medium) for k, p in chosen]
        if len(points) > MAX_POINTS_PER_GROUP:
            raise RequestError(413, "too many points: lower the resolution or narrow the period")
        out_groups.append({"subContext": _sub_context_json(sc), "points": points})

    tiers_used = sorted({p["tier"] for g in out_groups for p in g["points"] if p["tier"] is not None})
    seen = set()
    uniq_warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    return {
        "feature": req.feature, "property": req.property,
        "unitCode": unit_code, "unitSymbol": prop.get("unit"),
        "medium": medium, "mode": req.mode, "resolution": req.resolution, "tz": req.tz,
        "from": iso_z(req.start), "to": iso_z(req.end),
        "groups": out_groups, "tiersUsed": tiers_used, "warnings": uniq_warnings,
    }
