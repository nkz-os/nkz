"""
Weather API — standalone FastAPI service.
Extracted from entity-manager (blueprints/weather.py).

Serves all weather endpoints: municipalities, locations, observations,
parcel weather with spatial downscaling, agro-status, and alerts.
"""

import sys

# Ensure /app is on the path so common/ imports work inside the container
if "/app" not in sys.path:
    sys.path.insert(0, "/app")
