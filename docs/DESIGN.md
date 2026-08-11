# Design notes — coopclock

Why the system is built the way it is. The [README](../README.md) covers usage; this
covers reasoning, and the constraints that are not obvious from the code.

---

## 1. The problem

The Omlet Smart Autodoor offers two scheduling modes, neither of which tracks the
seasons:

| Mode | Behaviour | Why it is insufficient |
| --- | --- | --- |
| **Timer** | Fixed wall-clock open/close | Correct on the day it is set, wrong within weeks |
| **Light sensor** | Triggers at a set lux level, 15 min debounce | Omlet themselves advise switching to timer in darker months; shade and overcast make trigger time erratic, and the failure direction is *late closing* |

At 51.5°N, between early August and midwinter sunrise slides ~2.5 hours later and
sunset ~4.9 hours earlier. A timer set in August leaves the door open **over four hours
after dark** by late October — a predation risk, not a comfort issue.

`coopclock` drives the door's timer from an astronomical calculation, updated daily.
The light sensor is deliberately unused: its failure mode is closing late, which is
precisely the risk being engineered out, and a computed timer is deterministic and
verifiable.

---

## 2. Deriving the schedule

### 2.1 Offsets come from the door's existing settings

The design principle is that a keeper already has times they trust. The job is to
preserve that relationship to the sun, not to impose new times. `coopclock calibrate`
measures what the existing times mean on the day they were set:

```
OPEN  = sunrise + N min
CLOSE = sunset  + M min
```

Worked example (51.5074, −0.1278, 06/08/2026): an existing 05:40/21:35 resolves to
sunrise +9 and sunset +54, which is civil dawn +49 and civil dusk +15.

### 2.2 The close-time anchor — rule C

A fixed sunset offset cannot track civil dusk, because the sunset-to-dusk gap varies
seasonally: **33–48 minutes at 51.5°N**, longest at midsummer. With a bare sunset
offset, the close lands between 5 minutes *before* and 15 minutes *after* civil dusk
across a year — and the negative case falls in midsummer, exactly when the behavioural
evidence reports the latest stragglers.

The implemented rule:

```python
close = max(sunset + close_offset, civil_dusk + dusk_floor)
```

Measured over a full year with a sunset+46 baseline:

| Rule | vs civil dusk | Nights closing before dusk |
| --- | --- | --- |
| `sunset + 46` | −2 to +13 min | **28 / 365** |
| `civil dusk + 8` | +8 constant | 0 / 365 |
| **`max(both)`** | **+5 to +13 min** | **0 / 365** |

This preserves the calibrated offset on ~92% of nights and intervenes only where the
fixed offset falls short. Cost: at most 7 minutes later than the bare offset.

### 2.3 Why the open time needs no equivalent floor

Open lands 34–53 minutes after civil dawn year-round — always in full light, never
before dawn. Morning timing is not safety-critical: it is light by sunrise, and
predator activity has tailed off. No source consulted treats it as a risk variable.

---

## 3. Behavioural constraints

Three independent research passes (chicken roosting, UK fox ecology, keeper practice)
informed the offsets. They **disagreed**, and the disagreement is the useful part.

| Lens | Recommendation | Basis |
| --- | --- | --- |
| Fox ecology | Close **earlier**, sunset +15 | Doncaster & Macdonald (1997): fox activity begins ~sunset +30 |
| Chicken behaviour | Close **later**, sunset +50–55 | Birds settle up to civil dusk |
| Keeper practice | Keep ~+46 | Consensus 30–45 min; commercial defaults ~20 |

The fox recommendation was tested and rejected: `sunset + 15` closes before civil dusk
on **365/365 nights**, by 18–33 minutes. It would lock birds out every night of the
year. It optimises one variable while ignoring the binding constraint.

**The binding constraint is that the close cannot precede the last bird roosting.** Fox
ecology can only minimise the tail above that floor; it cannot push below it.

### The irreducible overlap

Combining the two evidence bases:

- Fox activity onset: **sunset + 30**
- Roosting completion: **≈ civil dusk = sunset + 33 to +48**

These overlap by **3–18 minutes in every season**. No close time is both after the last
hen and before the first fox.

