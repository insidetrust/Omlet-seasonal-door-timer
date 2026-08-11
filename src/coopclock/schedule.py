"""Compute the door's open and close times for a given date.

Implements PRD sections 5.1 (scheduling engine), 5.2 (safety bounds) and 6.1
(close-time anchor, rule C).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .solar import civil_dusk, sunrise, sunset


@dataclass(frozen=True)
class Bounds:
    """Hard clamps applied after the astronomical calculation (PRD 5.2)."""

    earliest_open: str = "04:30"
    latest_open: str = "08:30"
    earliest_close: str = "16:00"
    latest_close: str = "22:30"
    min_open_hours: float = 6.0
    max_minutes_after_sunset: int = 60


@dataclass(frozen=True)
class Offsets:
    """Offsets from the solar events (PRD 3.2, 6.1)."""

    open_after_sunrise_min: int = 3
    close_after_sunset_min: int = 46
    close_min_after_civil_dusk_min: int = 5


@dataclass
class Schedule:
    """A computed schedule for one date."""

    day: date
    open_time: str
    close_time: str
    sunrise_local: str
    sunset_local: str
    civil_dusk_local: str
    tzname: str
    clamped: list[str] = field(default_factory=list)

    @property
    def is_clamped(self) -> bool:
        """True if any safety bound altered the astronomical result."""
        return bool(self.clamped)


def _hhmm_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _minutes_to_hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _round_to(minutes: int, step: int) -> int:
    """Round to the nearest `step` minutes."""
    return (minutes + step // 2) // step * step


def compute(
    day: date,
    latitude: float,
    longitude: float,
    timezone: str = "Europe/London",
    offsets: Offsets | None = None,
    bounds: Bounds | None = None,
    rounding_min: int = 5,
) -> Schedule:
    """Compute open and close times for `day` at the given location.

    All solar events are computed in UTC then converted to the local zone, so
    BST/GMT transitions are handled by `zoneinfo` rather than manual arithmetic
    (PRD SCH-4).

    Args:
        day: The date to compute for.
        latitude: Degrees north, positive.
        longitude: Degrees east, negative for west.
        timezone: IANA timezone name for the output wall-clock times.
        offsets: Solar offsets; defaults to the values derived in PRD 3.2.
        bounds: Safety clamps; defaults to PRD 5.2.
        rounding_min: Round results to this many minutes.

    Returns:
        A `Schedule` with local "HH:MM" strings and a list of any clamps fired.

    Raises:
        ValueError: If the sun does not rise or set on `day` at this latitude.
    """
    offsets = offsets or Offsets()
    bounds = bounds or Bounds()
    tz = ZoneInfo(timezone)

    sr = sunrise(day, latitude, longitude)
    ss = sunset(day, latitude, longitude)
    cd = civil_dusk(day, latitude, longitude)
    if sr is None or ss is None or cd is None:
        raise ValueError(f"No sunrise/sunset/civil dusk at {latitude},{longitude} on {day}")

    sr_l, ss_l, cd_l = sr.astimezone(tz), ss.astimezone(tz), cd.astimezone(tz)

    def as_minutes(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute + round(moment.second / 60)

    open_min = as_minutes(sr_l) + offsets.open_after_sunrise_min

    # Rule C (PRD 6.1): never close before civil dusk. A fixed sunset offset
    # cannot track dusk, whose gap from sunset varies 33-48 min seasonally.
    close_by_sunset = as_minutes(ss_l) + offsets.close_after_sunset_min
    close_by_dusk = as_minutes(cd_l) + offsets.close_min_after_civil_dusk_min
    close_min = max(close_by_sunset, close_by_dusk)

    open_min = _round_to(open_min, rounding_min)
    close_min = _round_to(close_min, rounding_min)

    clamped: list[str] = []

    def clamp(value: int, low: str | None, high: str | None, label: str) -> int:
        if low is not None and value < _hhmm_to_minutes(low):
            clamped.append(f"{label} raised to {low} (was {_minutes_to_hhmm(value)})")
            return _hhmm_to_minutes(low)
        if high is not None and value > _hhmm_to_minutes(high):
            clamped.append(f"{label} lowered to {high} (was {_minutes_to_hhmm(value)})")
            return _hhmm_to_minutes(high)
        return value

    open_min = clamp(open_min, bounds.earliest_open, bounds.latest_open, "open")
    close_min = clamp(close_min, bounds.earliest_close, bounds.latest_close, "close")

    # SAF-7: never leave the door open more than N minutes after sunset.
    latest_by_sunset = as_minutes(ss_l) + bounds.max_minutes_after_sunset
    if close_min > latest_by_sunset:
        clamped.append(
            f"close pulled to sunset+{bounds.max_minutes_after_sunset}min "
            f"({_minutes_to_hhmm(_round_to(latest_by_sunset, rounding_min))})"
        )
        close_min = _round_to(latest_by_sunset, rounding_min)

    # SAF-5: minimum open window.
    if close_min - open_min < bounds.min_open_hours * 60:
        clamped.append(
            f"open window {(close_min - open_min) / 60:.1f}h below minimum "
            f"{bounds.min_open_hours}h"
        )

    return Schedule(
        day=day,
        open_time=_minutes_to_hhmm(open_min),
        close_time=_minutes_to_hhmm(close_min),
        sunrise_local=sr_l.strftime("%H:%M"),
        sunset_local=ss_l.strftime("%H:%M"),
        civil_dusk_local=cd_l.strftime("%H:%M"),
        tzname=sr_l.tzname() or "",
        clamped=clamped,
    )


def local_today(timezone: str = "Europe/London") -> date:
    """Return today's date in the given timezone, not the host's."""
    return datetime.now(ZoneInfo(timezone)).date()


def date_range(start: date, days: int) -> list[date]:
    """Return `days` consecutive dates beginning at `start`."""
    return [start + timedelta(days=i) for i in range(days)]
