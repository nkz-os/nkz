"""Context terms that share one IRI, resolved to the term Orion-LD returns.

Orion-LD compacts two terms with the same IRI in `config/ngsi-ld-context.json` to the
LAST one defined there, so a Device attribute written as `temperature` is read back as
`airTemperature`. Profiles, IoT Agent mappings and the measurement bridge must all use
the term Orion returns.
"""

CANONICAL = {
    "temperature": "airTemperature",
    "humidity": "relativeHumidity",
}


def canonical_attribute(name: str) -> str:
    """The term Orion-LD returns for `name`; identity for names without an alias."""
    return CANONICAL.get(name, name)
