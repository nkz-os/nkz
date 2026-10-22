import pytest
from nkz_platform_sdk.agronomy import (
    Source, AgronomicValue,
    combine_confidence, confidence_from_match, confidence_from_fidelity,
)


def test_combine_confidence_picks_minimum():
    assert combine_confidence(["high", "medium", "low"]) == "low"
    assert combine_confidence(["high", "high"]) == "high"
    assert combine_confidence(["high", "medium"]) == "medium"


def test_combine_confidence_empty_is_low():
    assert combine_confidence([]) == "low"


def test_confidence_from_match():
    assert confidence_from_match("exact", False) == "high"
    assert confidence_from_match("management", False) == "medium"
    assert confidence_from_match("generic", False) == "medium"
    assert confidence_from_match("species_only", False) == "low"
    assert confidence_from_match("none", False) == "low"
    # is_default override beats any match level
    assert confidence_from_match("exact", True) == "low"


def test_confidence_from_fidelity_both_vocabs():
    # meteo vocab
    assert confidence_from_fidelity("iot_sensor") == "high"
    assert confidence_from_fidelity("parcel_weather") == "medium"
    assert confidence_from_fidelity("regional_proxy") == "low"
    assert confidence_from_fidelity("unavailable") == "low"
    # engine vocab
    assert confidence_from_fidelity("onsite_calibrated") == "high"
    assert confidence_from_fidelity("local_proxy") == "medium"
    assert confidence_from_fidelity("modeled_opendata") == "low"
    # unknown → low (fail-safe)
    assert confidence_from_fidelity("???") == "low"


def test_agronomic_value_shape():
    av = AgronomicValue(
        value=1.15,
        source=Source(short="FAO-56", doi="10.x/y"),
        confidence="high",
    )
    d = av.model_dump()
    assert d == {
        "value": 1.15,
        "source": {"short": "FAO-56", "doi": "10.x/y", "institution": None},
        "confidence": "high",
        "fidelity": None,
        "notes": [],
    }


def test_agronomic_value_allows_none_value_and_str():
    AgronomicValue(value=None, source=Source(short="default"), confidence="low", notes=["sin dato"])
    AgronomicValue(value="flowering", source=Source(short="bioorchestrator"), confidence="medium")
