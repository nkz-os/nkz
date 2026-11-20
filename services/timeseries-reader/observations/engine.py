"""Pure aggregation and fusion engine of the `best` observation service.

No I/O. Contract 2.2 and 8.4; design 6.2 (sub-contexts never fused) and 6.3 (counter rule).
All datetimes are timezone-aware; output datetimes are UTC.

Caller contract: fetch readings for [buckets[0][0], buckets[-1][1]) plus, for every cumulative
source, the last reading before that window (it supplies the first delta). P1D expected hours
are the full local day, except the bucket containing `now` (partial day): ceil((now-b0)/1h).

Folded readings (contract 8.10, instant/rate only): one Reading may stand for `n` raw readings of
one source that SQL already grouped inside one grain (a bucket, or one hour from b0 for P1D):
`value_sum` is the sum of the n in-range values, `t` the first of them, and `oor_n` / `oor_first`
/ `oor_last` count the out-of-range readings of the same group (excluded from n). n == 0 carries
only out-of-range statistics (it still defines its sub-context group). A Reading with the
defaults (n=1, value_sum None, oor_n 0) is a plain raw reading.
"""
from __future__ import annotations

import math
import statistics
import bisect
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
_LONG_GAP = timedelta(hours=6)


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
    counter_max: Optional[float] = None  # per-source counter ceiling; wins over the call-level one
    n: int = 1                           # raw readings folded in (see module doc); 0 = oor only
    value_sum: Optional[float] = None    # sum of the n in-range values; None = single raw reading
    oor_n: int = 0                       # out-of-range readings folded in (not counted in n)
    oor_first: Optional[datetime] = None
    oor_last: Optional[datetime] = None


def _folded(r: Reading) -> bool:
    return r.value_sum is not None or r.n != 1 or r.oor_n > 0


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


