"""Flask routes of /api/observations/*. Registered by app.py; never imports ``app``."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict

import psycopg2.errors
from flask import jsonify

from common.unit_codes import to_unit_code

from .catalogue import visible_properties
from .db import tenant_cursor, timeout_response
from .request import iso_z

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
