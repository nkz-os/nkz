"""UN/CEFACT Recommendation 20 unit-code lookup — single source of truth.

Translates the raw unit symbols stored in the `sensor_profiles` PostgreSQL catalogue
(`mapping->measurements[].unit`) into UN/CEFACT Recommendation 20 codes for NGSI-LD
`unitCode`. Consolidates — does not replace — the codes already hardcoded per attribute in
`services/entity-manager/orion_writer.py` (CEL, P1, MTS, DD, HPA, MMT): those values are
reused here verbatim, not re-derived.

Rules, in order (frozen for the 36 symbols measured in production, 2026-08-29):

1. Empty string -> `None`. No unit is not an error and not `C62`; the caller must omit
   `unitCode` entirely rather than write a fabricated one.
2. A verified real UN/CEFACT code where one exists.
3. No verified code, or genuinely dimensionless (`SPAD`, `pH`, ...) -> `C62` ("one"), the
   UN/CEFACT dimensionless code the platform already uses elsewhere. Every symbol mapped to
   `C62` is also listed in `DEGRADED_UNITS`, so a `C62` never silently passes as if it were a
   confirmed code — it always carries a visible "this was degraded" marker.
4. `geo:json` and `boolean` are not units at all — they are data types sitting in the
   catalogue's unit column. Mapped to `C62` so nothing blocks; the catalogue itself is not
   fixed here (see PENDING.md).
5. Any symbol NOT in this table raises. A unit that is new to the catalogue must fail loudly,
   never fall through as a bare symbol (the bug this module fixes) or a guessed code.

Several compound agronomic symbols (`kg/ha`, `L/ha`, `L/h`, `W/m2`, `µm`) are mapped to `C62`
not because no real code could plausibly exist, but because none could be verified with
confidence in this pass — see the Task 1 report for the reasoning per symbol. Degrading them
is a deliberate, documented choice: a wrong code claims a precision that is not there.
"""

from __future__ import annotations

from typing import Dict, Optional

# symbol -> UN/CEFACT Recommendation 20 code, or None for "no unit at all".
_UNIT_CODES: Dict[str, Optional[str]] = {
    "": None,
    "%": "P1",
    "A": "AMP",
    "L": "LTR",
    "L/h": "C62",
    "L/ha": "C62",
    "MPa": "MPA",
    "RPM": "RPM",
    "SPAD": "C62",
    "V": "VLT",
    "W/m2": "C62",
    "bar": "BAR",
    "boolean": "C62",
    "cm": "CMT",
    "dS/m": "C62",
    "deg": "DD",
    "geo:json": "C62",
    "h": "HUR",
    "ha": "HAR",
    "hPa": "HPA",
    "kPa": "KPA",
    "kVAR": "KVR",
    "kW": "KWT",
    "kWh": "KWH",
    "kg": "KGM",
    "kg/ha": "C62",
    "km/h": "KMH",
    "m": "MTR",
    "m/s": "MTS",
    "mg/kg": "NA",
    "mm": "MMT",
    "pH": "C62",
    "seeds/ha": "C62",
    "umol/m2/s": "C62",
    "°C": "CEL",
    "µm": "C62",
}

# Every symbol mapped to C62 — either because it is genuinely dimensionless (SPAD, pH,
# boolean), a data type stranded in the unit column (geo:json, boolean), or because no real
# code could be verified with confidence (kg/ha, L/ha, L/h, W/m2, µm, dS/m, seeds/ha,
# umol/m2/s). Kept as a derived set, not a hand-maintained list, so it can never drift from
# _UNIT_CODES.
DEGRADED_UNITS: frozenset = frozenset(
    symbol for symbol, code in _UNIT_CODES.items() if code == "C62"
)


def to_unit_code(symbol: str) -> Optional[str]:
    """Translate a catalogue unit symbol into its UN/CEFACT Recommendation 20 code.

    Returns `None` for the empty string (no unit — omit `unitCode`, don't fabricate one).
    Raises `ValueError` for any symbol absent from the table: an unrecognized unit must fail
    loudly rather than pass through as a raw symbol or a silently-guessed code.
    """
    if symbol not in _UNIT_CODES:
        raise ValueError(
            f"Unknown unit symbol {symbol!r} — not in the measured production catalogue. "
            "Add it to common/unit_codes.py explicitly; do not guess a code here."
        )
    return _UNIT_CODES[symbol]
