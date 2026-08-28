"""Discovery of the services that reconcile their own Orion-LD subscriptions.

Shared by the tests that assert cross-cutting invariants over them. Discovery is
from the filesystem on purpose: a hand-maintained list is exactly what lets the
next service ship a subscription nobody checked.
"""

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
