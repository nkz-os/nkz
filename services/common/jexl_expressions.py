"""Validation of IoT Agent attribute expressions (JEXL over incoming keys).

The IoT Agent evaluates `expression` with the incoming message keys as variables
(e.g. `r3 * 0.1`). The legacy `${@...}` forms are silently ignored by the agent and
write nothing, so they are rejected. Only simple arithmetic and clamp forms are accepted;
the anchored patterns allow nothing but identifiers, numbers and `+ - * / > < ? :`, so no
call, member access or string can get through.
"""
import re
from typing import Iterable, Optional

_VAR = r"[A-Za-z_][A-Za-z0-9_]*"
_NUM = r"\d+(?:\.\d+)?"
_OP = r"[\+\-\*\/]"

JEXL_ALLOWED_PATTERNS = [
    rf"^{_VAR}$",
    rf"^{_VAR}\s*{_OP}\s*{_NUM}$",
    rf"^{_NUM}\s*{_OP}\s*{_VAR}$",
    rf"^{_VAR}\s*{_OP}\s*{_NUM}\s*{_OP}\s*{_NUM}$",
    rf"^({_VAR})\s*>\s*{_NUM}\s*\?\s*{_NUM}\s*:\s*\1$",
    rf"^({_VAR})\s*<\s*{_NUM}\s*\?\s*{_NUM}\s*:\s*\1$",
]


def validate_jexl_expression(expr: str, variables: Optional[Iterable[str]] = None) -> tuple[bool, str]:
    """Return (is_valid, error_message). An empty expression is valid (none).

    `variables`: the identifiers the expression may reference (e.g. the incoming keys of a
    profile). None allows any identifier.
    """
    if not expr or expr.strip() == "":
        return True, ""
    expr = expr.strip()
    if "${" in expr:
        return False, "Expresión no permitida: las formas '${...}' no escriben nada en el IoT Agent"
    if not any(re.match(p, expr) for p in JEXL_ALLOWED_PATTERNS):
        example = sorted(variables)[0] if variables else "val"
        return (False, f"Expresión JEXL no válida. Usa patrones simples como '{example}', "
                       f"'{example} * 0.1', '{example} + 273'")
    if variables is not None:
        allowed = set(variables)
        used = set(re.findall(_VAR, expr))
        unknown = sorted(used - allowed)
        if unknown:
            return False, f"Expresión no permitida: variable desconocida '{unknown[0]}'"
    return True, ""
