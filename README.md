# Cover Automation for Home Assistant

Drives roller covers fully open or fully closed from sun position, weather forecast, room
temperature, wind, frost, door sensors and per-cover schedule profiles, event-driven, with
per-cover configuration through Home Assistant config subentries.

Minimum Home Assistant: 2026.8.

## Install

This is a private HACS custom repository.

1. In Home Assistant, open **HACS** → the **⋮** menu (top right) → **Custom repositories**.
2. Paste `https://github.com/pujux/hass-cover-automation`, pick category **Integration**, and
   select **Add**.
3. Open the new **Cover Automation** entry and select **Download**.
4. Restart Home Assistant.
5. Settings → Devices & services → Add integration → **Cover Automation**.

Alternatively, copy `custom_components/cover_automation` into your `config/custom_components/`
and restart Home Assistant.

Needs the `sun` integration (included in `default_config`) and a weather integration that
provides a daily forecast.

## Configure

1. **Hub**: pick your weather entity (must provide a daily forecast), optionally a wind sensor
   and an outdoor temperature sensor, then the thresholds.
2. **Schedule profiles** (optional): Add a *schedule profile* subentry with up to four rules
   (close at a time or relative to sunset, open once at a time or relative to sunrise) and
   optional quiet hours.
3. **Covers**: Add one *cover* subentry per cover: window azimuth and sun tolerances, shading
   rule, room temperature sensor, door sensor, wind thresholds and a schedule profile.

Every subentry can be edited on its own from the integration page.

## How it decides

Every cover is evaluated on its own whenever an input changes (sun, weather, sensors, door,
the cover itself) and on the schedule's own timers. Layers are checked in this order, and the
first one with an opinion wins:

1. **Frost** — below the frost threshold nothing moves at all, in either direction.
2. **Wind** — above the per-cover upper threshold the cover takes its wind action and is held
   there until the wind stays below the lower threshold for the hold time.
3. **Door** — while the door/window sensor of a cover is open, the cover is not closed.
4. **Quiet hours** — inside a profile's quiet hours the automation does not move the cover.
5. **Schedule** — a profile's close rule holds the cover closed until the next open rule; an
   open rule is one-shot and gives up once the cover is open or its window has passed.
6. **Shading** — on a hot day, while the sun actually hits the window and the room calls for
   it, the cover closes; it reopens when the sun leaves the window or the day cools off.

Covers are only ever driven fully open (100) or fully closed (0) — there is no tilt and no
intermediate position. When you move a cover yourself away from what the automation asked
for, that cover goes into **manual override** and the automation stops driving it. The
override ends when one of these happens:

- the automation's own desired state stays the opposite of what you overrode for the whole
  override dwell time;
- the sun leaves the window, for an override against a sun-driven shading close;
- a schedule rule fires for that cover;
- you press the cover's *Reset override* button or call `cover_automation.reset_override`.

## Entities

Per hub (diagnostic unless noted):

- `binary_sensor`: *Hot day*, *Sunny*, *Frost protection active*, *Any wind protection
  active*, *Problem* (on while this integration has an open repair issue).
- `sensor`: *Forecast high today*, *Forecast low today*, *Next scheduled event* (with the
  profile, action and affected covers as attributes).
- `select` (config): *Shading mode* (off / automatic / forced on sunlit covers / forced on all
  covers) and *Reopening mode* (active / passive / off).
- `switch` (config): *Simulation mode* and *Verbose logging*.
- `button` (config): *Evaluate now*.

Per cover:

- `sensor`: *Status* — the winning layer in one value (`closed_shading`, `open_no_shade`,
  `held_frost`, `protected_wind`, `door_open`, `quiet_hours`, `schedule_hold`,
  `manual_override`, `disabled`, `cover_unavailable`, `command_failed`, `unconfirmed`,
  `partial`, `idle`) plus the full reasoning as attributes.
- `binary_sensor`: *Manual override*, *Sun hits window* and *Wind protection active*.
- `select` (config): *Mode* — automatic, darkening only, or protection only.
- `switch` (config): *Automation enabled*.
- `button` (config): *Reset override*.

## Services

- `cover_automation.reset_override` — clears the manual override on the targeted covers.
- `cover_automation.evaluate_now` — re-evaluates the targeted covers immediately.

Both accept the usual Home Assistant targets (entity, device, area, label); without a target
they apply to every configured cover.

## Simulation mode and the logbook

Turn **Simulation mode** on and the integration keeps evaluating and reporting exactly as it
would normally — status, override, next event — but never sends a command to a cover. It is
the safe way to watch what the automation *would* do before letting it drive.

Every command the integration sends is written to the Home Assistant logbook against the
cover entity, with the layer and reason that produced it ("closed for shading: sun hits
window", "opened for schedule: open rule fired", and "… (simulated)" while simulation mode is
on), so the reason a cover moved is visible next to the movement itself. **Verbose logging**
additionally raises the integration's own logger to debug level, which logs the full
per-evaluation reasoning to the Home Assistant log.

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/pyright
```

Design spec: `docs/superpowers/specs/2026-09-15-cover-automation-design.md`.
