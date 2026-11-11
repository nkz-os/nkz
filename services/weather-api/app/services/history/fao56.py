"""FAO-56 helpers (stdlib floats). Units: degC, kPa, m/s, MJ m-2 day-1, mm day-1."""
import math
from typing import Optional

_WIND_10M_TO_2M = 4.87 / math.log(67.8 * 10.0 - 5.42)  # eq. 47 at z = 10 m


def es_kpa(t_c: float) -> float:
    """Saturation vapour pressure e0(T), eq. 11."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def vapour_pressure_kpa(td_c: float) -> float:
    """Actual vapour pressure from the dew point, eq. 14."""
    return es_kpa(td_c)


def rh_pct(t_c: float, td_c: float) -> float:
    return min(100.0, 100.0 * es_kpa(td_c) / es_kpa(t_c))


def wind_to_2m(u10: float) -> float:
    return u10 * _WIND_10M_TO_2M


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Ra, eq. 21-25 (MJ m-2 day-1)."""
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)
    dec = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    return (24.0 * 60.0 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.sin(ws)
    )


def et0_fao56_daily(tmax, tmin, ea, u2, rs_mj, lat_deg, elev_m, doy) -> Optional[float]:
    """FAO-56 Penman-Monteith daily ET0 (mm/day), G = 0, Rs/Rso clipped to [0.3, 1.0].

    Returns None when Rso <= 0 (no clear-sky radiation, e.g. polar night): there is no
    defensible cloudiness ratio. A negative result is clamped to 0, as FAO-56 practice
    (ET0 cannot be negative).
    """
    tmean = (tmax + tmin) / 2.0
    p = 101.3 * ((293.0 - 0.0065 * elev_m) / 293.0) ** 5.26
    gamma = 0.665e-3 * p
    delta = 4098.0 * es_kpa(tmean) / (tmean + 237.3) ** 2
    es = (es_kpa(tmax) + es_kpa(tmin)) / 2.0
    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = (0.75 + 2e-5 * elev_m) * ra
    if rso <= 0:
        return None
    ratio = max(0.3, min(1.0, rs_mj / rso))
    rns = 0.77 * rs_mj
    sigma = 4.903e-9
    rnl = (sigma * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2.0
           * (0.34 - 0.14 * math.sqrt(max(ea, 0.0))) * (1.35 * ratio - 0.35))
    rn = rns - rnl
    num = 0.408 * delta * rn + gamma * 900.0 / (tmean + 273.0) * u2 * (es - ea)
    return max(0.0, num / (delta + gamma * (1.0 + 0.34 * u2)))
