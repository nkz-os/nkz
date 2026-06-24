"""Tests for _guess_dist_content_type MIME type mapping.

Ensures that all dist/ file extensions relevant to Module Federation 2.0
and Vite output receive the correct Content-Type for MinIO upload.

The function under test lives in blueprints/modules.py but that module
has a circular import with entity_management_api. We mock the gateway
module to break the cycle and test the pure function in isolation.
"""

import sys
import os
from unittest.mock import MagicMock, patch

# Environment variables required by module-level code
os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("ASSETS_BUCKET", "test-bucket")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

# entity-manager tests need both the entity-manager dir (for blueprints.*)
# and the parent services/ dir (for common.*).
_entity_mgr_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_services_dir = os.path.normpath(os.path.join(_entity_mgr_dir, ".."))
for d in (_entity_mgr_dir, _services_dir):
    if d not in sys.path:
        sys.path.insert(0, d)

# Break the circular import: entity_management_api imports blueprints.modules,
# which imports from entity_management_api. Mock entity_management_api before
# importing blueprints.modules so the module-level code doesn't cascade.
_mock_entity_mgmt = MagicMock()
sys.modules["entity_management_api"] = _mock_entity_mgmt

from blueprints.modules import _guess_dist_content_type


def test_js_extensions():
    """All JavaScript-related extensions must map to application/javascript."""
    assert _guess_dist_content_type("remoteEntry.js") == "application/javascript"
    assert _guess_dist_content_type("chunk-abc123.js") == "application/javascript"
    assert _guess_dist_content_type("virtualExposes-DALhD3FO.js") == "application/javascript"


def test_mjs_extension():
    """Pure .mjs files must map to application/javascript (preventive coverage)."""
    assert _guess_dist_content_type("module.mjs") == "application/javascript"
    assert _guess_dist_content_type("loadShare.mjs") == "application/javascript"


def test_mf_virtual_module_naming():
    """MF2 virtual module files (hash-suffixed) must be detected by last extension."""
    assert _guess_dist_content_type(
        "_virtual_mf___mfe_internal__datahub__loadShare__react__loadShare__.mjs-BPpBxphf.js"
    ) == "application/javascript"
    assert _guess_dist_content_type(
        "_virtual_mf-localSharedImportMap___mfe_internal__vegetation_prime-DKKhlX91.js"
    ) == "application/javascript"


def test_manifest_and_json():
    assert _guess_dist_content_type("mf-manifest.json") == "application/json"
    assert _guess_dist_content_type("manifest.json") == "application/json"
    assert _guess_dist_content_type("chunk.js.map") == "application/json"


def test_css():
    assert _guess_dist_content_type("styles.css") == "text/css"


def test_html():
    assert _guess_dist_content_type("index.html") == "text/html"


def test_images():
    assert _guess_dist_content_type("icon.png") == "image/png"
    assert _guess_dist_content_type("photo.jpg") == "image/jpeg"
    assert _guess_dist_content_type("photo.jpeg") == "image/jpeg"
    assert _guess_dist_content_type("logo.svg") == "image/svg+xml"
    assert _guess_dist_content_type("animated.gif") == "image/gif"


def test_fonts():
    assert _guess_dist_content_type("font.woff") == "font/woff"
    assert _guess_dist_content_type("font.woff2") == "font/woff2"


def test_unknown_extension_falls_back_to_octet_stream():
    assert _guess_dist_content_type("file.xyz") == "application/octet-stream"
    assert _guess_dist_content_type("noextension") == "application/octet-stream"


def test_empty_filename():
    assert _guess_dist_content_type("") == "application/octet-stream"


def test_none_filename():
    assert _guess_dist_content_type(None) == "application/octet-stream"
