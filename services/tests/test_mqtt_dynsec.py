"""Tests for common.mqtt_dynsec against a fake in-memory dynsec broker."""
import pytest

from common.mqtt_dynsec import (
    DynsecClient,
    DynsecError,
    DynsecTimeout,
    create_account,
    delete_account,
    disable,
    dynsec_from_env,
    enable,
    set_password,
    set_role_topics,
)


class FakeBroker:
    """Implements the subset of dynsec commands used by the client."""

    def __init__(self, fail_on=None):
        self.clients = {}
        self.roles = {}
        self.fail_on = fail_on or set()
        self.log = []

    def __call__(self, commands):
        out = []
        for cmd in commands:
            name = cmd["command"]
            self.log.append(name)
            out.append(self._one(name, cmd))
        return out

    def _one(self, name, c):
        err = lambda m: {"command": name, "error": m}  # noqa: E731
        ok = {"command": name}
        if name in self.fail_on:
            return err("forced failure")
        if name == "createClient":
            if c["username"] in self.clients:
                return err("Client already exists")
            self.clients[c["username"]] = {"roles": set(), "disabled": False}
        elif name == "createRole":
            if c["rolename"] in self.roles:
                return err("Role already exists")
            self.roles[c["rolename"]] = []
        elif name == "addClientRole":
            if c["username"] not in self.clients or c["rolename"] not in self.roles:
                return err("not found")
            self.clients[c["username"]]["roles"].add(c["rolename"])
        elif name == "deleteClient":
            if c["username"] not in self.clients:
                return err("Client not found")
            del self.clients[c["username"]]
        elif name == "deleteRole":
            if c["rolename"] not in self.roles:
                return err("Role not found")
            del self.roles[c["rolename"]]
        elif name == "getRole":
            if c["rolename"] not in self.roles:
                return err("Role not found")
            acls = [
                {"acltype": "publishClientSend", "topic": t, "allow": True, "priority": 0}
                for t in self.roles[c["rolename"]]
            ]
            return {"command": name, "data": {"role": {"rolename": c["rolename"], "acls": acls}}}
        elif name == "addRoleACL":
            self.roles[c["rolename"]].append(c["topic"])
        elif name == "removeRoleACL":
            self.roles[c["rolename"]].remove(c["topic"])
        elif name == "setClientPassword":
            self.clients[c["username"]]["password_set"] = True
        elif name in ("disableClient", "enableClient"):
            if c["username"] not in self.clients:
                return err("Client not found")
            self.clients[c["username"]]["disabled"] = name == "disableClient"
        else:
            return err("unknown")
        return ok


def make(broker):
    return DynsecClient("h", 1883, "u", "p", transport=broker)


def test_run_raises_on_command_error():
    b = FakeBroker()
    c = make(b)
    with pytest.raises(DynsecError) as e:
        c.run([{"command": "deleteClient", "username": "nope"}])
    assert e.value.command == "deleteClient"
    assert "Client not found" in e.value.error


def test_create_datalogger_rolls_back_client_when_role_fails():
    b = FakeBroker(fail_on={"createRole"})
    with pytest.raises(DynsecError):
        create_account(make(b), "dl_0123456789abcdef", "pw-synthetic")
    assert b.clients == {} and b.roles == {}


def test_create_account_rolls_back_both_when_link_fails():
    b = FakeBroker(fail_on={"addClientRole"})
    with pytest.raises(DynsecError):
        create_account(make(b), "dl_a", "pw-synthetic")
    assert b.clients == {} and b.roles == {}


def test_create_account_success():
    b = FakeBroker()
    create_account(make(b), "dl_a", "pw-synthetic")
    assert b.clients["dl_a"]["roles"] == {"dl_a"}


def test_set_role_topics_adds_and_removes_to_match():
    b = FakeBroker()
    c = make(b)
    create_account(c, "dl_a", "pw-synthetic")
    set_role_topics(c, "dl_a", {"/k/a/attrs", "/k/b/attrs"})
    assert sorted(b.roles["dl_a"]) == ["/k/a/attrs", "/k/b/attrs"]
    set_role_topics(c, "dl_a", {"/k/b/attrs", "/k/c/attrs"})
    assert sorted(b.roles["dl_a"]) == ["/k/b/attrs", "/k/c/attrs"]
    set_role_topics(c, "dl_a", set())
    assert b.roles["dl_a"] == []


def test_reconcile_recreates_missing_role_from_rows():
    b = FakeBroker()
    c = make(b)
    set_role_topics(c, "dl_a", {"/k/a/attrs", "/k/b/attrs"})
    assert sorted(b.roles["dl_a"]) == ["/k/a/attrs", "/k/b/attrs"]
    assert b.clients == {}  # never creates a client implicitly