> **The dusk exposure window cannot be closed by scheduling.** Physical run security —
> welded mesh rather than chicken wire, a buried or outward-facing dig-proof apron,
> solid roofing, two-action latches — is the primary control. Door timing is a
> secondary layer. Any keeper relying on this software as their main fox defence has
> misunderstood the problem.

Equally, no offset helps a bird that will not roost — broody, bullied, injured, or
newly introduced. That needs a daily headcount, not a better calculation.

**Evidence caveat:** Doncaster & Macdonald is the load-bearing citation for both
twilight margins. It is the best UK data available, but it is Oxford 1980–83 and
pre-dates much of the urban food-subsidy effect on fox behaviour. Treat sunset+30 as
indicative, not exact.

**Welfare:** RSPCA's laying-hen standard is 8 h continuous daylight. A midwinter
schedule at 51.5°N gives ~8.5 h access against ~7.8 h of natural daylight, clearing the
published benchmark. The confinement hours outside that window are almost entirely
coextensive with darkness, during which hens are inactive regardless of door state.

---

## 4. Safety bounds

Clamps applied after the astronomical calculation, so a bad config, a bad calculation
or a bad clock cannot produce a dangerous schedule.

| Bound | Default | Purpose |
| --- | --- | --- |
| `earliest_open` / `latest_open` | 04:30 / 08:30 | Absolute open window |
| `earliest_close` / `latest_close` | 16:00 / 22:30 | Absolute close window |
| `min_open_hours` | 6 | Minimum daylight access |
| `max_minutes_after_sunset` | 60 | Hard ceiling on post-sunset exposure |

Two design points:

**Defaults sit just outside the real annual envelope**, so in normal operation no clamp
ever fires. A clamp firing is therefore a genuine signal, not routine noise — and a
test asserts exactly this across a full year.

**Precedence is defined where bounds conflict.** On short midwinter days a high
`earliest_close` floor could push the door past the exposure ceiling.
`max_minutes_after_sunset` wins, because a comfort floor must never override a safety
cap. A test pins this.

Clamps are reported as alerts, never applied silently.

---

## 5. Astronomical calculation

A self-contained NOAA solar-position implementation, no third-party dependency,
validated against `api.sunrise-sunset.org` across six dates including both solstices
and both DST transitions:

- Civil dawn/dusk: **within 71 seconds**
- Sunrise/sunset: **within 174 seconds** (~3 min), systematically biased

The residual is most likely a refraction-constant difference in the reference engine.
The standard `90.833°` zenith was **kept rather than tuned to match**, since the
reference could not be independently corroborated. A ±3 minute tolerance is immaterial
against offsets of tens of minutes, but it is a real limit.

Computing offline means no dependency on a sunrise API at runtime — one less thing to
fail on a winter night. Harness in [`reference/`](../reference).

---

## 6. Architecture

**There is no local/LAN API.** The door operates standalone from its control panel
without WiFi, but exposes no local control endpoint — Omlet's webhook configuration
rejects `localhost`, `.local` and private IPs, which only makes sense for a cloud
service reaching in. All programmatic control is cloud-mediated.

```
┌──────────────────────────────────────┐
│  Scheduler (AWS Lambda / cron)       │  runs anywhere with outbound HTTPS;
│  daily, 02:00 local                  │  NOT required to be on the home LAN
└──────────────┬───────────────────────┘
               │  HTTPS + API key
               ▼
┌──────────────────────────────────────┐
│  Omlet cloud                         │
└──────────────┬───────────────────────┘
               │  device polls every 600 s
               ▼
┌──────────────────────────────────────┐
│  Home WiFi ──► Autodoor control panel│
│  stores openTime / closeTime         │
└──────────────┬───────────────────────┘
               ▼
     Door opens and closes AUTONOMOUSLY
     from its own clock, with no network
```

**The door is autonomous — this is the core safety property.** A failure of home
internet, WiFi, the Omlet backend or the scheduler does not stop the door operating. It
only stops the schedule being *updated*, after which it degrades at ~2 min/day. That is
why staleness is measured in days rather than treating one failed run as an incident.

**No webhooks required.** Those exist to receive events and would demand a publicly
reachable URL. This project only pushes, and polls `GET /device` for state on the same
run.

### Run cadence

Compute daily, write only on change. With 5-minute rounding the target moves on about a
third of days:

| | Open | Close |
| --- | --- | --- |
| Days needing a write | ~29% | ~31% |
| Longest static run | 21 days | 25 days |
| Step when it moves | 5 min (60 at DST) | 5 min (60 at DST) |

