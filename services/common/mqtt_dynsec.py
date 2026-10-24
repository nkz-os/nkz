"""Client for the Mosquitto dynamic-security plugin.

Protocol: subscribe to ``$CONTROL/dynamic-security/v1/response``, publish
``{"commands": [...]}`` to ``$CONTROL/dynamic-security/v1``; the response
lists one entry per command, in order, with an ``error`` key on failure.

Every command carries a per-call ``correlationData`` id that the plugin echoes
in its response entry; responses with another id (concurrent admins sharing the
response topic) are ignored.

Assumes the in-cluster plaintext listener (port 1883, no TLS); do not point it
at an untrusted network. Passwords are never logged and never placed in
exceptions.
"""
import json
import logging
import os
import threading
import uuid
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTROL_TOPIC = "$CONTROL/dynamic-security/v1"
RESPONSE_TOPIC = "$CONTROL/dynamic-security/v1/response"
ACL_TYPE = "publishClientSend"

Transport = Callable[[List[dict]], List[dict]]


class DynsecError(Exception):
    """A dynsec command failed. Carries only the command name and broker error."""

    def __init__(self, command: str, error: str):
        super().__init__(f"dynsec {command} failed: {error}")
        self.command = command
        self.error = error


class DynsecTimeout(DynsecError):
    """No (matching) response in time: the command may or may not have been applied."""


class DynsecClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: float = 5, transport: Optional[Transport] = None):
        self.host = host
        self.port = int(port)
        self._username = username
        self._password = password
        self.timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        return f"DynsecClient(host={self.host!r}, port={self.port})"

    def run(self, commands: List[dict]) -> List[dict]:
        """Send commands, return responses; raise DynsecError on the first error."""
        cid = uuid.uuid4().hex
        sent = [dict(cmd, correlationData=cid) for cmd in commands]
        responses = (self._transport or self._mqtt_transport)(sent)
        if len(responses) != len(commands):
            raise DynsecError(commands[0]["command"] if commands else "", "response count mismatch")
        for cmd, resp in zip(commands, responses):
            if resp.get("error"):
                raise DynsecError(cmd["command"], str(resp["error"]))
        return responses

    def _mqtt_transport(self, commands: List[dict]) -> List[dict]:
        import paho.mqtt.client as mqtt  # paho-mqtt 1.6.x API

        name = commands[0]["command"] if commands else ""
        cid = commands[0]["correlationData"]
        ready = threading.Event()  # subscribed, or connect failed
        subscribed = threading.Event()
        done = threading.Event()
        result: Dict[str, object] = {}
        client = mqtt.Client(client_id=f"nkz-dynsec-{uuid.uuid4().hex[:12]}")
        client.username_pw_set(self._username, self._password)

        def on_connect(cl, _ud, _flags, rc):
            if rc != 0:
                result["error"] = f"connect refused rc={rc}"
                ready.set()
                return
            cl.subscribe(RESPONSE_TOPIC, qos=1)

        def on_subscribe(_cl, _ud, _mid, _granted):
            subscribed.set()
            ready.set()

        def on_message(_cl, _ud, msg):
            try:
                body = json.loads(msg.payload.decode("utf-8"))
                if not isinstance(body, dict) or not isinstance(body.get("responses"), list):
                    raise ValueError("not a responses object")
                entries = body["responses"]
                if not all(isinstance(e, dict) for e in entries):
                    raise ValueError("response entry is not an object")
            except (ValueError, UnicodeDecodeError):
                result["error"] = "invalid response payload"
                done.set()
                return
            if any(e.get("correlationData") != cid for e in entries):
                return  # someone else's response
            result["responses"] = entries
            done.set()

        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message
        try:
            client.connect(self.host, self.port, keepalive=30)
            client.loop_start()
            ready.wait(self.timeout)
            if "error" in result:
                raise DynsecError(name, str(result["error"]))
            if not subscribed.is_set():
                raise DynsecTimeout(name, "subscribe timeout")
            client.publish(CONTROL_TOPIC, json.dumps({"commands": commands}), qos=1)
            if not done.wait(self.timeout):
                raise DynsecTimeout(name, "response timeout")
        except OSError as exc:
            raise DynsecError(name, f"broker unreachable: {type(exc).__name__}") from None
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            client.loop_stop()
        if "error" in result:
            raise DynsecError(name, str(result["error"]))
        return result["responses"]  # type: ignore[return-value]


