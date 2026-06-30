#!/usr/bin/env python3
# =============================================================================
# MQTT Credentials Manager Service
# =============================================================================
# Servicio para gestionar credenciales MQTT por dispositivo
# Genera usuarios/contraseñas únicos y gestiona ACLs en Mosquitto
# =============================================================================

import os
import re
import subprocess
import hashlib
import secrets
import sys
import logging
from typing import Dict, Optional, Tuple
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
_cors_origins = [o.strip() for o in os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',') if o.strip()]
CORS(app, origins=_cors_origins, supports_credentials=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add common directory to path for keycloak_auth
common_paths = [
    os.path.join(os.path.dirname(__file__), '..', 'common'),
    '/common'
]
for path in common_paths:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

# Import authentication
try:
    from keycloak_auth import require_keycloak_auth
    AUTH_AVAILABLE = True
except ImportError:
    AUTH_AVAILABLE = False
    logger.warning("Keycloak auth not available — MQTT credentials API is UNPROTECTED")

# Configuration
MOSQUITTO_POD = os.getenv('MOSQUITTO_POD', 'mosquitto')
MOSQUITTO_NAMESPACE = os.getenv('MOSQUITTO_NAMESPACE', 'nekazari')
MOSQUITTO_PASSWD_PATH = '/mosquitto/config/passwords'
MOSQUITTO_ACL_PATH = '/mosquitto/config/acl.conf'

# Input validation pattern: only allow alphanumeric, hyphens, underscores
SAFE_IDENTIFIER = re.compile(r'^[a-zA-Z0-9_-]+$')

def validate_identifier(value: str, field_name: str) -> bool:
    """Validate that an identifier contains only safe characters"""
    if not value or len(value) > 128:
        return False
    if not SAFE_IDENTIFIER.match(value):
        logger.warning(f"Invalid characters in {field_name}: {value!r}")
        return False
    return True


def generate_mqtt_credentials() -> Tuple[str, str]:
    """Generate unique MQTT username and password"""
    # Username: device_{tenant_id}_{device_id} (sanitized)
    # Password: random 32-byte hex string
    password = secrets.token_hex(16)  # 32 characters
    return password


def hash_mqtt_password(password: str) -> str:
    """Hash password using mosquitto_passwd format (PBKDF2)"""
    # Use mosquitto_passwd command to generate hash
    # Format: $7$rounds=...$salt$hash
    try:
        result = subprocess.run(
            ['mosquitto_passwd', '-b', '-', 'temp_user', password],
            input=password.encode(),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Extract hash from output
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if ':' in line:
                    return line.split(':')[1]
        # Fallback: use simple hash (less secure but works)
        logger.warning("mosquitto_passwd not available, using fallback hash")
        return hashlib.sha256(password.encode()).hexdigest()  # lgtm[py/weak-sensitive-data-hashing]
    except Exception as e:
        logger.error(f"Error generating password hash: {e}")
        return hashlib.sha256(password.encode()).hexdigest()  # lgtm[py/weak-sensitive-data-hashing]


def create_mqtt_user_in_mosquitto(username: str, password: str, tenant_id: str, device_id: str) -> bool:
    """Create MQTT user in Mosquitto pod"""
    try:
        # Validate all inputs before using in commands
        for name, val in [('username', username), ('tenant_id', tenant_id), ('device_id', device_id)]:
            if not validate_identifier(val, name):
                logger.error(f"Invalid {name} rejected: {val!r}")
                return False

        # Generate password hash
        password_hash = hash_mqtt_password(password)

        # Create user entry via stdin (no shell interpolation)
        user_entry = f"{username}:{password_hash}\n"

        # Use 'tee -a' with stdin to avoid shell injection
        cmd = [
            'kubectl', 'exec', '-i', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'tee', '-a', MOSQUITTO_PASSWD_PATH
        ]

        result = subprocess.run(cmd, input=user_entry, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            logger.error(f"Failed to add user to Mosquitto: {result.stderr}")
            return False

        # Reload Mosquitto configuration
        reload_cmd = [
            'kubectl', 'exec', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'pkill', '-HUP', 'mosquitto'
        ]
        subprocess.run(reload_cmd, timeout=5)

        # Add ACL entries via stdin (no shell interpolation)
        acl_entries = f"\nuser {username}\ntopic write {tenant_id}/{device_id}/data\ntopic read {tenant_id}/{device_id}/cmd\n"

        acl_cmd = [
            'kubectl', 'exec', '-i', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'tee', '-a', MOSQUITTO_ACL_PATH
        ]

        result = subprocess.run(acl_cmd, input=acl_entries, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            logger.error(f"Failed to add ACL entries: {result.stderr}")
            return False

        logger.info(f"Created MQTT user {username} for device {device_id} in tenant {tenant_id}")
        return True

    except Exception as e:
        logger.error(f"Error creating MQTT user: {e}")
        return False


def _kubectl_cat_file(path: str) -> subprocess.CompletedProcess:
    """Read a file from the Mosquitto pod without shell interpolation."""
    return subprocess.run(
        [
            'kubectl', 'exec', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'cat', path,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _kubectl_write_file(path: str, content: str) -> subprocess.CompletedProcess:
    """Overwrite a file in the Mosquitto pod via stdin (no shell)."""
    return subprocess.run(
        [
            'kubectl', 'exec', '-i', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'tee', path,
        ],
        input=content,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _filter_password_file(content: str, username: str) -> str:
    prefix = f"{username}:"
    lines = [line for line in content.splitlines() if not line.startswith(prefix)]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _filter_acl_user_block(content: str, username: str) -> str:
    """Drop the ACL stanza for `user <username>` through the following blank line."""
    user_line = f"user {username}"
    out: list[str] = []
    skip = False
    for line in content.splitlines():
        if line.strip() == user_line:
            skip = True
            continue
        if skip:
            if line.strip() == "":
                skip = False
            continue
        out.append(line)
    if not out:
        return ""
    return "\n".join(out) + "\n"


def delete_mqtt_user_from_mosquitto(username: str) -> bool:
    """Delete MQTT user from Mosquitto"""
    try:
        # Validate username before using in commands
        if not validate_identifier(username, 'username'):
            logger.error(f"Invalid username rejected for deletion: {username!r}")
            return False

        passwd_result = _kubectl_cat_file(MOSQUITTO_PASSWD_PATH)
        if passwd_result.returncode != 0:
            logger.error(f"Failed to read passwords file: {passwd_result.stderr}")
            return False

        filtered_passwd = _filter_password_file(passwd_result.stdout, username)
        write_result = _kubectl_write_file(MOSQUITTO_PASSWD_PATH, filtered_passwd)
        if write_result.returncode != 0:
            logger.error(f"Failed to remove user from passwords: {write_result.stderr}")
            return False

        acl_result = _kubectl_cat_file(MOSQUITTO_ACL_PATH)
        if acl_result.returncode != 0:
            logger.error(f"Failed to read ACL file: {acl_result.stderr}")
            return False

        filtered_acl = _filter_acl_user_block(acl_result.stdout, username)
        acl_write = _kubectl_write_file(MOSQUITTO_ACL_PATH, filtered_acl)
        if acl_write.returncode != 0:
            logger.error(f"Failed to remove ACL entries: {acl_write.stderr}")
            return False

        # Reload Mosquitto
        reload_cmd = [
            'kubectl', 'exec', '-n', MOSQUITTO_NAMESPACE, MOSQUITTO_POD,
            '--', 'pkill', '-HUP', 'mosquitto'
        ]
        subprocess.run(reload_cmd, timeout=5)

        logger.info(f"Deleted MQTT user {username}")
        return True

    except Exception as e:
        logger.error(f"Error deleting MQTT user: {e}")
        return False


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'mqtt-credentials-manager'
    }), 200


@app.route('/api/mqtt/credentials/create', methods=['POST'])
@(require_keycloak_auth if AUTH_AVAILABLE else lambda f: f)
def create_credentials():
    """Create MQTT credentials for a device"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        tenant_id = data.get('tenant_id')
        device_id = data.get('device_id')

        if not tenant_id or not device_id:
            return jsonify({'error': 'Missing tenant_id or device_id'}), 400

        # Validate inputs
        if not validate_identifier(tenant_id, 'tenant_id') or not validate_identifier(device_id, 'device_id'):
            return jsonify({'error': 'Invalid tenant_id or device_id. Only alphanumeric, hyphens, and underscores allowed.'}), 400

        # Generate username (sanitized)
        username = f"device_{tenant_id}_{device_id}".replace('-', '_').replace('.', '_')
        
        # Generate password
        password = generate_mqtt_credentials()
        
        # Create user in Mosquitto
        success = create_mqtt_user_in_mosquitto(username, password, tenant_id, device_id)
        
        if not success:
            return jsonify({'error': 'Failed to create MQTT user'}), 500
        
        return jsonify({
            'success': True,
            'username': username,
            'password': password,  # Only returned on creation
            'mqtt_host': os.getenv('MQTT_HOST', 'mosquitto-service'),
            'mqtt_port': int(os.getenv('MQTT_PORT', '1883')),
            'topics': {
                'data': f'{tenant_id}/{device_id}/data',
                'commands': f'{tenant_id}/{device_id}/cmd'
            },
            'warning': 'Save these credentials securely. Password cannot be retrieved later.'
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating MQTT credentials: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/mqtt/credentials/delete', methods=['POST'])
@(require_keycloak_auth if AUTH_AVAILABLE else lambda f: f)
def delete_credentials():
    """Delete MQTT credentials for a device"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        tenant_id = data.get('tenant_id')
        device_id = data.get('device_id')

        if not tenant_id or not device_id:
            return jsonify({'error': 'Missing tenant_id or device_id'}), 400

        # Validate inputs
        if not validate_identifier(tenant_id, 'tenant_id') or not validate_identifier(device_id, 'device_id'):
            return jsonify({'error': 'Invalid tenant_id or device_id. Only alphanumeric, hyphens, and underscores allowed.'}), 400

        username = f"device_{tenant_id}_{device_id}".replace('-', '_').replace('.', '_')
        
        success = delete_mqtt_user_from_mosquitto(username)
        
        if not success:
            return jsonify({'error': 'Failed to delete MQTT user'}), 500
        
        return jsonify({
            'success': True,
            'message': f'MQTT credentials deleted for device {device_id}'
        }), 200
        
    except Exception as e:
        logger.error(f"Error deleting MQTT credentials: {e}")
        return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

