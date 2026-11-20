import http.client
import importlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import entity_type_guard as g

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config"
URL = "http://broker.invalid:1026"
EXPECTED_URL = URL + "/ngsi-ld/v1/types?details=true&limit=1000"
DEFAULT_VOCAB = "https://uri.etsi.org/ngsi-ld/default-context/"


@pytest.fixture(scope="module")
def accepted():
    return g.load_accepted(CONFIG)


class FakeResponse:
    def __init__(self, body):
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Plays one scripted outcome per call: an exception is raised, anything else is the body."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def http_error(code):
    return urllib.error.HTTPError(EXPECTED_URL, code, "x", {}, io.BytesIO(b""))


def items(*ids):
    return [{"id": i, "type": "EntityType"} for i in ids]


def fetch(opener, **kw):
    sleep = kw.pop("sleep", FakeSleep())
    return g.stored_type_iris(URL + "/", "tenant-a", opener=opener, sleep=sleep, **kw)


def test_request_shape_and_order():
    opener = FakeOpener(items("https://nkz-os.org/ns/B", "https://nkz-os.org/ns/A"))
    assert fetch(opener) == ["https://nkz-os.org/ns/B", "https://nkz-os.org/ns/A"]
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == EXPECTED_URL
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers == {
        "ngsild-tenant": "tenant-a",
        "fiware-service": "tenant-a",
        "fiware-servicepath": "/",
        "accept": "application/json",
    }


def test_retries_connection_errors_then_succeeds():
    opener = FakeOpener(urllib.error.URLError("refused"), urllib.error.URLError("refused"), items("x:y"))
    sleep = FakeSleep()
    assert fetch(opener, sleep=sleep, delay=7.0) == ["x:y"]
    assert len(opener.requests) == 3
    assert sleep.calls == [7.0, 14.0]  # the wait grows with each attempt


def test_gives_up_after_attempts():
    opener = FakeOpener(urllib.error.URLError("refused"))
    sleep = FakeSleep()
    with pytest.raises(g.BrokerCheckError):
        fetch(opener, sleep=sleep, attempts=3)
    assert len(opener.requests) == 3
    assert len(sleep.calls) == 2


def test_non_http_answer_is_retried():
    opener = FakeOpener(http.client.BadStatusLine("x"), http.client.BadStatusLine("x"), items("a:b"))
    sleep = FakeSleep()
    assert fetch(opener, sleep=sleep) == ["a:b"]
    assert len(opener.requests) == 3
    assert len(sleep.calls) == 2


def test_body_cut_off_mid_read_ends_in_broker_check_error():
    class CutResponse(FakeResponse):
        def read(self):
            raise http.client.IncompleteRead(b"")

    requests = []

    def opener(request, timeout=None):
        requests.append(request)
        return CutResponse(b"")

    with pytest.raises(g.BrokerCheckError):
        fetch(opener, attempts=3)
    assert len(requests) == 3


def test_address_without_scheme_fails_at_once_without_retry():
    sleep = FakeSleep()
    opener = FakeOpener(items("a:b"))
    with pytest.raises(g.BrokerCheckError):
        g.stored_type_iris("broker.invalid:1026", "tenant-a", opener=opener, sleep=sleep)
    assert sleep.calls == []


def test_value_error_from_opener_fails_at_once_without_retry():
    sleep = FakeSleep()
    opener = FakeOpener(ValueError("unknown url type"))
    with pytest.raises(g.BrokerCheckError):
        fetch(opener, sleep=sleep)
    assert len(opener.requests) == 1
    assert sleep.calls == []


def test_timeout_counts_as_connection_error():
    opener = FakeOpener(TimeoutError("slow"), items("a:b"))
    assert fetch(opener) == ["a:b"]
    assert len(opener.requests) == 2


def test_http_5xx_is_retried():
    opener = FakeOpener(http_error(503), items("a:b"))
    assert fetch(opener) == ["a:b"]
    assert len(opener.requests) == 2


def test_http_4xx_fails_at_once():
    opener = FakeOpener(http_error(400))
    sleep = FakeSleep()
    with pytest.raises(g.BrokerCheckError):
        fetch(opener, sleep=sleep)
    assert len(opener.requests) == 1
    assert sleep.calls == []


@pytest.mark.parametrize("body", [{}, [1], [{"typeName": "X"}], [{"id": 5}], b"not json"])
def test_bad_body_raises(body):
    with pytest.raises(g.BrokerCheckError):
        fetch(FakeOpener(body))


def test_empty_list_is_fine():
    assert fetch(FakeOpener([])) == []


def test_full_page_is_truncated_listing():
    full = items(*[f"https://nkz-os.org/ns/T{i}" for i in range(g.TYPES_PAGE_LIMIT)])
    with pytest.raises(g.BrokerCheckError, match="truncated"):
        fetch(FakeOpener(full))
    almost = items(*[f"https://nkz-os.org/ns/T{i}" for i in range(g.TYPES_PAGE_LIMIT - 1)])
    assert len(fetch(FakeOpener(almost))) == g.TYPES_PAGE_LIMIT - 1


def test_unknown_stored_types(accepted):
    ok = [
        "https://saref.etsi.org/saref4agri/AgriParcel",
        "https://nkz-os.org/ns/AgriPlant",
        DEFAULT_VOCAB + "AgriManagementZone",
    ]
    assert g.unknown_stored_types(ok, accepted) == []
    bad = [
        "https://nkz-os.org/ns/AgriManagementZone",
        DEFAULT_VOCAB + "FooAsset",
        "https://nkz-os.org/ns/AgriEnergyTracker",
        DEFAULT_VOCAB + "FooAsset",
    ]
    assert g.unknown_stored_types(bad, accepted) == [
        "https://nkz-os.org/ns/AgriEnergyTracker",
        "https://nkz-os.org/ns/AgriManagementZone",
        DEFAULT_VOCAB + "FooAsset",
    ]


