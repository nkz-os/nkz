"""Contract for `common.unit_codes` — the single source of truth for UN/CEFACT `unitCode`.

The 36 symbols here are the ones actually measured in the production `sensor_profiles`
catalogue (not a guess, not the truncated 30-symbol list from plan v1). See
`common/unit_codes.py` module docstring for the degradation rules.
"""

import pytest

from common.unit_codes import DEGRADED_UNITS, _UNIT_CODES, to_unit_code

# The 36 symbols measured in production. Kept as a literal list (not `_UNIT_CODES.keys()`) so
# this test fails loudly if the table drifts from what was actually measured.
ALL_36_SYMBOLS = [
    "%", "", "A", "L", "L/h", "L/ha", "MPa", "RPM", "SPAD", "V", "W/m2", "bar",
    "boolean", "cm", "dS/m", "deg", "geo:json", "h", "ha", "hPa", "kPa", "kVAR",
    "kW", "kWh", "kg", "kg/ha", "km/h", "m", "m/s", "mg/kg", "mm", "pH",
    "seeds/ha", "umol/m2/s", "°C", "µm",
]

DIMENSIONLESS_SYMBOLS = {"SPAD", "pH", "boolean", "geo:json"}


def test_covers_exactly_the_36_measured_symbols():
    assert len(ALL_36_SYMBOLS) == 36
    assert set(ALL_36_SYMBOLS) == set(_UNIT_CODES.keys())


@pytest.mark.parametrize("symbol", ALL_36_SYMBOLS)
def test_every_measured_symbol_has_an_entry(symbol):
    # Must not raise — every measured symbol is covered.
    to_unit_code(symbol)


@pytest.mark.parametrize(
    "symbol,expected_code",
    [
        ("°C", "CEL"),
        ("%", "P1"),
        ("m/s", "MTS"),
        ("mm", "MMT"),
        ("hPa", "HPA"),
        ("deg", "DD"),
        ("kVAR", "KVR"),
        ("mg/kg", "NA"),
    ],
)
def test_unambiguous_codes_match_orion_writer_and_plan(symbol, expected_code):
    """These six (CEL/P1/MTS/DD/HPA/MMT) are the codes already hardcoded per-attribute in
    entity-manager's orion_writer.py — this table must not diverge from them. kVAR->KVR and
    mg/kg->NA are given explicitly by the plan as unambiguous, previously mis-degraded in v1.
    """
    assert to_unit_code(symbol) == expected_code


def test_empty_string_means_no_unit_not_an_error_and_not_c62():
    assert to_unit_code("") is None
    assert "" not in DEGRADED_UNITS


@pytest.mark.parametrize("symbol", sorted(DIMENSIONLESS_SYMBOLS))
def test_dimensionless_and_datatype_symbols_degrade_to_c62(symbol):
    assert to_unit_code(symbol) == "C62"
    assert symbol in DEGRADED_UNITS


def test_no_symbol_with_a_real_code_is_flagged_as_degraded():
    """DEGRADED_UNITS must contain exactly the C62 entries — nothing that has a real code."""
    for symbol in ALL_36_SYMBOLS:
        code = to_unit_code(symbol)
        if code is not None and code != "C62":
            assert symbol not in DEGRADED_UNITS, (
                f"{symbol!r} resolves to a real code {code!r} but is also in DEGRADED_UNITS"
            )


def test_every_c62_result_is_registered_in_degraded_units():
    for symbol in ALL_36_SYMBOLS:
        if to_unit_code(symbol) == "C62":
            assert symbol in DEGRADED_UNITS, f"{symbol!r} maps to C62 but is not in DEGRADED_UNITS"


def test_unknown_symbol_raises_instead_of_a_default():
    with pytest.raises(ValueError):
        to_unit_code("this-symbol-does-not-exist-in-the-catalogue")


def test_unknown_symbol_does_not_silently_return_none_or_c62():
    """A brand-new unit sneaking into the catalogue must never be swallowed as either 'no
    unit' or 'dimensionless' — both would hide the fact that nobody classified it yet.
    """
    with pytest.raises(ValueError):
        to_unit_code("kN/m3-not-a-real-catalogue-symbol")