def _val_key(v: Any) -> Tuple[int, float, str, str]:
    """Total-order, hashable key for values of any type (numbers numerically, rest by type+str)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, float(v), "", "")
    return (1, 0.0, type(v).__name__, str(v))


def group_by_sub_context(readings: List[Reading], tolerance: Dict[str, float]) -> List[Tuple[dict, List[Reading]]]:
    """Group readings; every non-height sub-context key is exact, height is clustered.

    The partition key is the sorted tuple of all non-height (key, value) pairs (None = absent), so
    different contexts of any kind (depth range, organ, contextRef, structureRef, ...) never fuse.
    Height clusters are anchored on the lowest member: a height joins the cluster while it is
    within `tolerance[heightKey]` of that anchor. The reported sub_context holds the exact fields
    present plus the anchor (minimum) height. Groups are sorted deterministically.
    """
    tolerance = tolerance or {}
    parts: Dict[tuple, Tuple[dict, List[Reading]]] = {}
    for r in readings:
        sub = r.sub_context or {}
        items = sorted((k, v) for k, v in sub.items() if k not in _HEIGHT_KEYS and v is not None)
        key = tuple((k,) + _val_key(v) for k, v in items)
        parts.setdefault(key, (dict(items), []))[1].append(r)
    groups: List[Tuple[tuple, tuple, dict, List[Reading]]] = []
    for key, (base, members) in parts.items():
        with_h = [(m, _height_key(m.sub_context or {})) for m in members]
        no_h = [m for m, hk in with_h if hk is None]
        if no_h:
            groups.append((key, (0, 0.0), dict(base), no_h))
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
            groups.append((key, (1, float(anchor)), sc, cluster))
            i = j
    groups.sort(key=lambda g: (g[0], g[1]))  # uniform shapes: 5-tuples, then (0|1, float)
    return [(sc, ms) for _, _, sc, ms in groups]


# ------------------------------------------------------------ counters

def _deltas(readings: List[Reading], counter_max: Optional[float],
            max_rate_per_hour: Optional[float]) -> Tuple[List[Tuple[Reading, datetime]], List[tuple]]:
    """Counter rule; returns (delta reading attributed to the later t, previous t) pairs."""
    by_src: Dict[str, Dict[datetime, Reading]] = {}
    for r in readings:
        slot = by_src.setdefault(r.source_id, {})
        old = slot.get(r.t)
        if old is None or r.value > old.value:  # duplicate (source, t): keep the max value
            slot[r.t] = r
    out: List[Tuple[Reading, datetime]] = []
    warns: List[tuple] = []
    for src in sorted(by_src):
        seq = [by_src[src][t] for t in sorted(by_src[src])]
        for prev, cur in zip(seq, seq[1:]):
            elapsed = (cur.t - prev.t).total_seconds()
            if elapsed <= 0:
                continue
            hours = elapsed / 3600.0
            delta = cur.value - prev.value
            if delta >= 0:
                out.append((replace(cur, value=delta), prev.t))
                continue
            cmax = cur.counter_max if cur.counter_max is not None else (
                prev.counter_max if prev.counter_max is not None else counter_max)
            wrap = False
            if cmax is not None:
                wrapped = cmax - prev.value + cur.value
                if prev.value <= cmax and wrapped >= 0:
                    if max_rate_per_hour is not None:
                        wrap = wrapped <= max_rate_per_hour * hours
                    else:
                        wrap = prev.value >= 0.9 * cmax and cur.value < 0.1 * cmax
                if wrap:
                    out.append((replace(cur, value=wrapped), prev.t))
                    continue
            if max_rate_per_hour is None or cur.value <= max_rate_per_hour * hours:
                warns.append(("counter_reset", src, cur.t))
                out.append((replace(cur, value=cur.value), prev.t))
            else:
                warns.append(("counter_reset_discarded", src, cur.t))
    return out, warns


def counter_deltas(readings: List[Reading], counter_max: Optional[float],
                   max_rate_per_hour: Optional[float]) -> Tuple[List[Reading], List[str]]:
    """Per source, sorted by t: turn a cumulative counter into increments (design 6.3).

    A delta is attributed to the later reading. Duplicate (source, t) readings keep the max value;
    pairs with elapsed <= 0 are skipped. Negative delta: wrap-around only if the wrapped increment
    (counter_max - previous + current) <= max_rate x elapsed hours (without a max rate: previous >=
    0.9 x counter_max and current < 0.1 x counter_max); otherwise a reset, where the current value
    counts as the delta if plausible (current <= max_rate x elapsed; no max rate: accepted), else
    discarded. A Reading.counter_max wins over the `counter_max` argument (fallback).
    """
    pairs, warns = _deltas(readings, counter_max, max_rate_per_hour)
    return [d for d, _ in pairs], [_fmt_one(w) for w in warns]


# ----------------------------------------------------------- aggregate

def _fuse(values: List[float], fusion: str, warnings: List[tuple]) -> float:
    if fusion not in KNOWN_FUSIONS:
        warnings.append((f"unknown_fusion:{fusion}", None, None))
    # median; with fewer than 3 values the median is the mean
    return statistics.median(values)


def _empty(t: datetime) -> BucketPoint:
    return BucketPoint(t=t, value=None, n_sources=0, coverage=0.0, sources=[])


Pair = Tuple[Reading, Optional[datetime]]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_one(w: tuple) -> str:
    kind, src, t = w
    return f"{kind}:{src}@{t.astimezone(UTC).isoformat()}"


def _dedupe_warnings(ws: List[tuple]) -> List[str]:
    """One entry per (warning type, source) with a count and first/last timestamp.

    Entries are (kind, src, t) for one occurrence, or (kind, src, t_first, t_last, n) for n
    occurrences already counted (folded readings); both merge into the same message.
    """
    groups: Dict[Tuple[str, Optional[str]], List[Any]] = {}
    for w in ws:
        kind, src = w[0], w[1]
        lo, hi, n = (w[2], w[3], w[4]) if len(w) == 5 else (w[2], w[2], 1)
        g = groups.setdefault((kind, src), [0, None, None])
        g[0] += n
        if lo is not None:
            g[1] = lo if g[1] is None else min(g[1], lo)
        if hi is not None:
            g[2] = hi if g[2] is None else max(g[2], hi)
    out: List[str] = []
    for (kind, src), (count, t_lo, t_hi) in groups.items():
        msg = kind if src is None else f"{kind} src={src}"
        if count > 1:
            msg += f" x{count}"
        if t_lo is not None:
            lo, hi = _iso(t_lo), _iso(t_hi)
            msg += f" ({lo})" if lo == hi else f" ({lo}..{hi})"
        out.append(msg)
    return out


def _folded_oor_warnings(rs: List[Reading], ws: List[tuple]) -> None:
    """reading_out_of_range entries of folded readings, in order of first occurrence."""
    oor = sorted(((r.oor_first, r.source_id, r.oor_last, r.oor_n) for r in rs if r.oor_n > 0),
                 key=lambda x: (x[0], x[1]))
    for t_lo, src, t_hi, n in oor:
        ws.append(("reading_out_of_range", src, t_lo, t_hi, n))


def aggregate_tier(readings: List[Reading], bucket_list: List[Tuple[datetime, datetime]],
                   semantics: str, fusion: str, valid_min: Optional[float],
                   valid_max: Optional[float], resolution: str, *,
                   counter_max: Optional[float] = None,
                   max_rate_per_hour: Optional[float] = None,
                   warnings: Optional[List[str]] = None,
                   now: Optional[datetime] = None,
                   pre_aggregated: bool = False) -> List[BucketPoint]:
    """One BucketPoint per bucket (per distinct reading timestamp for `raw`).

    semantics: instant | rate (mean per source), cumulative (counter rule, then sum of deltas),
    amount (plain sum of readings per source, no counter rule). For cumulative and amount the range
    check applies to each source's per-bucket sum; an out-of-range source is excluded from that
    bucket with warning `increment_out_of_range`. For instant/rate it applies per reading.

    Extra keyword args (not in the frozen positional signature): `counter_max` (fallback for
    readings without their own), `max_rate_per_hour`, `warnings` (list the engine appends to;
    repeated identical warnings are merged as "msg xN"), `now` (P1D: the bucket containing it
    expects ceil((now - b0)/1h) hours instead of the full local day), `pre_aggregated` (readings
    are already one row per bucket, e.g. daily model rows: any bucket with a point has coverage 1.0).
    """
    ws: List[tuple] = []
    summing = semantics in ("cumulative", "amount")
    folded = [r for r in readings if _folded(r)]
    if folded and (summing or resolution == "raw" or len(folded) != len(readings)):
        raise ValueError("folded readings are only valid alone, for instant/rate bucketed series")
    if folded:
        # range already applied in SQL: n > 0 readings are in range, oor counts become warnings
        _folded_oor_warnings(folded, ws)
        pairs: List[Pair] = [(r, None) for r in folded if r.n > 0]
    elif semantics == "cumulative":
        pairs, cw = _deltas(readings, counter_max, max_rate_per_hour)
        ws.extend(cw)
    elif semantics == "amount":
        uniq: Dict[Tuple[str, datetime], Reading] = {}
        for r in readings:  # duplicate (source, t): keep the max value, never double-count
            old = uniq.get((r.source_id, r.t))
            if old is None or r.value > old.value:
                uniq[(r.source_id, r.t)] = r
        pairs = [(uniq[k], None) for k in sorted(uniq, key=lambda k: (k[0], k[1]))]
    else:
        pairs = []
        for r in readings:
            bad = (valid_min is not None and r.value < valid_min) or (valid_max is not None and r.value > valid_max)
            if bad:
                ws.append(("reading_out_of_range", r.source_id, r.t))
            else:
                pairs.append((r, None))

    def build(t: datetime, in_b: List[Pair], cov) -> BucketPoint:
        return _point(t, in_b, summing, fusion, ws, valid_min, valid_max, cov)

    out: List[BucketPoint] = []
    if resolution == "raw":
        lo = min(b[0] for b in bucket_list) if bucket_list else None
        hi = max(b[1] for b in bucket_list) if bucket_list else None
        by_t: Dict[datetime, List[Pair]] = {}
        for r, tp in pairs:
            if lo is not None and not (lo <= r.t < hi):
                continue
            by_t.setdefault(r.t.astimezone(UTC), []).append((r, tp))
        for t in sorted(by_t):
            pt = build(t, by_t[t], lambda kept: 1.0)
            if pt.value is not None:
                out.append(pt)
    else:
        by_bucket = _assign(pairs, bucket_list) if folded else None
        for i, (b0, b1) in enumerate(bucket_list):
            if by_bucket is not None:
                in_b = by_bucket[i]
            else:
                in_b = [(r, tp) for r, tp in pairs if b0 <= r.t < b1]
            if not in_b:
                out.append(_empty(b0))
                continue
            cov = (lambda kept: 1.0) if pre_aggregated else _coverage_fn(b0, b1, resolution, now, ws)
            out.append(build(b0, in_b, cov))
    if warnings is not None:
        warnings.extend(_dedupe_warnings(ws))
    return out


def _assign(pairs: List[Pair], bucket_list: List[Tuple[datetime, datetime]]) -> List[List[Pair]]:
    """Single pass: pairs per bucket, each list in `pairs` order (same lists as the per-bucket
    filter `b0 <= t < b1`, for sorted non-overlapping buckets)."""
    starts = [b[0] for b in bucket_list]
    out: List[List[Pair]] = [[] for _ in bucket_list]
    for pr in pairs:
        i = bisect.bisect_right(starts, pr[0].t) - 1
        if i >= 0 and pr[0].t < bucket_list[i][1]:
            out[i].append(pr)
    return out


def _coverage_fn(b0: datetime, b1: datetime, resolution: str, now: Optional[datetime], ws: List[tuple]):
    if resolution != "P1D":
        return lambda kept: 1.0 if kept else 0.0
    expected = max(round((b1 - b0) / _HOUR), 1)
    if now is not None and b0 <= now < b1:
        expected = max(math.ceil((now - b0) / _HOUR), 1)

    def cov(kept: List[Pair]) -> float:
        hours = set()
        for r, tp in kept:
            cur = (r.t - b0) // _HOUR
            lo, hi = cur, cur
            if tp is not None:
                if r.t - tp <= _LONG_GAP:
                    # local hours overlapped by the half-open [t_prev, t_cur)
                    lo = (tp - b0) // _HOUR
                    hi = -((b0 - r.t) // _HOUR) - 1
                else:
                    ws.append(("long_gap", r.source_id, r.t))
            hours.update(range(max(lo, 0), hi + 1))
        return min(1.0, len(hours) / expected)

    return cov


def _point(t: datetime, in_b: List[Pair], summing: bool, fusion: str, ws: List[tuple],
           valid_min: Optional[float], valid_max: Optional[float], cov) -> BucketPoint:
    per_src: Dict[str, List[Pair]] = {}
    for pr in in_b:
        per_src.setdefault(pr[0].source_id, []).append(pr)
    srcs: List[dict] = []
    kept: List[Pair] = []
    for sid in sorted(per_src):
        prs = per_src[sid]
        n_readings = sum(r.n for r, _ in prs)
        if summing:
            v = sum(r.value for r, _ in prs)
        else:  # folded readings: sum of sums / sum of counts (raw: the plain mean)
            v = sum(r.value if r.value_sum is None else r.value_sum for r, _ in prs) / n_readings
        if summing and ((valid_min is not None and v < valid_min) or (valid_max is not None and v > valid_max)):
            ws.append(("increment_out_of_range", sid, t))
            continue
        first = prs[0][0]
        srcs.append({
            "source_id": sid, "deployment_id": first.deployment_id, "value": v,
            "n_readings": n_readings, "distance_m": first.distance_m, "kind": first.kind,
            "calibrated": all(r.calibrated for r, _ in prs),
        })
        kept.extend(prs)
    if not srcs:
        return _empty(t)
    fused = _fuse([x["value"] for x in srcs], fusion, ws)
    return BucketPoint(t=t, value=fused, n_sources=len(srcs), coverage=cov(kept), sources=srcs)


# --------------------------------------------------------- fall-through

def fall_through(tiers: List[Tuple[int, List[BucketPoint]]], min_coverage: Optional[float],
                 resolution: str) -> List[Tuple[Optional[int], BucketPoint]]:
    """Per bucket pick the first (lowest) tier whose point is covered.

    P1D: covered when coverage >= min_coverage (None -> 0.5). Other resolutions: covered when
    the point has a value (>= 1 valid reading); min_coverage does not apply (contract 8.4).
    Buckets are matched by t across tiers; none covered -> (None, unavailable point).
    raw: one tier for the whole window (first with any point); no tier -> [].
    """
    if resolution == "raw":
        # one tier for the whole window: the first with any non-empty point, never interleaved
        for tier, pts in sorted(tiers, key=lambda x: x[0]):
            real = [p for p in pts if p.value is not None]
            if real:
                return [(tier, p) for p in sorted(real, key=lambda p: p.t)]
        return []
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
