#!/usr/bin/env python3
"""Parcels Blueprint — entity-manager is the SOLE writer of AgriParcel.

Source of truth: Orion-LD AgriParcel. cadastral_parcels (PostGIS) is a
read-model projected by subscription + reconcile.
"""
import logging
import os
import re
import uuid

import requests
from flask import Blueprint, request, jsonify, g

from common.auth_middleware import require_auth, inject_fiware_headers
from helpers import ORION_URL, CONTEXT_URL
from parcel_geometry import validate_parcel_geometry, GeometryError
from parcel_projection import project_rows, urn_to_uuid
from parcel_subscription import ensure_projection_subscription

logger = logging.getLogger(__name__)
parcels_bp = Blueprint("parcels", __name__)

_LINK = f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
_PROJECTION_ENDPOINT = os.getenv(
    "PARCEL_PROJECTION_ENDPOINT",
    "http://entity-manager-service:5000/internal/parcels/project",
)


def _current_tenant() -> str:
    return getattr(g, "tenant", None) or request.headers.get("X-Tenant-ID", "")


def _orion_upsert(tenant: str, entity: dict):
    """Create/replace a full NGSI-LD entity (carries @context in body → ld+json, no Link)."""
    headers = inject_fiware_headers({}, tenant)
    headers["Content-Type"] = "application/ld+json"
    headers.pop("Link", None)
    body = dict(entity)
    body["@context"] = CONTEXT_URL
    r = requests.post(
        f"{ORION_URL}/ngsi-ld/v1/entityOperations/upsert?options=update",
        json=[body],
        headers=headers,
        timeout=15,
    )
    return r.status_code, (r.json() if r.content else {})


def _orion_query_by_cadastral_ref(tenant: str, cadastral_ref: str):
    """Find existing AgriParcel with this cadastralReference in the tenant."""
    headers = inject_fiware_headers({"Accept": "application/json", "Link": _LINK}, tenant)
    params = {
        "type": "AgriParcel",
        "q": f'cadastralReference=="{cadastral_ref}"',
        "limit": 5,
    }
    r = requests.get(
        f"{ORION_URL}/ngsi-ld/v1/entities",
        params=params,
        headers=headers,
        timeout=15,
    )
    if r.status_code != 200:
        return []
    return r.json() or []


# Parcel attributes carried as plain NGSI-LD Properties (preserve the full FE set so
# routing writes through this API does not drop data).
_PARCEL_PROPERTY_KEYS = (
    "name", "municipality", "province", "cropType", "cadastralReference",
    "area", "ndviEnabled", "notes", "generationMethod", "aiModel",
    "confidence", "elevation", "terrainSlope", "terrainAspect",
)


def _build_parcel_entity(parcel_id: str, data: dict) -> dict:
    ent = {
        "id": parcel_id,
        "type": "AgriParcel",
        "category": {"type": "Property", "value": data.get("category", "cadastral")},
    }
    geometry = data.get("geometry")
    if geometry is not None:
        ent["location"] = {"type": "GeoProperty", "value": geometry}
    for key in _PARCEL_PROPERTY_KEYS:
        val = data.get(key)
        if val is not None:
            ent[key] = {"type": "Property", "value": val}
    parent = data.get("refParent")
    if parent:
        ent["hasAgriParcel"] = {"type": "Relationship", "object": parent}
    return ent


def _orion_patch_attrs(tenant: str, parcel_id: str, attrs: dict):
    """Attribute fragment → application/json + Link (NO @context in body)."""
    headers = inject_fiware_headers({"Content-Type": "application/json", "Link": _LINK}, tenant)
    r = requests.patch(
        f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_id}/attrs",
        json=attrs,
        headers=headers,
        timeout=15,
    )
    return r.status_code, (r.json() if r.content else {})


def _update_existing(tenant: str, parcel_id: str, data: dict):
    entity = _build_parcel_entity(parcel_id, data)
    attrs = {k: v for k, v in entity.items() if k not in ("id", "type")}
    status, _ = _orion_patch_attrs(tenant, parcel_id, attrs)
    if status not in (200, 204):
        return jsonify({"error": "orion_write_failed", "status": status}), 502
    logger.info("Dedup update AgriParcel %s tenant=%s", parcel_id, tenant)
    return jsonify({"id": parcel_id, "created": False}), 200


