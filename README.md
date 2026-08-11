# coopclock

Keeps an **Omlet Smart Autodoor** aligned to sunrise and sunset all year round.

The Omlet app can open and close the door on a fixed timer, or on a light sensor.
Neither tracks the seasons. Set a timer in August and by December it is badly wrong:
at 51.5°N, sunset moves nearly five hours between the two. A door still open hours
after dark is a fox problem, not a comfort problem.

`coopclock` computes the correct times each day from the position of the sun at your
location and writes them to the door through Omlet's official API.

```
      Aug 06          fixed timer          coopclock
      sunset 20:41    close 21:25 ✓        close 21:25 ✓
      Dec 15
      sunset 15:51    close 21:25 ✗        close 16:35 ✓
                      5h34m after sunset   4 min after civil dusk
```

---

## How it works

- Sunrise, sunset and civil twilight are computed offline with a NOAA solar-position
  algorithm. No third-party weather or sunrise API at runtime.
- Your offsets are held constant. The clock times move; the relationship to the sun
  does not.
- Times are pushed to the door daily, but **only written when they change** — about a
  third of days, since 5-minute rounding absorbs the rest.
- The door stores local wall-clock time and handles DST itself, so BST/GMT needs no
  special handling beyond computing in the right timezone.
- Once written, **the door runs the schedule from its own clock.** If your internet,
  Omlet's servers, or the scheduler go down, the door still opens and closes. Only
  updates stop, and the schedule then drifts at roughly 2 minutes a day.

---

## Working out your schedule

**Do not guess the offsets.** Derive them from the times your door already uses and
that you are happy with. That way you keep the behaviour you have, and only add
seasonal tracking.

