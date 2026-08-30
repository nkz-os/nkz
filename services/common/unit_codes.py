"""UN/CEFACT Recommendation 20 unit-code lookup — single source of truth.

Translates the raw unit symbols stored in the `sensor_profiles` PostgreSQL catalogue
(`mapping->measurements[].unit`) into UN/CEFACT Recommendation 20 codes for NGSI-LD
`unitCode`. Consolidates — does not replace — most of the codes already hardcoded per
attribute in `services/entity-manager/orion_writer.py` (CEL, P1, MTS, DD, MMT): those values
are reused here verbatim, not re-derived. **`hPa` is the one deliberate exception** — see
below.

Rules, in order (frozen for the 36 symbols measured in production, 2026-08-29):

1. Empty string -> `None`. No unit is not an error and not `C62`; the caller must omit
   `unitCode` entirely rather than write a fabricated one.
2. A verified real UN/CEFACT code where one exists.
3. No real code, or genuinely dimensionless (`SPAD`, ...) -> `C62` ("one"), the UN/CEFACT
   dimensionless code the platform already uses elsewhere. Every symbol mapped to `C62` is
   also listed in `DEGRADED_UNITS`, so a `C62` never silently passes as if it were a confirmed
   code — it always carries a visible "this was degraded" marker.
4. `geo:json` and `boolean` are not units at all — they are data types sitting in the
   catalogue's unit column. Mapped to `C62` so nothing blocks; the catalogue itself is not
   fixed here (see PENDING.md).
5. Any symbol NOT in this table raises. A unit that is new to the catalogue must fail loudly,
   never fall through as a bare symbol (the bug this module fixes) or a guessed code.

First pass (2026-08-29) treated `pH`, `µm`, `W/m2`, `L/h` as unverifiable and degraded them to
`C62`, flagging the uncertainty instead of guessing. A second pass (2026-08-30) checked the
full official UNECE Recommendation 20 list (2136 entries) and found real codes for all four —
`Q30`, `4H`, `D54`, `E32` respectively — now used below. `kg/ha`, `L/ha`, `seeds/ha`,
`umol/m2/s`, `dS/m` (only kilo-/megasiemens per metre exist in Rec20, not deci-), `SPAD`,
`geo:json` and `boolean` were checked against the same list and confirmed to genuinely have no
code — `DEGRADED_UNITS` now holds exactly (and only) those.

**`hPa` bug inherited from `orion_writer.py:120`, NOT copied here:** that file hardcodes
`"unitCode": "HPA"` with a `# Hectopascal` comment, but in Rec20 `HPA` means "hectolitre of
pure alcohol" — a volume of alcohol, not a pressure. The real code for hectopascal is `A97`.
This table uses `A97` for `hPa`; `orion_writer.py` was deliberately left unfixed (separate
blast radius, see `PENDING.md` at the workspace root).
"""

from __future__ import annotations

from typing import Dict, Optional

# symbol -> UN/CEFACT Recommendation 20 code, or None for "no unit at all".
_UNIT_CODES: Dict[str, Optional[str]] = {
    "": None,
    "%": "P1",
    "A": "AMP",
    "L": "LTR",
    "L/h": "E32",
    "L/ha": "C62",
    "MPa": "MPA",
    "RPM": "RPM",
    "SPAD": "C62",
    "V": "VLT",
    "W/m2": "D54",
    "bar": "BAR",
    "boolean": "C62",
    "cm": "CMT",
    "dS/m": "C62",
    "deg": "DD",
    "geo:json": "C62",
    "h": "HUR",
    "ha": "HAR",
    "hPa": "A97",
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
    "pH": "Q30",
    "seeds/ha": "C62",
    "umol/m2/s": "C62",
    "°C": "CEL",
    "µm": "4H",
}

# Every symbol mapped to C62 — either genuinely dimensionless (SPAD), a data type stranded in
# the unit column (geo:json, boolean), or confirmed against the official UNECE Recommendation
# 20 list (2136 entries, checked 2026-08-30) to have no real code at all (kg/ha, L/ha,
# seeds/ha, umol/m2/s, dS/m — only kilo-/megasiemens per metre exist, not deci-). Kept as a
# derived set, not a hand-maintained list, so it can never drift from _UNIT_CODES.
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