def test_password_disable_enable_delete():
    b = FakeBroker()
    c = make(b)
    create_account(c, "dl_a", "pw-synthetic")
    set_password(c, "dl_a", "pw2-synthetic")
    assert b.clients["dl_a"]["password_set"]
    disable(c, "dl_a")
    assert b.clients["dl_a"]["disabled"]
    enable(c, "dl_a")
    assert not b.clients["dl_a"]["disabled"]
    delete_account(c, "dl_a")
    assert b.clients == {} and b.roles == {}
    delete_account(c, "dl_a")  # idempotent


def test_password_not_in_error():
    def echoing(commands):
        # a broker that echoes the whole command (password included) in its error
        return [{"command": commands[0]["command"], "error": f"bad {commands[0]}"}]

    c = DynsecClient("h", 1883, "u", "adminpw-xyz", transport=echoing)
    with pytest.raises(DynsecError) as e:
        c.run([{"command": "createClient", "username": "dl_a", "password": "secret-pw-xyz"}])
    # error text is the broker's; the client itself must not add credentials
    assert "adminpw-xyz" not in str(e.value) and "adminpw-xyz" not in repr(c)


def test_create_account_cleans_up_when_create_client_times_out():
    calls = []

    def transport(commands):
        calls.append(commands[0]["command"])
        if commands[0]["command"] == "createClient":
            raise DynsecTimeout("createClient", "response timeout")
        return [{"command": commands[0]["command"], "error": "Client not found"}]

    with pytest.raises(DynsecTimeout):
        create_account(DynsecClient("h", 1, "u", "p", transport=transport), "dl_a", "pw")
    assert calls == ["createClient", "deleteClient"]


def test_create_account_cleans_up_when_create_role_times_out():
    calls = []

    def transport(commands):
        cmd = commands[0]["command"]
        calls.append(cmd)
        if cmd == "createRole":
            raise DynsecTimeout(cmd, "response timeout")
        return [{"command": cmd}]

    with pytest.raises(DynsecTimeout):
        create_account(DynsecClient("h", 1, "u", "p", transport=transport), "dl_a", "pw")
    assert calls == ["createClient", "createRole", "deleteRole", "deleteClient"]


def test_create_account_already_exists_does_not_delete_existing():
    b = FakeBroker()
    create_account(make(b), "dl_a", "pw")
    with pytest.raises(DynsecError):
        create_account(make(b), "dl_a", "pw")
    assert "dl_a" in b.clients and "dl_a" in b.roles


def test_run_sends_same_correlation_id_on_every_command():
    seen = []

    def transport(commands):
        seen.extend(commands)
        return [{"command": x["command"]} for x in commands]

    DynsecClient("h", 1, "u", "p", transport=transport).run(
        [{"command": "getRole", "rolename": "a"}, {"command": "getRole", "rolename": "b"}])
    ids = {x["correlationData"] for x in seen}
    assert len(ids) == 1 and all(ids)


@pytest.mark.parametrize("port", ["abc", "18 83"])
def test_from_env_bad_port(monkeypatch, port):
    monkeypatch.setenv("MQTT_HOST", "h")
    monkeypatch.setenv("MQTT_PORT", port)
    monkeypatch.setenv("MQTT_DYNSEC_USERNAME", "u")
    monkeypatch.setenv("MQTT_DYNSEC_PASSWORD", "p")
    with pytest.raises(RuntimeError, match="MQTT_PORT"):
        dynsec_from_env()


# ---- real transport against a mocked paho client ----------------------------

import json  # noqa: E402


class FakeMsg:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()


class FakePaho:
    """Scripted paho client. `script` controls behaviour; instances recorded in `made`."""
    made = []
    script = {}

    def __init__(self, client_id=None):
        self.events = []
        self.on_connect = self.on_subscribe = self.on_message = None
        FakePaho.made.append(self)

    def username_pw_set(self, u, p):
        pass

    def connect(self, host, port, keepalive=60):
        self.events.append("connect")
        if FakePaho.script.get("connect_oserror"):
            raise ConnectionRefusedError()

    def loop_start(self):
        self.events.append("loop_start")
        self.on_connect(self, None, {}, FakePaho.script.get("rc", 0))

    def subscribe(self, topic, qos=0):
        if not FakePaho.script.get("no_suback"):
            self.on_subscribe(self, None, 1, (1,))

    def publish(self, topic, payload, qos=0):
        cid = json.loads(payload)["commands"][0]["correlationData"]
        for reply in FakePaho.script.get("replies", []):
            if callable(reply):
                reply = reply(cid)
            self.on_message(self, None, FakeMsg(reply))

    def disconnect(self):
        self.events.append("disconnect")

    def loop_stop(self):
        self.events.append("loop_stop")