A **weekly** cadence was rejected: it accumulates 16–17 min of error at the equinoxes
and **up to 74 min across a DST transition** — an hour wrong for up to a week, in late
October, which is the exact failure this project exists to prevent.

Because the door legitimately goes 3–4 weeks without a write in midwinter, staleness
alerting tracks **runs, not writes**. Otherwise a quiet December is indistinguishable
from a dead updater.

**02:00 local** is chosen deliberately: after midnight so the date is correct, outside
the door's operating window, and it occurs exactly once on both DST transition nights.
A 01:30 schedule would be skipped in spring and repeated in autumn.

---

## 7. Omlet API notes

Base URL `https://x107.omlet.co.uk/api/v1`, auth `Authorization: Bearer <key>`.
Endpoints: `/device`, `/device/{id}/configuration`, `/device/{id}/action/{action}`,
`/whoami`, `/group`, and an undocumented `/events`.

Confirmed against a live device:

- **Time format** is `"HH:MM"`, 24-hour, **local wall-clock**.
- **`openMode`/`closeMode`** take `"time"`. The client asserts this, since writes would
  otherwise be ignored in light-sensor mode.
- **The device holds local time and tracks DST itself** (`"timezone": "Europe/London"`
  with a live UTC offset). Write plain local time; no conversion. This was the
  highest-risk unknown — getting it wrong means an hour of error for half the year.
- **`pollFreq` is 600 s.** The device reports `"connected": false` between check-ins;
  that is normal deep sleep, not a fault.

### Two traps worth documenting

**The official SDK drops fields.** `ConfigurationDoor.to_json()` emits a fixed 10
fields, but real devices also carry `lightOffset` and `temperatureUnit`. A round-trip
through the SDK silently wipes them. This project therefore uses raw JSON for writes,
mutating only `openTime`/`closeTime`, and PATCHes the `door` subtree alone so
`general.datetime` is never echoed back — that field could reset the device clock.

**The config endpoint returns desired state, not device state.** It echoes a write back
instantly, so a read-back match proves only that the cloud accepted it. Verification
additionally waits for `lastConnected` to advance past the write, which is what proves
the *device* collected the new times. Without that, a run reports success in seconds
while the door still runs yesterday's schedule for up to another 10 minutes.

### No historical data

`GET /events` (paginate with `?page=N`) returns state changes for `door`, `light`,
`connectivity` and `general.firmwareVersionCurrent` — useful for reconstructing what
the door actually did. It **never** carries `batteryLevel`, the
`parameterName`/`parameterSetName` query params are silently ignored, and every
candidate history endpoint 404s. The mobile app's battery chart comes from a private
endpoint.

Mitigation: the Lambda publishes `BatteryLevel` and `RunSucceeded` to CloudWatch so a
trend accrues locally.

---

## 8. Power

**This is the weakest part of any battery-powered deployment, and no scheduling change
mitigates it.**

Omlet advise against rechargeable cells, and the reason is voltage rather than
capacity. The fuel gauge maps an alkaline discharge curve:

| Chemistry | Fresh | Discharge shape | 4-cell pack |
| --- | --- | --- | --- |
| Alkaline AA | 1.6 V | gradual decline to ~1.0 V | 6.4 → 4.0 V |
| NiMH AA | ~1.4 V, settles 1.25 V | flat plateau, then cliff | ~5.0 → 4.8 V → cliff |

A fully charged NiMH pack reads ~74% rather than 100%, so the usable band before the
firmware sheds the WiFi radio is **37 points instead of 63** — roughly 40% of the range
gone before the first day. Surface charge also inflates the reading for a few hours
after charging, so early "drain" is partly artefact.

Observed in practice: ~10%/day against Omlet's ~1.7%/day spec, reaching WiFi dropout in
under four days.

**The coop light is not the culprit**, despite being a 1.5 W LED against a ~10–13 Wh
pack — one hour would be 10–15% of capacity. The event log showed every on-period at
~10 minutes, exactly `maxOnTime`, costing ~2%/night.

### The failure that matters

A flat battery does not merely break updates. Observed: the pack fell below the WiFi
threshold, the door kept operating locally from its RTC while unable to report, then
went fully flat overnight and **died in the open state**, missing the next scheduled
open entirely.

