# Smart Cover Automation (Helge Klein) — Reference Analysis

Analysis date: 2026-09-15. Analyzed version: **6.3.0** (requires HA 2026.4.1+, HACS 2.0.5+).
Sources: docs site, GitHub repository (source + issues + release notes), HA community thread.
Purpose: baseline for designing a replacement integration in this repo.

---

## 1. What it is

A HACS custom integration (`smart_cover_automation`, iot_class `calculated`) that moves
window covers automatically based on sun position, weather forecast and time schedules.
Configured entirely through the UI (6-step options wizard + device-page control entities).
Depends on the `sun`, `weather` and `logbook` integrations.

Scale: ~13k lines of integration code, ~40k lines of tests (100 % coverage claimed),
10 UI languages (de, en, es, fr, it, nl, pl, pt, sv, zh-CN) with full key parity.

Feature history (v0.7 → v6.3, roughly 14 months): logbook + blocked time range (0.8),
window lockout (0.9), evening closure (1.0), lock mode + settings moved to entities (1.1),
multi-instance (1.2), tilt control + external "sunny" switch (1.3), external "hot" +
external tilt (2.0), morning opening + min/max temperature + reopening modes (3.0),
tilt calibration + forecast fallback (4.0), per-cover sun angles + pre-close + stagger (5.0),
heat protection mode select + before-sunset/sunrise (6.0), cover→tilt delay (6.2).

---

## 2. Architecture

```
DataUpdateCoordinator (polls every 60 s)
  └─ AutomationEngine.run()            global gates + sensor gathering
       └─ CoverAutomation (per cover, recreated each cycle)
            ├─ evaluate()  → CoverExecutionPlan (desired position, reason, tilt target)
            └─ execute_plan() → HAInterface (service calls, logbook)
CoverPositionHistoryManager            cross-cycle state (history, ownership, override)
AutomationStateStore                   HA Store: ownership + daily temperature extrema
```

Key architectural facts:

- **Pure polling, no event listeners.** No `async_track_state_change_event` anywhere.
  A manual cover move, a window opening, or a weather change is noticed up to 60 s later.