@parcels_bp.route("/api/entities/parcels", methods=["POST"])
@require_auth
def create_parcel():
    tenant = _current_tenant()
    data = request.get_json(silent=True) or {}
    geometry = data.get("geometry")
    try:
        validate_parcel_geometry(geometry)
    except GeometryError as e:
        return jsonify({"error": "invalid_geometry", "detail": str(e)}), 422

    cadastral_ref = data.get("cadastralReference")
    if cadastral_ref is not None and not re.match(r"^[A-Za-z0-9\-._/ ]+$", str(cadastral_ref)):
        return jsonify({"error": "invalid_cadastral_reference"}), 422
    if cadastral_ref:
        existing = _orion_query_by_cadastral_ref(tenant, cadastral_ref)
        if existing:
            return _update_existing(tenant, existing[0]["id"], data)

    parcel_id = f"urn:ngsi-ld:AgriParcel:{uuid.uuid4()}"
    entity = _build_parcel_entity(parcel_id, data)
    status, _ = _orion_upsert(tenant, entity)
    if status not in (200, 201, 204):
        logger.error(
            "Orion upsert failed (%s) tenant=%s parcel=%s", status, tenant, parcel_id
        )
        return jsonify({"error": "orion_write_failed", "status": status}), 502
    logger.info(
        "Created AgriParcel %s tenant=%s ref=%s", parcel_id, tenant, cadastral_ref
    )
    try:
        ensure_projection_subscription(tenant, _PROJECTION_ENDPOINT, os.getenv("INTERNAL_SERVICE_SECRET", ""))
    except Exception:
        logger.exception("ensure_projection_subscription failed (non-fatal) tenant=%s", tenant)
    return jsonify({"id": parcel_id, "created": True}), 201


def _orion_delete(tenant: str, parcel_id: str) -> int:
    headers = inject_fiware_headers({"Link": _LINK}, tenant)
    return requests.delete(
        f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_id}", headers=headers, timeout=15
    ).status_code


def _orion_entity_exists(tenant: str, entity_id: str) -> bool:
    headers = inject_fiware_headers({"Accept": "application/json", "Link": _LINK}, tenant)
    r = requests.get(f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}", headers=headers, timeout=15)
    return r.status_code == 200


def _orion_query_children(tenant: str, parent_id: str):
    headers = inject_fiware_headers({"Accept": "application/json", "Link": _LINK}, tenant)
    params = {"type": "AgriParcel", "q": f'hasAgriParcel=="{parent_id}"', "limit": 200}
    r = requests.get(
        f"{ORION_URL}/ngsi-ld/v1/entities", params=params, headers=headers, timeout=15
    )
    return (r.json() or []) if r.status_code == 200 else []


@parcels_bp.route("/api/entities/parcels/<path:parcel_id>", methods=["PATCH"])
@require_auth
def patch_parcel(parcel_id):
    tenant = _current_tenant()
    data = request.get_json(silent=True) or {}
    if "geometry" in data:
        try:
            validate_parcel_geometry(data["geometry"])
        except GeometryError as e:
            return jsonify({"error": "invalid_geometry", "detail": str(e)}), 422
    entity = _build_parcel_entity(parcel_id, data)
    attrs = {k: v for k, v in entity.items() if k not in ("id", "type")}
    status, _ = _orion_patch_attrs(tenant, parcel_id, attrs)
    return ("", 204) if status in (200, 204) else (jsonify({"error": "orion_write_failed", "status": status}), 502)


@parcels_bp.route("/api/entities/parcels/<path:parcel_id>/attrs", methods=["PATCH"])
@require_auth
def patch_parcel_attrs_raw(parcel_id):
    """Forward a raw NGSI-LD attribute fragment to Orion (entity-manager = sole writer).

    Used by the generic SDM editor for AgriParcel: it sends NGSI-LD attrs as-is
    (e.g. an ``refAgriFarm``/``hasAgriFarm`` Relationship) which the flat PATCH does
    not model and would silently drop. The fragment carries no @context →
    application/json + Link (handled by ``_orion_patch_attrs``). The read-model is
    reprojected by the AgriParcel subscription (no manual projection here).
    """
    tenant = _current_tenant()
    attrs = request.get_json(silent=True) or {}
    for k in ("id", "type", "@context"):
        attrs.pop(k, None)
    if not attrs:
        return jsonify({"error": "empty_attrs"}), 422
    status, _ = _orion_patch_attrs(tenant, parcel_id, attrs)
    return ("", 204) if status in (200, 204) else (jsonify({"error": "orion_write_failed", "status": status}), 502)