> **A battery that dies while the door is open leaves it open all night.** That is the
> exact fox exposure this project exists to prevent, arriving via power rather than
> scheduling. Mains or solar is the real fix.

### Solar sizing

The DC input is **12 V / 500 mA / 6 W max**, P1J barrel 2.1×5.5×11 mm, centre positive.
Only doors bought after **April 2023** have the socket.

Real load is ~0.2 Wh/day. Designing at 1 Wh/day for 5× margin, PVGIS for southern UK
with 20 Wp at 60° tilt due south:

| | Yield | Margin |
| --- | --- | --- |
| December average | 29 Wh/day | 29× |
| Overcast week (~25% of average) | ~7 Wh/day | 7× |

BOM ~£90–120: 20 Wp panel, PWM controller, 12 V 7 Ah SLA (~40 days autonomy), **12 V
fixed buck regulator**, IP65 enclosure.

> The regulator is **not optional**. A PWM controller passes raw battery voltage to the
> load, reaching **14.4 V during absorption charge**, into a 12 V input.

Omlet's published position is *"we do not recommend using a solar panel with your
Autodoor."* That reads as a support-burden stance rather than a technical limit given a
6 W ceiling, but the configuration is unsupported and self-owned.

---

## 9. Known limitations

- Requires internet and Omlet's cloud to **update**; not to operate.
- No local control path exists.
- Sunrise/sunset accurate to ~3 min, civil twilight to ~1 min.
- Single door. No support for the coop light, fan or feeder.
- Untested above the polar circles; the calculation raises rather than guessing where
  the sun does not rise or set.
- Alerts are emitted to logs and CloudWatch metrics. Wiring them to a notification
  channel is left to the deployment.
- API key lifetime is unestablished — whether it expires is unknown.
- The API key may authenticate as a different household member's Omlet account than
  expected. That account owns the device, receives all fault and battery notifications,
  and a password change on it may invalidate the key.

---

## 10. Sources

**API and product**

- [Omlet Developer Console — Python SDK](https://smart.omlet.com/developers/python-sdk)
- [SmartCoop TypeScript SDK](https://github.com/Omlet-Ltd/smartcoop-ts-sdk)
- [Omlet Smart Autodoor](https://www.omlet.co.uk/smart-automatic-chicken-coop-door-opener/)
- [Smart Autodoor firmware changelog](https://help.omlet.com/en/category/smart-automatic-door/article/smart-autodoor-firmware-changelog)
- [Omlet — battery guidance](https://help.omlet.com/en/category/smart-automatic-door/article/what-batteries-should-i-use-to-power-my-autodoor)
- [Omlet — solar panels](https://help.omlet.com/en/category/smart-automatic-door/article/can-i-connect-a-solar-panel-or-other-power-supply)
- [Home Assistant Omlet integration](https://github.com/krozgrov/ha-omlet-integration)

**Behaviour and welfare**

- Doncaster & Macdonald (1997), *J. Zool.* 241:73–87 — UK urban fox activity
  ([summary](https://www.wildlifeonline.me.uk/animals/article/red-fox-activity))
- [Kent, Hurnik & Yardley (1997), *Appl. Anim. Behav. Sci.* 51 — roosting light-level cue](https://www.sciencedirect.com/science/article/abs/pii/S0376635796007644)
- [Díaz-Ruiz et al. (2016), *J. Zool.* 298:128–138 — fox diel activity](https://zslpublications.onlinelibrary.wiley.com/doi/10.1111/jzo.12294)
- ["Are British urban foxes bold?"](https://pmc.ncbi.nlm.nih.gov/articles/PMC7820170/) — boldness tracks social status, not urbanisation
- [RSPCA welfare standards for laying hens](https://www.rspca.org.uk/documents/d/rspca/rspca-welfare-standards-for-laying-hens)
- [BHWT — fox-proof chicken coop](https://www.bhwt.org.uk/blog/health-welfare/top-tips-for-a-fox-proof-chicken-coop/)
- [National Fox Welfare Society](https://www.national-fox-welfare.com/fox-problems)

**Other**

- [PVGIS v5.2 (EC JRC)](https://re.jrc.ec.europa.eu/pvg_tools/en/) — irradiance for panel sizing
- [sunrise-sunset.org API](https://api.sunrise-sunset.org/) — validation only
