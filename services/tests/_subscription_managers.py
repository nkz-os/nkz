"""Discovery of the services that reconcile their own Orion-LD subscriptions.

Shared by the tests that assert cross-cutting invariants over them. Discovery is
from the filesystem on purpose: a hand-maintained list is exactly what lets the
next service ship a subscription nobody checked.
"""

import ast
import importlib.util
import pathlib

SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1]
CONTEXT_FILE = SERVICES_DIR.parent / "config" / "ngsi-ld-context.json"

# Managers sit either directly under the service dir or inside its package dir.
MANAGERS = sorted(SERVICES_DIR.glob("*/subscription_manager.py")) + sorted(
    SERVICES_DIR.glob("*/*/subscription_manager.py")
)

# Present in every checkout; asserted so an empty or broken glob fails loudly
# instead of making every parametrized test vacuously pass.
KNOWN = {"entity-manager", "risk-worker", "telemetry_worker"}


def service_id(path: pathlib.Path) -> str:
    return path.parent.name


_LOADED: dict = {}


def load(path: pathlib.Path):
    """Import a manager once and reuse it.

    Memoised because these modules register Prometheus collectors at import
    time, and the default registry rejects a second registration of the same
    metric — a fresh exec per test would fail on the second one.
    """
    key = str(path)
    if key not in _LOADED:
        spec = importlib.util.spec_from_file_location(
            f"_subs_{service_id(path).replace('-', '_')}", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED[key] = module
    return _LOADED[key]


def subscribed_types(path: pathlib.Path) -> set:
    """Entity types a manager subscribes to, read from the source, not imported.

    Parsed rather than imported on purpose: this is the guard that catches a
    subscription type missing from the platform @context, and it must not stop
    working the day a service adds a dependency the test environment lacks.
    Anything built dynamically yields nothing here, which the caller asserts on.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    types: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "SUBSCRIPTIONS" for t in node.targets):
            continue
        for entities in _values_for_key(node.value, "entities"):
            for entity in getattr(entities, "elts", []):
                for value in _values_for_key(entity, "type"):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        types.add(value.value)
    return types


def _values_for_key(node, key: str):
    """Every value bound to `key` in any dict literal inside `node`."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Dict):
            continue
        for k, v in zip(sub.keys, sub.values):
            if isinstance(k, ast.Constant) and k.value == key:
                yield v