You need your latitude and longitude. Right-click your coop in Google Maps, or use
[openstreetmap.org](https://www.openstreetmap.org) — four decimal places is far more
than enough. **Longitude is negative west of Greenwich.**

```bash
coopclock calibrate --open 05:40 --close 21:35 \
    --lat 51.5074 --lon -0.1278 --tz Europe/London --date 2026-08-06
```

`--date` is the date those times were right for, because the answer depends on where
the sun was that day.

```
On 2026-08-06 at 51.5074, -0.1278 (BST):

  civil dawn   04:51
  sunrise      05:31
  your open    05:40   = sunrise +9 min   (civil dawn +49 min)
  sunset       20:41
  civil dusk   21:20
  your close   21:35   = sunset +54 min   (civil dusk +15 min)
```

It prints a config block ready to paste, and warns you if the times you gave it are
unsafe — opening before civil dawn, closing before civil dusk, or leaving the door
open a long time after sunset.

Then check a whole year before trusting it:

```bash
coopclock almanac --days 365 --out almanac.csv
```

That gives you the annual envelope: earliest and latest open and close. Set your
`bounds` just outside it, so a clamp firing is a real signal rather than routine noise.

### Choosing offsets from scratch

If you have no existing times to calibrate from, the short version of what the
research supports:

| | Guidance |
|---|---|
| **Open** | At or shortly after sunrise. Not safety-critical — it is fully light by then, and predator activity has tailed off. Anywhere from sunrise+0 to sunrise+30 is unremarkable. |
| **Close** | 30–45 minutes after sunset is the keeper consensus; commercial defaults are nearer 20. The binding constraint is that **it must not precede the last bird going in**, which is close to civil dusk. |

`close_min_after_civil_dusk_min` exists because a fixed sunset offset cannot track
dusk: the sunset-to-civil-dusk gap varies with the season (33–48 min at 51.5°N). Without
that floor, a summer close can land *before* dusk and shut out late roosters.

Be aware there is an irreducible overlap here. Fox activity begins around 30 minutes
after sunset; roosting finishes around civil dusk, which is 33–48 minutes after sunset.
No close time is both after the last hen and before the first fox. **Door timing is a
secondary control — physical run security is the primary one.**

---

## Setup

```bash
git clone https://github.com/insidetrust/coopclock.git
cd coopclock
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e .

cp config/example.yaml config/coop.yaml   # gitignored; edit with your details
```

### Get an API key

1. Log in at [smart.omlet.com/developers/login](https://smart.omlet.com/developers/login)
   with your normal Omlet app credentials.
2. Go to **API Keys** → **Generate Key**.
3. Your **Device ID** is in the Omlet app, on your door's settings screen.

```bash
export OMLET_API_KEY='your-key'
coopclock state          # confirm it can see your door
coopclock show           # today's computed times, no API calls
coopclock run --dry-run  # full run, writes nothing
coopclock run            # for real
```

Treat the key as equivalent to your Omlet password — it can open and close your door.

---

## Running it daily

### AWS Lambda (recommended)

Runs whether or not your computer is on, which matters when the failure mode is a
door left open after dark.

```bash
CONFIG_FILE=config/coop.yaml TIMEZONE=Europe/London AWS_REGION=eu-west-1 \
  ./deploy/deploy.sh

aws secretsmanager put-secret-value --secret-id coopclock/omlet-api-key \
  --region eu-west-1 --secret-string 'your-key'
```

Creates the Lambda, an EventBridge schedule at 02:00 local (DST-aware), a Secrets
Manager entry, and least-privilege IAM roles. Re-run it to deploy changes. Override
`RUN_AT` to move the hour.

Battery level and run health are published as CloudWatch metrics in namespace
`coopclock`, because **the Omlet API exposes no battery history** — only a
point-in-time reading.

### cron

Simpler, but it silently stops whenever the machine is off.

```cron
0 2 * * * cd /path/to/coopclock && OMLET_API_KEY=... ./venv/bin/coopclock run
```

---

## Commands

| Command | Purpose |
|---|---|
| `coopclock calibrate` | Derive offsets from your existing times. Start here. |
| `coopclock show` | Today's computed times. No network. |
| `coopclock almanac` | Year-ahead CSV of sun times and door times. |
| `coopclock state` | Live device state: times, battery, signal, faults. |
| `coopclock run` | Compute and push. `--dry-run` to rehearse. |

---

## Safety bounds

Clamps applied after the astronomical calculation, so a bad config or a bad clock
cannot produce a dangerous schedule:

- `earliest_open` / `latest_open`
- `earliest_close` / `latest_close`
- `min_open_hours` — minimum daylight access
- `max_minutes_after_sunset` — hard ceiling on post-sunset exposure

Where two bounds conflict, **`max_minutes_after_sunset` wins**: a floor on the close
time must never push the door past the exposure ceiling on short winter days.

Any clamp that fires is reported as an alert, not applied silently.

---

## A note on batteries

If you run the door on batteries and use WiFi, expect trouble with rechargeables.
Omlet's own guidance is to avoid them, and the reason is voltage, not capacity: NiMH
cells sit at 1.2 V against alkaline's 1.5 V, so a **fully charged** NiMH pack reads
well under 100% on a gauge calibrated for alkaline, and reaches the low-voltage
cutoff far sooner.

More seriously: if the battery dies while the door is open, **it stays open**. No
scheduler prevents that. Mains or solar power is the real fix.

---

## Limitations

- Requires internet and Omlet's cloud to *update*. Not to operate.
- No local/LAN control exists — Omlet's API is cloud-only.
- Sunrise/sunset agree with reference sources to within ~3 minutes; civil twilight to
  within ~1 minute. Immaterial against offsets of tens of minutes.
- Single door. No support for the coop light, fan, or feeder.
- Untested above the Arctic/Antarctic circles, where the sun may not rise or set at
  all. The calculation raises rather than guessing.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest          # ~400 tests
ruff check src tests
```

The suite includes year-long sweeps asserting the door never closes before civil dusk
and that default bounds never fire — so that a clamp in production is meaningful.

Design reasoning and API findings: [docs/DESIGN.md](docs/DESIGN.md).

Not affiliated with or endorsed by Omlet Ltd.

## Licence

MIT — see [LICENSE](LICENSE).
