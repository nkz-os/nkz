"""Diagnósticos del vocabulario: expansiones almacenadas contra el @context vigente.

Una suscripción guarda el tipo de entidad expandido internamente en Orion, en el momento de
crearse, y no se re-expande nunca. Si el tipo no estaba definido en el @context de la
plataforma en ese momento, Orion lo almacena bajo el vocabulario por defecto de NGSI-LD
(`https://uri.etsi.org/ngsi-ld/default-context/<Term>`) y esa suscripción no puede volver a
disparar nunca contra una entidad escrita con el contexto de la plataforma: falla en
silencio, sin error, sin log, sin notificación. Este es el fallo más común de la plataforma,
y el que este endpoint existe para detectar. El test de contrato estático no puede verlo —
valida código fuente, no lo que hay guardado en el broker.

SEGUNDA VEZ que este módulo se equivoca sobre cómo Orion compacta el `type` almacenado. La
primera versión trataba cualquier término compactado como sano. Pero el contexto núcleo
(core context) de NGSI-LD fija `@vocab` al vocabulario por defecto
(`https://uri.etsi.org/ngsi-ld/default-context/`), y JSON-LD compacta CUALQUIER IRI que
empiece por el valor de `@vocab` al nombre local sin que ningún término lo defina
explícitamente. Orion siempre mezcla el core context con el que se le pase — así que esto
pasa TANTO si mandamos el @context de la plataforma en el Link como si no lo mandamos. Un
tipo huérfano `default-context/AgriSensor` compacta a `AgriSensor`; un tipo sano
`nkz:AgriSensor` definido por la plataforma TAMBIÉN compacta a `AgriSensor`. Con el @context
de la plataforma puesto, término corto sano y término corto huérfano son indistinguibles.

Medido en un tenant real (43 suscripciones): pidiendo con el @context de la plataforma en el
Link, 41 términos cortos / 2 IRIs completos — solo 2 de 30 huérfanos reales detectados (más 2
de un namespace obsoleto, indistinguibles de los 30 huérfanos y de los 11 sanos). Pidiendo
SIN ningún Link (solo el core context que Orion pone siempre), 30 términos cortos / 13 IRIs
completos — coincide exacto con la BD: 30 suscripciones bajo `default-context/`, 2 bajo un
namespace `saref4agri` retirado, 11 canónicas.

La clave: sin el Link de la plataforma, el único contexto en juego es el core context, y su
`@vocab` es EXACTAMENTE `default-context/`. Un término corto en esa respuesta solo puede
significar una cosa — un tipo `default-context/<Term>` — sin ambigüedad posible. Un IRI
completo es cualquier cosa que el core context no pudo compactar por `@vocab` (namespace de
la plataforma, namespace obsoleto...); ahí sí hace falta comparar contra el @context vigente
para saber si es sano.

NO volver a mandar el @context de la plataforma en el Link al LISTAR suscripciones para este
audit. Si algún refactor futuro reintroduce eso aquí, el endpoint vuelve a fallar en la misma
dirección peligrosa: reportando como sanas suscripciones huérfanas.

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

logger = logging.getLogger(__name__)

diagnostics_bp = Blueprint("diagnostics", __name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv("CONTEXT_URL", "")
ORION_PAGE_SIZE = 1000

# @vocab del core context de NGSI-LD. Un `type` almacenado bajo este prefijo es exactamente
# lo que compacta a un término corto cuando la petición no lleva el @context de la
# plataforma — ver el docstring del módulo.
DEFAULT_CONTEXT_VOCAB = "https://uri.etsi.org/ngsi-ld/default-context/"


def _load_context() -> dict:
    """Términos del @context vigente, servido por el api-gateway.

    Se usa para calcular `expected` — el IRI que la plataforma le daría a un término si lo
    definiera — NO para pedir las suscripciones a Orion. Mandar este contexto al listar es
    el bug que este módulo ya cometió una vez (ver docstring del módulo).
    """
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

    Precondición OBLIGATORIA: `subscriptions` viene de pedir a Orion SIN el @context de la
    plataforma en el Link — solo con el core context que Orion pone siempre (ver
    `expansions()` y el docstring del módulo). Bajo esa condición, y SOLO bajo esa
    condición, la forma del `type` que devuelve Orion identifica sin ambigüedad el caso
    stale:

    - un TÉRMINO CORTO (`AgriSensor`) solo puede venir de que el `@vocab` del core context
      (`https://uri.etsi.org/ngsi-ld/default-context/`) haya compactado el IRI almacenado —
      y eso solo es posible si ese IRI empezaba exactamente por ese vocabulario, es decir,
      si la suscripción es un huérfano `default-context/<Term>`. SIEMPRE stale.
    - un IRI COMPLETO es cualquier cosa que el core context no pudo compactar por `@vocab`
      (namespace de la plataforma o namespace obsoleto). Aquí sí hace falta comparar contra
      lo que el @context vigente le daría al nombre local: coincide → sano; no coincide o
      el contexto vigente no define ese término → stale.

    NO reintroducir el @context de la plataforma en la petición que alimenta esta función.
    Esa es exactamente la forma en la que este módulo ya se equivocó una vez — ver docstring
    del módulo.

    Para toda entrada stale, `expected` es el IRI que el @context vigente le daría al nombre
    local de ese tipo si lo define, o None si no lo define en absoluto — ese None es el caso
    peligroso (un tipo que ni siquiera la plataforma reconoce) y no se puede callar.
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
                # Término corto: solo el @vocab del core context pudo producirlo, así que
                # el IRI real almacenado en Orion es default-context/<local>. Stale
                # incondicional — ver docstring de la función.
                local = stored
                real_stored = f"{DEFAULT_CONTEXT_VOCAB}{local}"
                stale.append({
                    "description": subscription.get("description", ""),
                    "stored": real_stored,
                    "expected": _expand(local, terms),
                })
                continue

            local = stored.rsplit("/", 1)[-1]
            expected = _expand(local, terms)
            if expected == stored:
                continue  # El @context vigente reproduce exactamente lo almacenado: sano.
            stale.append({
                "description": subscription.get("description", ""),
                "stored": stored,
                "expected": expected,
            })
    return {"checked": checked, "stale": stale}


def _fetch_all_subscriptions(headers: dict) -> list:
    """Todas las suscripciones del tenant, siguiendo la paginación de Orion.

    Orion devuelve 20 si se omite `limit` y rechaza limit > 1000.

    `headers` NO debe llevar el Link del @context de la plataforma para este audit — quien
    construye `headers` (`expansions()`) es responsable de eso. Ver el docstring del módulo:
    mandarlo hace indistinguibles los términos sanos de los huérfanos default-context/.
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

    # A propósito SIN el Link del @context de la plataforma, y por eso construidas a mano en
    # vez de con inject_fiware_headers (que lo añadiría). Mandarlo aquí hace indistinguibles
    # los términos sanos de los huérfanos default-context/ — es la causa raíz del falso
    # negativo que este endpoint tuvo antes. Ver el docstring del módulo.
    headers = {
        "NGSILD-Tenant": tenant,
        "Fiware-Service": tenant,
        "Accept": "application/json",
    }

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
