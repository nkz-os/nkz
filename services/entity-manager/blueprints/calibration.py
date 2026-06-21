#!/usr/bin/env python3
"""
Calibration Blueprint — Sensor calibration periods CRUD + reliability PATCH.

Routes:
  GET    /api/entities/sensors/{sensor_id}/calibration   — list calibration periods
  POST   /api/entities/sensors/{sensor_id}/calibration   — add a new period
  PATCH  /api/entities/sensors/{sensor_id}/reliability   — set reliability status (internal)
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor

import requests

from common.auth_middleware import require_auth
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_simple, return_db_connection

from helpers import ORION_URL, CONTEXT_URL

logger = logging.getLogger(__name__)

calibration_bp = Blueprint('calibration', __name__)


# =============================================================================
# Helpers
# =============================================================================

def _extract_tenant() -> Optional[str]:
    """Extract tenant from request context, checking both g.tenant_id and g.tenant."""
    return (
        getattr(g, 'tenant_id', None)
        or getattr(g, 'tenant', None)
        or request.headers.get('X-Tenant-ID')
    )


def _sensor_exists_in_orion(sensor_id: str, tenant_id: str) -> bool:
    """Check if a sensor entity exists in Orion-LD for the given tenant."""
    try:
        headers = inject_fiware_headers(
            {'Accept': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities/{sensor_id}",
            headers=headers,
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error("Error checking sensor %s in Orion: %s", sensor_id, e)
        return False


def _read_calibration_config(sensor_id: str, tenant_id: str) -> dict:
    """Read the current calibrationConfig from the sensor entity in Orion-LD."""
    try:
        headers = inject_fiware_headers(
            {'Accept': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities/{sensor_id}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
        entity = resp.json()
        cal_config = entity.get('calibrationConfig', {})
        if isinstance(cal_config, dict) and cal_config.get('type') == 'Property':
            return cal_config.get('value', {})
        if isinstance(cal_config, dict) and 'value' in cal_config:
            return cal_config['value']
        return cal_config if isinstance(cal_config, dict) else {}
    except Exception as e:
        logger.error("Error reading calibrationConfig for %s: %s", sensor_id, e)
        return {}


def _patch_calibration_config(
    sensor_id: str, tenant_id: str, variable: str,
    slope: float, offset_val: float, sensor_hardware_id: str,
) -> bool:
    """Update the calibrationConfig on the sensor entity in Orion-LD.

    Reads the current config, updates/replaces the entry for `variable`,
    and PATCHes the entity attrs.
    """
    try:
        current = _read_calibration_config(sensor_id, tenant_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        current[variable] = {
            'slope': slope,
            'offset': offset_val,
            'lastCalibratedAt': now_iso,
            'sensorHardwareId': sensor_hardware_id,
        }

        patch_body = {
            'calibrationConfig': {
                'type': 'Property',
                'value': current,
            },
        }

        headers = inject_fiware_headers(
            {'Content-Type': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )
        resp = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/entities/{sensor_id}/attrs",
            json=patch_body,
            headers=headers,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info(
                "Updated calibrationConfig for %s variable=%s", sensor_id, variable
            )
            return True
        else:
            logger.error(
                "Failed to PATCH calibrationConfig for %s: %s - %s",
                sensor_id, resp.status_code, resp.text,
            )
            return False
    except Exception as e:
        logger.error("Error patching calibrationConfig for %s: %s", sensor_id, e)
        return False


# =============================================================================
# GET /api/entities/sensors/{sensor_id}/calibration
# =============================================================================

@calibration_bp.route('/api/entities/sensors/<sensor_id>/calibration', methods=['GET'])
@require_auth
def list_calibration_periods(sensor_id: str):
    """List calibration periods for a sensor, ordered by valid_from DESC."""
    tenant_id = _extract_tenant()
    if not tenant_id:
        return jsonify({'error': 'Tenant not found'}), 401

    conn = get_db_connection_simple()
    if not conn:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, sensor_id, tenant_id, variable, slope, offset_val,
                   valid_from, valid_to, sensor_hardware_id, notes, created_by, created_at
            FROM calibration_periods
            WHERE sensor_id = %s AND tenant_id = %s
            ORDER BY valid_from DESC
        """, (sensor_id, tenant_id))

        periods = []
        for row in cur.fetchall():
            period = {
                'id': str(row['id']),
                'sensor_id': row['sensor_id'],
                'variable': row['variable'],
                'slope': row['slope'],
                'offset_val': row['offset_val'],
                'valid_from': row['valid_from'].isoformat() if row['valid_from'] else None,
                'valid_to': row['valid_to'].isoformat() if row['valid_to'] else None,
                'sensor_hardware_id': row['sensor_hardware_id'],
                'notes': row['notes'],
                'created_by': row['created_by'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            }
            periods.append(period)

        cur.close()
        return_db_connection(conn)

        return jsonify({'periods': periods}), 200

    except Exception as e:
        logger.error("Error listing calibration periods for sensor %s: %s", sensor_id, e)
        return_db_connection(conn)
        return jsonify({'error': 'Database error', 'details': str(e)}), 500


# =============================================================================
# POST /api/entities/sensors/{sensor_id}/calibration
# =============================================================================

@calibration_bp.route('/api/entities/sensors/<sensor_id>/calibration', methods=['POST'])
@require_auth
def add_calibration_period(sensor_id: str):
    """Add a new calibration period for a sensor.

    Request body:
    {
        "variable": "temperature",
        "slope": 1.02,
        "offset_val": -0.5,
        "sensor_hardware_id": "HW-001",
        "valid_from": "2024-06-01T00:00:00Z",
        "notes": "Annual calibration"   (optional)
    }

    Logic:
    1. Close any existing active period (valid_to IS NULL) for this sensor+variable
    2. Insert new period
    3. Update calibrationConfig in Orion-LD for the sensor
    """
    tenant_id = _extract_tenant()
    if not tenant_id:
        return jsonify({'error': 'Tenant not found'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # ── Validate required fields ───────────────────────────────────────
    variable = data.get('variable')
    slope = data.get('slope')
    offset_val = data.get('offset_val')
    sensor_hardware_id = data.get('sensor_hardware_id')
    valid_from = data.get('valid_from')

    missing = []
    if not variable:
        missing.append('variable')
    if slope is None:
        missing.append('slope')
    if offset_val is None:
        missing.append('offset_val')
    if not sensor_hardware_id:
        missing.append('sensor_hardware_id')
    if not valid_from:
        missing.append('valid_from')

    if missing:
        return jsonify({
            'error': 'Missing required fields',
            'missing': missing,
        }), 400

    # Validate slope/offset are numbers
    try:
        slope = float(slope)
        offset_val = float(offset_val)
    except (TypeError, ValueError):
        return jsonify({'error': 'slope and offset_val must be numbers'}), 400

    # Parse valid_from
    try:
        valid_from_dt = datetime.fromisoformat(valid_from.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return jsonify({'error': 'valid_from must be a valid ISO datetime'}), 400

    notes = data.get('notes')
    created_by = data.get('created_by') or getattr(g, 'user_id', None)

    # ── Verify sensor exists in Orion-LD ───────────────────────────────
    if not _sensor_exists_in_orion(sensor_id, tenant_id):
        return jsonify({'error': 'Sensor not found in context broker'}), 404

    conn = get_db_connection_simple()
    if not conn:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # ── Step 1: Get current active period id before closing it ──
        cur.execute("""
            SELECT id FROM calibration_periods
            WHERE sensor_id = %s
              AND variable = %s
              AND tenant_id = %s
              AND valid_to IS NULL
            LIMIT 1
        """, (sensor_id, variable, tenant_id))
        old_row = cur.fetchone()
        old_period_id = str(old_row['id']) if old_row else None

        # ── Step 1b: Close existing active period for this sensor+variable ──
        cur.execute("""
            UPDATE calibration_periods
            SET valid_to = %s
            WHERE sensor_id = %s
              AND variable = %s
              AND tenant_id = %s
              AND valid_to IS NULL
        """, (valid_from_dt, sensor_id, variable, tenant_id))

        closed_count = cur.rowcount
        if closed_count > 0:
            logger.info(
                "Closed %d active period(s) for sensor=%s variable=%s",
                closed_count, sensor_id, variable,
            )

            # Mark data from the closed period as stale
            if old_period_id:
                try:
                    cur.execute("""
                        UPDATE telemetry_events
                        SET quality_flag = 'stale'
                        WHERE calibration_period_id = %s
                          AND quality_flag IS NULL
                    """, (old_period_id,))
                    logger.info(
                        "Marked %d telemetry rows as stale for calibration period %s",
                        cur.rowcount, old_period_id,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to mark stale data for period %s: %s",
                        old_period_id, e,
                    )

        # ── Step 2: Insert new calibration period ──────────────────────
        cur.execute("""
            INSERT INTO calibration_periods
                (sensor_id, tenant_id, variable, slope, offset_val,
                 valid_from, sensor_hardware_id, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, sensor_id, tenant_id, variable, slope, offset_val,
                      valid_from, valid_to, sensor_hardware_id, notes, created_by, created_at
        """, (
            sensor_id, tenant_id, variable, slope, offset_val,
            valid_from_dt, sensor_hardware_id, notes, created_by,
        ))

        row = cur.fetchone()
        conn.commit()
        cur.close()
        return_db_connection(conn)

        if not row:
            return jsonify({'error': 'Failed to insert calibration period'}), 500

        new_period = {
            'id': str(row['id']),
            'sensor_id': row['sensor_id'],
            'variable': row['variable'],
            'slope': row['slope'],
            'offset_val': row['offset_val'],
            'valid_from': row['valid_from'].isoformat() if row['valid_from'] else None,
            'valid_to': row['valid_to'].isoformat() if row['valid_to'] else None,
            'sensor_hardware_id': row['sensor_hardware_id'],
            'notes': row['notes'],
            'created_by': row['created_by'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        }

        # ── Step 3: Update calibrationConfig in Orion-LD (best-effort) ─
        orion_ok = _patch_calibration_config(
            sensor_id, tenant_id, variable, slope, offset_val, sensor_hardware_id,
        )
        if not orion_ok:
            logger.warning(
                "Calibration period %s created but Orion-LD update failed "
                "(calibrationConfig may be stale)", row['id'],
            )

        logger.info(
            "Created calibration period %s for sensor=%s variable=%s",
            row['id'], sensor_id, variable,
        )

        return jsonify({
            'period': new_period,
            'orion_updated': orion_ok,
        }), 201

    except Exception as e:
        logger.error(
            "Error creating calibration period for sensor %s: %s", sensor_id, e
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return_db_connection(conn)
        return jsonify({'error': 'Database error', 'details': str(e)}), 500


# =============================================================================
# PATCH /api/entities/sensors/{sensor_id}/reliability
# =============================================================================

@calibration_bp.route('/api/entities/sensors/<sensor_id>/reliability', methods=['PATCH'])
def set_sensor_reliability(sensor_id: str):
    """Set reliability status for a sensor.

    Internal endpoint — authenticated by X-Internal-Service-Secret (not user JWT).

    Headers:
      X-Internal-Service-Secret  — must match configured secret
      X-Tenant-ID                — tenant context

    Body:
    {
        "reliabilityStatus": "optimal" | "maintenance" | "degraded" | "error"
    }

    If status is "optimal", also resolves any active Alert entities for this sensor.
    """
    # ── Verify internal service secret ─────────────────────────────────
    provided_secret = request.headers.get('X-Internal-Service-Secret', '')
    expected_secret = os.getenv('INTERNAL_SERVICE_SECRET', '')

    if not expected_secret:
        logger.error("INTERNAL_SERVICE_SECRET not configured on server")
        return jsonify({'error': 'Internal server configuration error'}), 500

    if provided_secret != expected_secret:
        logger.warning(
            "Invalid X-Internal-Service-Secret for reliability PATCH on %s", sensor_id
        )
        return jsonify({'error': 'Unauthorized'}), 401

    # ── Extract tenant ─────────────────────────────────────────────────
    tenant_id = request.headers.get('X-Tenant-ID')
    if not tenant_id:
        return jsonify({'error': 'X-Tenant-ID header is required'}), 400

    # ── Validate request body ──────────────────────────────────────────
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    reliability_status = data.get('reliabilityStatus')
    valid_statuses = {'optimal', 'maintenance', 'degraded', 'error'}

    if not reliability_status or reliability_status not in valid_statuses:
        return jsonify({
            'error': 'reliabilityStatus is required',
            'valid_values': sorted(valid_statuses),
        }), 400

    now_iso = datetime.now(timezone.utc).isoformat()

    # ── PATCH the sensor entity in Orion-LD ────────────────────────────
    try:
        patch_body = {
            'reliabilityStatus': {
                'type': 'Property',
                'value': reliability_status,
                'observedAt': now_iso,
            },
        }

        headers = inject_fiware_headers(
            {'Content-Type': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )

        resp = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/entities/{sensor_id}/attrs",
            json=patch_body,
            headers=headers,
            timeout=10,
        )

        if resp.status_code not in (200, 204):
            logger.error(
                "Failed to PATCH reliabilityStatus for sensor %s: %s - %s",
                sensor_id, resp.status_code, resp.text,
            )
            return jsonify({'error': 'Failed to update sensor in context broker'}), 502

        logger.info(
            "Set reliabilityStatus=%s for sensor %s (tenant=%s)",
            reliability_status, sensor_id, tenant_id,
        )

    except requests.RequestException as e:
        logger.error("Orion-LD request error for sensor %s: %s", sensor_id, e)
        return jsonify({'error': 'Failed to communicate with context broker'}), 502

    # ── If status is "optimal", close active Alert entities ────────────
    alerts_resolved = 0
    if reliability_status == 'optimal':
        try:
            # Query Orion-LD for active Alerts related to this sensor
            alert_headers = inject_fiware_headers(
                {'Accept': 'application/json'}, tenant=tenant_id, has_context_in_body=False
            )
            alert_resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                params={
                    'type': 'Alert',
                    'q': f'refSensor=="{sensor_id}";status=="active"',
                    'limit': 50,
                },
                headers=alert_headers,
                timeout=10,
            )

            if alert_resp.status_code == 200:
                alerts = alert_resp.json()
                if isinstance(alerts, list):
                    for alert in alerts:
                        alert_id = alert.get('id')
                        if not alert_id:
                            continue
                        try:
                            resolve_body = {
                                'status': {
                                    'type': 'Property',
                                    'value': 'resolved',
                                },
                                'resolvedAt': {
                                    'type': 'Property',
                                    'value': now_iso,
                                },
                            }
                            resolve_headers = inject_fiware_headers(
                                {'Content-Type': 'application/json'},
                                tenant=tenant_id,
                                has_context_in_body=False,
                            )
                            resolve_resp = requests.patch(
                                f"{ORION_URL}/ngsi-ld/v1/entities/{alert_id}/attrs",
                                json=resolve_body,
                                headers=resolve_headers,
                                timeout=10,
                            )
                            if resolve_resp.status_code in (200, 204):
                                alerts_resolved += 1
                        except Exception as e:
                            logger.error(
                                "Failed to resolve alert %s: %s", alert_id, e,
                            )

            logger.info(
                "Resolved %d active alert(s) for sensor %s",
                alerts_resolved, sensor_id,
            )

        except requests.RequestException as e:
            logger.error(
                "Error querying/resolving alerts for sensor %s: %s", sensor_id, e,
            )

    return jsonify({
        'status': 'updated',
        'sensor_id': sensor_id,
        'reliabilityStatus': reliability_status,
        'observedAt': now_iso,
        'alertsResolved': alerts_resolved,
    }), 200
