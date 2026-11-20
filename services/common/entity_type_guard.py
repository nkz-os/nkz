"""Runtime check of NGSI-LD entity types against the closed list.

The list lives in `config/entity-types/` (`allowed.txt`, `legacy.txt`) and the
platform context in `config/ngsi-ld-context.json`. The CI checker
(`.github/scripts/check_entity_types.py`) parses the same files, but service
images do not contain `.github/`, so parsing is re-implemented here; a parity
test keeps the two in step.

No Flask import and no relative import: the module is loaded both as
`entity_type_guard` and as `common.entity_type_guard`, and by non-web jobs.
"""

from __future__ import annotations

import functools
import http.client
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

logger = logging.getLogger(__name__)

CONFIG_DIR_ENV = "ENTITY_TYPE_CONFIG_DIR"
ENFORCE_ENV = "ENTITY_TYPE_GUARD_ENFORCE"
LISTS_SUBDIR = "entity-types"
ALLOWED_FILE = "allowed.txt"
LEGACY_FILE = "legacy.txt"
CONTEXT_FILE = "ngsi-ld-context.json"
BROKER_HEADER = "## broker"
CODE_HEADER = "## code"
# Bounds on what one rejection echoes (log line and 422 body): a client controls
# these strings, and log pipelines drop or cut oversized lines.
MAX_ECHOED_TYPES = 20
MAX_ECHOED_CHARS = 200
# JSON-LD only treats a term as a prefix when its IRI ends in one of these.
PREFIX_DELIMITERS = ("/", "#", ":", "?", "@", "[", "]")
# The one type without a term in the platform context: the broker stored it
# under the default vocabulary, so that IRI must keep matching.
ZONE_NAME = "AgriManagementZone"
ZONE_DEFAULT_IRI = "https://uri.etsi.org/ngsi-ld/default-context/" + ZONE_NAME
# Environment of the `broker` subcommand and the page size of the types listing.
ORION_URL_ENV = "ORION_URL"
TENANTS_ENV = "ENTITY_TYPE_CHECK_TENANTS"
TYPES_PAGE_LIMIT = 1000
TYPES_PATH = f"/ngsi-ld/v1/types?details=true&limit={TYPES_PAGE_LIMIT}"


class GuardUnavailable(Exception):
    """The lists or the context could not be loaded; the guard must not judge."""


@dataclass(frozen=True)
class AcceptedTypes:
    names: frozenset[str]
    iris: frozenset[str]
    # Prefix term -> absolute IRI, from the same context: `nkz:X` is judged by
    # the IRI the broker will store, not by the bare name after the colon.
    prefixes: Mapping[str, str]


def parse_names(text: str) -> list[str]:
    """One name per line; blank lines and `#` comments are ignored."""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def parse_legacy(text: str) -> tuple[list[str], list[str]]:
    """Return (broker, code) names.

    A name before a header and any `## <other>` header are errors: an unknown
    header would otherwise be read as a comment and silently merge its names
    into the previous block.
    """
    blocks: dict[str, list[str]] = {BROKER_HEADER: [], CODE_HEADER: []}
    current: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line in blocks:
            current = blocks[line]
        elif line.startswith("##"):
            raise ValueError(f"line {number}: unknown block header '{line}'")
        elif not line or line.startswith("#"):
            continue
        elif current is None:
            raise ValueError(f"line {number}: name {line!r} before a '## broker' or '## code' header")
        else:
            current.append(line)
    return blocks[BROKER_HEADER], blocks[CODE_HEADER]


def find_config_dir() -> Path | None:
    """The directory holding `entity-types/allowed.txt`, or None.

    An explicit override is the only candidate: falling back from a wrong
    override to a bundled copy would hide a misconfiguration.
    """
    override = os.environ.get(CONFIG_DIR_ENV, "")
    if override:
        candidates = [Path(override)]
    else:
        here = Path(__file__).resolve().parent
        candidates = [here.parent / "config", here.parent.parent / "config"]
    for candidate in candidates:
        if (candidate / LISTS_SUBDIR / ALLOWED_FILE).is_file():
            return candidate
    return None


def _context_terms(context: object) -> dict:
    """The term table of the platform context; unusable shapes are an error.

    A context that parses but yields no terms would load an empty table and
    silently reject every IRI-form type, so it is as unavailable as a missing file.
    """
    parts = context.get("@context") if isinstance(context, dict) else None
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        raise GuardUnavailable("platform context has no usable @context")
    terms: dict = {}
    for part in parts:
        if isinstance(part, dict):
            terms.update(part)
    if not terms:
        raise GuardUnavailable("platform context defines no terms")
    return terms


def _expand(terms: dict, name: str, depth: int = 0) -> str | None:
    """The IRI a short name expands to in the context, or None if it has no term."""
    value = terms.get(name)
    if isinstance(value, dict):
        value = value.get("@id")
    if not isinstance(value, str):
        return None
    prefix, sep, rest = value.partition(":")
    if sep and not rest.startswith("//") and prefix in terms and depth < 5:
        base = _expand(terms, prefix, depth + 1)
        if base:
            return base + rest
    return value


