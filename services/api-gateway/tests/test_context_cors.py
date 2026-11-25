"""The public @context can be loaded by a browser from any origin, without credentials."""

import io

import pytest

PATH = "/ngsi-ld-context.json"
LISTED = "http://localhost:3000"
OTHER = "https://client.example.org"


@pytest.fixture
def get(client, monkeypatch):
    import fiware_api_gateway as gw

    monkeypatch.setattr(
        gw, "open", lambda *a, **k: io.StringIO('{"@context": []}'), raising=False
    )

    def _get(path, origin=None):
        return client.get(path, headers={"Origin": origin} if origin else {})

    return _get


@pytest.mark.parametrize("origin", [OTHER, "null", None])
def test_context_is_open_without_credentials(get, origin):
    resp = get(PATH, origin)
    assert resp.status_code == 200
    assert resp.headers.getlist("Access-Control-Allow-Origin") == ["*"]
    assert "Access-Control-Allow-Credentials" not in resp.headers
    assert "Origin" in resp.headers["Vary"]


def test_listed_origin_keeps_its_own_headers(get):
    resp = get(PATH, LISTED)
    assert resp.headers.getlist("Access-Control-Allow-Origin") == [LISTED]
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"
    assert "Origin" in resp.headers["Vary"]


@pytest.mark.parametrize("path", ["/health", "/ngsi-ld/v1/entities"])
def test_nothing_else_is_opened(get, path):
    assert "Access-Control-Allow-Origin" not in get(path, OTHER).headers
