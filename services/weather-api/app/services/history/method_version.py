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
    # /daily stored closed-day tmin_c: best_match forecast past_days with the worker lapse
    "daily_closed": "nkz@8c7050a: best_match /forecast past_days with the worker lapse (stored closed days)",
}
