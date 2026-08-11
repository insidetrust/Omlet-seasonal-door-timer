"""Self-contained NOAA sunrise/sunset calculator (no third-party deps).

Used to validate that an offline solar calculation matches the sunrise-sunset.org
API closely enough for the chicken-door scheduler.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

# Solar altitude at the moment of sunrise/sunset (accounts for refraction + solar disc)
ZENITH_OFFICIAL = 90.833
ZENITH_CIVIL = 96.0


def _julian_day(d: date) -> float:
    y, m, day = d.year, d.month, d.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + day + b - 1524.5


def _solar_event(d: date, lat: float, lon: float, zenith: float, rising: bool) -> datetime | None:
    """Return the UTC datetime of a solar event, or None if it never occurs that day."""
    jc = (_julian_day(d) - 2451545.0) / 36525.0

    # Geometric mean longitude and anomaly of the sun (degrees)
    gml = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    gma = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)

    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    # Equation of centre -> true longitude -> apparent longitude
    centre = (
        math.sin(math.radians(gma)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(math.radians(2 * gma)) * (0.019993 - 0.000101 * jc)
        + math.sin(math.radians(3 * gma)) * 0.000289
    )
    true_long = gml + centre
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    # Obliquity of the ecliptic, corrected
    seconds = 21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))
    obliq = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliq_corr = obliq + 0.00256 * math.cos(math.radians(omega))

    declination = math.degrees(
        math.asin(math.sin(math.radians(obliq_corr)) * math.sin(math.radians(app_long)))
    )

    # Equation of time (minutes)
    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2
    eot = 4 * math.degrees(
        var_y * math.sin(2 * math.radians(gml))
        - 2 * ecc * math.sin(math.radians(gma))
        + 4 * ecc * var_y * math.sin(math.radians(gma)) * math.cos(2 * math.radians(gml))
        - 0.5 * var_y * var_y * math.sin(4 * math.radians(gml))
        - 1.25 * ecc * ecc * math.sin(2 * math.radians(gma))
    )

    # Hour angle at the target zenith
    cos_ha = math.cos(math.radians(zenith)) / (
        math.cos(math.radians(lat)) * math.cos(math.radians(declination))
    ) - math.tan(math.radians(lat)) * math.tan(math.radians(declination))
    if not -1.0 <= cos_ha <= 1.0:
        return None  # sun never reaches this altitude (polar day/night)
    ha = math.degrees(math.acos(cos_ha))
    if not rising:
        ha = -ha

    minutes_utc = 720 - 4 * (lon + ha) - eot
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
        minutes=minutes_utc
    )


def sunrise(d: date, lat: float, lon: float) -> datetime | None:
    return _solar_event(d, lat, lon, ZENITH_OFFICIAL, rising=True)


def sunset(d: date, lat: float, lon: float) -> datetime | None:
    return _solar_event(d, lat, lon, ZENITH_OFFICIAL, rising=False)


def civil_dawn(d: date, lat: float, lon: float) -> datetime | None:
    return _solar_event(d, lat, lon, ZENITH_CIVIL, rising=True)


def civil_dusk(d: date, lat: float, lon: float) -> datetime | None:
    return _solar_event(d, lat, lon, ZENITH_CIVIL, rising=False)
