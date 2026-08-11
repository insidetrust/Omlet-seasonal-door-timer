# Design record — coopclock

> Working document behind [coopclock](../README.md). Retained because it records *why*
> each decision was made, including the ones that turned out wrong.

**Author:** insidetrust
**Date:** 06/08/2026
**Status:** Draft for review
**Working name:** `coopclock`

---

## 1. Problem

The Omlet Smart Autodoor is currently running on a **fixed timer**: open 05:40, close 21:35 (BST).
Those times were correct when they were set, but they do not move with the seasons.

At Swindon's latitude the sun moves fast. Between now and midwinter:

- Sunrise slides **~2.5 hours later** (05:37 BST → 08:13 GMT)
- Sunset slides **~4.9 hours earlier** (20:48 BST → 15:57 GMT)

Left alone, by late October the fixed 21:35 close would leave the door **open for
~4h40m after sunset** (over 4 hours after full dark). Foxes are active from dusk onwards,
so this is a direct predation risk, not a comfort issue.

**The Omlet app has no seasonal/astronomical scheduling.** It offers exactly two modes:

| Mode | Behaviour | Why it does not solve this |
| --- | --- | --- |
| **Timer** | Fixed wall-clock open/close | Does not track the seasons — the current problem |
| **Light sensor** | Triggers at a set lux level, 15 min debounce | Omlet themselves advise switching to timer in darker months; trees/shade and overcast skies make trigger time erratic, and the failure direction is *late closing* |

So the gap is real and there is no in-app fix. This project closes it by driving the
door's **timer** settings from an astronomical calculation, updated daily.

---

## 2. Goal

Keep the Autodoor's open and close times correctly aligned to Swindon sunrise/sunset
all year round, automatically, with no manual intervention and no reduction in the
safety margin the keeper is already happy with.

### Success criteria

1. Door close is never more than **15 minutes** away from its target offset after civil dusk.
2. Zero nights where the door is open more than **60 minutes** after sunset.
3. Zero manual schedule edits in the Omlet app over a full 12-month cycle.
4. DST transitions (25/10/2026 and 28/03/2027) handled with no manual action and no bad night.
5. Any failure to update is **surfaced to the operator within 24 hours**.

---

## 3. Research findings

### 3.1 Omlet API — confirmed available

Omlet publish an official API and first-party SDKs. This is a supported integration
path, not a reverse-engineered one.

- **Developer console:** `smart.omlet.com/developers` — log in with the normal Omlet
  app credentials, go to **API Keys → Generate Key**
- **Official SDKs:** Python (`pip install smartcoop-python-sdk`), TypeScript, PHP
- **Endpoints:** `/device`, `/device/{deviceId}/action/{action}`,
  `/device/{deviceId}/configuration`
- **Device ID:** shown in the Omlet app's device settings
- **Webhooks:** supported, but require a publicly reachable URL (LAN/localhost rejected)

The `ConfigurationDoor` object is the thing we need to write:

```
doorType, openMode, openDelay, openLightLevel, openTime,
closeMode, closeDelay, closeLightLevel, closeTime, colour
```

Documented Python update pattern:

```python
from smartcoop.client import SmartCoopClient
from smartcoop.api.omlet import Omlet

client = SmartCoopClient(client_secret=API_KEY)
omlet = Omlet(client)

devices = omlet.get_devices()
configuration = device.configuration
configuration.door.openTime = "06:45"
configuration.door.closeTime = "20:10"
omlet.update_configuration(device.deviceId, configuration)
```

There is also a community Home Assistant integration (`krozgrov/ha-omlet-integration`)
built on the same API — useful as a worked reference for auth headers and polling.

**CONFIRMED against the live device (09/08/2026).** Base URL is
`https://x107.omlet.co.uk/api/v1`, auth is `Authorization: Bearer <api_key>`. Actual
configuration read from the door:

```json
"door": {
  "openMode": "time",   "openTime":  "05:40",
  "closeMode": "time",  "closeTime": "21:35",
  "openDelay": 0, "closeDelay": 0,
  "openLightLevel": 27, "closeLightLevel": 6,
  "doorType": "sliding", "colour": "green", "lightOffset": 3
},
"general": {
  "datetime": "2026-08-09T19:28:50+01:00",
  "timezone": "Europe/London",
  "pollFreq": 600, "statusUpdatePeriod": 21600, "updateFrequency": 86400,
  "overnightSleepEnable": false,
  "overnightSleepStart": "22:30", "overnightSleepEnd": "05:00"
},
"connectivity": { "wifiState": "on", "wifiRoamingThreshold": -70 }
```

All three load-bearing unknowns are resolved — see §9.

### 3.2 Derived offsets — reverse-engineered from the current settings

Swindon: **51.5558° N, 1.7797° W**. On 06/08/2026:

| | Time (BST) |
| --- | --- |
| Civil dawn | 04:58 |
| Sunrise | 05:37 |
| **Current door open** | **05:40** |
| Sunset | 20:48 |
| Civil dusk | 21:27 |
| **Current door close** | **21:35** |

