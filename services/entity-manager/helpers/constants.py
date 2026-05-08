"""
Constants shared across entity-manager blueprints.
Imported from entity_management_api at top level; moved here to avoid circular imports.
"""

import os

POSTGRES_URL = os.getenv('POSTGRES_URL')
ORION_URL = os.getenv('ORION_URL')

# Get URLs from config manager or construct from PRODUCTION_DOMAIN
try:
    from common.config_manager import ConfigManager

    KEYCLOAK_PUBLIC_URL = ConfigManager.get_keycloak_public_url()
    CONTEXT_URL = os.getenv('CONTEXT_URL', '')
    if not CONTEXT_URL:
        domain = ConfigManager.get_production_domain()
        CONTEXT_URL = f'https://{domain}/ngsi-ld-context.json'
except ImportError:
    PRODUCTION_DOMAIN = os.getenv('PRODUCTION_DOMAIN', '')
    KEYCLOAK_PUBLIC_URL = (
        os.getenv(
            'KEYCLOAK_PUBLIC_URL',
            f'https://{PRODUCTION_DOMAIN}/auth' if PRODUCTION_DOMAIN else '',
        )
        .rstrip('/')
    )
    CONTEXT_URL = os.getenv(
        'CONTEXT_URL',
        f'https://{PRODUCTION_DOMAIN}/ngsi-ld-context.json' if PRODUCTION_DOMAIN else '',
    )

KEYCLOAK_REALM = os.getenv('KEYCLOAK_REALM', 'nekazari')

# Limits and controlled entity types
MAX_ROBOTS = int(os.getenv('MAX_ROBOTS', '999999'))
MAX_SENSORS = int(os.getenv('MAX_SENSORS', '999999'))
MAX_AREA_HECTARES = float(os.getenv('MAX_AREA_HECTARES', '1000000000'))
ROBOT_ENTITY_TYPES = {
    t.strip()
    for t in os.getenv('ROBOT_ENTITY_TYPES', 'AgriculturalRobot').split(',')
    if t.strip()
}
SENSOR_ENTITY_TYPES = {
    t.strip()
    for t in os.getenv('SENSOR_ENTITY_TYPES', 'AgriSensor').split(',')
    if t.strip()
}
# Only FIWARE Smart Data Model types are valid parcels.
# 'Parcel', 'Vineyard', 'OliveGrove' are NOT FIWARE SDM types.
# Use AgriParcel with category/cropType attributes for differentiation.
PARCEL_ENTITY_TYPES = {
    t.strip()
    for t in os.getenv(
        'PARCEL_ENTITY_TYPES',
        'AgriParcel',
    ).split(',')
    if t.strip()
}
ENTITY_BASE_PATH = os.getenv('ENTITY_BASE_PATH', '/app/config/entities')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
