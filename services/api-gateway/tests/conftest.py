import os
import sys
import pytest

# Ensure the service module and its sibling `common` package are importable.
SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_DIR)
sys.path.insert(0, os.path.join(SERVICE_DIR, "..", "common"))

os.environ.setdefault("KEYCLOAK_URL", "http://keycloak-test:8080/auth")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("MINIO_ENDPOINT", "minio-test:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "testkey")
os.environ.setdefault("MINIO_SECRET_KEY", "testsecret")
os.environ.setdefault("ORION_URL", "http://orion-test:1026")
os.environ.setdefault("CONTEXT_URL", "http://ctx-test/context.json")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")


@pytest.fixture
def app():
    import fiware_api_gateway as gw

    gw.app.config.update(TESTING=True)
    return gw


@pytest.fixture
def client(app):
    return app.app.test_client()