Which gives the offsets this project will preserve:

```
OPEN  = sunrise + 3 min
CLOSE = sunset  + 46 min      (≈ civil dusk + 8 min)
```

Both are comfortably inside daylight/twilight: the door opens 42 minutes *after* civil
dawn, and closes only 8 minutes after civil dusk. That is the margin the keeper is already
running, and the default behaviour is to keep it.

### 3.3 Astronomical calculation — validated

A self-contained NOAA solar-position implementation (no third-party dependency) was
written and checked against `api.sunrise-sunset.org` on six spread dates including both
solstices and both DST transitions:

- Civil dawn/dusk agreement: **within 71 seconds**
- Sunrise/sunset agreement: **within 174 seconds** (~3 min), systematically biased

The residual is likely a refraction-constant difference in the reference engine. The
standard `90.833°` zenith was **kept rather than tuned to match**, since the reference
could not be independently corroborated. A ±3 minute tolerance is immaterial against a
46-minute close offset, but it is a real limit and is stated rather than hidden.

Reference implementation and its validation harness are in `reference/`.

**Implication:** the schedule can be computed **entirely offline**. No dependency on a
third-party sunrise API at runtime — one less thing to fail on a winter night.

### 3.4 The generated almanac

Full 365-day almanac: `data/almanac-swindon-2026-2027.csv` (civil dawn, sunrise, sunset,
civil dusk, computed open/close, DST state, daylight hours).

Sampled:

| Date | TZ | Sunrise | **Open** | Sunset | **Close** | vs civil dusk |
| --- | --- | --- | --- | --- | --- | --- |
| 06/08/2026 | BST | 05:37 | **05:40** | 20:48 | **21:35** | +7 min |
| 15/09/2026 | BST | 06:41 | **06:45** | 19:23 | **20:10** | +13 min |
| 15/10/2026 | BST | 07:30 | **07:35** | 18:15 | **19:00** | +11 min |
| 15/11/2026 | GMT | 07:24 | **07:25** | 16:18 | **17:05** | +10 min |
| 15/12/2026 | GMT | 08:06 | **08:10** | 15:57 | **16:45** | +7 min |
| 15/01/2027 | GMT | 08:06 | **08:10** | 16:25 | **17:10** | +6 min |
| 15/03/2027 | GMT | 06:22 | **06:25** | 18:09 | **18:55** | +12 min |
| 15/05/2027 | BST | 05:15 | **05:20** | 20:51 | **21:35** | +2 min |
| 15/06/2027 | BST | 04:49 | **04:50** | 21:25 | **22:10** | −3 min |

Sanity check: the anchor day reproduces the original manual settings **exactly** (05:40 / 21:35).

Annual envelope:

- Earliest open **04:50** (12/06/2027) · Latest open **08:15** (10/01/2027)
- Earliest close **16:45** (01/12/2026) · Latest close **22:15** (04/07/2027)

Two findings that shape the requirements:

1. **A fixed sunset offset tracks civil dusk well but not perfectly.** The
   sunset→civil-dusk gap at this latitude varies 33–48 min, so `sunset + 46` lands
   between **5 minutes before** and **15 minutes after** civil dusk. The one direction
   that matters is midsummer, where the door would close *slightly before* civil dusk —
   see §6.1.
2. **Drift is slow but relentless.** Peak real astronomical drift is ~2 min/day, up to
   **20 min/week** around the equinoxes. So a missed update is harmless for a day or
   two and dangerous over weeks — which sets the staleness alerting threshold in §5.4.

---

## 4. Scope

### In scope

- Daily computation of open/close times for a configured location
- Pushing those times to the Autodoor via the official Omlet API
- Correct Europe/London DST handling
- Safety bounds, verification, and failure alerting
- Deployment so it runs unattended

### Out of scope (v1)

- Controlling the coop **light** (separate Omlet feature)
- Manual open/close, or a UI — the Omlet app already does this well
- Multiple coops or multiple doors
- Weather-reactive logic (heavy overcast, snow)
- Actual fox *detection* — this project manages schedule risk only

---

## 5. Requirements

### 5.1 Scheduling engine

| ID | Requirement | Priority |
| --- | --- | --- |
| SCH-1 | Compute sunrise, sunset, civil dawn and civil dusk offline for a configured lat/lon | Must |
| SCH-2 | Open at `sunrise + 3 min` (configurable) | Must |
| SCH-3 | Close at `max(sunset + 46 min, civil dusk + 5 min)` — never before civil dusk (§6.1) | Must |
| SCH-4 | Handle Europe/London DST via `zoneinfo` — never hand-rolled ±1h arithmetic | Must |
| SCH-5 | Round to 5-minute granularity | Should |
| SCH-6 | Location, offsets and bounds live in a config file, not in code | Must |

### 5.2 Safety bounds — the fox-risk controls

Hard clamps applied after the astronomical calculation. These exist so that a bad
config, a bad calculation, or a bad clock cannot produce a dangerous schedule.