@pytest.fixture
def paho(monkeypatch):
    import paho.mqtt.client as real
    FakePaho.made = []
    FakePaho.script = {}
    monkeypatch.setattr(real, "Client", FakePaho)
    return FakePaho


def real_client():
    return DynsecClient("h", 1883, "u", "p", timeout=0.05)


def ok_reply(cid):
    return {"responses": [{"command": "getRole", "data": {"x": 1}, "correlationData": cid}]}


def assert_cleaned(paho):
    ev = paho.made[-1].events
    assert ev[-2:] == ["disconnect", "loop_stop"]


def test_transport_happy_path(paho):
    paho.script = {"replies": [ok_reply]}
    out = real_client().run([{"command": "getRole", "rolename": "r"}])
    assert out[0]["data"] == {"x": 1}
    assert_cleaned(paho)


def test_transport_connect_refused_fails_fast(paho):
    import time
    paho.script = {"rc": 5}
    c = DynsecClient("h", 1883, "u", "p", timeout=5)
    t0 = time.monotonic()
    with pytest.raises(DynsecError, match="connect refused") as e:
        c.run([{"command": "getRole", "rolename": "r"}])
    assert time.monotonic() - t0 < 1
    assert not isinstance(e.value, DynsecTimeout)
    assert_cleaned(paho)


def test_transport_oserror_is_dynsec_error(paho):
    paho.script = {"connect_oserror": True}
    with pytest.raises(DynsecError, match="unreachable"):
        real_client().run([{"command": "getRole", "rolename": "r"}])
    assert paho.made[-1].events[-2:] == ["disconnect", "loop_stop"]


def test_transport_subscribe_timeout(paho):
    paho.script = {"no_suback": True}
    with pytest.raises(DynsecTimeout, match="subscribe timeout"):
        real_client().run([{"command": "getRole", "rolename": "r"}])
    assert_cleaned(paho)


def test_transport_response_timeout(paho):
    paho.script = {"replies": []}
    with pytest.raises(DynsecTimeout, match="response timeout"):
        real_client().run([{"command": "getRole", "rolename": "r"}])
    assert_cleaned(paho)


def test_transport_ignores_foreign_response_then_accepts_ours(paho):
    foreign = {"responses": [{"command": "getRole", "correlationData": "other"}]}
    paho.script = {"replies": [foreign, ok_reply]}
    out = real_client().run([{"command": "getRole", "rolename": "r"}])
    assert out[0]["data"] == {"x": 1}


def test_transport_only_foreign_response_times_out(paho):
    foreign = {"responses": [{"command": "getRole", "error": "boom", "correlationData": "other"}]}
    paho.script = {"replies": [foreign]}
    with pytest.raises(DynsecTimeout):
        real_client().run([{"command": "getRole", "rolename": "r"}])


@pytest.mark.parametrize("payload", [
    b"not json",
    b"\xff\xfe",
    b"[1, 2]",
    b'"text"',
    b'{"responses": "x"}',
    b'{"responses": [1, "a"]}',
    b"{}",
])
def test_transport_malformed_payload_raises_dynsec_error(paho, payload):
    paho.script = {"replies": [payload]}
    with pytest.raises(DynsecError, match="invalid response payload"):
        real_client().run([{"command": "getRole", "rolename": "r"}])
    assert_cleaned(paho)


@pytest.mark.parametrize("user,pw", [("", "x"), ("x", ""), ("", "")])
def test_from_env_fails_closed_without_credentials(monkeypatch, user, pw):
    monkeypatch.setenv("MQTT_HOST", "h")
    monkeypatch.setenv("MQTT_PORT", "1883")
    monkeypatch.setenv("MQTT_DYNSEC_USERNAME", user)
    monkeypatch.setenv("MQTT_DYNSEC_PASSWORD", pw)
    with pytest.raises(RuntimeError):
        dynsec_from_env()


def test_from_env_ok(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "h")
    monkeypatch.setenv("MQTT_PORT", "1884")
    monkeypatch.setenv("MQTT_DYNSEC_USERNAME", "u")
    monkeypatch.setenv("MQTT_DYNSEC_PASSWORD", "p")
    c = dynsec_from_env()
    assert (c.host, c.port) == ("h", 1884)
