"""Shape of the NGSI-LD entity reads a service forwards to the broker.

Only known parameters, in a known form. Everything else is refused, and the
validators return what may be forwarded. No Flask import.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import unquote

MAX_FILTER_CHARS = 2048
MAX_FILTER_TERMS = 12
MAX_FILTER_DEPTH = 4
MAX_PATH_CHARS = 256
MAX_FILTER_PATTERNS = 2
MAX_LIST = 50
MAX_TYPES = 20
MAX_ENTITIES = 20
MAX_PAGE = 1000
MAX_OFFSET = 10**9
MAX_IRI_CHARS = 200
MAX_URI_CHARS = 256
MAX_PATTERN_CHARS = 128
MAX_COORDINATES_CHARS = 4096
MAX_DECODES = 5
OPTIONS = frozenset({"keyValues", "simplified", "normalized", "concise", "sysAttrs", "count"})

_NAME = r"[A-Za-z_@][A-Za-z0-9_:\-]{0,127}"
_PATH = rf"{_NAME}(?:\.{_NAME}){{0,4}}"
_STRING = r'"[^"\\\x00-\x1f\x7f]{0,256}"'
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2}))?"
_NUMBER = r"-?[0-9]{1,15}(?:\.[0-9]{1,15})?(?:[eE][+-]?[0-9]{1,2})?"
_WORD = r"(?!null(?![A-Za-z0-9_:\-]))[A-Za-z][A-Za-z0-9_:\-]{0,127}"
_ORDERED = rf"(?:{_STRING}|{_DATE}|{_NUMBER})"
_VALUE = rf"(?:{_STRING}|{_DATE}|{_NUMBER}|{_WORD})"
_PATTERN = r"\^?(?:[\w :/\-]|\.\*|\.(?![.*])){1,64}\$?"
_TERM = re.compile(
    rf"(?P<path>{_PATH}(?:\[{_PATH}\])?)"
    r"(?:"
    rf"(?:==|!=)(?:{_ORDERED}\.\.{_ORDERED}|{_VALUE}(?:,{_VALUE}){{0,{MAX_LIST - 1}}})"
    rf"|(?:>=|<=|>|<){_ORDERED}"
    rf"|!?~=(?P<pattern>{_PATTERN})"
    r")?(?=[;|)]|\Z)",
)


_TERM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_:\-]{0,127}")
_IRI_NAME = re.compile(r"https?://[A-Za-z0-9._~:/#\-]+")
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:[A-Za-z0-9._~:/@+#=\-]+")
_ID_PATTERN = re.compile(r"\^?(?:[A-Za-z0-9_:/\-]|\.\*|\.(?![.*]))+\$?")
_NEAR = re.compile(r"near;(?:max|min)Distance==[0-9]{1,10}(?:\.[0-9]{1,6})?")
_GEORELS = frozenset({"within", "contains", "overlaps", "intersects", "equals", "disjoint"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InvalidQuery(ValueError):
    """A read that is not in the accepted form; `parameter` names the part."""

    def __init__(self, parameter: str):
        super().__init__(parameter)
        self.parameter = parameter


def _valid_wildcard(pattern: str) -> bool:
    """At most one `.*`: at either end, or anywhere after a leading `^`."""
    if ".*" not in pattern:
        return True
    return pattern.count(".*") == 1 and (pattern.startswith(("^", ".*")) or pattern.endswith(".*"))


def _valid_pattern(pattern: str) -> bool:
    body = pattern.removeprefix("^").removesuffix("$")
    return len(pattern) <= 64 and _valid_wildcard(pattern) and body == body.strip()


def valid_filter(q: object) -> bool:
    """True for a `q` made of well-formed terms joined by `;` or `|`."""
    if not isinstance(q, str) or not 0 < len(q) <= MAX_FILTER_CHARS:
        return False
    position = depth = terms = patterns = 0
    expect_term = True
    while position < len(q):
        char = q[position]
        if expect_term:
            if char == "(":
                depth += 1
                position += 1
                if depth > MAX_FILTER_DEPTH:
                    return False
                continue
            match = _TERM.match(q, position)
            if match is None or len(match.group("path")) > MAX_PATH_CHARS:
                return False
            pattern = match.group("pattern")
            if pattern is not None:
                patterns += 1
                if patterns > MAX_FILTER_PATTERNS or not _valid_pattern(pattern):
                    return False
            terms += 1
            if terms > MAX_FILTER_TERMS:
                return False
            position = match.end()
            expect_term = False
        elif char == ")":
            depth -= 1
            position += 1
            if depth < 0:
                return False
        elif char in ";|":
            position += 1
            expect_term = True
        else:
            return False
    return depth == 0 and not expect_term


def valid_name(value: object) -> bool:
    """True for a term or an http(s) IRI."""
    if not isinstance(value, str):
        return False
    if _TERM_NAME.fullmatch(value):
        return True
    return len(value) <= MAX_IRI_CHARS and _IRI_NAME.fullmatch(value) is not None


def _is_uri(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_URI_CHARS and _URI.fullmatch(value) is not None


def _is_id_pattern(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_PATTERN_CHARS
        and _valid_wildcard(value)
        and _ID_PATTERN.fullmatch(value) is not None
    )


def _comma_list(is_item: Callable[[object], bool], maximum: int) -> Callable[[str], bool]:
    def check(value: str) -> bool:
        items = value.split(",")
        return len(items) <= maximum and all(is_item(item) for item in items)

    return check


def _whole_number(maximum: int) -> Callable[[str], bool]:
    digits = re.compile(rf"[0-9]{{1,{len(str(maximum))}}}")
    return lambda value: digits.fullmatch(value) is not None and int(value) <= maximum


def _is_boolean(value: str) -> bool:
    return value in ("true", "false")


def _is_georel(value: object) -> bool:
    return isinstance(value, str) and (value in _GEORELS or _NEAR.fullmatch(value) is not None)


def _is_number(value: object, bound: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and -bound <= value <= bound


def _is_position(value: object) -> bool:
    if not isinstance(value, list) or len(value) not in (2, 3):
        return False
    return (
        _is_number(value[0], 180)
        and _is_number(value[1], 90)
        and (len(value) == 2 or _is_number(value[2], 100000))
    )


def _list_of(check: Callable[[object], bool], minimum: int) -> Callable[[object], bool]:
    return lambda value: isinstance(value, list) and len(value) >= minimum and all(check(v) for v in value)


_is_line = _list_of(_is_position, 2)
_closed_ring = _list_of(_is_position, 4)


def _is_ring(value: object) -> bool:
    return _closed_ring(value) and value[0] == value[-1]


_is_polygon = _list_of(_is_ring, 1)
_GEOMETRIES: Mapping[str, Callable[[object], bool]] = {
    "Point": _is_position,
    "MultiPoint": _list_of(_is_position, 1),
    "LineString": _is_line,
    "MultiLineString": _list_of(_is_line, 1),
    "Polygon": _is_polygon,
    "MultiPolygon": _list_of(_is_polygon, 1),
}


def _refuse_constant(name: str):
    raise ValueError(name)


def _parsed_coordinates(text: str) -> object:
    if len(text) > MAX_COORDINATES_CHARS:
        return None
    try:
        return json.loads(text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        return None


_LIST_CHECKS: Mapping[str, Callable[[str], bool]] = {
    "type": _comma_list(valid_name, MAX_TYPES),
    "id": _comma_list(_is_uri, MAX_LIST),
    "idPattern": _is_id_pattern,
    "attrs": _comma_list(valid_name, MAX_LIST),
    "q": valid_filter,
    "georel": _is_georel,
    "geometry": lambda value: value in _GEOMETRIES,
    "coordinates": lambda value: True,  # judged with its geometry below
    "geoproperty": valid_name,
    "limit": _whole_number(MAX_PAGE),
    "offset": _whole_number(MAX_OFFSET),
    "count": _is_boolean,
    "options": _comma_list(lambda item: item in OPTIONS, len(OPTIONS)),
}


def _checks(*names: str) -> Mapping[str, Callable[[str], bool]]:
    return {name: _LIST_CHECKS[name] for name in names}


_ENTITY_CHECKS = _checks("type", "attrs", "options")
_TYPES_CHECKS = {"details": _is_boolean, **_checks("limit", "offset")}
_QUERY_CHECKS = _checks("limit", "offset", "count", "options")


def _checked(args: Iterable[tuple[str, str]], checks: Mapping[str, Callable[[str], bool]]) -> dict[str, str]:
    params: dict[str, str] = {}
    for name, value in args:
        if name not in checks or name in params or not checks[name](value):
            raise InvalidQuery(name)
        params[name] = value
    return params


def list_params(args: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Parameters of an entity list."""
    params = _checked(args, _LIST_CHECKS)
    if any(name in params for name in ("georel", "geometry", "coordinates", "geoproperty")):
        for name in ("georel", "geometry", "coordinates"):
            if name not in params:
                raise InvalidQuery(name)
        if not _GEOMETRIES[params["geometry"]](_parsed_coordinates(params["coordinates"])):
            raise InvalidQuery("coordinates")
    return params


