"""Route arguments that name an entity are only ids."""

from flask import jsonify, request

from ngsi_read_guard import valid_identifier

ROUTE_IDS = frozenset({"entity_id", "entity_type", "sensor_id", "parcel_id", "device_urn", "device_id"})


def install_identifier_check(app) -> None:
    @app.before_request
    def check_route_identifiers():
        if request.method == "OPTIONS":
            return None
        for name, value in (request.view_args or {}).items():
            if name in ROUTE_IDS and not valid_identifier(value):
                return jsonify({"error": "Invalid identifier"}), 400
        return None