| ID | Requirement | Default | Priority |
| --- | --- | --- | --- |
| SAF-1 | Never open before `earliest_open` | 04:30 | Must |
| SAF-2 | Never open after `latest_open` | 08:30 | Must |
| SAF-3 | Never close before `earliest_close` | 16:00 | Must |
| SAF-4 | Never close after `latest_close` | 22:30 | Must |
| SAF-5 | Close must always be later than open by a minimum gap | 6 h | Must |
| SAF-6 | Reject and alert on any computed time that hits a clamp — a clamp firing means something is wrong | — | Must |
| SAF-7 | Never write a schedule that leaves the door open >60 min after sunset | — | Must |

> Note the defaults deliberately sit just outside the observed annual envelope
> (04:50–08:15 open, 16:45–22:15 close), so in normal operation **no clamp ever fires**.
> A clamp firing is therefore a genuine signal, not routine noise.

### 5.3 Omlet integration

| ID | Requirement | Priority |
| --- | --- | --- |
| API-1 | Authenticate using an API key from AWS Secrets Manager (deployed) or `.env` (local dev) | Must |
| API-2 | Read current device configuration before writing; mutate only `openTime`/`closeTime` | Must |
| API-3 | Ensure `openMode`/`closeMode` are set to time-based, so the light sensor cannot override | Must |
| API-4 | **Read back** configuration after writing and assert the times took effect. Must **poll for up to ~12 min** — the device wakes only every `pollFreq` = 600 s (§9 Q5) | Must |
| API-5 | Skip the write entirely if the target times already match — avoid needless API calls | Should |
| API-6 | Retry with exponential backoff on transient failure (door offline, 5xx) | Must |
| API-7 | Never log the API key, and redact it from tracebacks | Must |

### 5.4 Reliability and alerting

| ID | Requirement | Priority |
| --- | --- | --- |
| REL-1 | Run daily, well before the earliest possible open time (target 02:00 local) | Must |
| REL-2 | Alert the operator if no successful **run** has completed in **72 hours** (≈20 min of drift). Track runs, *not* writes — see below | Must |
| REL-3 | Alert on clamp firing, verification mismatch, or auth failure | Must |
| REL-4 | Log every run: computed times, times written, verification result | Must |
| REL-5 | Dry-run mode that computes and logs without writing | Must |
| REL-6 | Idempotent — safe to run repeatedly | Must |

**Cadence: compute daily, write only on change.** With 5-minute rounding, the target times
only actually move on about a third of days:

| | Open | Close |
| --- | --- | --- |
| Days needing a write | 107/364 (29%) | 112/364 (31%) |
| Typical gap between writes | 3.4 days | 3.2 days |
| Longest static run | 21 days from 21/12 | 25 days from 01/12 |
| Step size when it moves | 5 min (60 min at DST) | 5 min (60 min at DST) |

Near the solstices the schedule is static for 3–4 weeks; near the equinoxes it steps every
couple of days. A **weekly** cadence was rejected: it accumulates 16–17 min of
astronomical error at the equinoxes and **up to 74 min across a DST transition** — an hour
wrong for up to a week, in late October, which is the exact failure this project exists to
prevent.

Running daily but writing on change gives full accuracy at ~⅓ of the API traffic, which
matters because each write is a wake-and-transmit cycle on a battery-powered radio (§6.2).

> **This is why REL-2 tracks runs rather than writes.** A legitimate 25-day static period
> in December is indistinguishable from a dead updater if you only monitor writes.

**Failure posture:** if the updater dies, the door keeps its last written schedule. That
degrades *gracefully* (~2 min/day), which is why REL-2's threshold is days rather than
hours. The dangerous scenario is silent failure over weeks, so alerting on staleness is
the single most important reliability control here.

### 5.5 Non-functional

Per the project's conventions:

- Python 3.12, `pip` + `venv`, pinned `requirements.txt`
- `ruff` for lint/format
- Type hints throughout (3.10+ style), Google-style docstrings on public functions
- `pytest` tests alongside implementation, mirroring source layout in `tests/`
- No secrets in code, comments, or logs

---

## 6. Design decisions

### 6.1 Close-time anchor — **resolved by behavioural research**

Three parallel research passes (chicken roosting, UK fox ecology, keeper practice)
produced *conflicting* recommendations, which is itself the useful finding:

| Lens | Recommendation | Basis |
| --- | --- | --- |
| Fox ecology | **Earlier** — `sunset + 15` | Doncaster & Macdonald (1997): fox activity begins sunset+30 |
| Chicken behaviour | **Later** — `sunset + 50–55` or dusk-anchored | Birds settle up to civil dusk; midsummer close falls short |
| Keeper practice | **Keep** `+46` | Consensus 30–45 min; commercial defaults ~20 min |

**The fox recommendation was tested and rejected.** `sunset + 15` closes *before* civil
dusk on **365/365 nights**, by 18–33 minutes — it would lock hens out every night of the
year. It optimises fox exposure while ignoring the binding constraint.

