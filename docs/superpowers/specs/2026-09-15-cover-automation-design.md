# Cover Automation Integration — Design Spec

Date: 2026-09-15. Status: approved section by section in brainstorming; awaiting final review.
Related: `docs/design-decisions.md` (decision log), `docs/feature-selection.md` (chosen
features), `docs/reference/smart-cover-automation-analysis.md` (analysis of the integration
being replaced).

## 0. Scope

A Home Assistant custom integration, domain **`cover_automation`**, that drives roller-type
covers to **fully open or fully closed only** based on sun position, weather forecast, room
temperature, wind, frost, door sensors and per-cover schedule profiles. Event-driven.
Per-cover configuration through config subentries. Transparent through per-cover status
entities, logbook entries, repairs and diagnostics. Single hub instance per Home Assistant.

**Non-goals:** tilt/slat control; intermediate positions or sun-tracking positions;
lux/irradiance sensors; presence; rain or hail logic; workday/calendar schedules;
translations beyond English (may follow later); multiple hub instances.

## 1. Layer stack

Every relevant event triggers one evaluation per cover. The evaluation walks the layers
top to bottom; the first layer with an opinion sets the **desired state** — `closed`,
`open`, or `leave_alone` — plus a reason. An **act gate** then decides whether the engine
may make the actual state match the desired state now.

Per-cover **mode select**: `auto` | `dark_only` | `protection_only`.
Per-cover **enable switch**: full kill switch above everything, including protection
(intended for maintenance).

Layers in precedence order:

1. **Frost.** Outdoor temperature below the frost threshold (default 0 °C; released at
   threshold + 1 K) → `leave_alone` for every cover. Evaluation continues for status only.
   If the wind layer would want a cover open while frost is active, raise a persistent
   notification and a repair issue for that cover.
2. **Wind.** Wind value ≥ the cover's upper threshold → protection active. It releases
   once the value has stayed below the cover's lower threshold for the hold time.
   Opinion: `open`, or `leave_alone` if the cover's wind action is `hold`. Ignores
   overrides, doors, schedules and quiet hours.
3. **Door.** Door sensor `on` → `open`. Nothing below this layer may close a cover on an
   open door.
