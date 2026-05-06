#!/usr/bin/env python3
"""
Assets Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import logging
import uuid
from typing import Optional, Dict, Any
from datetime import datetime

from flask import Blueprint, request, jsonify, g, Response
import requests
import boto3
from botocore.exceptions import ClientError

from common.auth_middleware import require_auth
from common import inject_fiware_headers

logger = logging.getLogger(__name__)

# Import shared helpers
from helpers import _get_user_roles, log_entity_operation, CONTEXT_URL

assets_bp = Blueprint('assets', __name__)


# =============================================================================
# Asset Digitization Configuration
# =============================================================================

ASSET_TYPE_TO_SDM = {
    "OliveTree": {
        "sdm_type": "AgriParcel",
        "geometry_type": "Point",
        "default_attributes": {
            "cropType": {"type": "Property", "value": "Olive"},
            "treeCount": {"type": "Property", "value": 1}
        }
    },
    "VineRow": {
        "sdm_type": "AgriParcel",
        "geometry_type": "LineString",
        "default_attributes": {
            "cropType": {"type": "Property", "value": "Grape"},
            "rowCount": {"type": "Property", "value": 1}
        }
    },
    "VineRowSegment": {
        "sdm_type": "AgriParcel",
        "geometry_type": "LineString",
        "default_attributes": {
            "cropType": {"type": "Property", "value": "Grape"}
        }
    },
    "CerealParcel": {
        "sdm_type": "AgriParcel",
        "geometry_type": "Polygon",
        "default_attributes": {
            "cropType": {"type": "Property", "value": "Cereal"}
        }
    }
}

ASSETS_BUCKET = os.getenv('ASSETS_BUCKET', 'assets-3d')
PUBLIC_ASSETS_PREFIX = 'public'
ASSETS_URL_EXPIRATION = int(os.getenv('ASSETS_URL_EXPIRATION', '86400'))  # 24 hours default


def map_asset_type_to_sdm(asset_type: str) -> Optional[Dict[str, Any]]:
    """Map asset type to SDM configuration"""
    return ASSET_TYPE_TO_SDM.get(asset_type)


def generate_asset_name(asset_type: str, tenant_id: str, parcel_id: Optional[str] = None) -> str:
    """Generate unique asset name"""
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    if parcel_id:
        # Try to get sequential number for this parcel
        # For now, use timestamp
        return f"{asset_type.lower()}-{parcel_id}-{timestamp}"
    else:
        return f"{asset_type.lower()}-{tenant_id}-{timestamp}"


def build_ngsi_ld_entity_from_asset(
    data: Dict[str, Any],
    mapping: Dict[str, Any],
    tenant_id: str,
    name: str
) -> Dict[str, Any]:
    """Build complete NGSI-LD entity from asset creation payload"""
    geometry = data.get('geometry', {})
    properties = data.get('properties', {})

    # Build entity ID (URN format)
    entity_id = f"urn:ngsi-ld:{mapping['sdm_type']}:{tenant_id}:{name}"

    # Build base entity
    entity = {
        "@context": CONTEXT_URL,
        "id": entity_id,
        "type": mapping['sdm_type'],
        "name": {
            "type": "Property",
            "value": name
        },
        "location": {
            "type": "GeoProperty",
            "value": {
                "type": geometry.get('type', mapping['geometry_type']),
                "coordinates": geometry.get('coordinates', [])
            }
        },
        "createdAt": {
            "type": "Property",
            "value": {
                "@type": "DateTime",
                "@value": datetime.utcnow().isoformat() + "Z"
            }
        }
    }

    # Add default attributes from mapping
    if 'default_attributes' in mapping:
        for attr_name, attr_value in mapping['default_attributes'].items():
            entity[attr_name] = attr_value

    # Add 3D model properties if present
    if properties.get('model3d'):
        entity['ref3DModel'] = {
            "type": "Property",
            "value": properties['model3d']
        }

    if properties.get('scale') is not None:
        entity['modelScale'] = {
            "type": "Property",
            "value": float(properties['scale']),
            "unitCode": "SCL"
        }

    if properties.get('rotation') is not None:
        entity['modelRotation'] = {
            "type": "Property",
            "value": float(properties['rotation']),
            "unitCode": "DD"
        }

    return entity


# =============================================================================
# Routes
# =============================================================================


@assets_bp.route('/api/assets', methods=['POST'])
@require_auth
def create_asset():
    """Create a new asset from digitization workflow"""
    try:
        # Verify permissions
        user_roles = _get_user_roles()
        if not any(role in ['PlatformAdmin', 'TenantAdmin', 'TechnicalConsultant'] for role in user_roles):
            return jsonify({'error': 'Insufficient permissions. Only TechnicalConsultant or higher can create assets.'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body required'}), 400

        tenant_id = g.tenant

        # Validate payload
        asset_type = data.get('assetType')
        if not asset_type:
            return jsonify({'error': 'assetType is required'}), 400

        # Map asset type to SDM
        mapping = map_asset_type_to_sdm(asset_type)
        if not mapping:
            return jsonify({'error': f'Invalid asset type: {asset_type}'}), 400

        # Validate geometry
        geometry = data.get('geometry')
        if not geometry:
            return jsonify({'error': 'geometry is required'}), 400

        geometry_type = geometry.get('type')
        if geometry_type != mapping['geometry_type']:
            return jsonify({
                'error': f'Geometry type mismatch. Expected {mapping["geometry_type"]}, got {geometry_type}'
            }), 400

        # Generate name if not provided
        name = data.get('name')
        if not name:
            parcel_id = data.get('parcelId')  # Optional parcel ID
            name = generate_asset_name(asset_type, tenant_id, parcel_id)

        # Build NGSI-LD entity
        entity = build_ngsi_ld_entity_from_asset(data, mapping, tenant_id, name)

        # Persist in Orion-LD
        orion_url = f"{os.getenv('ORION_URL')}/ngsi-ld/v1/entities"
        headers = {
            'Content-Type': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, tenant_id)

        response = requests.post(orion_url, json=entity, headers=headers)

        if response.status_code in [200, 201]:
            # Log the operation
            log_entity_operation(
                'create',
                entity['id'],
                mapping['sdm_type'],
                tenant_id,
                g.farmer_id,
                'asset_digitization',
                {'asset_type': asset_type, 'name': name}
            )

            return jsonify({
                'entity_id': entity['id'],
                'name': name,
                'type': mapping['sdm_type'],
                'message': 'Asset created successfully'
            }), 201
        else:
            error_msg = response.text or 'Unknown error'
            logger.error(f"Failed to create entity in Orion-LD: {response.status_code} - {error_msg}")
            return jsonify({
                'error': 'Failed to create entity in Orion-LD',
                'details': error_msg
            }), 500

    except Exception as e:
        logger.error(f"Error creating asset: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Bucket: assets-3d/{tenant_id}/{asset_type}/{asset_id}.{ext}
# =============================================================================


def get_assets_s3_client():
    """Get boto3 S3 client configured for MinIO assets bucket"""
    s3_endpoint = os.getenv('S3_ENDPOINT_URL', 'http://minio-service:9000')
    s3_access_key = os.getenv('S3_ACCESS_KEY')
    s3_secret_key = os.getenv('S3_SECRET_KEY')
    s3_region = os.getenv('S3_REGION', 'us-east-1')

    if not s3_access_key or not s3_secret_key:
        return None

    return boto3.client(
        's3',
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=s3_region,
        config=boto3.session.Config(signature_version='s3v4')
    )


@assets_bp.route('/api/assets/upload', methods=['POST'])
@assets_bp.route('/entity-manager/api/assets/upload', methods=['POST'])
@require_auth(require_hmac=False)
def upload_asset():
    """
    Upload a 3D model or icon directly to MinIO.

    Replaces Vercel Blob upload with local MinIO storage.

    Request: multipart/form-data with:
      - file: The file to upload
      - asset_type: 'model' or 'icon'

    Response: {
      "url": "https://...",
      "asset_id": "uuid",
      "size": bytes,
      "content_type": "..."
    }
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No filename provided'}), 400

        asset_type = request.form.get('asset_type', 'model')
        if asset_type not in ['model', 'icon']:
            return jsonify({'error': 'asset_type must be "model" or "icon"'}), 400

        # Validate file type
        filename = file.filename.lower()
        if asset_type == 'model':
            allowed_extensions = ['.glb', '.gltf']
            max_size_mb = 50  # 50MB for 3D models
            content_type = 'model/gltf-binary' if filename.endswith('.glb') else 'model/gltf+json'
        else:  # icon
            allowed_extensions = ['.png', '.jpg', '.jpeg', '.svg', '.webp']
            max_size_mb = 5  # 5MB for icons
            ext = filename.split('.')[-1]
            content_type = f'image/{ext}' if ext != 'svg' else 'image/svg+xml'

        if not any(filename.endswith(ext) for ext in allowed_extensions):
            return jsonify({
                'error': f'Invalid file type for {asset_type}',
                'allowed_extensions': allowed_extensions
            }), 400

        # Read file to check size
        file_data = file.read()
        file_size = len(file_data)

        if file_size > max_size_mb * 1024 * 1024:
            return jsonify({
                'error': f'File too large. Max {max_size_mb}MB for {asset_type}',
                'size_mb': round(file_size / (1024 * 1024), 2)
            }), 400

        # Generate asset ID and path
        asset_id = str(uuid.uuid4())
        extension = '.' + filename.split('.')[-1]
        s3_key = f"{tenant_id}/{asset_type}/{asset_id}{extension}"

        # Get S3 client
        s3_client = get_assets_s3_client()
        if not s3_client:
            logger.error("MinIO credentials not configured for asset upload")
            return jsonify({'error': 'Asset storage not configured'}), 503

        # Upload to MinIO
        try:
            s3_client.put_object(
                Bucket=ASSETS_BUCKET,
                Key=s3_key,
                Body=file_data,
                ContentType=content_type,
                Metadata={
                    'tenant_id': tenant_id,
                    'asset_type': asset_type,
                    'original_filename': file.filename
                }
            )
            logger.info(f"Uploaded asset to MinIO: {ASSETS_BUCKET}/{s3_key}")
        except ClientError as e:
            logger.error(f"Failed to upload asset to MinIO: {e}")
            return jsonify({'error': 'Failed to upload asset'}), 500

        # Generate presigned URL for access
        # Using public bucket, so we can use direct URL
        s3_endpoint = os.getenv('S3_ENDPOINT_URL', 'http://minio-service:9000')
        # For internal access, use internal endpoint
        # For external access, use the public endpoint
        public_endpoint = os.getenv('ASSETS_PUBLIC_URL', s3_endpoint)
        direct_url = f"{public_endpoint}/{ASSETS_BUCKET}/{s3_key}"

        return jsonify({
            'url': direct_url,
            'asset_id': asset_id,
            'key': s3_key,
            'size': file_size,
            'content_type': content_type,
            'tenant_id': tenant_id
        }), 201

    except Exception as e:
        logger.error(f"Error uploading asset: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@assets_bp.route('/api/assets/<asset_id>', methods=['GET'])
@require_auth(require_hmac=False)
def get_asset_url(asset_id):
    """
    Get a presigned URL for an asset.

    Query params:
      - type: 'model' or 'icon' (required)
      - extension: file extension (default: .glb for model, .png for icon)
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        asset_type = request.args.get('type', 'model')
        if asset_type == 'model':
            extension = request.args.get('extension', '.glb')
        else:
            extension = request.args.get('extension', '.png')

        s3_key = f"{tenant_id}/{asset_type}/{asset_id}{extension}"

        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Asset storage not configured'}), 503

        # Check if object exists
        try:
            s3_client.head_object(Bucket=ASSETS_BUCKET, Key=s3_key)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return jsonify({'error': 'Asset not found'}), 404
            raise

        # Generate presigned URL
        try:
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': ASSETS_BUCKET, 'Key': s3_key},
                ExpiresIn=ASSETS_URL_EXPIRATION
            )
            return jsonify({
                'url': url,
                'expires_in': ASSETS_URL_EXPIRATION,
                'asset_id': asset_id
            }), 200
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return jsonify({'error': 'Failed to generate URL'}), 500

    except Exception as e:
        logger.error(f"Error getting asset URL: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@assets_bp.route('/api/assets/<asset_id>', methods=['DELETE'])
@require_auth(require_hmac=False)
def delete_asset(asset_id):
    """
    Delete an asset from MinIO.

    Query params:
      - type: 'model' or 'icon' (required)
      - extension: file extension
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        asset_type = request.args.get('type', 'model')
        if asset_type == 'model':
            extension = request.args.get('extension', '.glb')
        else:
            extension = request.args.get('extension', '.png')

        s3_key = f"{tenant_id}/{asset_type}/{asset_id}{extension}"

        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Asset storage not configured'}), 503

        try:
            s3_client.delete_object(Bucket=ASSETS_BUCKET, Key=s3_key)
            logger.info(f"Deleted asset from MinIO: {ASSETS_BUCKET}/{s3_key}")
            return jsonify({
                'deleted': True,
                'asset_id': asset_id
            }), 200
        except ClientError as e:
            logger.error(f"Failed to delete asset: {e}")
            return jsonify({'error': 'Failed to delete asset'}), 500

    except Exception as e:
        logger.error(f"Error deleting asset: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@assets_bp.route('/api/assets/tenant', methods=['GET'])
@assets_bp.route('/entity-manager/api/assets/tenant', methods=['GET'])
@require_auth(require_hmac=False)
def list_tenant_assets():
    """
    List tenant-scoped assets from MinIO assets-3d bucket (prefix {tenant_id}/).
    Same response shape as list_public_assets; includes asset_id, asset_type, extension for delete.
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Asset storage not configured'}), 503

        try:
            prefix = f"{tenant_id}/"
            response = s3_client.list_objects_v2(
                Bucket=ASSETS_BUCKET,
                Prefix=prefix,
            )
            assets = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']
                    if not any(key.lower().endswith(ext) for ext in ['.glb', '.gltf', '.png', '.jpg', '.jpeg']):
                        continue
                    # key is like "tenant_id/model/uuid.glb" -> asset_type, asset_id, extension for DELETE
                    parts = key.split('/')
                    asset_type = parts[1] if len(parts) > 2 else 'model'
                    filename = parts[-1] if parts else key
                    ext = ''
                    for e in ['.glb', '.gltf', '.png', '.jpg', '.jpeg']:
                        if filename.lower().endswith(e):
                            ext = e
                            break
                    asset_id = filename[:-len(ext)] if ext else filename
                    assets.append({
                        'id': key,
                        'name': filename,
                        'key': key,
                        'url': f"/assets/assets-3d/{key}",
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'asset_id': asset_id,
                        'asset_type': asset_type,
                        'extension': ext or '.glb',
                    })
            return jsonify({'assets': assets}), 200
        except ClientError as e:
            logger.error(f"Failed to list tenant assets: {e}")
            return jsonify({'error': 'Failed to list assets'}), 500

    except Exception as e:
        logger.error(f"Error listing tenant assets: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@assets_bp.route('/api/assets/public', methods=['GET'])
@assets_bp.route('/entity-manager/api/assets/public', methods=['GET'])
@require_auth(require_hmac=False)
def list_public_assets():
    """
    List GLOBAL/PUBLIC assets from MinIO assets-3d bucket.
    """
    try:
        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Asset storage not configured'}), 503

        try:
            response = s3_client.list_objects_v2(
                Bucket=ASSETS_BUCKET,
                Prefix=PUBLIC_ASSETS_PREFIX + '/',
            )
            assets = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    filename = obj['Key']
                    if any(filename.lower().endswith(ext) for ext in ['.glb', '.gltf', '.png', '.jpg', '.jpeg']):
                        assets.append({
                            'id': filename,
                            'name': filename,
                            'url': f"/assets/assets-3d/{filename}",
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'].isoformat()
                        })
            return jsonify({'assets': assets}), 200
        except ClientError as e:
            logger.error(f"Failed to list public assets: {e}")
            return jsonify({'error': 'Failed to list assets'}), 500

    except Exception as e:
        logger.error(f"Error listing public assets: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@assets_bp.route('/api/assets/public', methods=['POST'])
@assets_bp.route('/entity-manager/api/assets/public', methods=['POST'])
@require_auth(require_hmac=False)
def upload_public_asset():
    """
    Upload a GLOBAL/PUBLIC asset to MinIO (Platform Admin only).
    """
    try:
        user_roles = _get_user_roles()
        if 'PlatformAdmin' not in user_roles:
            return jsonify({'error': 'Only Platform Admin can upload global assets'}), 403

        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No filename provided'}), 400

        asset_type = request.form.get('asset_type', 'model')
        if asset_type not in ['model', 'icon']:
            return jsonify({'error': 'Invalid asset type'}), 400

        filename = file.filename.lower()
        if asset_type == 'model':
            allowed_extensions = ['.glb', '.gltf']
            content_type = 'model/gltf-binary' if filename.endswith('.glb') else 'model/gltf+json'
        else:
            allowed_extensions = ['.png', '.jpg', '.jpeg', '.svg', '.webp']
            ext = filename.split('.')[-1]
            content_type = f'image/{ext}' if ext != 'svg' else 'image/svg+xml'

        if not any(filename.endswith(ext) for ext in allowed_extensions):
            return jsonify({'error': 'Invalid file extension'}), 400

        file_data = file.read()

        safe_filename = "".join([c for c in file.filename if c.isalpha() or c.isdigit() or c in ['.', '-', '_']]).strip()
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        s3_key = f"{PUBLIC_ASSETS_PREFIX}/{asset_type}/{timestamp}_{safe_filename}"

        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Storage not configured'}), 503

        s3_client.put_object(
            Bucket=ASSETS_BUCKET,
            Key=s3_key,
            Body=file_data,
            ContentType=content_type,
            Metadata={
                'original_filename': file.filename,
                'is_public': 'true'
            }
        )

        s3_endpoint = os.getenv('S3_ENDPOINT_URL', 'http://minio-service:9000')
        public_endpoint = os.getenv('ASSETS_PUBLIC_URL', s3_endpoint)
        url = f"{public_endpoint}/{ASSETS_BUCKET}/{s3_key}"

        return jsonify({
            'success': True,
            'url': url,
            'key': s3_key,
            'filename': safe_filename
        }), 201

    except Exception as e:
        logger.error(f"Error uploading public asset: {e}")
        return jsonify({'error': str(e)}), 500


@assets_bp.route('/api/assets/public/<path:filename>', methods=['DELETE'])
@assets_bp.route('/entity-manager/api/assets/public/<path:filename>', methods=['DELETE'])
@require_auth
def delete_public_asset(filename):
    """Delete a public asset (Platform Admin only)"""
    try:
        user_roles = _get_user_roles()
        if 'PlatformAdmin' not in user_roles:
            return jsonify({'error': 'Only Platform Admin can delete global assets'}), 403

        # Reconstruct key from URL param (which might be just filename or partial path)
        # We expect the client to send the full key or enough info.
        # Ideally, the client sends the 'key' field returned by list.
        # But here we capture <path:filename> so it handles slashes.

        # Security check: ensure it starts with public/
        if not filename.startswith(f"{PUBLIC_ASSETS_PREFIX}/"):
            # If the user sent just "model/foo.glb", prepend public/
            s3_key = f"{PUBLIC_ASSETS_PREFIX}/{filename}"
        else:
            s3_key = filename

        s3_client = get_assets_s3_client()
        if not s3_client:
            return jsonify({'error': 'Storage not configured'}), 503

        s3_client.delete_object(Bucket=ASSETS_BUCKET, Key=s3_key)
        return jsonify({'success': True}), 200

    except Exception as e:
        logger.error(f"Error deleting public asset: {e}")
        return jsonify({'error': str(e)}), 500