**The binding constraint:** close time cannot precede the last bird roosting. Fox ecology
can only minimise the tail *above* that floor; it cannot push below it.

**Irreducible overlap.** Combining the two evidence bases:

- Fox activity onset: **sunset + 30**
- Roosting completion: **≈ civil dusk = sunset + 33 to +48** (seasonally variable)

These overlap by **3–18 minutes in every season**. No close time is both after the last
hen and before the first fox. *The dusk exposure window cannot be closed by scheduling.*
This is why run security — welded mesh, buried apron, solid roof — is the primary
control and door timing is a secondary layer. See §10.

**Decision — adopt rule C:**

```
close = max(sunset + 46 min, civil dusk + 5 min)
```

| Rule | vs civil dusk | Nights closing before dusk |
| --- | --- | --- |
| A `sunset+46` (today) | −2 to +13 min | **28 / 365** |
| B `civil dusk+8` | +8 constant | 0 / 365 |
| **C `max(A, dusk+5)`** | **+5 to +13 min** | **0 / 365** |

C preserves the trusted baseline on 337 nights and intervenes only on the 28 midsummer
nights where the fixed offset falls short — the sunset→dusk gap stretches from 33 min at
the equinox to 48 min at midsummer, which a fixed offset cannot track. Cost: at most
+7 min later than today. This supersedes SCH-3.

**Open time unchanged at `sunrise + 3 min`** — all three lenses agreed. It sits ~18 min
after foxes cease activity (sunrise −15) and no source treats morning timing as
safety-critical.

**Evidence caveat:** Doncaster & Macdonald is the load-bearing citation for both twilight
margins. It is the best UK data available, but it is Oxford 1980–83 and pre-dates much of
the urban food-subsidy effect on fox behaviour. Treat sunset+30 as indicative, not exact.

### 6.2 Power supply — **hard dependency, resolve before build**

The door currently runs on **NiMH rechargeable AA cells and drops off WiFi weekly at
~37% indicated charge**. This is a blocker for the whole project: a door that is
unreachable cannot receive daily schedule pushes, REL-2 would fire continuously, and the
system degrades to exactly the manual fixed timer it is meant to replace.

