"""Local command line interface: run, dry-run and almanac."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .config import load, load_api_key
from .runner import build_schedule, run, to_dict
from .schedule import date_range, local_today

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "swindon.yaml"


def _parse_day(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Returns:
        0 on success, 1 if the run raised alerts.
    """
    parser = argparse.ArgumentParser(prog="coopclock")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="compute and push today's schedule")
    p_run.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--no-verify", action="store_true")

    p_show = sub.add_parser("show", help="compute without touching the API")
    p_show.add_argument("--date", help="YYYY-MM-DD (default: today)")

    p_alm = sub.add_parser("almanac", help="generate a CSV almanac")
    p_alm.add_argument("--days", type=int, default=365)
    p_alm.add_argument("--start", help="YYYY-MM-DD (default: today)")
    p_alm.add_argument("--out", default="almanac.csv")

    sub.add_parser("state", help="read and print live device state")

    p_cal = sub.add_parser(
        "calibrate",
        help="derive offsets from your door's existing manual open/close times",
    )
    p_cal.add_argument("--open", required=True, metavar="HH:MM", help="current open time")
    p_cal.add_argument("--close", required=True, metavar="HH:MM", help="current close time")
    p_cal.add_argument("--lat", type=float, required=True)
    p_cal.add_argument("--lon", type=float, required=True, help="negative for west")
    p_cal.add_argument("--tz", default="Europe/London", help="IANA timezone")
    p_cal.add_argument("--name", default="My coop")
    p_cal.add_argument("--date", help="date those times were right for (default: today)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # calibrate needs no config file - it is what you run *before* you have one.
    if args.command == "calibrate":
        from datetime import date as _date

        from .calibrate import derive, to_yaml

        cal = derive(
            open_time=args.open,
            close_time=args.close,
            day=_parse_day(args.date) or _date.today(),
            latitude=args.lat,
            longitude=args.lon,
            timezone=args.tz,
        )
        print(f"\nOn {cal.day} at {args.lat}, {args.lon} ({cal.tzname}):\n")
        print(f"  civil dawn   {cal.civil_dawn_local}")
        print(f"  sunrise      {cal.sunrise_local}")
        print(f"  your open    {cal.open_time}   = sunrise {cal.open_after_sunrise_min:+d} min"
              f"   (civil dawn {cal.open_after_civil_dawn_min:+d} min)")
        print(f"  sunset       {cal.sunset_local}")
        print(f"  civil dusk   {cal.civil_dusk_local}")
        print(f"  your close   {cal.close_time}   = sunset {cal.close_after_sunset_min:+d} min"
              f"   (civil dusk {cal.close_after_civil_dusk_min:+d} min)")
        if cal.warnings:
            print("\nWarnings:")
            for warning in cal.warnings:
                print(f"  ! {warning}")
        print("\nConfig fragment - paste into your config YAML:\n")
        print(to_yaml(cal, args.lat, args.lon, args.tz, args.name))
        return 0

    config = load(args.config)

    if args.command == "show":
        schedule = build_schedule(config, _parse_day(args.date))
        print(json.dumps(schedule.__dict__, indent=2, default=str))
        return 0

    if args.command == "almanac":
        start = _parse_day(args.start) or local_today(config.location.timezone)
        rows = []
        for day in date_range(start, args.days):
            s = build_schedule(config, day)
            rows.append(
                {
                    "date": s.day.isoformat(),
                    "tz": s.tzname,
                    "sunrise": s.sunrise_local,
                    "sunset": s.sunset_local,
                    "civil_dusk": s.civil_dusk_local,
                    "door_open": s.open_time,
                    "door_close": s.close_time,
                    "clamped": ";".join(s.clamped),
                }
            )
        with open(args.out, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.out}")
        return 0

    api_key = load_api_key()

    if args.command == "state":
        from .omlet import OmletClient

        state = OmletClient(api_key, base_url=config.api.base_url).read_state(config.device_id)
        data = state.__dict__.copy()
        data.pop("raw_door_config", None)
        print(json.dumps(data, indent=2))
        return 0

    result = run(
        config=config,
        api_key=api_key,
        day=_parse_day(args.date),
        dry_run=args.dry_run,
        verify=not args.no_verify,
    )
    print(json.dumps(to_dict(result), indent=2))
    for alert in result.alerts:
        print(f"ALERT: {alert}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