def dynsec_from_env() -> DynsecClient:
    """Build a client from env; fail closed when anything is missing."""
    host = os.getenv("MQTT_HOST", "")
    username = os.getenv("MQTT_DYNSEC_USERNAME", "")
    password = os.getenv("MQTT_DYNSEC_PASSWORD", "")
    if not host or not username or not password:
        raise RuntimeError("MQTT dynsec is not configured (host/credentials missing)")
    raw_port = os.getenv("MQTT_PORT", "1883")
    try:
        port = int(raw_port)
    except ValueError:
        raise RuntimeError("MQTT_PORT is not a number") from None
    return DynsecClient(host, port, username, password)


def _is_not_found(exc: DynsecError) -> bool:
    return exc.error in ("Client not found", "Role not found")


def create_account(c: DynsecClient, name: str, password: str) -> None:
    """createClient + createRole + addClientRole; compensate in reverse on failure.

    A timeout means the command may have been applied, so it is cleaned up too.
    An error reply (e.g. "already exists") means nothing of ours was created.
    """
    try:
        c.run([{"command": "createClient", "username": name, "password": password}])
    except DynsecTimeout:
        _safe_delete_client(c, name)
        raise
    try:
        try:
            c.run([{"command": "createRole", "rolename": name}])
        except DynsecTimeout:
            _safe_delete_role(c, name)
            raise
        try:
            c.run([{"command": "addClientRole", "username": name, "rolename": name}])
        except DynsecError:
            _safe_delete_role(c, name)
            raise
    except DynsecError:
        _safe_delete_client(c, name)
        raise


def _safe_delete_client(c: DynsecClient, name: str) -> None:
    try:
        c.run([{"command": "deleteClient", "username": name}])
    except DynsecError as exc:
        if _is_not_found(exc):
            return
        logger.error("dynsec compensation failed: deleteClient name=%s error=%s", name, exc.error)


def _safe_delete_role(c: DynsecClient, name: str) -> None:
    try:
        c.run([{"command": "deleteRole", "rolename": name}])
    except DynsecError as exc:
        if _is_not_found(exc):
            return
        logger.error("dynsec compensation failed: deleteRole name=%s error=%s", name, exc.error)


def set_role_topics(c: DynsecClient, role: str, topics: set) -> None:
    """Make the role's publishClientSend ACLs equal `topics`; create the role if missing.

    Never creates a client.
    """
    try:
        resp = c.run([{"command": "getRole", "rolename": role}])[0]
    except DynsecError as exc:
        if not _is_not_found(exc):
            raise
        c.run([{"command": "createRole", "rolename": role}])
        current: set = set()
    else:
        acls = (resp.get("data") or {}).get("role", {}).get("acls", [])
        current = {a["topic"] for a in acls if a.get("acltype") == ACL_TYPE}
    cmds = [
        {"command": "removeRoleACL", "rolename": role, "acltype": ACL_TYPE, "topic": t}
        for t in sorted(current - topics)
    ] + [
        {"command": "addRoleACL", "rolename": role, "acltype": ACL_TYPE, "topic": t, "allow": True}
        for t in sorted(topics - current)
    ]
    if cmds:
        c.run(cmds)


def set_password(c: DynsecClient, name: str, password: str) -> None:
    c.run([{"command": "setClientPassword", "username": name, "password": password}])


def disable(c: DynsecClient, name: str) -> None:
    c.run([{"command": "disableClient", "username": name}])


def enable(c: DynsecClient, name: str) -> None:
    c.run([{"command": "enableClient", "username": name}])


def delete_account(c: DynsecClient, name: str) -> None:
    """Delete client then role; absent ones are ignored (idempotent)."""
    for cmd in ({"command": "deleteClient", "username": name},
                {"command": "deleteRole", "rolename": name}):
        try:
            c.run([cmd])
        except DynsecError as exc:
            if not _is_not_found(exc):
                raise
