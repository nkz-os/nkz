"""Tests for module publish and versioned deploy routes."""

import json
import os
import sys
from functools import wraps
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level mocks — must be set BEFORE importing entity_management_api
# ---------------------------------------------------------------------------

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("ASSETS_BUCKET", "test-bucket")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

_common_mock = MagicMock()


def _require_auth(f=None, **kwargs):
    """Smart mock: returns 401 when no auth cookie/header is present."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            from flask import request, g, jsonify

            token = request.cookies.get("nkz_token") or request.headers.get(
                "Authorization", ""
            )
            if not token:
                return jsonify({"error": "Unauthorized"}), 401
            g.current_user = {
                "sub": "test-user",
                "tenant_id": "test-tenant",
                "realm_access": {"roles": ["PlatformAdmin"]},
            }
            g.roles = ["PlatformAdmin"]
            g.tenant_id = "test-tenant"
            g.tenant = "test-tenant"
            g.farmer_id = "test-farmer"
            return func(*args, **kw)

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules["common"] = _common_mock
sys.modules["common.auth_middleware"] = _common_mock
sys.modules["common.ngsi_headers"] = _common_mock
sys.modules["common.config_manager"] = MagicMock()
# parcel_activation.py imports common.tier_quotas at module level; the parent
# "common" stub is a plain MagicMock (not a package), so the submodule must be
# pre-seeded for `from common.tier_quotas import ...` to resolve.
_tier_quotas_mock = MagicMock()
_tier_quotas_mock.LEVEL_TO_TIER = {0: "free"}
_tier_quotas_mock.quotas_for_tier = lambda tier: {"max_parcels": 0}
sys.modules["common.tier_quotas"] = _tier_quotas_mock

import importlib.util

_api_errors_path = os.path.join(_services_dir, "common", "api_errors.py")
_api_spec = importlib.util.spec_from_file_location("common.api_errors", _api_errors_path)
_api_errors_mod = importlib.util.module_from_spec(_api_spec)
assert _api_spec.loader is not None
_api_spec.loader.exec_module(_api_errors_mod)
sys.modules["common.api_errors"] = _api_errors_mod

# blueprints/entities.py imports the pure payload guard at module level.
_guard_path = os.path.join(_services_dir, "common", "ngsi_payload_guard.py")
_guard_spec = importlib.util.spec_from_file_location("common.ngsi_payload_guard", _guard_path)
_guard_mod = importlib.util.module_from_spec(_guard_spec)
assert _guard_spec.loader is not None
_guard_spec.loader.exec_module(_guard_mod)
sys.modules["common.ngsi_payload_guard"] = _guard_mod

# modules.py imports common.internal_auth at module level. It only needs os and
# logging, so load the real thing rather than a mock: the header contract is
# exactly what these tests exercise.
_ia_path = os.path.join(_services_dir, "common", "internal_auth.py")
_ia_spec = importlib.util.spec_from_file_location("common.internal_auth", _ia_path)
_ia_mod = importlib.util.module_from_spec(_ia_spec)
assert _ia_spec.loader is not None
_ia_spec.loader.exec_module(_ia_mod)
sys.modules["common.internal_auth"] = _ia_mod

# Heavy / infrastructure dependencies
sys.modules["db_helper"] = MagicMock()

from tests.real_common import seed_real_common  # noqa: E402

seed_real_common()
sys.modules["orion_writer"] = MagicMock()
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()

# Mock geo_utils to avoid shapely import error
_geo_utils_mock = MagicMock()
_geo_utils_mock.get_parcel_location.return_value = {"lat": 42.0, "lon": -2.0}
sys.modules["geo_utils"] = _geo_utils_mock

import pytest  # noqa: E402
from entity_management_api import app  # noqa: E402


@pytest.fixture
def client():
    """Test client with Authorization header."""
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_versioned_deploy_is_reachable(client):
    # versioned handler validates {version}; bad version => 400 mentioning "version"
    r = client.post(
        "/api/modules/demo/deploy",
        headers={"Authorization": "Bearer x"},
        json={"version": "ZZZ"},
    )
    assert r.status_code == 400
    assert "version" in r.get_json()["error"].lower()


def test_legacy_deploy_upload_removed(client):
    r = client.post(
        "/api/modules/demo/deploy-upload",
        headers={"Authorization": "Bearer x"},
        json={},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Internal publish endpoint tests
# ---------------------------------------------------------------------------

from io import BytesIO  # noqa: E402

_MANIFEST = (
    b'{"id":"demo","version":"1.0.0","hostApiVersion":"1.0",'
    b'"name":"demo","display_name":"Demo"}'
)


def _dist_form():
    return {
        "version_hash": "abc1234",
        "file": [
            (BytesIO(_MANIFEST), "manifest.json"),
            (BytesIO(b"{}"), "mf-manifest.json"),
        ],
    }


def test_publish_rejected_without_secret(client):
    r = client.post(
        "/api/internal/modules/demo/publish",
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 401


def test_publish_rejected_wrong_secret(client):
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "nope"},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 401


def test_publish_ok_with_secret(client, monkeypatch):
    import blueprints.modules as m

    monkeypatch.setattr(
        m,
        "_upload_dist_and_activate",
        lambda mid, files, manifest, vh: (201, {"success": True, "version_hash": vh}),
    )
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "test-secret"},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert r.get_json()["version_hash"] == "abc1234"


def test_publish_rejects_version_hash_that_is_not_the_token_commit(client, monkeypatch):
    import blueprints.modules as m

    upload = MagicMock()
    monkeypatch.setattr(m, "_upload_dist_and_activate", upload)
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "test-secret", "X-OIDC-Sha": "d" * 40},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "commit of the OIDC token" in r.get_json()["error"]
    upload.assert_not_called()


def test_publish_records_who_published_what(client, monkeypatch):
    import blueprints.modules as m

    monkeypatch.setattr(
        m,
        "_upload_dist_and_activate",
        lambda mid, files, manifest, vh: (201, {"success": True, "version_hash": vh}),
    )
    recorded = {}
    monkeypatch.setattr(
        m,
        "_record_publish_provenance",
        lambda mid, repo, sha: recorded.update(module=mid, repo=repo, sha=sha),
    )
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={
            "X-Internal-Service-Secret": "test-secret",
            "X-OIDC-Repository": "nkz-os/demo",
            "X-OIDC-Sha": "abc1234",
        },
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert recorded == {"module": "demo", "repo": "nkz-os/demo", "sha": "abc1234"}


def test_failed_upload_records_nothing(client, monkeypatch):
    import blueprints.modules as m

    monkeypatch.setattr(
        m, "_upload_dist_and_activate", lambda mid, files, manifest, vh: (500, {"error": "s3"})
    )
    record = MagicMock()
    monkeypatch.setattr(m, "_record_publish_provenance", record)
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "test-secret", "X-OIDC-Sha": "abc1234"},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 500
    record.assert_not_called()


def test_invalidate_gateway_route_cache_posts_routes_key(monkeypatch):
    import blueprints.modules as m

    posted = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        posted["timeout"] = timeout
        return MagicMock(status_code=200, text="")

    monkeypatch.setattr(m.requests, "post", fake_post)
    monkeypatch.setenv("API_GATEWAY_URL", "http://api-gateway-service:5000")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    m._invalidate_gateway_route_cache()
    assert posted["url"] == "http://api-gateway-service:5000/internal/cache/invalidate"
    assert posted["json"] == {"key": "routes"}
    assert posted["timeout"] == 5
    # The gateway guard fails closed: without this header the call is a 401.
    assert posted["headers"] == {
        "X-Internal-Service-Secret": "correct-horse-battery-staple"
    }


def test_upload_dist_and_activate_invalidates_route_cache(monkeypatch):
    import blueprints.modules as m

    invalidate = MagicMock()
    monkeypatch.setattr(m, "_invalidate_gateway_route_cache", invalidate)
    monkeypatch.setattr(m, "_get_frontend_s3_client", lambda: None)

    status, payload = m._upload_dist_and_activate("demo", [], {"id": "demo", "version": "1"}, "abc1234")
    assert status == 503
    invalidate.assert_not_called()

    s3 = MagicMock()
    monkeypatch.setattr(m, "_get_frontend_s3_client", lambda: s3)
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda _c: None)

    manifest = {"id": "demo", "version": "1.0.0"}
    with app.app_context():
        status, payload = m._upload_dist_and_activate("demo", [], manifest, "abc1234")
    assert status == 201
    invalidate.assert_called_once()


def _publish_upsert(monkeypatch, manifest):
    """Run _upload_dist_and_activate with mocked S3/DB; return (sql, params) of the upsert."""
    import blueprints.modules as m

    monkeypatch.setattr(m, "_invalidate_gateway_route_cache", MagicMock())
    monkeypatch.setattr(m, "_get_frontend_s3_client", lambda: MagicMock())
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda _c: None)
    with app.app_context():
        status, _ = m._upload_dist_and_activate("demo", [], manifest, "abc1234")
    assert status == 201
    for call in cur.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO marketplace_modules" in sql:
            return sql, call.args[1]
    raise AssertionError("marketplace_modules upsert not executed")


_ROLES_UPDATE = "required_roles = COALESCE(%s::text[], marketplace_modules.required_roles)"


def test_publish_without_required_roles_preserves_db_roles(monkeypatch):
    sql, params = _publish_upsert(monkeypatch, {"id": "demo", "version": "1.0.0"})
    assert _ROLES_UPDATE in sql
    assert params[-1] is None
    # New rows still get a role so the module is visible to someone.
    assert ["Farmer"] in params


def test_publish_with_required_roles_overwrites(monkeypatch):
    roles = ["Farmer", "TechnicalConsultant"]
    sql, params = _publish_upsert(
        monkeypatch, {"id": "demo", "version": "1.0.0", "requiredRoles": roles}
    )
    assert _ROLES_UPDATE in sql
    assert params[-1] == roles


# ---------------------------------------------------------------------------
# FIWARE publish gate (_fiware_publish_gate)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402
import blueprints.modules as _mod  # noqa: E402


def _gate_conn(fiware, deployed_version):
    cur = MagicMock()
    cur.fetchone.return_value = (fiware, deployed_version)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_fiware_gate_stamps_compliant_and_proceeds():
    """Publish stamps compliant and proceeds (OIDC chain auth)."""
    conn, cur = _gate_conn({"status": "pending"}, None)
    with patch.object(_mod, "get_db_connection_simple", return_value=conn), \
         patch.object(_mod, "return_db_connection"):
        result = _mod._fiware_publish_gate("demo")
    assert result is None  # proceed
    assert any("UPDATE marketplace_modules" in str(c.args[0])
               for c in cur.execute.call_args_list), "should stamp compliant"
    conn.commit.assert_called_once()


def test_fiware_gate_returns_404_if_module_not_found():
    """No marketplace_modules row => 404."""
    cur = MagicMock()
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur
    with app.app_context(), \
         patch.object(_mod, "get_db_connection_simple", return_value=conn), \
         patch.object(_mod, "return_db_connection"):
        result = _mod._fiware_publish_gate("nonexistent")
    assert result is not None
    assert result[1] == 404


# ---------------------------------------------------------------------------
# A bundle under modules/<id>/ is served to every tenant: only the platform
# may write it. TenantAdmin must be refused on dist, deploy and rollback.
# ---------------------------------------------------------------------------

import pytest as _pytest


@_pytest.mark.parametrize(
    "path",
    [
        "/api/modules/some-module/dist",
        "/api/modules/some-module/deploy",
        "/api/modules/some-module/rollback",
    ],
)
def test_bundle_routes_refuse_tenant_admin(client, monkeypatch, path):
    import blueprints.modules as m

    monkeypatch.setattr(m, "_get_user_roles", lambda: ["TenantAdmin", "Farmer"])
    r = client.post(path, headers={"Authorization": "Bearer t"}, data={})
    assert r.status_code == 403
    assert "PlatformAdmin" in r.get_json()["error"]


@_pytest.mark.parametrize(
    "path",
    [
        "/api/modules/some-module/dist",
        "/api/modules/some-module/deploy",
        "/api/modules/some-module/rollback",
    ],
)
def test_bundle_routes_let_platform_admin_reach_validation(client, monkeypatch, path):
    import blueprints.modules as m

    monkeypatch.setattr(m, "_get_user_roles", lambda: ["PlatformAdmin"])
    r = client.post(path, headers={"Authorization": "Bearer t"}, data={})
    # Past the role gate: an empty body is a validation error, not a 403.
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Translated module names and descriptions (label_i18n / description_i18n)
# ---------------------------------------------------------------------------


def _published_metadata(monkeypatch, manifest):
    """Metadata JSON that a publish of ``manifest`` sends to the upsert."""
    _, params = _publish_upsert(monkeypatch, {"id": "demo", "version": "1.0.0", **manifest})
    candidates = [p for p in params if isinstance(p, str) and p.startswith("{")]
    assert len(candidates) == 1
    return json.loads(candidates[0])



def test_publish_without_description_i18n_leaves_the_key_out(monkeypatch):
    """An absent key keeps the stored translations through the jsonb merge."""
    metadata = _published_metadata(monkeypatch, {})
    assert "description_i18n" not in metadata


def test_publish_with_empty_description_i18n_leaves_the_key_out(monkeypatch):
    metadata = _published_metadata(monkeypatch, {"description_i18n": {}})
    assert "description_i18n" not in metadata


def test_publish_with_description_i18n_stores_it(monkeypatch):
    texts = {"es": "Hola", "en": "Hello"}
    metadata = _published_metadata(monkeypatch, {"description_i18n": texts})
    assert metadata["description_i18n"] == texts


def test_publish_with_only_unusable_description_i18n_leaves_the_key_out(monkeypatch):
    """Nothing usable must not replace the stored translations with junk."""
    metadata = _published_metadata(monkeypatch, {"description_i18n": {"de": "Hallo", "es": " "}})
    assert "description_i18n" not in metadata


def test_publish_ignores_a_non_dict_description_i18n(monkeypatch):
    metadata = _published_metadata(monkeypatch, {"description_i18n": "Hola"})
    assert "description_i18n" not in metadata


def test_publish_stores_navigation_label_as_label_i18n(monkeypatch):
    label = {"es": "Planificación", "en": "Planning", "eu": "Plangintza"}
    metadata = _published_metadata(monkeypatch, {"navigation": {"label": label}})
    assert metadata["label_i18n"] == label


def test_publish_filters_navigation_label(monkeypatch):
    label = {
        "es": "  Planificación ",   # trimmed
        "en": "x" * 80,             # at the limit: kept
        "fr": "x" * 81,             # too long: dropped
        "de": "Planung",            # unsupported language: dropped
        "pt": "",                   # empty: dropped
        "ca": "   ",                # blank: dropped
        "eu": 3,                    # not a string: dropped
    }
    metadata = _published_metadata(monkeypatch, {"navigation": {"label": label}})
    assert metadata["label_i18n"] == {"es": "Planificación", "en": "x" * 80}


@pytest.mark.parametrize(
    "navigation",
    [
        None,
        "menu",
        {},
        {"section": "tools"},
        {"label": "Planning"},
        {"label": {}},
        {"label": {"de": "Planung"}},
    ],
)
def test_publish_without_usable_label_leaves_label_i18n_out(monkeypatch, navigation):
    """No usable label: the key is omitted so the stored one survives the merge."""
    manifest = {} if navigation is None else {"navigation": navigation}
    metadata = _published_metadata(monkeypatch, manifest)
    assert "label_i18n" not in metadata


def test_publish_metadata_is_merged_not_replaced(monkeypatch):
    sql, _ = _publish_upsert(monkeypatch, {"id": "demo", "version": "1.0.0"})
    assert "metadata = COALESCE(marketplace_modules.metadata, '{}'::jsonb) || EXCLUDED.metadata" in sql


def test_publish_with_lucide_icon_stores_in_metadata_and_keeps_icon_url_null(monkeypatch):
    sql, params = _publish_upsert(monkeypatch, {"id": "demo", "version": "1.0.0", "icon": "sprout"})
    assert "icon_url = COALESCE(EXCLUDED.icon_url, marketplace_modules.icon_url)" in sql
    # icon_url is the 8th parameter in the INSERT tuple
    assert params[7] is None
    metadata = _published_metadata(monkeypatch, {"icon": "sprout"})
    assert metadata["icon"] == "sprout"


@pytest.mark.parametrize(
    "url_icon",
    [
        "/modules/demo/icon.svg",
        "https://example.org/module-icons/demo.svg",
        "data:image/svg+xml;utf8,<svg></svg>",
    ],
)
def test_publish_with_url_icon_stores_in_icon_url_not_metadata(monkeypatch, url_icon):
    _, params = _publish_upsert(monkeypatch, {"id": "demo", "version": "1.0.0", "icon": url_icon})
    assert params[7] == url_icon
    metadata = _published_metadata(monkeypatch, {"icon": url_icon})
    assert "icon" not in metadata


# -- list endpoints -----------------------------------------------------------

_MODULE_ROW = {
    "id": "demo",
    "name": "demo",
    "display_name": "Demo",
    "description": "Plain description",
    "remoteEntry": "/modules/demo/mf-manifest.json",
    "scope": "demo",
    "module": "./Module",
    "version": "1.0.0",
    "icon_url": None,
    "route_path": "/demo",
    "label": "Demo",
    "is_local": False,
    "tenant_config": {},
}


def _fake_tenant_connection(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


def _get_my_modules(client, monkeypatch, metadata, accept_language=None):
    import blueprints.modules as m

    row = {**_MODULE_ROW, "metadata": metadata}
    monkeypatch.setattr(m, "get_db_connection_with_tenant", lambda _t: _fake_tenant_connection([row]))
    headers = {"Authorization": "Bearer t"}
    if accept_language:
        headers["Accept-Language"] = accept_language
    r = client.get("/api/modules/me", headers=headers)
    assert r.status_code == 200
    return r.get_json()[0]


def test_modules_me_returns_label_and_description_maps(client, monkeypatch):
    metadata = {
        "label_i18n": {"es": "Demo es", "en": "Demo en"},
        "description_i18n": {"es": "Descripción", "en": "Description"},
    }
    module = _get_my_modules(client, monkeypatch, metadata)
    assert module["labelI18n"] == metadata["label_i18n"]
    assert module["descriptionI18n"] == metadata["description_i18n"]


def test_modules_me_maps_are_filtered_to_supported_languages_and_strings(client, monkeypatch):
    metadata = {
        "label_i18n": {"es": "Demo", "de": "Demo de", "en": 5, "fr": ""},
        "description_i18n": {"pt": "Descrição", "xx": "x", "ca": ["a"]},
    }
    module = _get_my_modules(client, monkeypatch, metadata)
    assert module["labelI18n"] == {"es": "Demo"}
    assert module["descriptionI18n"] == {"pt": "Descrição"}


@pytest.mark.parametrize("metadata", [{}, {"label_i18n": "x", "description_i18n": ["x"]}])
def test_modules_me_maps_default_to_empty(client, monkeypatch, metadata):
    module = _get_my_modules(client, monkeypatch, metadata)
    assert module["labelI18n"] == {}
    assert module["descriptionI18n"] == {}


def test_modules_me_reads_metadata_given_as_json_text(client, monkeypatch):
    metadata = json.dumps({"label_i18n": {"es": "Demo"}})
    module = _get_my_modules(client, monkeypatch, metadata)
    assert module["labelI18n"] == {"es": "Demo"}


def test_modules_me_description_still_follows_accept_language(client, monkeypatch):
    metadata = {"description_i18n": {"es": "Descripción", "en": "Description"}}
    module = _get_my_modules(client, monkeypatch, metadata, accept_language="en")
    assert module["description"] == "Description"
    assert module["descriptionI18n"] == metadata["description_i18n"]


def test_marketplace_returns_label_and_description_maps(client, monkeypatch):
    import blueprints.modules as m

    metadata = {
        "label_i18n": {"es": "Demo es", "xx": "ignored"},
        "description_i18n": {"es": "Descripción", "en": "Description"},
    }
    row = {**_MODULE_ROW, "metadata": metadata}
    cur = MagicMock()
    cur.fetchall.return_value = [row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda _c: None)
    r = client.get(
        "/api/modules/marketplace",
        headers={"Authorization": "Bearer t", "Accept-Language": "en"},
    )
    assert r.status_code == 200
    module = r.get_json()[0]
    assert module["description"] == "Description"
    assert module["labelI18n"] == {"es": "Demo es"}
    assert module["descriptionI18n"] == metadata["description_i18n"]


def test_modules_me_and_marketplace_normalize_lucide_icon_in_icon_url(client, monkeypatch):
    import blueprints.modules as m

    row = {**_MODULE_ROW, "icon_url": "tractor", "metadata": {}}
    monkeypatch.setattr(m, "get_db_connection_with_tenant", lambda _t: _fake_tenant_connection([row]))
    r_me = client.get("/api/modules/me", headers={"Authorization": "Bearer t"})
    assert r_me.status_code == 200
    mod_me = r_me.get_json()[0]
    assert mod_me["icon"] == "tractor"
    assert mod_me["icon_url"] is None

    cur = MagicMock()
    cur.fetchall.return_value = [row]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(m, "get_db_connection_simple", lambda: conn)
    monkeypatch.setattr(m, "return_db_connection", lambda _c: None)
    r_mp = client.get("/api/modules/marketplace", headers={"Authorization": "Bearer t"})
    assert r_mp.status_code == 200
    mod_mp = r_mp.get_json()[0]
    assert mod_mp["icon"] == "tractor"
    assert mod_mp["icon_url"] is None

