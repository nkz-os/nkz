"""Raw observations (contract 2.3): every stored observation matching the `best` filters, unfused.

Rows are the stored observations that qualify for tiers 1-3 of the same request (same medium
resolution, same sub-context filter, same per-row tier membership as `sources.observed_tiers`).
Model tiers 4/5 are not stored observations and are excluded. Quality flags are NOT filtered
unless the body carries `qualityFlags`.

Order and paging: keyset on (observed_at, source_event_id, attribute_name) ascending. Tier
membership is decided in Python per row, so a page is built from SQL batches until `limit`
qualifying rows are found; `nextCursor` is the position of the last RETURNED row (or, when the
per-call examination cap stops the scan early, of the last EXAMINED row; every row up to it was
either returned or did not qualify, so nothing is skipped or repeated).

Transactions: the plan (feature/property resolution via Orion, then candidate deployments) is made
first; every SQL batch then runs in its own `tenant_cursor` transaction (own statement_timeout),
rolled back. The caller owns the connection.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import psycopg2.errors
from dateutil.parser import isoparse

from .best import _resolve_feature
from .db import tenant_cursor
from .geometry import fetch_orion_entity_status
from .request import BestRequest, RequestError, parse_best
from .sources import (_VALUE_COLUMNS, _VALUE_FROM, _covered_features, _candidate_deployments,
                      _device_id, _matches_request_sub_context, _num, _property_attrs, _row_tier,
                      _sub_context, resolve_medium)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 1_000
MAX_LIMIT = 10_000
MAX_CSV_ROWS = 1_000_000
CSV_PAGE = 10_000
MAX_EXAMINED = 200_000        # rows examined per page call (bounds a long run of non-qualifying rows)
MIN_BATCH = 500
QUALITY_FLAGS = frozenset({"valid", "out_of_range", "suspect"})
TRUNCATED_LINE = "# truncated: narrow the period\n"

CSV_COLUMNS = ["t", "value", "observedProperty", "featureOfInterest", "medium", "depthFromCm",
               "depthToCm", "heightM", "organ", "contextRef", "deploymentId", "sourceKind",
               "lon", "lat", "timeSource", "qualityFlag", "calibrated", "deviceId", "tier",
               "distanceM"]

Position = Tuple[datetime, int, str]


# ----------------------------------------------------------------- request

@dataclass(frozen=True)
class RawRequest:
    base: BestRequest
    limit: int
    cursor: Optional[Position]
    quality_flags: Optional[Tuple[str, ...]]


def _iso_full(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def encode_cursor(pos: Position) -> str:
    raw = json.dumps({"t": _iso_full(pos[0]), "e": pos[1], "a": pos[2]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: Any) -> Position:
    try:
        if not isinstance(token, str) or not token:
            raise ValueError("not a string")
        data = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        if not isinstance(data, dict) or set(data) != {"t", "e", "a"}:
            raise ValueError("bad keys")
        t, e, a = data["t"], data["e"], data["a"]
        if not isinstance(t, str) or isinstance(e, bool) or not isinstance(e, int) \
                or not isinstance(a, str) or not a:
            raise ValueError("bad types")
        if "\x00" in t or "\x00" in a:
            raise ValueError("NUL")
        dt = isoparse(t)
        if dt.tzinfo is None:
            raise ValueError("naive")
        return dt.astimezone(timezone.utc), e, a
    except (ValueError, TypeError, OverflowError, binascii.Error, UnicodeError):
        raise RequestError(400, "invalid cursor")


def parse_raw(body: Any) -> RawRequest:
    base = parse_best(body)
    limit = body.get("limit")
    if limit is None:
        limit = DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise RequestError(400, "limit must be a positive integer")
    limit = min(limit, MAX_LIMIT)  # larger values are clamped, not rejected
    cursor = decode_cursor(body["cursor"]) if body.get("cursor") not in (None, "") else None
    flags = body.get("qualityFlags")
    if flags is not None:
        if (not isinstance(flags, list) or not flags
                or not all(isinstance(f, str) and f in QUALITY_FLAGS for f in flags)):
            raise RequestError(400, "qualityFlags must be a non-empty list of: "
                                    + ", ".join(sorted(QUALITY_FLAGS)))
        flags = tuple(sorted(set(flags)))
    return RawRequest(base=base, limit=limit, cursor=cursor, quality_flags=flags)


# -------------------------------------------------------------------- plan

@dataclass
class RawPlan:
    req: BestRequest
    prop: dict
    medium: str
    area: Optional[dict]
    attrs: List[str]
    urns: Dict[str, str]            # deployment id -> device URN
    device_ids: List[str]
    radius: float
    covered: Set[str] = field(default_factory=set)
    checked: Set[str] = field(default_factory=set)   # features whose coverage is already known

    @property
    def empty(self) -> bool:
        return not self.urns or not self.attrs


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # connection already broken: the caller closes it
        logger.warning("rollback failed", exc_info=True)


def make_plan(conn, tenant_id: str, req: BestRequest, fetch=None) -> RawPlan:
    prop, entity, area = _resolve_feature(conn, tenant_id, req, fetch or fetch_orion_entity_status)
    medium = resolve_medium(req, prop, entity)
    radius = _num(prop.get("nearby_radius_m")) or 0.0
    try:
        with tenant_cursor(conn, tenant_id) as cur:
            attrs = _property_attrs(cur, tenant_id, req.property)
            urns: Dict[str, str] = {}
            probed: Set[str] = set()
            covered: Set[str] = set()
            if attrs:
                urns, probed, covered = _candidate_deployments(
                    cur, tenant_id, req, prop, medium, attrs, area, req.start, req.end)
    finally:
        _rollback(conn)
    checked = set(probed)
    if req.feature:
        checked.add(req.feature)
    return RawPlan(req=req, prop=prop, medium=medium, area=area, attrs=attrs, urns=urns,
                   device_ids=sorted({_device_id(u) for u in urns.values()}), radius=radius,
                   covered=set(covered), checked=checked)


# ------------------------------------------------------------------- fetch

_RAW_SQL = (
    "-- obs:raw\nWITH area AS (SELECT ST_GeomFromGeoJSON(%s::text)::geography AS g)\n"
    f"SELECT {_VALUE_COLUMNS},\n"
    "       m.source_event_id, m.medium, m.time_source, m.quality_flag,\n"
    "       ST_AsGeoJSON(m.location::geometry)::text AS location_json\n"
    f"{_VALUE_FROM}\n"
    "WHERE m.tenant_id = %s\n"
    "  AND m.attribute_name = ANY(%s)\n"
    "  AND m.device_id = ANY(%s)\n"
    "  AND m.observed_property = %s\n"
    "  AND m.medium = %s\n"
    "  AND m.deployment_id IS NOT NULL\n"
    "  AND m.deployment_id = ANY(%s::uuid[])\n"
    "  AND m.observed_at >= %s AND m.observed_at < %s\n"
    "{extra}"
    "ORDER BY m.observed_at, m.source_event_id, m.attribute_name\n"
    "LIMIT %s"
)
_FLAG_SQL = "  AND m.quality_flag = ANY(%s)\n"
# plain observed_at bound first: chunk pruning; the tuple comparison gives the exact keyset
_AFTER_SQL = ("  AND m.observed_at >= %s\n"
              "  AND (m.observed_at, m.source_event_id, m.attribute_name) > (%s, %s, %s)\n")


def _fetch_rows(cur, tenant_id: str, plan: RawPlan, flags: Optional[Tuple[str, ...]],
                after: Optional[Position], n: int) -> List[dict]:
    req = plan.req
    lo = max(req.start, after[0]) if after else req.start
    area_json = json.dumps(plan.area) if plan.area else None
    extra = ""
    tail: List[Any] = []
    if flags:
        extra += _FLAG_SQL
        tail.append(list(flags))
    if after:
        extra += _AFTER_SQL
        tail += [after[0], after[0], after[1], after[2]]
    params = [area_json, tenant_id, plan.device_ids, lo, req.end,
              tenant_id, plan.attrs, plan.device_ids, req.property, plan.medium,
              sorted(plan.urns), lo, req.end] + tail + [n]
    cur.execute(_RAW_SQL.format(extra=extra), params)
    return [dict(r) for r in cur.fetchall()]


def _iso_t(dt: datetime) -> str:
    """UTC ISO-8601 with Z; fractional seconds only when present (raw rows keep sub-second time)."""
    u = dt.astimezone(timezone.utc)
    if u.microsecond:
        return u.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(plan: RawPlan, row: dict, tier: int, dist: Optional[float]) -> dict:
    loc = row.get("location_json")
    return {
        "t": _iso_t(row["observed_at"]),
        "value": float(row["value"]),
        "observedProperty": plan.req.property,
        "featureOfInterest": row.get("feature_of_interest"),
        "medium": row.get("medium") or plan.medium,
        "depthFromCm": _num(row.get("depth_from_cm")),
        "depthToCm": _num(row.get("depth_to_cm")),
        "heightM": _num(row.get("height_m")),
        "organ": row.get("organ"),
        "contextRef": row.get("context_ref"),
        "deploymentId": row["deployment_id"],
        "sourceKind": row.get("source_kind"),
        "location": json.loads(loc) if loc else None,
        "timeSource": row.get("time_source"),
        "qualityFlag": row.get("quality_flag"),
        "calibrated": bool(row.get("calibrated")),
        "deviceId": plan.urns[row["deployment_id"]],
        "tier": tier,
        "distanceM": dist,
    }


def _position(row: dict) -> Position:
    return row["observed_at"], int(row["source_event_id"]), row["attribute_name"]


def raw_page(conn, tenant_id: str, plan: RawPlan, *, limit: int,
             cursor: Optional[Position] = None,
             quality_flags: Optional[Tuple[str, ...]] = None) -> Tuple[List[dict], Optional[str]]:
    """(items, nextCursor). See the module docstring for the cursor invariant."""
    if plan.empty:
        return [], None
    batch = min(max(limit + 1, MIN_BATCH), MAX_LIMIT + 1)
    after = cursor
    items: List[dict] = []
    positions: List[Position] = []
    examined = 0
    while True:
        try:
            with tenant_cursor(conn, tenant_id) as cur:  # own transaction: own statement_timeout
                rows = _fetch_rows(cur, tenant_id, plan, quality_flags, after, batch)
                if plan.area is not None:
                    extra = sorted({r["feature_of_interest"] for r in rows
                                    if r.get("feature_of_interest")
                                    and r["feature_of_interest"] not in plan.checked})
                    if extra:
                        plan.covered |= _covered_features(cur, tenant_id, extra,
                                                          json.dumps(plan.area))
                        plan.checked |= set(extra)
        finally:
            _rollback(conn)
        for row in rows:
            examined += 1
            after = _position(row)
            if row["deployment_id"] not in plan.urns:
                continue
            tier, dist = _row_tier(plan.req, row, plan.area, plan.covered, plan.radius)
            if tier is None:
                continue
            if not _matches_request_sub_context(_sub_context(row), plan.req.sub_context, plan.prop):
                continue
            if len(items) == limit:
                # one more qualifying row exists: more data follows the last returned row
                return items, encode_cursor(positions[-1])
            items.append(_item(plan, row, tier, dist))
            positions.append(after)
        if len(rows) < batch:
            return items, None
        if examined >= MAX_EXAMINED:
            logger.warning("raw page stopped by the examination cap: tenant_id=%s examined=%d",
                           tenant_id, examined)
            return items, encode_cursor(after)


# --------------------------------------------------------------------- CSV

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(v: Any) -> Any:
    """Neutralise spreadsheet formulas in STRING cells only (numbers stay untouched)."""
    if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES):
        return "'" + v
    return v


def _csv_row(item: dict) -> List[Any]:
    loc = item.get("location")
    lon = lat = None
    if isinstance(loc, dict) and loc.get("type") == "Point" and loc.get("coordinates"):
        lon, lat = loc["coordinates"][0], loc["coordinates"][1]
    out = []
    for col in CSV_COLUMNS:
        if col == "lon":
            out.append(lon)
        elif col == "lat":
            out.append(lat)
        else:
            v = item.get(col)
            out.append("" if v is None else (str(v).lower() if isinstance(v, bool) else _safe_cell(v)))
    return out


def csv_chunks(conn, tenant_id: str, plan: RawPlan, *, cursor: Optional[Position] = None,
               quality_flags: Optional[Tuple[str, ...]] = None) -> Iterator[str]:
    """Header once, then pages followed server-side up to MAX_CSV_ROWS (then a truncation comment)."""
    buf = io.StringIO()
    csv.writer(buf).writerow(CSV_COLUMNS)
    yield buf.getvalue()
    total = 0
    after = cursor
    while True:
        items, nxt = raw_page(conn, tenant_id, plan, limit=min(CSV_PAGE, MAX_CSV_ROWS - total),
                              cursor=after, quality_flags=quality_flags)
        buf = io.StringIO()
        w = csv.writer(buf)
        for it in items:
            w.writerow(_csv_row(it))
        if items:
            yield buf.getvalue()
        total += len(items)
        if nxt is None:
            return
        if total >= MAX_CSV_ROWS:
            logger.warning("raw CSV truncated at %d rows: tenant_id=%s property=%s", total,
                           tenant_id, plan.req.property)
            yield TRUNCATED_LINE
            return
        after = decode_cursor(nxt)


class CsvStream:
    """Response iterable owning the connection: closed on exhaustion, error or `close()`."""

    def __init__(self, conn, tenant_id: str, plan: RawPlan, **kw):
        self._conn = conn
        self._tenant_id = tenant_id
        self._gen = csv_chunks(conn, tenant_id, plan, **kw)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            return next(self._gen)
        except StopIteration:
            self.close()
            raise
        except psycopg2.errors.QueryCanceled:
            logger.warning("raw CSV stream timed out: tenant_id=%s", self._tenant_id)
            self._conn_close()
            self._gen = iter(())
            return "# error: timeout\n"
        except Exception:
            # headers are already sent: never end silently, mark the file and log
            logger.exception("raw CSV stream aborted: tenant_id=%s", self._tenant_id)
            self._conn_close()
            self._gen = iter(())
            return "# error: export aborted, retry or narrow the period\n"

    def _conn_close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            logger.warning("connection close failed", exc_info=True)

    def close(self) -> None:
        try:
            if hasattr(self._gen, "close"):
                self._gen.close()
        finally:
            self._conn_close()