- **All settings live in `ConfigEntry.options`**, including the state of every control
  entity (switch/number/select/time). Entities have no own state; they write options.
  Per-cover settings are flat keys `"{cover_entity_id}_{suffix}"` (no nesting, no schema;
  renaming a cover's entity_id silently orphans its settings).
- **Smart reload:** an options change whose keys are all flagged `runtime_configurable`
  only triggers a coordinator refresh; any other change (wizard) reloads the whole entry
  and loses in-memory state (position history, manual-override timers, evening one-shot).
- **Persistence:** HA `Store` per instance holds `automation_managed_states`
  (which covers the automation "owns", at which position, for which mode) and the
  day's running temperature max/min. Position history and override timers are memory-only.
- **Weather forecast service** (`weather.get_forecasts`, daily) is called every 60 s.
  Failures fall back to the last in-memory snapshot.
- **Sun position** comes from `sun.sun` attributes; astral is only used for
  the pre-close feature to sample future sun positions (15-min steps).
- Fatal error (entities unavailable): `sun.sun` missing. Everything else degrades softly.
- Setup errors return `False` (no `ConfigEntryNotReady` retry). `ConfigFlow.VERSION`
  never bumped; migrations re-run at every boot. No `diagnostics.py`, no repairs,
  no reconfigure step, no config subentries.

---

## 3. Decision pipeline

### 3.1 Global gates (engine, in order)

1. Config changed since last run → cancel pending deferred tilt tasks.
2. No covers / `enabled == off` / all cover states None → stop.
3. Gather sensors: sun azimuth+elevation (unusable → skip cycle), forecast today
   (max/min temperature, latched monotonic per day), current weather condition.
4. **Blocked time range** active → cancel pending work; if the range *just started*
   (or HA started within 10 min of the start) and pre-close is enabled →
   run pre-close pass; then stop. Nothing else runs during the blocked range.
5. Process covers (optionally staggered).

### 3.2 Per-cover order (`CoverAutomation.evaluate`)

1. Missing/invalid cover azimuth → skip. State unavailable/unknown → skip.
   State `opening`/`closing` → skip this cycle.
2. Read `supported_features`; position from `current_position`, or inferred 0/100
   from `closed`/`open` for binary covers.
3. **Lock mode** ≠ unlocked → handled entirely here, return (see 4.10).
4. **Manual override** active → skip (unless it is the evening-closure trigger cycle
   and "ignore manual override" is on).
5. Compute **sun hitting** (see 4.1).
6. **Movement decision**, priority chain:
   a. Evening closure trigger or keep-closed hold → close to evening position
      (window-open lockout → hold).
   b. Heat protection active → close to heat position (lockout → hold;
      already at/more closed → hold; automation-owned more-closed → open to target).
   c. Heat protection state unknown (weather unavailable while sun hits) → hold.
   d. Otherwise reopen branch: pre-closing → hold; evening-closure cover before
      morning opening → hold; else reopen per reopening mode (active/passive/off),
      never when sun elevation ≤ 0 except for morning opening.
7. If no reason → record history, no plan. Else plan = position (if change ≥ min delta)
   + tilt target.

Priority summary: **lock > window-open (closing only) > manual override >
evening closure > heat protection > reopening**; blocked range and `enabled` gate all.

---

## 4. Features in detail

### 4.1 Sun-hitting geometry

```
signed_diff = (sun_az - cover_az + 180) % 360 - 180          # [-180, 180)
hitting = elev_min <= sun_elev <= elev_max
          and -tol_start < signed_diff < tol_end
```
- `cover_azimuth` per cover (default 180 if the user never expands the wizard section).
- `tol_start`/`tol_end` per cover, fallback global `sun_azimuth_tolerance` (60°).
- `elev_min`/`elev_max` per cover, fallback global 0° / 90°.
- Per-cover angles model overhangs, trees, neighbouring buildings.
- No hysteresis at the boundaries.

### 4.2 Heat protection

- **Hot** = today's forecast `temp_max >= 24 °C` **and** `temp_min >= 13 °C`
  (both thresholds are number entities). The daily max can only rise and the min
  only fall during the day (latched, persisted) — prevents afternoon retraction when
  the forecast shifts to tomorrow. °F converted to °C.
- **Sunny** = current weather condition ∈ {`sunny`, `partlycloudy`}. No hysteresis.
- **Heat protection mode** (select): `off` / `auto` (hot ∧ sunny ∧ hitting) /
  `forced_sunny_windows` (hitting only) / `forced_all_windows` (elevation range only).
- **Target position** = per-cover `cover_max_closure` else global `covers_max_closure`
  (default 0 = fully closed). If the cover is already at or more closed than the target
  it is left alone (nap-room case, issue #157) unless the automation itself owns the
  more-closed position (e.g. after evening closure → opens to the heat target).
- **Reopen** target = per-cover `cover_min_closure` else global `covers_min_closure`
  (default 100); reopening never moves toward closed.
- Naming quirk: "max closure"/"min closure" are *positions* with 0 = closed, which the
  UI labels "maximum/minimum cover position" — a recurring source of confusion.

### 4.3 Automatic reopening (ownership model)

Select `automatic_reopening_mode`, default **passive**:
- `active`: always reopen when closing conditions end (also user-closed covers,
  after the override duration).
- `passive`: reopen only covers the automation closed **and** that still sit at the
  owned position (within min delta). User-closed covers stay closed.
- `off`: never reopen automatically.
Ownership (`AutomationManagedState{position, mode ∈ heat_protection|evening_closure|lock}`)
is persisted across restarts.

### 4.4 Manual override detection

- Per cover: deque(3) of `(position, cover_moved, timestamp, tilt)`; only the newest
  entry is compared to the live position/tilt each cycle.
- After each automation command a **settle window** of 120 s allows drift of
  `covers_min_position_delta` (5 %) / `tilt_drift_tolerance` (5 %).
- A deviation outside that → override; cover skipped for `manual_override_duration`
  (default 1800 s, number entity in minutes, likely capped at 100 by HA defaults).
- Quirks: timer is anchored to the last *automation-written* history entry, not the
  manual move; further manual moves do **not** extend the pause; a restart clears it;
  detection latency up to 60 s; tilt-only changes also count (v6.0).
- Evening closure may ignore an active override (default on) on its trigger cycle.
- Simulation mode has the inferred side effect of self-triggering overrides, since the
  physical cover does not move but history assumes it did.

### 4.5 Tilt control (covers with `SET_TILT_POSITION`)

Modes, separately for **day** and **night** (night = evening-closure movement, not sun
state), global with per-cover override:
`auto` (day default) / `manual` (re-assert user angle after a move) / `open` (100) /
`closed` (0, night default) / `set_value` (fixed, day 50 / night 0) / `external`
(managed number entity, global and per-cover, per-cover wins).

Auto formula (profile-angle slat cut-off, single tunable `d/L = 0.9`):
```
omega  = atan(tan(elev) / cos(azimuth_diff))       # profile angle
theta  = asin((d/L) * cos(omega)) - omega           # slat cut-off angle, clamped 0..90°
tilt%  = 100 * (1 - theta/90)                       # 100 = horizontal
```
mapped through `tilt_vertical_position` (0) / `tilt_horizontal_position` (100)
calibration for covers that invert HA's convention. No tilt is ever set when the
cover is fully open (100). Supporting settings: `tilt_min_change_delta` (5 %),
`tilt_open_to_cover_open_delay` (open slats first, reopen cover later; auto-day only),
`cover_movement_to_tilt_delay` (tilt command after position command; Somfy/KNX fix),
`tilt_drift_tolerance` (5 %). Slats keep tracking the sun every minute while heat
protection holds.

### 4.6 Evening closure, keep closed, morning opening

- Enable + **subset of covers** + mode `after_sunset` (default, +15 min) /
  `before_sunset` / `fixed_time` / `external` (managed time entity).
- Trigger is a **one-shot inside a 10-minute window** after the computed time
  (in memory; missed if HA is down for the whole window; re-fires after restart in window).
- Closes to per-cover/global `evening_closure_max_closure` (default 0); night tilt mode.
- **Keep closed** (default on): re-closes evening covers that get reopened overnight,
  once the manual override has elapsed.
- **Morning opening**: `fixed_time` (default 08:00) / `before_sunrise` /
  `after_sunrise` / `external`. Between evening closure and morning opening all
  reopening of evening covers is blocked; at morning time they reopen subject to the
  reopening mode and heat protection (v6.1: only if min position is more open).

### 4.7 Blocked time range ("night silence") + pre-close

- Disabled by default; `fixed_time` (22:00–08:00) or `external` (two managed time
  entities). Inside the range **nothing moves** (all covers, all reasons).
- **Pre-close** (default on): at range start, sample sun positions from next sunrise
  to range end (15-min steps), fetch that day's forecast; if hot ∧ sunny (or forced
  mode) close every cover the sun will hit before the range ends → silence in the
  morning while still protecting from heat.

### 4.8 Window-sensor lockout

Per-cover list of binary sensors. Any `on` → closing (heat, evening, keep-closed)
is held; opening is never blocked. Not latched; `force_close` lock ignores it.

### 4.9 Lock mode

Select + service `smart_cover_automation.set_lock` (targeted per instance or broadcast):
`unlocked` / `hold_position` / `force_open` (→100, tilt 100) / `force_close` (→0, tilt 0).
Global per instance. Bypasses min delta and window lockout; no logbook entry.
Wind/hail protection is **not** built in — the docs provide a recipe: per-source toggle
helpers with hysteresis automations → template binary sensor → automation calling
`set_lock`.

### 4.10 External control ("bring your own signal")

Tri-state switches disabled in the registry by default; enabling one replaces the
forecast-derived value: `Weather: sunny?` (global only), `Weather: hot?` (global +
per cover). Documented use: pyranometer/lux meter, PV output (with a supplied
clear-sky model + Jupyter calibration notebook), indoor temperature.
Managed entities for external tilt (day/night, global/per cover) and external times
(evening closure, morning opening, blocked range start/end) are created only while the
corresponding mode is `external`, and removed again otherwise.

### 4.11 Movement layer

- Position 100 → `cover.open_cover` (fixes blinds that keep tilt otherwise, #163);
  else `set_cover_position`; binary covers → open if >50 else close.
- Moves smaller than `covers_min_position_delta` (5 %, **no UI**) are skipped.
- `cover_movement_stagger_delay` (0–3600 s) offsets the n-th cover by n×delay via
  cancellable tasks deduped by plan signature.
- Fire-and-forget service calls, no read-back verification.
- Every position move writes a translated **logbook entry** attached to the instance's
  status binary sensor (not the cover): "Closing cover.x to protect from heat. New
  position: 0%." Reasons: heat protection, end heat protection, end manual override,
  let light in, privacy at night, end overnight privacy, keep privacy overnight.

### 4.12 Multi-instance

One config entry = one instance = one device with its own entities, Store, logger
prefix (last 5 chars of entry_id). Same cover may be in several instances (by design).
Used to give groups of windows different thresholds. `set_lock` broadcasts when
untargeted.

### 4.13 Observability

Switches: enabled, simulation mode, verbose logging (per instance).
Binary sensors: status (problem), evening closure enabled, weather hot, weather sunny,
lock status. Sensors: sun azimuth, sun elevation, today's max/min temperature, evening
closure mode/time, morning opening mode/time, blocked range. **No per-cover entity
of any kind** (no "sun hitting", "target", "override active", "reason").

---

## 5. Configuration surface

Wizard (6 linear steps, no back navigation; changing one value means walking all six):
1. Weather entity + covers.
2. Per cover: azimuth, tolerance start/end, elevation min/max (text fields, section per
   setting listing all covers).
3. Global + per-cover max position, min position, evening position.
4. Tilt: day/night mode global + per cover, set values, min change, overlap ratio,
   delays, H/V calibration, drift tolerance.
5. Stagger delay; per-cover window sensors.
6. Blocked range (+mode, +pre-close), evening closure (+mode, time, covers, ignore
   override, keep closed), morning opening (+mode, time), reopening mode.

Device-page entities (runtime-changeable, automatable): enabled, simulation, verbose,
lock mode, reopening mode, heat protection mode, override duration, temperature
thresholds ×2, azimuth tolerance, elevation min/max, external sunny/hot switches,
external tilt numbers, external time entities.

Hidden (storage only): `covers_min_position_delta`.

---

## 6. Known problems and pain points

From the code:
- 60 s polling everywhere; manual moves/window events seen late; evening trigger can be missed.
- Position is binary per regime (heat target vs open target); no sun-tracking
  intermediate positions, no shadow-depth model (window height, sill, overhang depth).
- No hysteresis on sunny/azimuth/elevation → flapping on intermittent clouds is
  limited only by the 5 % delta.
- Only one global weather signal; indoor/room temperature, lux, wind, rain, presence
  must all be wired by the user through external switches and their own automations.
- Lock, blocked range and reopening mode are global per instance; per-cover
  differences require additional instances.
- Manual override semantics are non-obvious (anchor, no extension, lost on restart).
- Two parallel decision vocabularies plus legacy mapping code; dead legacy methods;
  per-cycle recreation of per-cover objects.
- Config data model: flat prefixed keys, text selectors without min/max, defaults
  bleeding across covers, dual-meaning time field, confusing min/max naming.

From users (issues + forum):
- Expected gradual, Adaptive-Cover-style positioning (#148, forum).
- Wanted indoor temperature / lux / current outdoor temperature inputs (#70, #71, #78, #94)
  — answered with external switches, which several users found to be "not all-in-one".
- Weekday vs weekend schedules (#91) — answered with external time entities.
- Confusion about orientation config and about how manual override/temporary
  disable works (forum).
- Somfy/KNX covers cancelling a movement when a tilt command follows immediately (#215).
- Tilt-only covers (SwitchBot) unsupported (#138).
- Maintainer's stated philosophy: not an all-in-one; wire extra signals yourself.

---

## 7. What competitors do differently

**Adaptive Cover** (basbruss): three cover geometries (vertical blind, horizontal
awning, venetian tilt) with real dimensions (window height, glare zone distance,
slat depth/spacing, awning length/angle) → computes a **continuous target position**
that keeps direct sun off a work area; field-of-view left/right, blind spots; climate
mode with indoor temp, outdoor temp, presence, weather, lux, irradiance thresholds
(winter: open for solar gain; summer: close/filter); "transparent blind" option;
start/end times with sunrise/sunset offsets; manual override with reset timer,
threshold and reset button; min delta position/time; security mode (close when nobody
home); per-cover sensors (target position, control method, sun start/end times,
override) and switches; aggregate hub entity + voice-assistant-friendly commands.

**Cover Control Automation blueprint** (hvorragend): time or calendar based
open/close with workday/non-workday; sun shading with azimuth, elevation, lux with
hysteresis, outdoor/forecast/indoor temperature and weather conditions; up to 4
elevation-based shading positions incl. tilt; ventilation position when window is
tilted vs opened; lockout when door open; presence/resident sensors; drive delays;
position tolerance; manual override via text helper; force close for rain/wind/frost
with state tracking; status/debug entities.

---

## 8. Sources

- Docs: https://ha-smart-cover-automation.helgeklein.com/
- Repo: https://github.com/helgeklein/ha-smart-cover-automation (v6.3.0, 2026-09)
- Forum: https://community.home-assistant.io/t/smart-cover-automation-new-custom-integration/948843
- Adaptive Cover: https://github.com/basbruss/adaptive-cover
- CCA blueprint: https://github.com/hvorragend/ha-blueprints
