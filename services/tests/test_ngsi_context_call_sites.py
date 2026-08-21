"""Static contract: the @context delivery mode must be explicit at every Orion write.

Orion-LD answers 400 BadRequestData when Content-Type is application/json and the
payload carries an @context member (ETSI GS CIM 009 §6.3.5). The shape that caused
it repeatedly across this codebase:

    entity["@context"] = CONTEXT_URL
    headers = {"Content-Type": "application/ld+json"}   # intent
    headers = inject_fiware_headers(headers, tenant)    # silently overwritten to json
    requests.post(orion, json=entity, headers=headers)  # -> 400

Setting Content-Type before the call never survives it, so a caller-set
"application/ld+json" is either a no-op or a live bug — it is never the mechanism
that selects ld+json. The mode is selected by body=<payload> (preferred: derived
from what is actually sent) or has_context_in_body=<bool>.

Rule enforced here: if a call site sets Content-Type application/ld+json, it MUST
also pass body= or has_context_in_body=. That is statically decidable, and it is
exactly the footgun that produced the outages.
"""

import pathlib
import re

SERVICES = pathlib.Path(__file__).resolve().parent.parent

LD_JSON = "application/ld+json"
# Accept: application/ld+json is a read-side header and is never overwritten.
ACCEPT_LD = re.compile(r"""['"]Accept['"]\s*:\s*['"]application/ld\+json['"]""")


def _iter_call_sites():
    """Yield (relpath, lineno, source_line, full_call, preceding_block)."""
    for path in sorted(SERVICES.rglob("*.py")):
        rel = path.relative_to(SERVICES)
        if "tests" in rel.parts or path.name.startswith("test_"):
            continue
        lines = path.read_text(errors="ignore").split("\n")
        for i, line in enumerate(lines):
            if "inject_fiware_headers(" not in line or "def inject_fiware_headers" in line:
                continue
            yield rel, i + 1, line.strip(), " ".join(lines[i : i + 4]), "\n".join(
                lines[max(0, i - 12) : i + 1]
            )


def _sets_ld_json_content_type(call: str, back: str) -> bool:
    """True if this site puts Content-Type: ld+json into the headers it passes in."""
    region = call if "{" in call and "}" in call.split("inject_fiware_headers", 1)[-1] else back
    for chunk in (call, region):
        without_accept = ACCEPT_LD.sub("", chunk)
        if LD_JSON in without_accept and "Content-Type" in without_accept:
            return True
    return False


def test_ld_json_call_sites_declare_their_mode():
    offenders = []
    for rel, lineno, line, call, back in _iter_call_sites():
        if "has_context_in_body" in call or "body=" in call:
            continue
        if _sets_ld_json_content_type(call, back):
            offenders.append(f"{rel}:{lineno}  ->  {line}")

    assert not offenders, (
        "Content-Type: application/ld+json set before inject_fiware_headers() without "
        "declaring the @context mode. The helper overwrites Content-Type, so this is a "
        "silent downgrade to application/json; if the body carries @context, Orion-LD "
        "returns 400. Pass body=<payload>.\n" + "\n".join(offenders)
    )


def test_scanner_actually_matches_call_sites():
    """Guard the guard: a scanner matching nothing would pass vacuously."""
    assert sum(1 for _ in _iter_call_sites()) > 10
