"""How weather-api knows which Tmin method it serves (pinned, tested against frost_margins.json).

The frost-risk margins are fitted to one Tmin method per source. They are served only while
the method below equals the ``method_version`` of the margins file; otherwise the field is
null with reason ``method_changed``. Change the string here ONLY together with a refit of the
margins, or when the Tmin computation itself changes (then the margins must go null until refit).
"""

TMIN_METHOD_VERSION = {
    # /history daily tmin_c: ERA5 grid hourly (elevation=nan) aggregated to the local civil day
    "history": ("nkz@8c7050a: ERA5 grid hourly (elevation=nan) aggregated to the local civil day "
                "by weather-api daily_from_hourly"),
    # /daily closed-day tmin_c (stored days from 2026-11-17, filled days always): Open-Meteo daily
    # temperature_2m_min of the raw model cell (elevation=nan) over the local day, no altitude
    # correction; coastal points keep the elevation-corrected series. The margins file still
    # carries the previous lapse-corrected method, so daily_closed margins are served null
    # (method_changed) until a refit on this method.
    "daily_closed": ("nkz@4d8bc4b: best_match /forecast temperature_2m_min of the raw model cell "
                     "(elevation=nan) over the local day, coastal points keep the corrected series"),
}
