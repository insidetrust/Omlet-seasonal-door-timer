"""Validate the offline NOAA calculator against api.sunrise-sunset.org for Swindon."""

import json
import subprocess
from datetime import date, datetime, timezone

from solar import civil_dawn, civil_dusk, sunrise, sunset

LAT, LON = 51.5558, -1.7797

SAMPLE_DATES = [
    date(2026, 1, 15),
    date(2026, 3, 29),   # BST starts
    date(2026, 6, 21),   # summer solstice
    date(2026, 8, 6),    # today
    date(2026, 10, 25),  # BST ends
    date(2026, 12, 21),  # winter solstice
]


def api(d: date) -> dict:
    url = (
        f"https://api.sunrise-sunset.org/json?lat={LAT}&lng={LON}"
        f"&date={d.isoformat()}&formatted=0"
    )
    out = subprocess.run(
        ["curl", "-s", "--max-time", "30", url], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)["results"]


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


print(f"{'date':<12} {'event':<12} {'calculated':<10} {'api':<10} {'delta'}")
print("-" * 56)
max_delta = 0.0
for d in SAMPLE_DATES:
    r = api(d)
    pairs = [
        ("sunrise", sunrise(d, LAT, LON), parse(r["sunrise"])),
        ("sunset", sunset(d, LAT, LON), parse(r["sunset"])),
        ("civil_dawn", civil_dawn(d, LAT, LON), parse(r["civil_twilight_begin"])),
        ("civil_dusk", civil_dusk(d, LAT, LON), parse(r["civil_twilight_end"])),
    ]
    for name, mine, theirs in pairs:
        delta = abs((mine - theirs).total_seconds())
        max_delta = max(max_delta, delta)
        print(
            f"{d.isoformat():<12} {name:<12} {mine.strftime('%H:%M:%S'):<10} "
            f"{theirs.strftime('%H:%M:%S'):<10} {delta:>5.0f}s"
        )

print("-" * 56)
print(f"max deviation: {max_delta:.0f} seconds")
