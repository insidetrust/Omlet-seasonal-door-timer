"""Tests for the scheduling engine."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from coopclock.schedule import Bounds, Offsets, compute, date_range

# Neutral example location (central London). Chosen so the fixture is
# obviously illustrative and not anyone's home.
EXAMPLE = {"latitude": 51.5074, "longitude": -0.1278, "timezone": "Europe/London"}


def sched(day: date, **kwargs):
    return compute(day=day, **EXAMPLE, **kwargs)


class TestKnownValues:
    """Pins the calculation against hand-checked reference values."""

    def test_summer_reference_day(self):
        result = sched(date(2026, 8, 6))
        assert (result.sunrise_local, result.sunset_local) == ("05:31", "20:41")
        assert result.open_time == "05:35"
        assert result.close_time == "21:25"
        assert result.tzname == "BST"

    def test_winter_solstice(self):
        result = sched(date(2026, 12, 21))
        assert (result.sunrise_local, result.sunset_local) == ("08:03", "15:53")
        assert result.open_time == "08:05"
        assert result.close_time == "16:40"
        assert result.tzname == "GMT"

    def test_no_clamp_on_reference_day(self):
        assert sched(date(2026, 8, 6)).clamped == []


class TestDst:
    """BST/GMT handling must come from zoneinfo, not manual arithmetic."""

    def test_bst_before_transition(self):
        assert sched(date(2026, 10, 24)).tzname == "BST"

    def test_gmt_after_transition(self):
        assert sched(date(2026, 10, 25)).tzname == "GMT"

    def test_close_steps_back_an_hour_across_autumn_transition(self):
        before = sched(date(2026, 10, 24))
        after = sched(date(2026, 10, 25))
        before_min = int(before.close_time[:2]) * 60 + int(before.close_time[3:])
        after_min = int(after.close_time[:2]) * 60 + int(after.close_time[3:])
        # ~60 min step from the clock change, plus a few minutes of real drift.
        assert 55 <= before_min - after_min <= 70

    def test_spring_transition(self):
        assert sched(date(2027, 3, 27)).tzname == "GMT"
        assert sched(date(2027, 3, 28)).tzname == "BST"


class TestRuleC:
    """Close must never precede civil dusk (PRD 6.1)."""

    @pytest.mark.parametrize("day_offset", range(0, 365, 1))
    def test_never_closes_before_civil_dusk(self, day_offset):
        result = sched(date(2026, 8, 6) + timedelta(days=day_offset))
        close = int(result.close_time[:2]) * 60 + int(result.close_time[3:])
        dusk = int(result.civil_dusk_local[:2]) * 60 + int(result.civil_dusk_local[3:])
        assert close >= dusk, (
            f"{result.day}: close {result.close_time} < dusk {result.civil_dusk_local}"
        )

    def test_midsummer_dusk_floor_engages(self):
        # Around the solstice a bare sunset+46 would fall before civil dusk;
        # rule C must lift it.
        result = sched(date(2027, 6, 15))
        close = int(result.close_time[:2]) * 60 + int(result.close_time[3:])
        dusk = int(result.civil_dusk_local[:2]) * 60 + int(result.civil_dusk_local[3:])
        assert close >= dusk

    def test_rule_c_matches_plain_offset_in_autumn(self):
        # In autumn the sunset offset already clears dusk, so rule C is inert.
        result = sched(date(2026, 9, 15))
        assert result.close_time == "20:05"


class TestOpenTime:
    def test_always_after_sunrise(self):
        for i in range(0, 365, 7):
            result = sched(date(2026, 8, 6) + timedelta(days=i))
            open_min = int(result.open_time[:2]) * 60 + int(result.open_time[3:])
            sunrise_min = int(result.sunrise_local[:2]) * 60 + int(result.sunrise_local[3:])
            # 5-minute rounding can pull it up to 2 min below sunrise+3.
            assert open_min >= sunrise_min - 2, result.day


class TestBounds:
    def test_latest_open_clamp_fires_and_is_reported(self):
        tight = Bounds(latest_open="06:00")
        result = sched(date(2026, 12, 21), bounds=tight)
        assert result.open_time == "06:00"
        assert result.is_clamped
        assert any("open lowered" in c for c in result.clamped)

    def test_earliest_close_clamp_fires(self):
        # Relax SAF-7 so this isolates the earliest_close bound; the two
        # interact in midwinter (see the precedence test below).
        tight = Bounds(earliest_close="16:50", max_minutes_after_sunset=180)
        result = sched(date(2026, 12, 21), bounds=tight)
        assert result.close_time == "16:50"
        assert result.is_clamped

    def test_saf7_wins_over_earliest_close_when_they_conflict(self):
        # earliest_close is a floor, max_minutes_after_sunset a safety ceiling.
        # On short midwinter days a high floor would push the door past the
        # fox-exposure cap - the cap must win. Locks in that precedence.
        conflicting = Bounds(earliest_close="18:00", max_minutes_after_sunset=60)
        result = sched(date(2026, 12, 21), bounds=conflicting)
        sunset_min = int(result.sunset_local[:2]) * 60 + int(result.sunset_local[3:])
        close_min = int(result.close_time[:2]) * 60 + int(result.close_time[3:])
        assert close_min - sunset_min <= 62  # 60 + rounding
        assert result.close_time < "18:00"
        assert any("sunset+60min" in c for c in result.clamped)

    def test_max_minutes_after_sunset_pulls_close_in(self):
        tight = Bounds(max_minutes_after_sunset=20)
        result = sched(date(2026, 9, 15), bounds=tight)
        sunset_min = int(result.sunset_local[:2]) * 60 + int(result.sunset_local[3:])
        close_min = int(result.close_time[:2]) * 60 + int(result.close_time[3:])
        assert close_min - sunset_min <= 22  # 20 + rounding
        assert result.is_clamped

    def test_default_bounds_never_fire_over_a_full_year(self):
        # PRD 5.2: defaults sit outside the observed envelope, so a clamp
        # firing is a genuine signal rather than routine noise.
        for i in range(365):
            result = sched(date(2026, 8, 6) + timedelta(days=i))
            assert result.clamped == [], f"{result.day}: {result.clamped}"


class TestRounding:
    def test_times_land_on_five_minute_boundaries(self):
        for i in range(0, 365, 11):
            result = sched(date(2026, 8, 6) + timedelta(days=i))
            assert int(result.open_time[3:]) % 5 == 0
            assert int(result.close_time[3:]) % 5 == 0

    def test_rounding_can_be_disabled(self):
        result = sched(date(2026, 8, 6), rounding_min=1)
        assert result.open_time == "05:34"


class TestOffsets:
    def test_custom_open_offset_shifts_result(self):
        base = sched(date(2026, 9, 15))
        later = sched(date(2026, 9, 15), offsets=Offsets(open_after_sunrise_min=33))
        assert later.open_time > base.open_time


class TestHelpers:
    def test_date_range_length_and_order(self):
        days = date_range(date(2026, 8, 6), 5)
        assert len(days) == 5
        assert days[0] == date(2026, 8, 6)
        assert days[-1] == date(2026, 8, 10)
