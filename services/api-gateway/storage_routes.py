"""Storage endpoints — presigned MinIO URLs scoped per tenant + module.

The path layout in the bucket is:
    tenants/<tenant-id>/modules/<module-id>/<user-path>

Modules can only read/write within their own scope. The gateway derives
the tenant from the authenticated token (X-Tenant-ID) and the module
from the X-Module-Id header injected by the SDK.
"""

import os
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
import boto3
from botocore.client import Config

logger = logging.getLogger(__name__)

storage_bp = Blueprint("storage", __name__, url_prefix="/api/storage")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-service:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_MODULE_DATA_BUCKET", "nekazari-module-data")

DEFAULT_PUT_TTL_SECONDS = 5 * 60  # 5 min
DEFAULT_GET_TTL_SECONDS = 60 * 60  # 1 h
ALLOWED_PUT_TTL_MAX = 30 * 60  # 30 min
ALLOWED_GET_TTL_MAX = 7 * 24 * 60 * 60  # 7 days
PATH_MAX_LEN = 512
INVALID_PATH_SEQUENCES = ("..", "\\", "\x00")


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _validate_path(path):
    """Return None if valid, else an error message string."""
    if not path or not isinstance(path, str):
        return "path is required and must be a string"
    if len(path) > PATH_MAX_LEN:
        return f"path too long (max {PATH_MAX_LEN})"
    if path.startswith("/"):
        return "path must not start with /"
    for bad in INVALID_PATH_SEQUENCES:
        if bad in path:
            return f"path contains forbidden sequence: {bad!r}"
    return None


def _scoped_key(tenant_id, module_id, path):
    return f"tenants/{tenant_id}/modules/{module_id}/{path}"


@storage_bp.route("/presigned-url", methods=["POST", "OPTIONS"])
def presigned_url():
    if request.method == "OPTIONS":
        return ("", 204)

    tenant_id = request.headers.get("X-Tenant-ID")
    module_id = request.headers.get("X-Module-Id")
    if not tenant_id:
        return jsonify({"error": "X-Tenant-ID header missing"}), 401
    if not module_id:
        return jsonify({"error": "X-Module-Id header missing"}), 400

    body = request.get_json(silent=True) or {}
    path = body.get("path")
    operation = (body.get("operation") or "GET").upper()
    content_type = body.get("contentType")
    expires = body.get("expiresInSeconds")

    if operation not in ("GET", "PUT"):
        return jsonify({"error": "operation must be GET or PUT"}), 400
    err = _validate_path(path)
    if err:
        return jsonify({"error": err}), 400

    key = _scoped_key(tenant_id, module_id, path)
    params = {"Bucket": MINIO_BUCKET, "Key": key}

    if operation == "PUT":
        ttl = min(
            int(expires) if expires else DEFAULT_PUT_TTL_SECONDS, ALLOWED_PUT_TTL_MAX
        )
        method = "put_object"
        if content_type:
            params["ContentType"] = str(content_type)
    else:
        ttl = min(
            int(expires) if expires else DEFAULT_GET_TTL_SECONDS, ALLOWED_GET_TTL_MAX
        )
        method = "get_object"

    try:
        url = _s3_client().generate_presigned_url(method, Params=params, ExpiresIn=ttl)
    except Exception as exc:  # pragma: no cover — boto config error
        logger.exception("presigned URL generation failed")
        return jsonify({"error": f"presigned URL generation failed: {exc}"}), 500

    logger.info(
        "presigned %s ttl=%ss tenant=%s module=%s path=%s",
        operation,
        ttl,
        tenant_id,
        module_id,
        path,
    )
    return jsonify({"url": url, "expiresInSeconds": ttl})


@storage_bp.route("/list", methods=["GET", "OPTIONS"])
def list_objects():
    if request.method == "OPTIONS":
        return ("", 204)

    tenant_id = request.headers.get("X-Tenant-ID")
    module_id = request.headers.get("X-Module-Id")
    if not tenant_id:
        return jsonify({"error": "X-Tenant-ID header missing"}), 401
    if not module_id:
        return jsonify({"error": "X-Module-Id header missing"}), 400

    prefix = request.args.get("prefix", "")
    if prefix:
        err = _validate_path(prefix)
        if err:
            return jsonify({"error": err}), 400

    full_prefix = _scoped_key(tenant_id, module_id, prefix)
    try:
        resp = _s3_client().list_objects_v2(Bucket=MINIO_BUCKET, Prefix=full_prefix)
    except Exception as exc:  # pragma: no cover
        logger.exception("list_objects failed")
        return jsonify({"error": f"list failed: {exc}"}), 500

    scope_strip = _scoped_key(tenant_id, module_id, "")
    items = [obj["Key"][len(scope_strip) :] for obj in resp.get("Contents", [])]
    return jsonify(
        {"items": items, "count": len(items), "ts": datetime.utcnow().isoformat() + "Z"}
    )