4. **Quiet hours** (from the cover's schedule profile) → `leave_alone`.
5. **Schedule hold.** A profile rule that has fired and is not yet superseded by the next
   rule, nor released by a manual move on this cover → `closed` or `open` per the rule.
6. **Shading** — only in mode `auto` or `dark_only`, and only when the hub shading mode
   is not `off`.
   `want_shade = sun_hits AND sunny AND NOT room_cold AND (hot_day OR room_hot)`
   (default rule `forecast_with_room`; per-cover alternatives: `room_only` =
   `sun_hits AND sunny AND room_hot`; `either` = `sun_hits AND sunny AND (hot_day OR
   room_hot)` without the comfort floor).
   Hub shading mode `forced_sunlit` → `want_shade = sun_hits`;
   `forced_all` → `want_shade = sun elevation within the cover's elevation range`.
   Mode `auto`: `closed` if want_shade else `open`.
   Mode `dark_only`: `closed` if want_shade else `leave_alone`.
7. **Default** → `leave_alone`.

**Act gate** — applied when desired is `open` or `closed` and differs from actual:

1. Frost active → never move.
2. Desired comes from the wind layer → move immediately.
3. Manual override active → do not move. (Definition in §1.2.)
4. Reopening mode (hub select, default `passive`):
   `passive` → send `open` only if the engine owns the current state, or the request
   comes from the schedule or door layer. `active` → open regardless. `off` → never open
   for shading reasons.
5. Minimum interval between engine moves, per cover, applies to **shading-layer moves
   only**; if too soon, schedule a re-evaluation at the earliest allowed time.
6. Simulation mode → log the command instead of sending it.

### 1.1 Actual state and transition classification

Actual state of a cover: `closed` if HA state is `closed` or position ≤ tolerance;
`open` if HA state is `open` and position ≥ 100 − tolerance (or no position attribute);
`partial` otherwise; `moving` while HA state is `opening`/`closing` (no classification
until settled); `unavailable` when unavailable/unknown.

Every settled cover state change is matched against a pending engine command:
- **Match** → the engine owns the new state (`owner = engine`, `engine_target` = state).
- **No match** → manual move: `owner = user`, `manual_move_at = now`,
  `desired_at_manual_move = current desired` (or `null` when desired is `leave_alone`).
  Any schedule hold on that cover is thereby released (see §1.3). `partial` counts as a
  manual state that is neither open nor closed.

### 1.2 Manual override

An override is active exactly when
`owner == user AND desired ∈ {open, closed} AND desired == desired_at_manual_move AND actual ≠ desired`.

Consequences: the override ends automatically as soon as the desired state changes
(decision 5); while desired is `leave_alone` the override status is frozen; a passing
cloud does not end it because debounce keeps desired stable.

Two explicit clears:
- **Schedule rule fires** → `desired_at_manual_move = null` for every cover in that
  profile. Schedules are authoritative even when the desired *state* does not change,
  only the reason (e.g. user opened at 20:00 against shading; rule closes at 21:30).
- **Reset button / service** → `owner = engine`, `engine_target = actual`,
  `desired_at_manual_move = null`. The engine may then act on the next evaluation.

### 1.3 Schedule hold and release

A rule holds from its fire time until the next rule of the profile fires. It is
**released** for a cover when `owner == user AND manual_move_at > rule fire time`.
A released cover is governed by the layers below the schedule layer until the next rule.

### 1.4 Approved defaults

Reopening mode `passive`. Door above quiet hours (a terrace door opened at night lifts
its cover). Min interval exempts wind, door and schedule moves.

## 2. Inputs and signal processing

- **Sun hits window** (per cover): azimuth inside [az − tol_left, az + tol_right] and
  elevation inside [elev_min, elev_max]. Hysteresis: on only when strictly inside on both
  axes; off only when outside by the margin (default 2°) on either axis. Source: `sun.sun`
  attributes, which HA updates adaptively. No own astronomy for this.
- **Sunny**: weather entity condition ∈ configurable set (default `sunny`,
  `partlycloudy`), debounced: on after continuously true for the on-delay (default
  10 min), off after continuously false for the off-delay (default 20 min). Weather entity
  unavailable longer than the grace period (default 30 min) → sunny = unknown → shading
  layer yields `leave_alone`.
- **Hot day**: daily forecast (`weather.get_forecasts`, type daily) fetched at startup,
  hourly, and after a weather entity state change (throttled). Within the local day,
  today's max only rises and today's min only falls; both persisted.
  `hot_day = max ≥ high threshold (24 °C) AND (low threshold disabled OR min ≥ low
  threshold (13 °C))`.
- **Override entities**: optional `sunny_override_entity` and `hot_override_entity` in
  the hub config; if set, their on/off state replaces the computed value.
- **Room temperature** (per cover, optional sensor): `room_cold = temp < floor`
  (default 21 °C); `room_hot = temp ≥ ceiling` (default 25 °C); 0.5 K hysteresis on
  both. Sensor unavailable → neither cold nor hot; status shows `degraded`.
- **Wind**: one hub sensor (speed or gust, any unit; thresholds are entered in that
  unit). Per cover: `wind_enabled`, upper, lower, hold time (default 15 min), action
  `open` | `hold`. Sensor unavailable → protection state frozen + repair issue.
- **Frost**: outdoor temperature from a sensor entity or the weather entity's
  `temperature` attribute. Threshold default 0 °C, release at +1 K.
- **Evaluation triggers**: state changes of covers, door sensors, room sensors, wind,
  outdoor temperature, weather entity, `sun.sun`, override entities, runtime control
  entities. Timers: debounce expiry, wind hold expiry, next schedule rule, min-interval
  retry, local midnight rollover, 5-minute fallback tick. Sunrise/sunset-relative rule
  times recomputed once per local day.

## 3. Configuration model

One **hub config entry** (`single_config_entry`) + two **config subentry types**, each
with create and reconfigure flows.

**Hub entry** (two-step config flow; options editable later):
`weather_entity` (required, domain weather); `wind_sensor` (optional; absent → wind layer
disabled and wind fields hidden in cover subentries); `outdoor_temperature_source`
(sensor entity; default = weather entity temperature attribute); `frost_threshold`
(0 °C); `sunny_conditions` (default sunny, partlycloudy); `sunny_on_delay` (10 min);
`sunny_off_delay` (20 min); `weather_grace` (30 min); `hot_high_threshold` (24 °C);
`hot_low_threshold` (13 °C) + `hot_low_enabled` (true); `sunny_override_entity`,
`hot_override_entity` (optional); `sun_hysteresis_margin` (2°); `open_closed_tolerance`
(5 %).

**Cover subentry** (one per cover; creates its own device linked `via_device` to the hub
device): `cover_entity` (required); `name` (default: cover friendly name); `azimuth`
(0–359); `tolerance_left`, `tolerance_right` (default 60°); `elevation_min` (0°),
`elevation_max` (90°); `shading_rule` (`forecast_with_room` | `room_only` | `either`);
`room_temperature_sensor` (optional); `comfort_floor` (21 °C); `comfort_ceiling`
(25 °C); `door_sensor` (optional, binary_sensor); `wind_enabled`; `wind_upper`;
`wind_lower`; `wind_hold` (15 min); `wind_action` (`open` | `hold`); `schedule_profile`
(reference to a profile subentry id, optional); `min_move_interval` (10 min).
Mode and enabled are runtime entities, not subentry data.

**Schedule profile subentry**: `name`; `rules` — a list (UI exposes up to 4 slots for
now), each `{action: close|open, time_mode: fixed|sunrise|sunset, time (fixed) or
offset_minutes (sun-relative), earliest?, latest? (clamps for sun-relative rules)}`;
optional `quiet_hours {start, end}` (may span midnight).

Renaming a cover entity does not lose settings (the subentry is the identity; update the
entity field in its reconfigure flow). Entry data carries `version`/`minor_version`;
migrations run once via `async_migrate_entry`. Deleting a profile still referenced by a
cover raises a repair issue; that cover behaves as if it had no profile.

## 4. Entities, services, observability

**Hub device:** select `shading_mode` (off | auto | forced_sunlit | forced_all; default
auto); select `reopening_mode` (active | passive | off; default passive); switch
`simulation_mode`; switch `verbose_logging` (sets integration logger to DEBUG while on);
diagnostic binary sensors `hot_day`, `sunny` (debounced), `frost_active`,
`any_wind_protection_active`, `problem` (on while any repair issue of this integration is
open); diagnostic sensors `forecast_max_today`, `forecast_min_today`,
`next_scheduled_event` (timestamp; attributes profile, action, covers); button
`evaluate_now`.

**Per-cover device:** switch `enabled`; select `mode` (auto | dark_only |
protection_only); sensor `status` (enum: `closed_shading`, `open_no_shade`,
`held_frost`, `protected_wind`, `door_open`, `quiet_hours`, `schedule_hold`,
`manual_override`, `command_failed`, `unconfirmed`, `degraded`, `disabled`, `idle`,
`unavailable`; attributes: desired_state, actual_state, winning_layer, reason, sun_hits,
sunny, hot_day, room_state, wind_state, active_rule, next_planned_action (+time),
last_engine_move, owner); binary sensor `manual_override` (attributes: since,
manual_state, desired_state); button `reset_override`; diagnostic binary sensors
`sun_hits`, `wind_protection_active`.

**Services:** `cover_automation.reset_override` (target: cover status entities; no target
= all covers); `cover_automation.evaluate_now`.

**Logbook:** every engine command fires event `cover_automation_action` with
`{entity_id (cover), action, reason, layer}`. `logbook.py` describes it, e.g. "Closed
Bedroom for shading: sun hits, hot day", attributed to the cover entity itself.

**Repairs** (non-fixable, auto-clearing): wind wanted open during frost (per cover, plus
persistent notification); wind sensor unavailable; weather unavailable beyond grace;
configured cover or door sensor missing; cover references a deleted profile; three
consecutive command failures on a cover.

**Diagnostics** (`diagnostics.py`): hub config, all subentries, per-cover evaluation
snapshot, persisted state. Nothing to redact.

## 5. Persistence, startup, error handling

**Store** (one per hub entry; delayed save after changes, immediate save on unload,
deleted on entry removal):
- Per cover, five scalars: `owner` (engine | user); `engine_target` (open | closed |
  null); `manual_move_at` (timestamp | null); `desired_at_manual_move` (open | closed |
  null); `wind_active` (bool).
- Hub: forecast latch `{date, max, min}`.
- Runtime entity states (hub selects/switches, per-cover enabled/mode) use
  `RestoreEntity`, not the Store.
- Recomputed from live sensors, never stored: sunny debounce, frost, sun hits, command
  backoff, schedule hold/release status (derived per §1.3). The wind hold timer restarts
  after a restart.

**Startup:** weather entity missing → `ConfigEntryNotReady` (HA retries). Missing optional
sensors → repair issue, continue. Load Store, subscribe listeners, first evaluation after
`EVENT_HOMEASSISTANT_STARTED`. A cover that is unavailable is skipped until it reports a
state.

**Reconcile after downtime:** if actual == `engine_target` → engine keeps ownership; else
`owner = user`, `manual_move_at = now`, `desired_at_manual_move = current desired`
(so an override exists iff actual ≠ desired).

**Command failures:** service call raises → log warning, status `command_failed`, one
retry after 30 s. No settled transition observed within 120 s of sending → status
`unconfirmed`, pending record dropped; subsequent re-sends of the same target back off by
doubling the min interval up to 1 h. Three consecutive failures → repair issue. An
exception while evaluating one cover is logged and isolated from other covers.

**Config changes:** subentry or hub option change → entry reload. Runtime entity change →
update engine state + evaluate, no reload. Timezone/location change
(`EVENT_CORE_CONFIG_UPDATE`) → recompute sun-relative timers.

## 6. Code structure, testing, tooling

**Layout** `custom_components/cover_automation/`:
- `engine/` — pure Python, no HA imports: `model.py` (enums, dataclasses for inputs,
  decision, per-cover persisted state), `layers.py` (layer stack + act gate), `signals.py`
  (hysteresis, debounce, daily latch primitives), `sun.py` (sun-hits geometry),
  `schedule.py` (rule fire times with sunrise/sunset + clamps, hold/release, quiet hours).
- `controller.py` — HA binding: subscriptions, timers, act gate execution, pending
  command tracking, Store access, repair issues, event firing.
- `config_flow.py` (hub flow + cover and profile subentry flows), `__init__.py`,
  `const.py`, `store.py`, entity platforms `switch.py`, `select.py`, `sensor.py`,
  `binary_sensor.py`, `button.py`, plus `logbook.py`, `diagnostics.py`, `services.yaml`,
  `strings.json`, `translations/en.json`, `manifest.json`.
- Repo root: `hacs.json`, `README.md`, `pyproject.toml`, `requirements_test.txt`,
  `.github/workflows/` (hassfest, HACS validation, tests), `tests/`.

**Targets:** minimum Home Assistant 2026.4 (subentries stable; matches the target
installation), tested against the current release. Python as required by that HA version.

**Tooling:** venv with pinned `pytest-homeassistant-custom-component`; ruff (lint +
format); pyright strict on `engine/`; GitHub Actions. HACS-installable as a custom
repository; version in `manifest.json`, GitHub releases.

**Testing:** table-driven unit tests for `engine/` (layer precedence incl. frost over wind
and door over quiet hours; every hysteresis/debounce edge; override lifecycle in active
and passive modes incl. the schedule-clears-override case; schedule hold/release; startup
reconciliation; actual-state classification with tolerance). A **day-replay harness**
feeding a synthetic sun path, weather sequence and room temperatures through the engine
and asserting the command sequence. Integration tests with the HA test harness for config
flows, entities, and state-change → service-call paths. Test-driven development
throughout.
