"""Pure aggregation and fusion engine of the `best` observation service.

No I/O. Contract 2.2 and 8.4; design 6.2 (sub-contexts never fused) and 6.3 (counter rule).
All datetimes are timezone-aware; output datetimes are UTC.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

UTC = timezone.utc
DEFAULT_MIN_COVERAGE = 0.5
KNOWN_FUSIONS = frozenset({"median"})
_STEP = {"PT1H": timedelta(hours=1), "PT15M": timedelta(minutes=15)}
_HOUR = timedelta(hours=1)
_EPS = 1e-9
_HEIGHT_KEYS = ("heightAboveGround", "heightM")
_EXACT_KEYS = ("depthFrom", "depthTo", "organ", "contextRef")


@dataclass(frozen=True)
class Reading:
    source_id: str
    deployment_id: Optional[str]
    t: datetime
    value: float
    sub_context: dict
    distance_m: Optional[float]
    kind: str
    calibrated: bool


@dataclass(frozen=True)
class BucketPoint:
    t: datetime
    value: Optional[float]
    n_sources: int
    coverage: float
    sources: List[dict]


# ---------------------------------------------------------------- buckets

def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return dt.astimezone(UTC)


def _local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


def buckets(start: datetime, end: datetime, resolution: str, tz: str) -> List[Tuple[datetime, datetime]]:
    """Half-open buckets [b0, b1) covering [start, end).

    P1D: local midnight in `tz` (23/24/25 h on DST days); the first bucket is the one containing
    `start`. PT1H / PT15M: aligned to the UTC hour / quarter. raw: a single window (start, end);
    aggregate_tier then makes one point per reading timestamp.
    """
    start, end = _utc(start), _utc(end)
    if resolution == "raw":
        return [(start, end)]
    out: List[Tuple[datetime, datetime]] = []
    if resolution == "P1D":
        zone = ZoneInfo(tz)
        day = start.astimezone(zone).date()
        b0 = _local_midnight_utc(day, zone)
        while b0 < end:
            day = day + timedelta(days=1)
            b1 = _local_midnight_utc(day, zone)
            out.append((b0, b1))
            b0 = b1
        return out
    if resolution in _STEP:
        step = _STEP[resolution]
        secs = int(step.total_seconds())
        epoch = int(start.timestamp())
        b0 = datetime.fromtimestamp(epoch - epoch % secs, tz=UTC)
        while b0 < end:
            out.append((b0, b0 + step))
            b0 += step
        return out
    raise ValueError(f"unknown resolution: {resolution}")


# ---------------------------------------------------------- sub-context

def _height_key(sub: dict) -> Optional[str]:
    for k in _HEIGHT_KEYS:
        if sub.get(k) is not None:
            return k
    return None


def _sort_key(v: Any) -> Tuple[int, Any]:
    return (0, 0) if v is None else (1, str(v) if not isinstance(v, (int, float)) else v)


def group_by_sub_context(readings: List[Reading], tolerance: Dict[str, float]) -> List[Tuple[dict, List[Reading]]]:
    """Group readings; depth range, organ and contextRef are exact, height is clustered.

    Height clusters are anchored on the lowest member: a height joins the cluster while it is
    within `tolerance[heightKey]` of that anchor. The reported sub_context holds the exact fields
    present plus the anchor (minimum) height. Groups are sorted deterministically.
    """
    tolerance = tolerance or {}
    parts: Dict[tuple, List[Reading]] = {}
    for r in readings:
        sub = r.sub_context or {}
        key = tuple(sub.get(k) for k in _EXACT_KEYS)
        parts.setdefault(key, []).append(r)
    groups: List[Tuple[tuple, dict, List[Reading]]] = []
    for key, members in parts.items():
        base = {k: v for k, v in zip(_EXACT_KEYS, key) if v is not None}
        with_h = [(m, _height_key(m.sub_context or {})) for m in members]
        no_h = [m for m, hk in with_h if hk is None]
        if no_h:
            groups.append((key + (None,), dict(base), no_h))
        hs = sorted(((m.sub_context[hk], hk, m) for m, hk in with_h if hk is not None),
                    key=lambda x: (x[0], x[1], x[2].source_id, x[2].t))
        i = 0
        while i < len(hs):
            anchor, hk, _ = hs[i]
            tol = tolerance.get(hk, tolerance.get("heightAboveGround", 0.0)) or 0.0
            j = i
            cluster = []
            while j < len(hs) and hs[j][0] - anchor <= tol + _EPS:
                cluster.append(hs[j][2])
                j += 1
            sc = dict(base)
            sc[hk] = anchor
            groups.append((key + (anchor,), sc, cluster))
            i = j
    groups.sort(key=lambda g: tuple(_sort_key(x) for x in g[0]))
    return [(sc, ms) for _, sc, ms in groups]


# ------------------------------------------------------------ counters

def counter_deltas(readings: List[Reading], counter_max: Optional[float],
                   max_rate_per_hour: Optional[float]) -> Tuple[List[Reading], List[str]]:
    """Per source, sorted by t: turn a cumulative counter into increments (design 6.3).

    A delta is attributed to the later reading. Negative delta: wrap-around if the previous value
    was within one plausible increment of counter_max (max_rate_per_hour x elapsed hours; without a
    max rate: previous >= 0.9 x counter_max); otherwise a reset, where the current value counts as
    the delta if plausible (current <= max_rate x elapsed; no max rate: accepted), else discarded.
    """
    by_src: Dict[str, List[Reading]] = {}
    for r in readings:
        by_src.setdefault(r.source_id, []).append(r)
    out: List[Reading] = []
    warns: List[str] = []
    for src in sorted(by_src):
        seq = sorted(by_src[src], key=lambda r: r.t)
        for prev, cur in zip(seq, seq[1:]):
            delta = cur.value - prev.value
            if delta >= 0:
                out.append(replace(cur, value=delta))
                continue
            hours = max((cur.t - prev.t).total_seconds() / 3600.0, 0.0)
            wrap = False
            if counter_max is not None:
                if max_rate_per_hour is not None:
                    wrap = (counter_max - prev.value) <= max_rate_per_hour * hours
                else:
                    wrap = prev.value >= 0.9 * counter_max
            if wrap:
                out.append(replace(cur, value=counter_max - prev.value + cur.value))
                continue
            stamp = f"{src}@{cur.t.astimezone(UTC).isoformat()}"
            if max_rate_per_hour is None or cur.value <= max_rate_per_hour * hours:
                warns.append(f"counter_reset:{stamp}")
                out.append(replace(cur, value=cur.value))
            else:
                warns.append(f"counter_reset_discarded:{stamp}")
    return out, warns


# ----------------------------------------------------------- aggregate

def _fuse(values: List[float], fusion: str, warnings: Optional[List[str]]) -> float:
    if fusion not in KNOWN_FUSIONS and warnings is not None:
        warnings.append(f"unknown_fusion:{fusion}")
    # median; with fewer than 3 values the median is the mean
    return statistics.median(values)


def _warn(warnings: Optional[List[str]], msg: str) -> None:
    if warnings is not None:
        warnings.append(msg)


def _empty(t: datetime) -> BucketPoint:
    return BucketPoint(t=t, value=None, n_sources=0, coverage=0.0, sources=[])


def aggregate_tier(readings: List[Reading], bucket_list: List[Tuple[datetime, datetime]],
                   semantics: str, fusion: str, valid_min: Optional[float],
                   valid_max: Optional[float], resolution: str, *,
                   counter_max: Optional[float] = None,
                   max_rate_per_hour: Optional[float] = None,
                   warnings: Optional[List[str]] = None) -> List[BucketPoint]:
    """One BucketPoint per bucket (per distinct reading timestamp for `raw`).

    Extra keyword args (not in the frozen positional signature): counter parameters for
    `cumulative`, and `warnings`, a list the engine appends warning strings to.
    """
    cumulative = semantics == "cumulative"
    if cumulative:
        items, cwarns = counter_deltas(readings, counter_max, max_rate_per_hour)
        for w in cwarns:
            _warn(warnings, w)
    else:
        items = list(readings)

    kept: List[Reading] = []
    for r in items:
        bad = (valid_min is not None and r.value < valid_min) or (valid_max is not None and r.value > valid_max)
        if bad:
            tag = "increment_out_of_range" if cumulative else "reading_out_of_range"
            _warn(warnings, f"{tag}:{r.source_id}@{r.t.astimezone(UTC).isoformat()}")
            continue
        kept.append(r)

    if resolution == "raw":
        return _aggregate_raw(kept, bucket_list, cumulative, fusion, warnings)

    out: List[BucketPoint] = []
    for b0, b1 in bucket_list:
        in_b = [r for r in kept if b0 <= r.t < b1]
        if not in_b:
            out.append(_empty(b0))
            continue
        out.append(_point(b0, in_b, cumulative, fusion, warnings,
                          coverage=_coverage(b0, b1, in_b, resolution)))
    return out


def _coverage(b0: datetime, b1: datetime, in_b: List[Reading], resolution: str) -> float:
    if resolution != "P1D":
        return 1.0 if in_b else 0.0
    expected = max(round((b1 - b0) / _HOUR), 1)
    hours = {int((r.t - b0) / _HOUR) for r in in_b}
    return len(hours) / expected


def _point(t: datetime, in_b: List[Reading], cumulative: bool, fusion: str,
           warnings: Optional[List[str]], coverage: float) -> BucketPoint:
    per_src: Dict[str, List[Reading]] = {}
    for r in in_b:
        per_src.setdefault(r.source_id, []).append(r)
    srcs: List[dict] = []
    for sid in sorted(per_src):
        rs = per_src[sid]
        vals = [r.value for r in rs]
        v = sum(vals) if cumulative else sum(vals) / len(vals)
        first = rs[0]
        srcs.append({
            "source_id": sid, "deployment_id": first.deployment_id, "value": v,
            "n_readings": len(rs), "distance_m": first.distance_m, "kind": first.kind,
            "calibrated": all(r.calibrated for r in rs),
        })
    fused = _fuse([s["value"] for s in srcs], fusion, warnings)
    return BucketPoint(t=t, value=fused, n_sources=len(srcs), coverage=coverage, sources=srcs)


def _aggregate_raw(kept: List[Reading], bucket_list, cumulative: bool, fusion: str,
                   warnings: Optional[List[str]]) -> List[BucketPoint]:
    lo = min(b[0] for b in bucket_list) if bucket_list else None
    hi = max(b[1] for b in bucket_list) if bucket_list else None
    by_t: Dict[datetime, List[Reading]] = {}
    for r in kept:
        if lo is not None and not (lo <= r.t < hi):
            continue
        by_t.setdefault(r.t.astimezone(UTC), []).append(r)
    return [_point(t, by_t[t], cumulative, fusion, warnings, coverage=1.0) for t in sorted(by_t)]


# --------------------------------------------------------- fall-through

def fall_through(tiers: List[Tuple[int, List[BucketPoint]]], min_coverage: Optional[float],
                 resolution: str) -> List[Tuple[Optional[int], BucketPoint]]:
    """Per bucket pick the first (lowest) tier whose point is covered.

    P1D: covered when coverage >= min_coverage (None -> 0.5). Other resolutions: covered when
    the point has a value (>= 1 valid reading); min_coverage does not apply (contract 8.4).
    Buckets are matched by t across tiers; none covered -> (None, unavailable point).
    """
    threshold = DEFAULT_MIN_COVERAGE if min_coverage is None else min_coverage
    ordered = sorted(tiers, key=lambda x: x[0])
    index = [(tier, {p.t: p for p in pts}) for tier, pts in ordered]
    all_t = sorted({p.t for _, pts in ordered for p in pts})
    out: List[Tuple[Optional[int], BucketPoint]] = []
    for t in all_t:
        chosen: Tuple[Optional[int], BucketPoint] = (None, _empty(t))
        for tier, by_t in index:
            p = by_t.get(t)
            if p is None or p.value is None:
                continue
            ok = p.coverage >= threshold - _EPS if resolution == "P1D" else p.coverage > 0
            if ok:
                chosen = (tier, p)
                break
        out.append(chosen)
    return out
