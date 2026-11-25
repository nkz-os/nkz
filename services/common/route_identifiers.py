"""Route arguments that name an entity are only ids (Flask services)."""

from flask import jsonify, request

from ngsi_read_guard import valid_identifier


def install_identifier_check(app, names) -> None:
    """Answer 400 when one of `names` in the matched route is not an id."""
    checked = frozenset(names)

    @app.before_request
    def check_route_identifiers():
        if request.method == "OPTIONS":
            return None
        for name, value in (request.view_args or {}).items():
            if name in checked and not valid_identifier(value):
                return jsonify({"error": "Invalid identifier"}), 400
        return None
