"""Validation of IoT Agent attribute expressions (JEXL over incoming keys).

The IoT Agent evaluates `expression` with the incoming message keys as variables
(e.g. `r3 * 0.1`). The legacy `${@...}` forms are silently ignored by the agent and
write nothing, so they are rejected. Only simple arithmetic and clamp forms are accepted.
"""
import re

_VAR = r"[A-Za-z_][A-Za-z0-9_]*"
_NUM = r"[\d\.]+"

JEXL_ALLOWED_PATTERNS = [
    rf"^{_VAR}$",
    rf"^{_VAR}\s*[\+\-\*\/]\s*{_NUM}$",
    rf"^{_NUM}\s*[\+\-\*\/]\s*{_VAR}$",
    rf"^{_VAR}\s*[\+\-\*\/]\s*{_NUM}\s*[\+\-\*\/]\s*{_NUM}$",
    rf"^({_VAR})\s*>\s*{_NUM}\s*\?\s*{_NUM}\s*:\s*\1$",
    rf"^({_VAR})\s*<\s*{_NUM}\s*\?\s*{_NUM}\s*:\s*\1$",
]

_DANGEROUS = ("import", "require", "eval", "exec", "function", "process", "global",
              "__", "constructor")


def validate_jexl_expression(expr: str) -> tuple[bool, str]:
    """Return (is_valid, error_message). An empty expression is valid (none)."""
    if not expr or expr.strip() == "":
        return True, ""
    expr = expr.strip()
    if "${" in expr:
        return False, "Expresión no permitida: las formas '${...}' no escriben nada en el IoT Agent"
    for pattern in _DANGEROUS:
        if pattern in expr.lower():
            return False, f"Expresión no permitida: contiene '{pattern}'"
    for pattern in JEXL_ALLOWED_PATTERNS:
        if re.match(pattern, expr):
            return True, ""
    return (False, "Expresión JEXL no válida. Usa patrones simples como 'r1', 'r1 * 0.1', 'r1 + 273'")