> **UNDER INVESTIGATION 09/08/2026 — two competing hypotheses.**
>
> Observed: battery flat overnight 08–09/08; fresh cells fitted, reading **74%**; coop
> light on during the day; pack fell **74% → 65% (~10%) over 09/08**.
>
> The **coop light is a 1.5 W LED**. A 4×AA pack holds ~10–13 Wh, so ~10% ≈ **1.0–1.3 Wh
> ≈ 45 min of light**. The observed loss therefore discriminates between:
>
> | If light was on for… | Conclusion |
> | --- | --- |
> | **~45 min** | Light is the dominant load; door electronics (~0.2 Wh/day) are noise. Disable it. |
> | **Several hours** | Light is **not** drawing 1.5 W from this pack; baseline drain is the door itself and **chemistry returns as prime suspect**. |
>
> **Open — actual light run-time on 09/08 is unknown and is the discriminating measurement.**
>
> Two facts hold either way:
> - **~10%/day is ~6× Omlet's spec** (2+ months on alkaline with WiFi ≈ 1.7%/day), giving
>   ~4 days to the 37% dropout — matching the observed weekly cycle.
> - **74% on a fresh set is unexplained by either hypothesis.** A fresh pack should read
>   near 100% regardless of downstream load. Chemistry of the fitted cells (NiMH vs
>   alkaline) is still unknown and still needed.
>
> Configured behaviour is `mode: auto, minutesBeforeClose: 10, maxOnTime: 10` — ~10 min a
> night, ~2%, not 10%. Known light bugs (**v1.0.25** "ignoring maximum on time…draining the
> battery"; **v1.0.35** "remaining on after door closed") were both fixed well before the
> installed 1.0.55, so over-running would be a new or different fault.
>
> **RESOLVED 09/08/2026 — the light is exonerated.** The undocumented `GET /events`
> endpoint (found by probing; paginated via `?page=N`) carries 35 days of state changes.
> **Every** light on-period is ~10 min, exactly `maxOnTime`, normally firing 10 min before
> the close and tracking it as it moves:
>
> ```
> 23/07 19:39->19:49 10.1m   30/07 21:35->21:45 10.0m
> 23/07 21:20->21:30 10.0m   04/08 21:35->21:45 10.0m
> 25/07 21:50->22:00 10.1m   05/08 21:25->21:35 10.0m
> 26/07 21:50->22:00 10.1m   09/08 19:07->19:17 10.1m
> 28/07 21:50->22:00 10.1m
> ```
>
> That is **~2.1%/night**, not the observed 9–10%. The light never ran long. Hypothesis A
> is dead; **hypothesis B (chemistry) stands unopposed.**
>
> **Solar consequence (if light-driven):** a stuck 1.5 W light draws ~14 Wh/day, exceeding a
> 20 Wp panel's yield in a bad Swindon December week (~7 Wh/day). Fix it rather than
> absorbing it with a bigger array.

**Hypothesis B — a voltage problem, not a capacity problem.** The gauge reads pack
voltage against an alkaline discharge curve:

| Chemistry | Fresh | Discharge shape | 4-cell pack |
| --- | --- | --- | --- |
| Alkaline AA | 1.6 V | gradual decline to ~1.0 V | 6.4 → 4.0 V |
| NiMH AA | ~1.4 V, settles 1.25 V | flat plateau, then cliff | ~5.0 → 4.8 V → cliff |

A fully charged NiMH pack sits at ~4.8–5.0 V, which maps to roughly **35–45%** on an
alkaline curve — i.e. the observed 37%. The pack begins life near the firmware's
low-voltage threshold, at which point the radio is shed to preserve door operation.
This matches the symptom exactly (door works, WiFi does not).

**Falsifiable test:** fit freshly charged cells and read the app immediately. A reading
of ~40–50% rather than 100% confirms the diagnosis. No NiMH cell can fix it.

**Firmware — ELIMINATED as a cause (07/08/2026).** Device is on **v1.0.55**, the current
latest release (30/03/2026). Every battery-drain fix Omlet has shipped is present:

| Version | Entry |
| --- | --- |
| 1.0.15 / 1.0.35 | Discovery Mode not exiting, leaving WiFi active and draining battery |
| 1.0.47 | Control panels not sleeping as long as they should |
| 1.0.50 / 1.0.51 | Batteries draining quickly when wifi is enabled, but unavailable |
| **1.0.51** | **"Added detection and notification of power faults due to low / poor quality batteries or power supply issues"** |
| 1.0.54 | On mains power, report power level only every 10 min to reduce load |
| 1.0.55 | Connect to the strongest available access point, not the first found |

Two consequences. First, the weak-WiFi-signal theory is largely ruled out too — 1.0.55
already selects the best AP. Second, **1.0.51 means the device has built-in detection for
poor-quality batteries**: check the app for an existing power-fault notification, which
would be direct confirmation from the device itself.

Note also that 1.0.54 shows the firmware runs a lighter duty cycle on external power —
an additional benefit of the solar route beyond raw energy availability.

**Live telemetry (09/08/2026)** — read from the device, and it reshapes the remediation:

| Field | Value | Reading |
| --- | --- | --- |
| `wifiStrength` | **−43 dBm** | Excellent. **Weak-signal theory is dead** — further isolates the NiMH diagnosis |
| `powerSource` | `internal` | On batteries, no external supply |
| `batteryLevel` | 63 | Mid-pack at time of reading |
| **`overnightSleepEnable`** | **`false`** | **An overnight sleep window (22:30–05:00) exists and is switched off — radio time paid for nightly with no benefit** |
| `pollFreq` | 600 | Wakes every 10 min |
| `statusUpdatePeriod` | 21600 | Full status every 6 h |
| `light.mode` | `auto`, `minutesBeforeClose: 10` | Coop light is fitted and firing nightly — a useful straggler aid (§6.1) but another draw on the same cells |
| `ssid` | *(redacted)* | Door was on a WiFi range extender, not the main router |

**Remediation, in order:**

1. **Free fixes** — firmware is already current, so: **enable `overnightSleepEnable`**
   (immediate, zero-cost saving), confirm Power Saving + Deep Sleep, and check the app for a
   v1.0.51 power-fault notification. Weak signal is ruled out by the −43 dBm reading.
2. **Solar (preferred).** DC input is **12 V / 500 mA / 6 W max**, P1J barrel
   2.1×5.5×11 mm, **centre positive**.

   Real load from Omlet's own spec (2 months on 4×AA ≈ 12 Wh usable) is **~0.2 Wh/day**.
   Design at **1 Wh/day** for 5× margin. PVGIS for Swindon, 20 Wp at 60° tilt due south:

   | | Yield | Margin at design load |
   | --- | --- | --- |
   | December average | 29 Wh/day | 29× |
   | Overcast week (~25% of avg) | ~7 Wh/day | 7× |
   | June | 68 Wh/day | 68× |

   BOM (~£90–120): 20 Wp panel (60° tilt, due south, unshaded — pole-mount if the coop is
   overshadowed) · PWM charge controller · 12 V 7 Ah SLA (84 Wh ≈ 40 days autonomy) ·
   **12 V fixed buck regulator** · IP65 enclosure.

   > The regulator is **not optional**. A PWM controller passes raw battery voltage to the
   > load, reaching **14.4 V during absorption charge**, into a 12 V input. An ~£8 part
   > removes the risk.

   Omlet's published position is *"we do not recommend using a solar panel with your
   Autodoor."* That is a support-burden stance rather than a technical limit given a 6 W
   ceiling, but it does mean this configuration is unsupported and self-owned.
3. **Mains** — official 12 V adaptor, but only a **1.8 m cable**, so it needs an
   RCD-protected outdoor socket at the coop (~£150–400, electrician). More reliable than
   solar, considerably more disruptive.

**Compatibility check — RESOLVED (07/08/2026).** Only Autodoors bought after April 2023
have the DC socket. the unit was purchased **~May 2026**, so the socket is present and
**both solar and mains are viable**. The lithium-primary fallback (Energizer Ultimate
Lithium L91) is no longer required, and is retained only as a stopgap while the solar
build is assembled.

**Corroborating evidence for the NiMH diagnosis.** A unit this new almost certainly
shipped with firmware well past 1.0.15, which makes the known drain bug an unlikely
cause. Observed life is ~1 week against Omlet's stated 2+ months on alkaline — roughly
**an eighth of spec**. That gap is too large for a firmware inefficiency and consistent
with a pack that begins life near the low-voltage cutoff.

### 6.3 Deployment — **needs the keeper's call**

| Option | Pros | Cons |
| --- | --- | --- |
| **AWS Lambda + EventBridge** (eu-west-1) | Always on; independent of the VM; Secrets Manager; matches the target stack | Small ongoing cost; most setup |
| **systemd timer** on the Ubuntu VM | Simplest; no cloud | **Silently stops if the VM is off** — bad failure mode for a fox problem |
| **GitHub Actions scheduled workflow** | Free; good Actions learning exercise | Scheduled runs can be delayed by tens of minutes; secrets in repo settings |

**Recommendation: Lambda + EventBridge in `eu-west-1`.** This is the only option whose
failure mode is not "the maintainer's laptop was off". Given the whole point is fox protection,
availability is the deciding factor. GitHub Actions would make a reasonable second
implementation later as an Actions learning exercise, but not as the primary.

### 6.4 Why drive the timer rather than use light-sensor mode

The door already has a light sensor, and it might seem like the obvious answer. It is
not, for three reasons: Omlet's own guidance is to switch to timer during darker months;
shade and overcast make trigger time erratic; and its failure mode is *closing late*,
which is precisely the risk being engineered out. A computed timer is deterministic and
verifiable.

---

## 7. Architecture — where the app runs and what it talks to

**There is no local/LAN API.** The Autodoor operates standalone from its control panel
without WiFi, but exposes no local control endpoint. Omlet's webhook configuration
explicitly rejects `localhost`, `.local` hostnames and private LAN IPs — behaviour that
only makes sense for a cloud service reaching in from outside. All programmatic control
is **cloud-mediated, authenticated against the keeper's Omlet account**.

```
┌──────────────────────────────────────┐
│  App - AWS Lambda (eu-west-1)        │  runs anywhere with outbound HTTPS;
│  EventBridge daily @ 02:00 local     │  NOT required to be on the home LAN
└──────────────┬───────────────────────┘
               │  HTTPS + API key (AWS Secrets Manager)
               ▼
┌──────────────────────────────────────┐
│  Omlet cloud - smart.omlet.com       │  the keeper's Omlet account
└──────────────┬───────────────────────┘
               │  Omlet's own push channel
               ▼
┌──────────────────────────────────────┐
│  Home WiFi ──► Autodoor control panel│
│  writes openTime / closeTime to the  │
│  device's internal clock             │
└──────────────┬───────────────────────┘
               ▼
     Door opens and closes AUTONOMOUSLY,
     from its own clock, with no network
```

### Consequences

**7.1 Location-independent.** Because all traffic is cloud-mediated, where the coop is and
where the app runs are fully decoupled. This is what makes Lambda viable, and it would be
equally true of a VPS or an off-site Raspberry Pi. It also means §6.3's rejection of the
local systemd option costs nothing architecturally.

**7.2 The door is autonomous — this is the core safety property.** The app writes times
*into* the device; the device acts on them from its own RTC. A failure of home internet,
home WiFi, the Omlet backend, or AWS **does not stop the door operating**. It only stops
the schedule being *updated*, after which it degrades at ~2 min/day. This is precisely why
REL-2 measures staleness in days rather than treating a single failed run as an incident.

**7.3 No webhooks required.** Webhooks exist to receive events and would demand a
publicly reachable URL. This project only *pushes* a schedule, and can poll
`GET /device` for battery and door state on the same daily run. No inbound endpoint, no
public URL, no tunnelling. Webhooks may be revisited later for real-time battery alerts.

**7.4 Dependency chain.** Every link below breaks *updates*; none breaks *door operation*:

`Lambda/EventBridge → Omlet cloud → home internet → home WiFi → door`

plus the door's own power supply (§6.2), which remains the weakest link and the only one
that can stop the door itself.

## 8. Proposed structure

```
chicken-door/
├── PRD.md
├── requirements.txt
├── config/
│   └── swindon.yaml            # lat/lon, offsets, bounds, device id
├── src/coopclock/
│   ├── solar.py                # NOAA calc (validated - see reference/)
│   ├── schedule.py             # offsets, clamps, DST, rounding
│   ├── omlet.py                # API client wrapper + read-back verify
│   ├── alerting.py
│   └── cli.py                  # run / dry-run / almanac subcommands
├── tests/                      # mirrors src/
├── data/
│   └── almanac-swindon-2026-2027.csv
└── reference/
    ├── solar.py                # validated prototype
    └── validate_against_api.py # accuracy harness vs sunrise-sunset.org
```

---

## 9. Open questions

### Resolved 09/08/2026 against the live device

1. **`openTime`/`closeTime` format** — ✅ `"HH:MM"`, 24-hour, **local wall-clock**.
2. **`openMode`/`closeMode` enum** — ✅ `"time"`. The door is **already in timer mode**,
   so writes will take effect and the light sensor cannot override them. API-3 becomes an
   assertion rather than a change.
3. **Device clock and DST** — ✅ **RESOLVED THE FAVOURABLE WAY.** The device reports
   `"datetime": "2026-08-09T19:28:50+01:00"` with `"timezone": "Europe/London"` — it holds
   local wall-clock and tracks BST/GMT itself. **We write plain local time; no conversion.**
   This was the highest-risk unknown in the document.
5. **Offline behaviour (partial)** — ✅ `"pollFreq": 600`: the door checks in every
   **10 minutes** and is `"connected": false` in between, which is normal deep sleep, not a
   fault. Writes are therefore queued server-side and collected on the next wake.

   > **Design consequence:** API-4 read-back verification must **poll for up to ~10–12
   > minutes**, not assert immediately after the write. An instant read-back would report a
   > false mismatch on every run.

### Still open

4. **API rate limits and key lifetime** — does the key expire? Not yet established.
6. **Account ownership** — `GET /whoami` showed the API key authenticating as a *different
   household member's* Omlet account than expected. Worth checking on any deployment: the
   account that owns the device also receives all fault and battery notifications, and a
   password change on it may invalidate the key.
8. Alert channel — email, ntfy, Telegram, AWS SNS? Battery level and run health are
   currently published as CloudWatch metrics in namespace `coopclock`; alarms on those are
   the cheapest route to a real notification.

### Also resolved 09/08/2026

7. **Missed open on 09/08/2026 — explained, and it is serious.** `GET /events` shows door
   activity stopping after `06/08 05:40` and resuming only at `09/08 07:04` with a
   boot-like `open -> opening -> open`, coinciding with fresh cells being fitted. The device
   reports `lastCloseTime` of 08/08 21:35 which is absent from the event log — so the
   battery fell below the WiFi threshold around 06/08 and the door **kept operating locally
   from its RTC while unable to report**, then went fully flat overnight on 08–09/08 and
   missed the 05:40 open.

   > **The door died while open.** It failed in the morning, so the cost was a late
   > opening. Failing a few hours earlier would have left the door **open all night**. This
   > is the fox-exposure scenario, arriving via power rather than scheduling, and no
   > schedule change mitigates it.

9. **Battery history in the API** — ❌ none. `GET /events` carries `door`, `light`,
   `connectivity` and `general.firmwareVersionCurrent` changes but never `batteryLevel`,
   and every candidate history endpoint returns 404 (`device/{id}/history|events|
   statistics|battery|telemetry|usage|report`, `battery`, `history`, `stats`). The
   `parameterName`/`parameterSetName` query params are silently ignored. The mobile app's
   battery chart therefore comes from a private endpoint, not the public developer API.
   Mitigation: the Lambda now publishes `BatteryLevel` and `RunSucceeded` to CloudWatch
   namespace `coopclock` on every run, so a trend accrues from 09/08/2026 onward.

**Proposed resolution:** a short discovery script (`GET /device`, dump the full
configuration object verbatim), run against the door before any implementation.
This is milestone 0.

Non-blocking:

6. Confirm 51.5558° N, 1.7797° W is close enough to the actual coop (anywhere in
   Swindon is within a few seconds of sun time — almost certainly fine).
7. Alert channel — email, ntfy, Telegram, AWS SNS?

---

## 10. Milestones

| # | Milestone | Output |
| --- | --- | --- |
| **0a** | **Power fix (§6.2)** | Door stays online continuously for 7 days. Blocks everything else. |
| ~~0~~ | ~~**API discovery**~~ | ✅ **DONE 09/08/2026.** Config dumped; Q1, Q2, Q3, Q5 answered (§3.1, §9) |
| 1 | Scheduling engine | `solar.py` + `schedule.py` + tests; almanac CLI reproduces `data/` |
| 2 | Omlet client | Authenticated read/write with read-back verification; dry-run works |
| 3 | Safety and alerting | Clamps, staleness detection, alert channel |
| 4 | Deployment | Lambda + EventBridge, Secrets Manager, daily 02:00 schedule |
| 5 | Observation | Two weeks live, including a manual DST rehearsal ahead of 25/10/2026 |

**Timing note:** milestone 5 should complete before **25/10/2026** (BST→GMT), the first
real DST transition and the first hard test of the whole system.

---

## 11. Risks

| Risk | Impact | Likelihood | Mitigation |
| --- | --- | --- | --- |
| Device clock is UTC, not local | 1h error for half the year — door open an hour after dark | Medium | §9 Q3 resolved at milestone 0; DST rehearsal at milestone 5 |
| Silent update failure over weeks | Schedule drifts into darkness | Medium | REL-2 staleness alert at 72h |
| Light-sensor mode overrides written times | Schedule ignored entirely | Medium | API-3 forces timer mode; API-4 verifies by read-back |
| Omlet changes or withdraws the API | Project dead | Low | Official documented API with first-party SDKs; clamps mean last-known schedule stays safe |
| **Battery dies while the door is OPEN, leaving it open all night** | **Flock loss — the exact exposure this project exists to prevent** | **Already occurred once (06–09/08/2026)** | **§6.2. No scheduling change mitigates this. Milestone 0a blocks all other work.** |
| **Door offline at update time (current battery fault)** | **Schedule pushes fail; project degrades to a manual timer** | **Currently certain — happens weekly** | **§6.2. Milestone 0a blocks all other work.** |
| Bird shut out at midsummer | Predation | Low | Resolved by rule C (§6.1); still observe June evenings |
| **Fox enters coop during the irreducible dusk overlap** | **Flock loss** | **Medium** | **Not solvable by scheduling (§6.1). Requires physical run security: welded mesh not chicken wire, buried/outward dig-proof apron, solid roof, two-action latches. Highest-priority action in this document and independent of the software.** |
| Door closes on a bird | Injury/death — documented in keeper reports | Low | Omlet's built-in safety sensors; rule C's dusk floor avoids closing mid-settle |
| Straggler never roosts (broody, bullied, injured, new pullet) | Bird outside overnight | Medium | Not fixable by any offset — needs a daily visual headcount |

---

## 12. Sources

- [Omlet Developer Console — Python SDK](https://smart.omlet.com/developers/python-sdk)
- [Omlet SmartCoop TypeScript SDK](https://github.com/Omlet-Ltd/smartcoop-ts-sdk/blob/main/README.md)
- [Omlet Smart Autodoor product page](https://www.omlet.us/smart-automatic-chicken-coop-door/)
- [Omlet help — Smart Autodoor settings](https://help.omlet.com/en/category/smart-automatic-door/article/setting-when-the-smart-autodoor-coop-light-turns-on-and-off)
- [Home Assistant Omlet integration](https://github.com/krozgrov/ha-omlet-integration)
- [Home Assistant community — Omlet Auto Door](https://community.home-assistant.io/t/omlet-auto-door-for-chicken-coop/191283/77)

### Power (§6.2)

- [Omlet — what batteries should I use?](https://help.omlet.com/en/category/smart-automatic-door/article/what-batteries-should-i-use-to-power-my-autodoor) (rechargeables not recommended)
- [Omlet — can I connect a solar panel?](https://help.omlet.com/en/category/smart-automatic-door/article/can-i-connect-a-solar-panel-or-other-power-supply) ("No, we do not recommend…")
- [Omlet — how can I power my Autodoor?](https://help.omlet.com/en/category/smart-automatic-door/article/how-can-i-power-my-autodoor)
- [Omlet 12V Power Adaptor (UK plug)](https://www.omlet.co.uk/12v-power-adaptor-for-autodoor-uk-plug/) — 12 V, 500 mA, 6 W, P1J 2.1×5.5×11 mm, 1.8 m
- [Omlet Smart Autodoor firmware changelog](https://help.omlet.com/en/category/smart-automatic-door/article/smart-autodoor-firmware-changelog)
- [PVGIS v5.2 (EC JRC)](https://re.jrc.ec.europa.eu/pvg_tools/en/) — Swindon irradiance used for panel sizing
- [sunrise-sunset.org API](https://api.sunrise-sunset.org/) — used for validation only

### Behavioural research (§6.1)

- Doncaster & Macdonald (1997), *J. Zool.* 241:73–87 — UK urban fox activity; summarised at [Wildlife Online](https://www.wildlifeonline.me.uk/animals/article/red-fox-activity)
- [Kent, Hurnik & Yardley (1997), *Appl. Anim. Behav. Sci.* 51 — roosting light-level cue](https://www.sciencedirect.com/science/article/abs/pii/S0376635796007644)
- [Díaz-Ruiz et al. (2016), *J. Zool.* 298:128–138 — fox diel activity](https://zslpublications.onlinelibrary.wiley.com/doi/10.1111/jzo.12294)
- ["Are British urban foxes bold?" — PMC7820170](https://pmc.ncbi.nlm.nih.gov/articles/PMC7820170/) (boldness tracks social status, not urbanisation)
- [RSPCA welfare standards for laying hens (2025)](https://www.rspca.org.uk/documents/d/rspca/rspca-welfare-standards-for-laying-hens) — 8h continuous daylight minimum
- [BHWT — fox-proof chicken coop](https://www.bhwt.org.uk/blog/health-welfare/top-tips-for-a-fox-proof-chicken-coop/)
- [National Fox Welfare Society — fox problems](https://www.national-fox-welfare.com/fox-problems)
