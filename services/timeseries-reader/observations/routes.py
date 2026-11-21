"""Flask routes of /api/observations/*. Registered by app.py; never imports ``app``."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict

import psycopg2.errors
from flask import Response, jsonify, request

from common.unit_codes import to_unit_code

from .catalogue import visible_properties
from .best import best
from . import raw as raw_mod
from .db import tenant_cursor, timeout_response
from .request import RequestError, iso_z, parse_best

logger = logging.getLogger(__name__)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.capitalize() for p in rest)


def property_item(row: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = iso_z(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        item[_camel(k)] = v
    try:
        item["unitCode"] = to_unit_code(row["unit"])
    except ValueError:
        logger.warning("unknown unit symbol for property %s: %r", row.get("name"), row.get("unit"))
        item["unitCode"] = None
    return item


_GET_NUMERIC = ("depthFrom", "depthTo", "height")


def query_to_body(args) -> Dict[str, Any]:
    """GET query string -> POST body: numbers coerced to float, allowMediumFallback to bool."""
    body: Dict[str, Any] = {k: args[k] for k in ("feature", "property", "from", "to", "mode",
                                                 "resolution", "tz", "medium", "tiers")
                            if args.get(k)}
    sub: Dict[str, Any] = {}
    for k in _GET_NUMERIC:
        if args.get(k):
            try:
                sub[k] = float(args[k])
            except ValueError:
                raise RequestError(400, f"{k} must be a number")
    if args.get("organ"):
        sub["organ"] = args["organ"]
    if sub:
        body["subContext"] = sub
    flag = args.get("allowMediumFallback")
    if flag:
        low = flag.strip().lower()
        if low not in ("true", "false"):
            raise RequestError(400, "allowMediumFallback must be true or false")
        body["allowMediumFallback"] = low == "true"
    return body


def register(app, *, require_auth, resolve_tenant: Callable, get_connection: Callable,
             internal_error: Callable) -> None:
    @app.route("/api/observations/properties", methods=["GET"])
    @require_auth
    def observations_properties():
        cmd = resolve_tenant()
        if isinstance(cmd, tuple):
            return cmd
        tenant_id = cmd
        conn = None
        try:
            conn = get_connection()
            with tenant_cursor(conn, tenant_id) as cur:
                rows = visible_properties(cur, tenant_id)
            conn.rollback()  # read-only: end the transaction
            return jsonify({"items": [property_item(r) for r in rows]})
        except psycopg2.errors.QueryCanceled:
            logger.warning("observation properties query timed out: tenant_id=%s", tenant_id)
            return timeout_response()
        except Exception as e:
            return internal_error(e, "observations")
        finally:
            if conn is not None:
                conn.close()

    @app.route("/api/observations/best", methods=["GET", "POST"])
    @require_auth
    def observations_best():
        cmd = resolve_tenant()
        if isinstance(cmd, tuple):
            return cmd
        tenant_id = cmd
        conn = None
        try:
            if request.method == "POST":
                body = request.get_json(silent=True)
            else:
                body = query_to_body(request.args)
            req = parse_best(body)
            conn = get_connection()
            return jsonify(best(conn, tenant_id, req))
        except RequestError as e:
            return jsonify({"error": e.message}), e.status
        except psycopg2.errors.QueryCanceled:
            logger.warning("observation best query timed out: tenant_id=%s", tenant_id)
            return timeout_response()
        except Exception as e:
            return internal_error(e, "observations")
        finally:
            if conn is not None:
                conn.close()

    @app.route("/api/observations", methods=["POST"])
    @require_auth
    def observations_raw():
        cmd = resolve_tenant()
        if isinstance(cmd, tuple):
            return cmd
        tenant_id = cmd
        conn = None
        stream = None
        try:
            rr = raw_mod.parse_raw(request.get_json(silent=True))
            conn = get_connection()
            plan = raw_mod.make_plan(conn, tenant_id, rr.base)
            if "text/csv" in (request.headers.get("Accept") or "").lower():
                # the stream owns the connection from here on (closed on end, error or abort)
                stream = raw_mod.CsvStream(conn, tenant_id, plan, cursor=rr.cursor,
                                           quality_flags=rr.quality_flags)
                return Response(stream, status=200, mimetype="text/csv", direct_passthrough=True,
                                headers={"Content-Disposition":
                                         'attachment; filename="observations.csv"'})
            items, nxt = raw_mod.raw_page(conn, tenant_id, plan, limit=rr.limit,
                                          cursor=rr.cursor, quality_flags=rr.quality_flags)
            return jsonify({"items": items, "nextCursor": nxt})
        except RequestError as e:
            return jsonify({"error": e.message}), e.status
        except psycopg2.errors.QueryCanceled:
            logger.warning("observation raw query timed out: tenant_id=%s", tenant_id)
            return timeout_response()
        except Exception as e:
            return internal_error(e, "observations")
        finally:
            if conn is not None and stream is None:
                conn.close()
