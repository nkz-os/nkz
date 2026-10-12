"""Module publish over GitHub OIDC: owner from the deployment, repository bound
to the module, and the gateway's own secret towards entity-manager."""

from unittest.mock import MagicMock

import pytest


def _payload(**over):
    base = {
        "repository": "nkz-os/nkz-module-soil",
        "repository_owner": "nkz-os",
        "ref": "refs/heads/main",
        "sha": "a" * 40,
    }
    base.update(over)
    return base


@pytest.fixture
def validator(app, monkeypatch):
    """Make jwt verification a no-op so the policy checks can be tested."""
    gw = app
    monkeypatch.setattr(gw.jwt, "get_unverified_header", lambda t: {"kid": "k1"})
    monkeypatch.setattr(gw, "_get_oidc_signing_key", lambda h: "signing-key")
    monkeypatch.setattr(gw, "_GITHUB_OIDC_ALLOWED_OWNER", "nkz-os")

    def run(payload):
        monkeypatch.setattr(gw.jwt, "decode", lambda *a, **k: payload)
        return gw._validate_oidc_token("tok", "soil")

    return run


class TestValidateToken:
    def test_returns_claims_for_allowed_owner_on_main(self, validator):
        claims = validator(_payload())
        assert claims == {
            "repository": "nkz-os/nkz-module-soil",
            "repository_owner": "nkz-os",
            "ref": "refs/heads/main",
            "sha": "a" * 40,
        }

    def test_tags_are_release_refs(self, validator):
        assert validator(_payload(ref="refs/tags/v1.2.0")) is not None

    def test_rejects_feature_branch(self, validator):
        assert validator(_payload(ref="refs/heads/feature")) is None

    def test_rejects_other_owner(self, validator):
        assert validator(_payload(repository_owner="someone-else")) is None

    def test_owner_comparison_ignores_case(self, validator):
        assert validator(_payload(repository_owner="NKZ-OS")) is not None

    def test_refuses_everything_when_owner_unset(self, app, validator, monkeypatch):
        monkeypatch.setattr(app, "_GITHUB_OIDC_ALLOWED_OWNER", "")
        assert validator(_payload()) is None

    def test_empty_token_is_none(self, app):
        assert app._validate_oidc_token("", "soil") is None


class TestRepoBinding:
    def test_no_registered_repo_refuses(self, app, monkeypatch):
        monkeypatch.setattr(app, "_module_source_repo", lambda m: None)
        assert app._oidc_repo_owns_module(_payload(), "soil") is False

    def test_other_repo_refuses(self, app, monkeypatch):
        monkeypatch.setattr(app, "_module_source_repo", lambda m: "nkz-os/nkz-module-carbon")
        assert app._oidc_repo_owns_module(_payload(), "soil") is False

    def test_registered_repo_matches_ignoring_case(self, app, monkeypatch):
        monkeypatch.setattr(app, "_module_source_repo", lambda m: "nkz-os/nkz-module-soil")
        assert app._oidc_repo_owns_module(_payload(repository="NKZ-OS/NKZ-Module-Soil"), "soil")

    def test_source_repo_without_database_is_none(self, app, monkeypatch):
        monkeypatch.setattr(app, "POSTGRES_URL", "")
        assert app._module_source_repo("soil") is None


class _Upstream:
    def __init__(self, status=201, body=b'{"success": true}'):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content = body
        self.text = body.decode()

    def json(self):
        import json
        return json.loads(self.content)


class TestPublishRoute:
    def _arm(self, gw, monkeypatch, owns=True):
        monkeypatch.setattr(gw, "_validate_oidc_token", lambda tok, mid: _payload())
        monkeypatch.setattr(gw, "_oidc_repo_owns_module", lambda c, mid: owns)
        monkeypatch.setattr(gw, "_PUBLISH_FORWARD_SECRET", "gateway-secret")
        calls = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            calls["url"] = url
            calls["headers"] = headers
            return _Upstream()

        monkeypatch.setattr(gw.requests, "post", fake_post)
        return calls

    def test_repo_not_owning_module_is_forbidden(self, app, client, monkeypatch):
        calls = self._arm(app, monkeypatch, owns=False)
        r = client.post(
            "/api/internal/modules/soil/publish",
            headers={"X-OIDC-Token": "tok", "X-Internal-Service-Secret": "client-secret"},
            data={"version_hash": "a" * 40},
        )
        assert r.status_code == 403
        assert calls == {}

    def test_forwards_gateway_secret_and_claims_never_client_secret(
        self, app, client, monkeypatch
    ):
        calls = self._arm(app, monkeypatch)
        r = client.post(
            "/api/internal/modules/soil/publish",
            headers={"X-OIDC-Token": "tok", "X-Internal-Service-Secret": "client-secret"},
            data={"version_hash": "a" * 40},
        )
        assert r.status_code == 201, r.data
        assert calls["url"].endswith("/api/internal/modules/soil/publish")
        assert calls["headers"]["X-Internal-Service-Secret"] == "gateway-secret"
        assert calls["headers"]["X-OIDC-Repository"] == "nkz-os/nkz-module-soil"
        assert calls["headers"]["X-OIDC-Sha"] == "a" * 40

    def test_invalid_token_is_forbidden_before_binding(self, app, client, monkeypatch):
        monkeypatch.setattr(app, "_validate_oidc_token", lambda tok, mid: None)
        binding = MagicMock()
        monkeypatch.setattr(app, "_oidc_repo_owns_module", binding)
        r = client.post("/api/internal/modules/soil/publish", headers={"X-OIDC-Token": "bad"})
        assert r.status_code == 403
        binding.assert_not_called()

    def test_resolve_url_carries_no_secret(self, app, client, monkeypatch):
        monkeypatch.setattr(app, "_PUBLISH_FORWARD_SECRET", "gateway-secret")
        calls = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            calls["headers"] = headers
            return _Upstream(200, b'{"url": "x"}')

        monkeypatch.setattr(app.requests, "get", fake_get)
        r = client.get(
            "/api/internal/modules/soil/resolve-url",
            headers={"X-Internal-Service-Secret": "client-secret"},
        )
        assert r.status_code == 200
        assert "X-Internal-Service-Secret" not in calls["headers"]
        assert "X-OIDC-Sha" not in calls["headers"]
