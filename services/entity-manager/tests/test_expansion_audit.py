"""Las expansiones almacenadas en el broker deben coincidir con el @context vigente.

Una suscripción guarda el tipo ya expandido y no lo re-expande nunca. Si el contexto cambia,
queda huérfana y deja de disparar en silencio. El test de contrato estático no lo ve: valida
código, no el broker.
"""

import os
import sys
from unittest.mock import MagicMock, patch

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# La suite de entity-manager stubea `common` antes de importar los blueprints.
_common_mock = MagicMock()
_common_mock.inject_fiware_headers = lambda h, **kw: dict(h)
sys.modules.setdefault("common", _common_mock)
sys.modules.setdefault("common.ngsi_headers", _common_mock)

from blueprints.diagnostics import audit_expansions  # noqa: E402

CTX = {
    "AgriSensor": {"@id": "nkz:AgriSensor"},
    "nkz": "https://nkz-os.org/ns/",
}


def _sub(description, entity_type):
    return {"description": description, "entities": [{"type": entity_type}]}


def test_flags_a_subscription_expanded_under_the_default_vocabulary():
    subs = [_sub("legacy", "https://uri.etsi.org/ngsi-ld/default-context/AgriSensor")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 1
    assert len(result["stale"]) == 1
    assert result["stale"][0]["description"] == "legacy"
    assert result["stale"][0]["expected"] == "https://nkz-os.org/ns/AgriSensor"


def test_accepts_a_subscription_that_matches_the_current_context():
    subs = [_sub("current", "https://nkz-os.org/ns/AgriSensor")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["stale"] == []


def test_flags_an_expansion_from_a_retired_namespace():
    subs = [_sub("pathological", "https://nekazari.robotika.cloud/ngsi-ld/AgriSensor")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert len(result["stale"]) == 1


def test_a_type_absent_from_the_context_is_reported_not_skipped():
    """Un tipo desconocido es el caso peligroso: no se puede callar."""
    subs = [_sub("unknown", "https://uri.etsi.org/ngsi-ld/default-context/Device")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert len(result["stale"]) == 1
    assert result["stale"][0]["expected"] is None
