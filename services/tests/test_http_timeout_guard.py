"""Guard: every requests.* HTTP call in critical services must set an explicit timeout.

AST-based so it gates ALL current and future calls in the listed files —
extend GUARDED_FILES as services are hardened.
"""
import ast
import os

import pytest

_SERVICES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUARDED_FILES = [
    os.path.join(_SERVICES, "api-gateway", "fiware_api_gateway.py"),
    os.path.join(
        _SERVICES, "telemetry-worker", "telemetry_worker", "subscription_manager.py"
    ),
]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "request"}


def _calls_missing_timeout(path):
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in HTTP_VERBS
            and isinstance(func.value, ast.Name)
            and func.value.id == "requests"
        ):
            if not any(kw.arg == "timeout" for kw in node.keywords):
                missing.append(f"{os.path.basename(path)}:{node.lineno}")
    return missing


@pytest.mark.parametrize("path", GUARDED_FILES, ids=os.path.basename)
def test_all_requests_calls_have_timeout(path):
    missing = _calls_missing_timeout(path)
    assert not missing, (
        "requests.* calls without timeout= (hangs exhaust the worker pool): "
        + ", ".join(missing)
    )
