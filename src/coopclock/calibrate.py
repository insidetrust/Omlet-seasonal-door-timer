"""Derive offsets from a door's existing manual open/close times.

This is how you move from "my door is set to 05:40 and 21:35 and I am happy
with that" to a year-round schedule: measure what those times mean relative to
the sun *on the day they were set*, then hold that relationship constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from .solar import civil_dawn, civil_dusk, sunrise, sunset


@dataclass
class Calibration:
    """Offsets derived from an existing pair of manual times."""

    day: date
    tzname: str
    sunrise_local: str
    sunset_local: str
    civil_dawn_local: str
    civil_dusk_local: str
    open_time: str
    close_time: str
    open_after_sunrise_min: int
    close_after_sunset_min: int
    open_after_civil_dawn_min: int
    close_after_civil_dusk_min: int
    warnings: list[str]


def _minutes(value: str) -> int:
    hours, mins = value.split(":")
    return int(hours) * 60 + int(mins)


def derive(
    open_time: str,
    close_time: str,
    day: date,
    latitude: float,
    longitude: float,
    timezone: str,
) -> Calibration:
    """Work out what a pair of manual times means relative to the sun.

    Args:
        open_time: Existing open time, local "HH:MM".
        close_time: Existing close time, local "HH:MM".
        day: The date those times were correct for.
        latitude: Degrees north, positive.
        longitude: Degrees east, negative for west.
        timezone: IANA timezone name, e.g. "Europe/London".

    Returns:
        A `Calibration` with derived offsets and any safety warnings.

    Raises:
        ValueError: If the sun does not rise or set at this location on `day`.
    """
    tz = ZoneInfo(timezone)
    events = {
        "sunrise": sunrise(day, latitude, longitude),
        "sunset": sunset(day, latitude, longitude),
        "civil_dawn": civil_dawn(day, latitude, longitude),
        "civil_dusk": civil_dusk(day, latitude, longitude),
    }
    if any(v is None for v in events.values()):
        raise ValueError(f"No sunrise/sunset at {latitude},{longitude} on {day}")

    local = {k: v.astimezone(tz) for k, v in events.items()}  # type: ignore[union-attr]
    mins = {k: v.hour * 60 + v.minute for k, v in local.items()}

    open_min, close_min = _minutes(open_time), _minutes(close_time)
    open_off = open_min - mins["sunrise"]
    close_off = close_min - mins["sunset"]
    open_dawn = open_min - mins["civil_dawn"]
    close_dusk = close_min - mins["civil_dusk"]

    warnings: list[str] = []
    if open_dawn < 0:
        warnings.append(
            f"Door opens {abs(open_dawn)} min BEFORE civil dawn - it would open in darkness."
        )
    if close_dusk < -10:
        warnings.append(
            f"Door closes {abs(close_dusk)} min BEFORE civil dusk - birds may still be out. "
            "Consider a later close offset."
        )
    if close_off > 90:
        warnings.append(
            f"Door closes {close_off} min after sunset - a long window of darkness with the "
            "door open. Consider an earlier close offset."
        )
    if close_min <= open_min:
        warnings.append("Close time is not after open time - check your inputs.")

    return Calibration(
        day=day,
        tzname=local["sunrise"].tzname() or "",
        sunrise_local=local["sunrise"].strftime("%H:%M"),
        sunset_local=local["sunset"].strftime("%H:%M"),
        civil_dawn_local=local["civil_dawn"].strftime("%H:%M"),
        civil_dusk_local=local["civil_dusk"].strftime("%H:%M"),
        open_time=open_time,
        close_time=close_time,
        open_after_sunrise_min=open_off,
        close_after_sunset_min=close_off,
        open_after_civil_dawn_min=open_dawn,
        close_after_civil_dusk_min=close_dusk,
        warnings=warnings,
    )


def to_yaml(cal: Calibration, latitude: float, longitude: float, timezone: str, name: str) -> str:
    """Render a calibration as a ready-to-paste config fragment."""
    dusk_floor = max(0, min(cal.close_after_civil_dusk_min, 15))
    return f"""location:
  name: {name}
  latitude: {latitude}
  longitude: {longitude}
  timezone: {timezone}

offsets:
  open_after_sunrise_min: {cal.open_after_sunrise_min}
  close_after_sunset_min: {cal.close_after_sunset_min}
  # Never close before civil dusk, however the sunset offset lands.
  close_min_after_civil_dusk_min: {dusk_floor}
"""