@parcels_bp.route("/api/entities/parcels/<path:parcel_id>", methods=["DELETE"])
@require_auth
def delete_parcel(parcel_id):
    tenant = _current_tenant()
    deleted_ids = []
    for child in _orion_query_children(tenant, parcel_id):
        sc = _orion_delete(tenant, child["id"])
        if sc not in (200, 204, 404):
            logger.error("Child zone delete failed (%s) parent=%s child=%s tenant=%s",
                         sc, parcel_id, child["id"], tenant)
            return jsonify({"error": "child_delete_failed", "child": child["id"], "status": sc}), 502
        deleted_ids.append(child["id"])
    status = _orion_delete(tenant, parcel_id)
    if status not in (200, 204):
        return jsonify({"error": "orion_delete_failed", "status": status}), 502
    deleted_ids.append(parcel_id)
    try:
        project_rows(tenant, [{"id": i} for i in deleted_ids], deleted=True)
    except Exception:
        logger.exception("read-model delete projection failed (non-fatal) tenant=%s", tenant)
    return ("", 204)


@parcels_bp.route("/internal/parcels/project", methods=["POST"])
def project_parcels():
    """Orion notification sink — projects AgriParcel entities into cadastral_parcels.

    Authenticated by X-Internal-Service-Secret header only (no JWT/cookie).
    Must remain exempt from @require_auth.
    """
    secret = os.getenv("INTERNAL_SERVICE_SECRET", "")
    if not secret or request.headers.get("X-Internal-Service-Secret") != secret:
        return jsonify({"error": "forbidden"}), 403
    # Defensive: if NGSILD-Tenant arrives duplicated (e.g. folded "t,t"), take the first.
    raw_tenant = request.headers.get("NGSILD-Tenant") or request.headers.get("X-Tenant-ID", "")
    tenant = raw_tenant.split(",")[0].strip()
    payload = request.get_json(silent=True) or {}
    entities = payload.get("data", [])
    project_rows(tenant, entities, deleted=False)
    return jsonify({"projected": len(entities)}), 200


@parcels_bp.route("/api/admin/parcels/reconcile", methods=["POST"])
@require_auth
def reconcile_parcels():
    """Rebuild the read-model for the current tenant from Orion (idempotent). Reads WITH @context."""
    tenant = _current_tenant()
    headers = inject_fiware_headers({"Accept": "application/json", "Link": _LINK}, tenant)
    params = {"type": "AgriParcel", "limit": 1000}
    r = requests.get(f"{ORION_URL}/ngsi-ld/v1/entities", params=params, headers=headers, timeout=30)
    entities = (r.json() or []) if r.status_code == 200 else []
    project_rows(tenant, entities, deleted=False)
    present_uuids = [u for u in (urn_to_uuid(e["id"]) for e in entities) if u]
    from parcel_projection import delete_orphans
    removed = delete_orphans(tenant, present_uuids)
    return jsonify({"reconciled": len(entities), "removed_orphans": removed}), 200


@parcels_bp.route("/api/entities/parcels/<path:parent_id>/zones", methods=["POST"])
@require_auth
def create_zones(parent_id):
    tenant = _current_tenant()
    if not _orion_entity_exists(tenant, parent_id):
        return jsonify({"error": "parent_not_found", "parent": parent_id}), 404
    data = request.get_json(silent=True) or {}
    inherit = data.get("inherit", {}) or {}
    created = []
    for z in data.get("zones", []):
        try:
            validate_parcel_geometry(z.get("geometry"))
        except GeometryError as e:
            return jsonify({"error": "invalid_geometry", "detail": str(e)}), 422
        merged = {**inherit, **z, "category": "managementZone"}
        zid = f"urn:ngsi-ld:AgriParcel:{uuid.uuid4()}"
        entity = _build_parcel_entity(zid, merged)
        entity["hasAgriParcel"] = {"type": "Relationship", "object": parent_id}
        status, _ = _orion_upsert(tenant, entity)
        if status not in (200, 201, 204):
            return jsonify({"error": "orion_write_failed", "status": status}), 502
        created.append(zid)
    return jsonify({"created": created}), 201
