"""Shape of the NGSI-LD entity reads a service forwards to the broker.

Only known parameters, in a known form. Everything else is refused, and the
validators return what may be forwarded. No Flask import.
"""

from __future__ import annotations

import re

MAX_FILTER_CHARS = 2048
MAX_FILTER_TERMS = 12
MAX_FILTER_DEPTH = 4
MAX_LIST = 50

_NAME = r"[A-Za-z_@][A-Za-z0-9_:\-]{0,127}"
_PATH = rf"{_NAME}(?:\.{_NAME}){{0,4}}"
_STRING = r'"[^"\\\x00-\x1f\x7f]{0,256}"'
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z)?"
_NUMBER = r"-?[0-9]{1,15}(?:\.[0-9]{1,15})?(?:[eE][+-]?[0-9]{1,2})?"
_WORD = r"(?!null(?![A-Za-z0-9_:\-]))[A-Za-z][A-Za-z0-9_:\-]{0,127}"
_ORDERED = rf"(?:{_STRING}|{_DATE}|{_NUMBER})"
_VALUE = rf"(?:{_STRING}|{_DATE}|{_NUMBER}|{_WORD})"
_PATTERN = r"\^?(?:[\w :/\-]|\.\*|\.(?![.*])){1,64}\$?"
_TERM = re.compile(
    rf"{_PATH}(?:\[{_PATH}\])?"
    r"(?:"
    rf"(?:==|!=)(?:{_ORDERED}\.\.{_ORDERED}|{_VALUE}(?:,{_VALUE}){{0,{MAX_LIST - 1}}})"
    rf"|(?:>=|<=|>|<){_ORDERED}"
    rf"|!?~=(?P<pattern>{_PATTERN})"
    r")?(?=[;|)]|\Z)",
)


def _valid_pattern(pattern: str) -> bool:
    body = pattern.removeprefix("^").removesuffix("$")
    return len(pattern) <= 64 and pattern.count(".*") <= 2 and body == body.strip()


def valid_filter(q: object) -> bool:
    """True for a `q` made of well-formed terms joined by `;` or `|`."""
    if not isinstance(q, str) or not 0 < len(q) <= MAX_FILTER_CHARS:
        return False
    position = depth = terms = 0
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
            if match is None:
                return False
            pattern = match.group("pattern")
            if pattern is not None and not _valid_pattern(pattern):
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
