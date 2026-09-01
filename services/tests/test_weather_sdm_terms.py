"""Los términos meteo del @context deben apuntar al IRI del SDM oficial.

Un término ausente expande contra el vocabulario por defecto en silencio: la entidad
se escribe, la consulta no la encuentra y nadie ve un error. Un término presente pero
con IRI propio produce lo mismo entre servicios que usen el nombre estándar.
"""

import json
from pathlib import Path

import pytest

CONTEXT_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "ngsi-ld-context.json"
)

SDM_WEATHER = "https://smartdatamodels.org/dataModel.Weather/"
SDM_ROOT = "https://smartdatamodels.org/"

EXPECTED = {
    "WeatherForecast": SDM_WEATHER + "WeatherForecast",
    "dayMinimum": SDM_WEATHER + "dayMinimum",
    "dayMaximum": SDM_WEATHER + "dayMaximum",
    "precipitationProbability": SDM_WEATHER + "precipitationProbability",
    "gustSpeed": SDM_WEATHER + "gustSpeed",
    "validFrom": SDM_ROOT + "validFrom",
    "validTo": SDM_ROOT + "validTo",
}


def _terms() -> dict:
    doc = json.loads(CONTEXT_FILE.read_text())
    ctx = doc.get("@context", doc)
    terms: dict = {}
    if isinstance(ctx, list):
        for part in ctx:
            if isinstance(part, dict):
                terms.update(part)
    elif isinstance(ctx, dict):
        terms.update(ctx)
    return terms


@pytest.mark.parametrize("term,iri", sorted(EXPECTED.items()))
def test_term_maps_to_the_official_sdm_iri(term, iri):
    terms = _terms()
    assert term in terms, (
        f"'{term}' no está en el @context de la plataforma. Sin él expande al "
        "vocabulario por defecto y toda consulta por ese nombre devuelve cero."
    )
    entry = terms[term]
    actual = entry["@id"] if isinstance(entry, dict) else entry
    assert actual == iri, f"'{term}' apunta a {actual}, se esperaba {iri}"