def run(accepted, results):
    lines = []

    def fake_fetch(url, tenant):
        value = results[tenant]
        if isinstance(value, BaseException):
            raise value
        return value

    code = g.run_broker_check(URL, list(results), accepted, fetch=fake_fetch, out=lines.append)
    return code, lines


GOOD = "https://nkz-os.org/ns/AgriPlant"
FOO = DEFAULT_VOCAB + "FooAsset"


def test_run_clean(accepted):
    code, lines = run(accepted, {"tenant-a": [GOOD], "tenant-b": [GOOD, GOOD]})
    assert code == 0
    assert lines == ["ok tenant-a: 1 types", "ok tenant-b: 2 types"]


def test_run_unknown_still_checks_every_tenant(accepted):
    code, lines = run(accepted, {"tenant-a": [GOOD, FOO], "tenant-b": [GOOD]})
    assert code == 1
    assert f"unknown tenant-a: {FOO}" in lines
    assert "ok tenant-b: 1 types" in lines


def test_run_error_beats_unknown(accepted):
    code, lines = run(accepted, {"tenant-a": g.BrokerCheckError("boom"), "tenant-b": [FOO]})
    assert code == 2
    assert "error tenant-a: BrokerCheckError" in lines
    assert f"unknown tenant-b: {FOO}" in lines


def test_run_error_names_underlying_cause_not_message(accepted):
    try:
        try:
            raise urllib.error.URLError("secret-detail")
        except urllib.error.URLError as exc:
            raise g.BrokerCheckError("wrapped secret-detail") from exc
    except g.BrokerCheckError as err:
        code, lines = run(accepted, {"tenant-a": err})
    assert code == 2
    assert lines == ["error tenant-a: URLError"]


def test_run_truncated_message(accepted):
    full = items(*[f"https://nkz-os.org/ns/T{i}" for i in range(g.TYPES_PAGE_LIMIT)])
    lines = []
    code = g.run_broker_check(
        URL, ["tenant-a"], accepted, fetch=lambda u, t: g.stored_type_iris(u, t, opener=FakeOpener(full), sleep=FakeSleep()), out=lines.append
    )
    assert code == 2
    assert lines == ["error tenant-a: listing may be truncated at 1000 types"]


def test_run_empty_listing_is_an_error(accepted):
    # The tenant list is written by hand: a wrong name must not read as a clean tenant.
    code, lines = run(accepted, {"tenant-a": []})
    assert code == 2
    assert lines == ["error tenant-a: no entity types found"]


def test_run_unexpected_exception_is_an_error_and_next_tenant_is_checked(accepted):
    code, lines = run(accepted, {"tenant-a": RuntimeError("secret-detail"), "tenant-b": [GOOD]})
    assert code == 2
    assert lines == ["error tenant-a: RuntimeError", "ok tenant-b: 1 types"]


def test_default_out_flushes_every_line(accepted, monkeypatch):
    class Recorder:
        def __init__(self):
            self.written = []
            self.flushes = 0

        def write(self, text):
            self.written.append(text)
            return len(text)

        def flush(self):
            self.flushes += 1

    recorder = Recorder()
    monkeypatch.setattr("sys.stdout", recorder)
    results = {"tenant-a": [GOOD], "tenant-b": [GOOD, FOO]}
    code = g.run_broker_check(URL, list(results), accepted, fetch=lambda u, t: results[t])
    assert code == 1
    printed = "".join(recorder.written).splitlines()
    assert len(printed) == 2
    assert recorder.flushes >= len(printed)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ENTITY_TYPE_CONFIG_DIR", str(CONFIG))
    monkeypatch.setenv("ORION_URL", URL)
    monkeypatch.setenv("ENTITY_TYPE_CHECK_TENANTS", "tenant-a, tenant-b")


def test_main_missing_orion_url(env, monkeypatch, capsys):
    monkeypatch.delenv("ORION_URL")
    assert g.main(["broker"]) == 2
    assert "error: ORION_URL is not set" in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "", " , "])
def test_main_tenants_missing_or_empty(env, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("ENTITY_TYPE_CHECK_TENANTS")
    else:
        monkeypatch.setenv("ENTITY_TYPE_CHECK_TENANTS", value)
    assert g.main(["broker"]) == 2
    assert "error: ENTITY_TYPE_CHECK_TENANTS is empty" in capsys.readouterr().out


def test_main_lists_unavailable(env, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENTITY_TYPE_CONFIG_DIR", str(tmp_path))
    assert g.main(["broker"]) == 2
    assert "error: entity type lists unavailable" in capsys.readouterr().out


def test_main_ok(env, monkeypatch, capsys):
    seen = []

    def fake(url, tenant, **kw):
        seen.append((url, tenant))
        return [GOOD]

    monkeypatch.setattr(g, "stored_type_iris", fake)
    assert g.main(["broker"]) == 0
    assert seen == [(URL, "tenant-a"), (URL, "tenant-b")]
    assert "ok tenant-a: 1 types" in capsys.readouterr().out


def test_main_never_lets_an_exception_escape(env, monkeypatch, capsys):
    def boom(url, tenant, **kw):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(g, "stored_type_iris", boom)
    assert g.main(["broker"]) == 2
    out = capsys.readouterr().out
    assert "secret-detail" not in out


def test_import_makes_no_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call at import")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    try:
        importlib.reload(g)
    finally:
        # Reload once more with the real opener, or the patched one stays as a default argument.
        monkeypatch.undo()
        importlib.reload(g)
    assert g.stored_type_iris.__kwdefaults__["opener"] is urllib.request.urlopen
