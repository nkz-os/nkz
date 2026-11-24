"""An Orion-LD at HTTP level: entities are stored under IRIs and read back through a context.

Behaviour recorded from fiware/orion-ld:1.12.0 (`-mongocOnly -coreContext v1.6`):
- a term missing from the context is stored under the default vocabulary, and the default
  vocabulary is read back as the BARE term, with the Link header and without it;
- without a Link only the core terms are short, everything else is a full IRI;
- a one-element array is stored as its bare element;
- an upsert cannot alter the type of an entity (207);
- an unknown tenant is a 404 and a listing carries `NGSILD-Results-Count` when asked to.

`install` routes every real `requests` call (the SDK's included) to it, so the headers that are
checked are the ones that would be sent.
"""
import copy
import json
from urllib.parse import parse_qs, unquote, urlsplit

import requests

DEFAULT = "https://uri.etsi.org/ngsi-ld/default-context/"
DEVICE_IRI = "https://smartdatamodels.org/dataModel.Device/"
CORE = {"status": "https://uri.etsi.org/ngsi-ld/status"}
SYNCED = {
    "name": "https://schema.org/name",
    "category": "https://smartdatamodels.org/dataModel.Agrifood/category",
    "sensorType": "https://saref.etsi.org/core/SensorType",
    "airTemperature": "https://saref.etsi.org/core/Temperature",
    **{term: DEVICE_IRI + term for term in (
        "Device", "DeviceModel", "controlledProperty", "deviceState", "refDeviceModel",
        "dateInstalled", "deviceCategory", "manufacturerName", "brandName", "modelName")},
}
NEW_TERMS = ("DeviceModel", "refDeviceModel", "dateInstalled", "deviceCategory",
             "manufacturerName", "brandName", "modelName")


def without(*terms):
    """The platform context as a broker that cached it before `terms` existed has it."""
    return {term: iri for term, iri in SYNCED.items() if term not in terms}


def prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def rel(urn):
    return {"type": "Relationship", "object": urn}


def _reduced(attr):
    attr = copy.deepcopy(attr)
    for member in ("value", "object"):
        if isinstance(attr, dict) and isinstance(attr.get(member), list) and len(attr[member]) == 1:
            attr[member] = attr[member][0]
    return attr


class FakeBroker:
    def __init__(self, tenant):
        self.tenant = tenant
        self.context = dict(SYNCED)
        self.entities = {}
        self.log = []       # (method, urn or path, with a Link)
        self.bodies = []    # (method, urn, body) of every write
        self.headers = []   # the headers of every request
        self.hooks = []     # called with (method, path, with a Link) before a request is answered

    # ---- vocabulary ----

    def expand(self, term, context=None):
        if "://" in term:
            return term
        context = self.context if context is None else context
        return CORE.get(term) or context.get(term) or DEFAULT + term

    def compact(self, iri, link):
        for term, known in {**(self.context if link else {}), **CORE}.items():
            if known == iri:
                return term
        return iri[len(DEFAULT):] if iri.startswith(DEFAULT) else iri

    # ---- direct access for the tests ----

    def seed(self, entity, context=None):
        """Store an entity as if written through `context` (default: the synced platform one)."""
        context = SYNCED if context is None else context
        types = entity["type"] if isinstance(entity["type"], list) else [entity["type"]]
        self.entities[entity["id"]] = {
            "types": [self.expand(t, context) for t in types],
            "attrs": {self.expand(n, context): _reduced(a) for n, a in entity.items()
                      if n not in ("id", "type", "@context")}}

    def view(self, urn, link=True):
        stored = self.entities[urn]
        types = [self.compact(t, link) for t in stored["types"]]
        return {"id": urn, "type": types[0] if len(types) == 1 else types,
                **{self.compact(i, link): copy.deepcopy(a) for i, a in stored["attrs"].items()}}

    @property
    def writes(self):
        return [(m, urn) for m, urn, _ in self.bodies]

    # ---- HTTP ----

    def install(self, monkeypatch):
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                            lambda adapter, request, **kw: self._answer(request))
        return self

    def _answer(self, request):
        url = urlsplit(request.url)
        path = unquote(url.path).removeprefix("/ngsi-ld/v1/")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        link = "Link" in request.headers
        subject = path.removeprefix("entities/") if path.startswith("entities/") else path
        self.headers.append(dict(request.headers))
        self.log.append((request.method, subject, link))
        for hook in list(self.hooks):
            result = hook(request.method, subject, link)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                return self._response(request, *(result if isinstance(result, tuple) else (result,)))
        if request.headers.get("NGSILD-Tenant") != self.tenant:
            return self._response(request, 404, {"title": "No such tenant"})
        body = json.loads(request.body) if request.body else None
        return self._response(request, *self._route(request.method, path, query, link, body))

    def _response(self, request, status, body=None, headers=None):
        resp = requests.Response()
        resp.status_code, resp.url, resp.request = status, request.url, request
        resp._content = json.dumps(body).encode() if body is not None else b""
        resp.headers.update(headers or {})
        return resp

    def _route(self, method, path, query, link, body):
        if method == "GET" and path == "entities":
            return self._list(query, link)
        if method == "GET" and path.startswith("entities/"):
            urn = path.removeprefix("entities/")
            return (200, self.view(urn, link)) if urn in self.entities else (404, {"title": "Not Found"})
        if method == "POST" and path == "entityOperations/upsert":
            return self._upsert(body)
        if method == "POST" and path.endswith("/attrs"):
            urn = path.removeprefix("entities/").removesuffix("/attrs")
            self.bodies.append((method, urn, body))
            if urn not in self.entities:
                return 404, {"title": "Not Found"}
            context = None if link else {}
            self.entities[urn]["attrs"].update(
                {self.expand(n, context): _reduced(a) for n, a in body.items()})
            return (204,)
        if method == "DELETE" and "/attrs/" in path:
            urn, attr = path.removeprefix("entities/").split("/attrs/")
            self.bodies.append((method, urn, attr))
            iri = self.expand(attr, None if link else {})
            found = urn in self.entities and self.entities[urn]["attrs"].pop(iri, None)
            return (204,) if found else (404, {"title": "Not Found"})
        raise AssertionError(f"unexpected request: {method} {path}")

    def _list(self, query, link):
        iri = self.expand(query["type"], None if link else {})
        found = [urn for urn, stored in self.entities.items() if iri in stored["types"]]
        offset, limit = int(query.get("offset", 0)), int(query["limit"])
        headers = {"NGSILD-Results-Count": str(len(found))} if query.get("count") == "true" else {}
        return 200, [self.view(urn, link) for urn in found[offset:offset + limit]], headers

    def _upsert(self, body):
        errors = []
        for entity in body:
            self.bodies.append(("UPSERT", entity["id"], entity))
            stored = self.entities.get(entity["id"])
            types = [self.expand(entity["type"])]
            if stored and stored["types"] != types:
                errors.append({"entityId": entity["id"],
                               "error": {"detail": "the Entity Type cannot be altered", "status": 400}})
                continue
            attrs = {self.expand(n): _reduced(a) for n, a in entity.items()
                     if n not in ("id", "type", "@context")}
            self.entities[entity["id"]] = {"types": types,
                                           "attrs": {**(stored["attrs"] if stored else {}), **attrs}}
        return (207, {"success": [], "errors": errors}) if errors else (201, [e["id"] for e in body])