def load_accepted(config_dir: Path | None = None) -> AcceptedTypes:
    directory = config_dir if config_dir is not None else find_config_dir()
    if directory is None:
        raise GuardUnavailable("entity type config directory not found")
    try:
        allowed = parse_names((directory / LISTS_SUBDIR / ALLOWED_FILE).read_text(encoding="utf-8"))
        broker, _code = parse_legacy((directory / LISTS_SUBDIR / LEGACY_FILE).read_text(encoding="utf-8"))
        context = json.loads((directory / CONTEXT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise GuardUnavailable(f"cannot load entity type lists: {type(exc).__name__}: {exc}") from exc

    names = frozenset(allowed) | frozenset(broker)
    terms = _context_terms(context)
    iris: set[str] = set()
    for name in names:
        iri = _expand(terms, name)
        if iri:
            iris.add(iri)
    if ZONE_NAME in names:
        iris.add(ZONE_DEFAULT_IRI)
    prefixes: dict[str, str] = {}
    for term in terms:
        if term.startswith("@"):
            continue
        base = _expand(terms, term)
        if base and "://" in base and base.endswith(PREFIX_DELIMITERS):
            prefixes[term] = base
    return AcceptedTypes(names=names, iris=frozenset(iris), prefixes=MappingProxyType(prefixes))


def _judgeable(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def declared_types(body: object) -> list[str] | None:
    """The type(s) a payload declares, or None when it cannot be judged.

    Both `type` and `@type` are read: a body carrying both must not get one
    past the check by hiding it under the key that is not looked at.
    """
    if not isinstance(body, dict):
        return None
    found: list[str] = []
    for key in ("type", "@type"):
        values = _judgeable(body.get(key))
        if values:
            found.extend(values)
    return found or None


def _is_accepted(value: str, accepted: AcceptedTypes) -> bool:
    if "://" in value:
        return value in accepted.iris
    prefix, sep, rest = value.partition(":")
    if sep:
        base = accepted.prefixes.get(prefix)
        return base is not None and (base + rest) in accepted.iris
    return value in accepted.names


def rejected_types(types: list[str], accepted: AcceptedTypes) -> list[str]:
    return [value for value in types if not _is_accepted(value, accepted)]


def _bounded(values: list[str]) -> list[str]:
    return [
        value if len(value) <= MAX_ECHOED_CHARS else value[:MAX_ECHOED_CHARS] + "\u2026"
        for value in values[:MAX_ECHOED_TYPES]
    ]


def enforce_enabled(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() == "true"


class EntityTypeGuard:
    def __init__(self, service: str, accepted: AcceptedTypes | None, enforce: bool):
        self.service = service
        self.accepted = accepted
        self.enforce = enforce

    @classmethod
    def from_env(cls, service: str) -> "EntityTypeGuard":
        """Never raises: a guard that cannot load its lists lets everything through, loudly."""
        enforce = enforce_enabled(os.environ.get(ENFORCE_ENV))
        try:
            accepted = load_accepted()
        except Exception as exc:  # noqa: BLE001 - this runs at import; any failure must degrade, not kill the worker
            # Class only: an exception message can carry a path or file content.
            logger.critical(
                "entity_type_guard %s",
                json.dumps({"enforced": enforce, "error": type(exc).__name__, "service": service, "status": "unavailable"}, sort_keys=True),
            )
            accepted = None
        else:
            # One line per start: the proof, in each running process, that the lists were found.
            logger.info(
                "entity_type_guard %s",
                json.dumps({"enforced": enforce, "names": len(accepted.names), "service": service, "status": "loaded"}, sort_keys=True),
            )
        return cls(service, accepted, enforce)

    def check(
        self,
        types: list[str] | None,
        *,
        tenant: str | None,
        path: str,
        module_id: str | None = None,
        foreign_context: bool = False,
    ) -> list[str]:
        """Names to reject: empty when fine, unavailable or not enforcing."""
        if self.accepted is None or not types:
            return []
        rejected = rejected_types(types, self.accepted)
        if not rejected and not foreign_context:
            return []
        rejected = _bounded(rejected)
        record = {
            "enforced": self.enforce,
            "foreign_context": foreign_context,
            "module_id": module_id,
            "path": path,
            "service": self.service,
            "tenant": tenant,
            "types": rejected,
        }
        level = logging.WARNING if rejected else logging.INFO
        logger.log(level, "entity_type_guard %s", json.dumps(record, sort_keys=True))
        return rejected if self.enforce else []


# --- Broker check: the types actually stored, per tenant (daily job) ---------


class BrokerCheckError(Exception):
    """The check could not be completed for a tenant (not the same as finding an unknown type)."""


class TruncatedListing(BrokerCheckError):
    def __init__(self) -> None:
        super().__init__(f"listing may be truncated at {TYPES_PAGE_LIMIT} types")


def stored_type_iris(
    orion_url: str,
    tenant: str,
    *,
    attempts: int = 5,
    delay: float = 10.0,
    timeout: float = 30.0,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """The IRIs the broker stores entity types under for one tenant, in the order received.

    No `Link` header is sent: the `id` of each item is the stored IRI either way.
    Connection failures and 5xx are retried because a job can start before its
    network policy is programmed; a broker that never answers must end in an
    error, never in a clean result. Every failure to get an answer, whatever its
    class, ends in BrokerCheckError so the caller can tell "could not check" from
    "found an unknown type".
    """
    # An address without a scheme ("host:1026") parses with the host as the
    # scheme, so urllib would only fail later and be retried: reject it here.
    if urllib.parse.urlsplit(orion_url).scheme not in ("http", "https"):
        raise BrokerCheckError("broker address needs an http or https scheme")
    try:
        request = urllib.request.Request(
            orion_url.rstrip("/") + TYPES_PATH,
            headers={
                "NGSILD-Tenant": tenant,
                "Fiware-Service": tenant,
                "Fiware-ServicePath": "/",
                "Accept": "application/json",
            },
            method="GET",
        )
    except ValueError as exc:
        raise BrokerCheckError("broker address is not valid") from exc
    last: Exception | None = None
    raw = b""
    for attempt in range(attempts):
        if attempt:
            # A growing wait: the network policy of a fresh pod can take tens of seconds.
            sleep(delay * attempt)
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
            last = None
            break
        except urllib.error.HTTPError as exc:  # before URLError: it is a subclass
            if exc.code < 500:
                raise BrokerCheckError(f"broker answered HTTP {exc.code}") from exc
            last = exc
        except (OSError, http.client.HTTPException) as exc:
            # URLError, connection refused, DNS failure, timeouts; and a
            # non-HTTP answer or a body cut off mid-read (a broker restarting).
            last = exc
        except ValueError as exc:  # a malformed request will not improve on retry
            raise BrokerCheckError("broker request is not valid") from exc
    if last is not None:
        raise BrokerCheckError("broker unreachable") from last
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise BrokerCheckError("broker answer is not JSON") from exc
    if not isinstance(body, list) or not all(isinstance(i, dict) and isinstance(i.get("id"), str) for i in body):
        raise BrokerCheckError("broker answer is not a list of types")
    # A full page may hide further types: never report it as clean.
    if len(body) >= TYPES_PAGE_LIMIT:
        raise TruncatedListing()
    return [item["id"] for item in body]


def unknown_stored_types(iris: list[str], accepted: AcceptedTypes) -> list[str]:
    return sorted(set(iris) - accepted.iris)


def _error_label(exc: BrokerCheckError) -> str:
    if isinstance(exc, TruncatedListing):
        return str(exc)
    cause = exc.__cause__
    return type(cause if cause is not None else exc).__name__


def run_broker_check(
    orion_url: str,
    tenants: list[str],
    accepted: AcceptedTypes,
    *,
    fetch: Callable[[str, str], list[str]] = stored_type_iris,
    out: Callable[[str], None] = functools.partial(print, flush=True),
) -> int:
    """0 clean, 1 an unknown type stored, 2 a tenant could not be checked.

    Every tenant is checked even after a finding. Lines carry the class of an
    error, never its message, which can hold addresses. Any exception from
    `fetch` counts as a tenant that could not be checked, and an empty listing
    does too: the tenant list is written by hand, and a wrong name would
    otherwise read as clean every day. Output is flushed per line so a job
    killed at its deadline keeps what it already reported.
    """
    unknown_found = False
    errored = False
    for tenant in tenants:
        try:
            iris = fetch(orion_url, tenant)
        except BrokerCheckError as exc:
            errored = True
            out(f"error {tenant}: {_error_label(exc)}")
            continue
        except Exception as exc:  # noqa: BLE001 - nothing may stop the other tenants
            errored = True
            out(f"error {tenant}: {type(exc).__name__}")
            continue
        if not iris:
            errored = True
            out(f"error {tenant}: no entity types found")
            continue
        unknown = unknown_stored_types(iris, accepted)
        if unknown:
            unknown_found = True
            for iri in unknown:
                out(f"unknown {tenant}: {iri}")
        else:
            out(f"ok {tenant}: {len(iris)} types")
    return 2 if errored else 1 if unknown_found else 0


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="entity_type_guard")
    parser.add_argument("command", choices=["broker"], help="check the entity types stored in the broker")
    parser.parse_args(argv)

    orion_url = os.environ.get(ORION_URL_ENV, "").strip()
    if not orion_url:
        print(f"error: {ORION_URL_ENV} is not set", flush=True)
        return 2
    tenants = [t.strip() for t in os.environ.get(TENANTS_ENV, "").split(",") if t.strip()]
    if not tenants:
        print(f"error: {TENANTS_ENV} is empty", flush=True)
        return 2
    try:
        accepted = load_accepted()
    except GuardUnavailable:
        print("error: entity type lists unavailable", flush=True)
        return 2
    # Looked up here, not bound as a default, so the name can be replaced in tests.
    return run_broker_check(orion_url, tenants, accepted, fetch=stored_type_iris)


def main(argv: list[str] | None = None) -> int:
    """Entry point: an unexpected error is a failed check (2), never a traceback."""
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the job's exit code is the alarm
        print(f"error: {type(exc).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