def entity_params(args: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Parameters of one entity."""
    return _checked(args, _ENTITY_CHECKS)


def types_params(args: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Parameters of the type listing."""
    return _checked(args, _TYPES_CHECKS)


def query_params(args: Iterable[tuple[str, str]]) -> dict[str, str]:
    """URL parameters of an entity query."""
    return _checked(args, _QUERY_CHECKS)


def _is_selector(value: object) -> bool:
    if not isinstance(value, dict) or not value or ("id" in value and "idPattern" in value):
        return False
    checks = {"type": valid_name, "id": _is_uri, "idPattern": _is_id_pattern}
    return all(key in checks and checks[key](item) for key, item in value.items())


def _is_geo_query(value: object) -> bool:
    if not isinstance(value, dict) or not {"geometry", "coordinates", "georel"} <= value.keys():
        return False
    if value.keys() - {"geometry", "coordinates", "georel", "geoproperty"}:
        return False
    geometry = value["geometry"]
    return (
        isinstance(geometry, str)
        and geometry in _GEOMETRIES
        and _is_georel(value["georel"])
        and ("geoproperty" not in value or valid_name(value["geoproperty"]))
        and _GEOMETRIES[geometry](value["coordinates"])
        and len(json.dumps(value["coordinates"], separators=(",", ":"))) <= MAX_COORDINATES_CHARS
    )


_BODY_CHECKS: Mapping[str, Callable[[object], bool]] = {
    "type": lambda value: value == "Query",
    "entities": lambda value: (
        isinstance(value, list) and 0 < len(value) <= MAX_ENTITIES and all(_is_selector(v) for v in value)
    ),
    "attrs": lambda value: (
        isinstance(value, list) and 0 < len(value) <= MAX_LIST and all(valid_name(v) for v in value)
    ),
    "q": valid_filter,
    "geoQ": _is_geo_query,
}


def query_body(body: object) -> dict:
    """Body of an entity query."""
    if not isinstance(body, dict):
        raise InvalidQuery("body")
    for key, value in body.items():
        if key not in _BODY_CHECKS or not _BODY_CHECKS[key](value):
            raise InvalidQuery(key)
    if "type" not in body:
        raise InvalidQuery("type")
    if not body.keys() & {"entities", "attrs", "q", "geoQ"}:
        raise InvalidQuery("entities")
    return body


def percent_decoded(value: str) -> str | None:
    """Percent-decode until stable, or None when it does not settle."""
    for _ in range(MAX_DECODES):
        nxt = unquote(value)
        if nxt == value:
            return value
        value = nxt
    return None


def valid_entity_path(path: str) -> bool:
    """True when what follows `entities/` names an entity and nothing but a path."""
    if not path.strip("/") or "?" in path or "#" in path:
        return False
    decoded = percent_decoded(path)
    if decoded is None or _CONTROL.search(decoded):
        return False
    return not {".", ".."} & set(decoded.split("/"))


def valid_identifier(value: object) -> bool:
    """True for an id that is only an id: in a path, or between quotes in a filter."""
    if not isinstance(value, str) or not valid_entity_path(value):
        return False
    decoded = percent_decoded(value)
    return decoded is not None and not set(decoded) & set('?#"\\')
