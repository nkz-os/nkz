"""Diagnósticos del vocabulario: expansiones almacenadas contra el @context vigente.

Una suscripción guarda el tipo de entidad expandido internamente en Orion, en el momento de
crearse, y no se re-expande nunca. Si el @context cambia después, ese tipo queda apuntando a
un IRI que ya no corresponde a nada y deja de disparar en silencio: sin error, sin log, sin
notificación. El test de contrato estático no puede verlo — valida el código fuente, no el
broker.

Al leer una suscripción, Orion-LD COMPACTA el tipo almacenado contra el @context que le
pasamos en el header Link de la petición (via `inject_fiware_headers`, en
`_fetch_all_subscriptions`): si ese contexto define un término que compacta al IRI guardado,
Orion devuelve el término corto (`AgriSensor`); si no, devuelve el IRI completo tal cual lo
tiene almacenado. `audit_expansions` explota exactamente ese comportamiento — ver su
docstring.

Endpoint interno de operación: no está expuesto por el api-gateway, que enruta por rutas
explícitas. Eso NO lo hace seguro por sí solo — la NetworkPolicy base del namespace permite
tráfico pod-a-pod sin restricción, así que cualquier pod (incluido un worker de módulo
comprometido) podría alcanzarlo. Autenticado con X-Internal-Service-Secret, igual que el
resto de endpoints internos del servicio.
"""

import hmac
import logging
import os

import requests
from flask import Blueprint, jsonify, request

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

diagnostics_bp = Blueprint("diagnostics", __name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv("CONTEXT_URL", "")
ORION_PAGE_SIZE = 1000


def _load_context() -> dict:
    """Términos del @context vigente, servido por el api-gateway."""
    response = requests.get(CONTEXT_URL, timeout=10)
    response.raise_for_status()
    document = response.json()
    context = document.get("@context", document)
    terms: dict = {}
    for part in (context if isinstance(context, list) else [context]):
        if isinstance(part, dict):
            terms.update(part)
    return terms


def _expand(term: str, terms: dict) -> str | None:
    """IRI que el @context vigente daría a `term`, o None si no lo define."""
    value = terms.get(term)
    iri = value.get("@id") if isinstance(value, dict) else value
    if not isinstance(iri, str) or not iri:
        return None
    prefix, _, rest = iri.partition(":")
    base = terms.get(prefix)
    if rest and isinstance(base, str) and base.endswith(("/", "#")):
        return f"{base}{rest}"
    return iri


def audit_expansions(subscriptions: list) -> dict:
    """Compara el tipo almacenado de cada suscripción con el @context vigente.

    `_fetch_all_subscriptions` pide las suscripciones con el @context vigente en el header
    Link, así que el valor de `type` que llega aquí ya viene COMPACTADO por Orion contra ese
    contexto, no expandido:

    - si el contexto vigente define un término que compacta al IRI que Orion tiene
      almacenado, Orion devuelve el término corto (`AgriSensor`) — eso solo es posible si el
      contexto lo define, así que es sano por construcción;
    - si no lo define, Orion no tiene con qué compactarlo y devuelve el IRI completo
      almacenado (`https://.../AgriSensor`) — esa suscripción es un fósil: no volverá a
      disparar contra una entidad escrita con el contexto actual.

    Por eso el criterio de staleness es la FORMA del valor devuelto (IRI completo con
    esquema, o término compacto), no una re-expansión y comparación. NO "arreglar" esto
    volviendo a expandir el término corto y comparándolo contra el IRI — eso es el bug
    original (todo se reportaba como stale).

    Para una entrada stale, `expected` es el IRI que el contexto vigente le daría al nombre
    local de ese tipo si lo define, o None si no lo define en absoluto — ese None es el caso
    peligroso y no se puede callar.
    """
    terms = _load_context()
    stale = []
    checked = 0
    for subscription in subscriptions:
        for entity in subscription.get("entities", []):
            stored = entity.get("type")
            if not stored:
                continue
            checked += 1
            if "://" not in stored:
                # Orion pudo compactar este tipo contra el @context vigente: sano.
                continue
            local = stored.rsplit("/", 1)[-1]
            expected = _expand(local, terms)
            stale.append({
                "description": subscription.get("description", ""),
                "stored": stored,
                "expected": expected,
            })
    return {"checked": checked, "stale": stale}


def _fetch_all_subscriptions(headers: dict) -> list:
    """Todas las suscripciones del tenant, siguiendo la paginación de Orion.

    Orion devuelve 20 si se omite `limit` y rechaza limit > 1000.
    """
    subs: list = []
    offset = 0
    while True:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
            params={"limit": ORION_PAGE_SIZE, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        page = response.json() or []
        subs.extend(page)
        if len(page) < ORION_PAGE_SIZE:
            return subs
        offset += ORION_PAGE_SIZE


@diagnostics_bp.route("/api/diagnostics/expansions", methods=["GET"])
def expansions():
    """Audita las suscripciones del tenant frente al @context vigente.

    Internal endpoint — authenticated by X-Internal-Service-Secret (not user JWT). Not being
    routed through the api-gateway does not make it safe on its own: any pod in the namespace
    can reach it without this secret.

    Headers:
      X-Internal-Service-Secret  — must match configured secret
      X-Tenant-ID                — tenant context
    """
    provided_secret = request.headers.get("X-Internal-Service-Secret", "")
    expected_secret = os.getenv("INTERNAL_SERVICE_SECRET", "")

    if not expected_secret:
        logger.error("INTERNAL_SERVICE_SECRET not configured on server")
        return jsonify({"error": "Internal server configuration error"}), 500

    if not hmac.compare_digest(provided_secret, expected_secret):
        logger.warning("Invalid X-Internal-Service-Secret for expansion audit")
        return jsonify({"error": "Unauthorized"}), 401

    tenant = request.headers.get("X-Tenant-ID", "")
    if not tenant:
        return jsonify({"error": "X-Tenant-ID required"}), 400
    headers = inject_fiware_headers({}, tenant=tenant, has_context_in_body=False)

    try:
        subscriptions = _fetch_all_subscriptions(headers)
    except Exception as exc:
        logger.error("expansion audit: failed to fetch subscriptions from Orion: %s", exc)
        return jsonify({"error": "failed to fetch subscriptions"}), 502

    try:
        result = audit_expansions(subscriptions)
    except Exception as exc:
        logger.error("expansion audit: failed to load vocabulary context: %s", exc)
        return jsonify({"error": "failed to load vocabulary context"}), 502

    return jsonify(result), 200
