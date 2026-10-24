"""Tests for common.mqtt_dynsec against a fake in-memory dynsec broker."""
import pytest

from common.mqtt_dynsec import (
    DynsecClient,
    DynsecError,
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
    b = FakeBroker(fail_on={"createRole"})
    with pytest.raises(DynsecError) as e:
        create_account(make(b), "dl_a", "secret-pw-xyz")
    assert "secret-pw-xyz" not in str(e.value) and "secret-pw-xyz" not in repr(e.value)


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
