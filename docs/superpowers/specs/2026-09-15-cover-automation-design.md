# Cover Automation Integration — Design Spec

Date: 2026-09-15. Revision 3.2 (after two review rounds and the engine implementation's whole-branch review, see `docs/reviews/`).
Related: `docs/design-decisions.md` (decision log, #1–#29), `docs/feature-selection.md`,
`docs/reference/smart-cover-automation-analysis.md`.

## 0. Scope

A Home Assistant custom integration, domain **`cover_automation`**, that drives roller-type
covers to **fully open or fully closed only** based on sun position, weather forecast, room
temperature, wind, frost, door sensors and per-cover schedule profiles. Event-driven.
Per-cover configuration through config subentries. Transparent through per-cover status
entities, logbook entries, repairs and diagnostics. Single hub instance per Home Assistant.
**Minimum Home Assistant 2026.8** (decision 11).

**Non-goals:** tilt/slat control; intermediate positions or sun-tracking positions;
lux/irradiance sensors; presence; rain or hail logic; workday/calendar schedules;
translations beyond English; multiple hub instances; integration-provided automation
triggers/conditions (mature in 2026.8 but not selected; the event model in §4 does not
preclude adding a `trigger.py` later).

## 1. Decision model

### 1.0 Vocabulary

- **Actual state** of a cover, classified from the HA state object:
  `closed` if HA state is `closed` or `current_position ≤ tolerance`;
  `open` if HA state is `open` and (`current_position ≥ 100 − tolerance` or no position
  attribute); `moving` while HA state is `opening`/`closing`; `cover_unavailable` when
  `unavailable`/`unknown`; `partial` otherwise. Tolerance = hub `open_closed_tolerance`
  (default 5 %). Only **changes of the classified state** are events; a raw change that
  does not change the classification (e.g. 100 → 97) is ignored.
- **Settled** state: `open`, `closed` or `partial`. **Contrary** to a pending target: a
  settled `open` or `closed` that is the opposite end state of the target. `partial`,
  `moving` and `cover_unavailable` are never contrary (many covers report `open` at every
  intermediate position because they implement no travel flags).
- **Desired state**: the output of the layer stack for one cover in one evaluation:
  `closed`, `open`, or `leave_alone`, plus the winning layer and a reason. Every evaluation
  additionally computes the **wind opinion** and the **door opinion** unconditionally and
  carries them alongside the desired state (used by gate 2 for notifications).
- **Per-cover persisted state** (§5): `owner` (engine|user), `engine_target`
  (open|closed|null), `manual_move_at`, `desired_at_manual_move` ("dam", open|closed|null),
  `dam_layer` (shading|other|null), `wind_active`, `enabled`, `mode`.
- **Engine owns the current state** ⇔ `owner == engine AND engine_target == actual`.
- **Override active** ⇔ `dam ≠ null`. An override blocks engine commands whose target
  equals `dam` (§1.3 gate 5); its lifecycle is §1.5.
- **Last completed evaluation**: the desired state, winning layer, wind opinion and door
  opinion produced by the most recent evaluation for the cover. Manual moves are judged
  against it (never against a value computed during the same event).

### 1.1 Modes and the enable switch

Per-cover **enable switch** off: the engine never commands the cover, no timers run for it,
status is `disabled`. Classified transitions are still recorded as manual moves without
creating overrides (`dam` stays null), so re-enabling starts from a consistent ownership.

Per-cover **mode select** decides which layers are consulted:

| Mode | Frost | Wind | Door | Quiet hours | Schedule | Shading |
|---|---|---|---|---|---|---|
| `auto` | ✓ | ✓ | ✓ | ✓ | ✓ | closed / open |
| `dark_only` | ✓ | ✓ | ✓ | ✓ | ✓ | closed only (never opens) |
| `protection_only` | ✓ | ✓ | ✓ | – | – | – |

Documented consequences: an explicit `open` schedule rule does open a `dark_only` cover
(rules are explicit instructions); hub shading mode `forced_all` with a `dark_only` cover
closes it at sunrise and never reopens it automatically; hub shading mode `off` leaves
covers already closed for shading closed until a schedule rule, a manual move or a reset.

### 1.2 Layer stack

Every relevant event (§2) triggers one evaluation per cover. Layers are walked top to
bottom; the first layer with an opinion sets the desired state.

1. **Frost.** `frost_active` (§2) → `leave_alone` for every cover. `frost = unknown`
   (source unavailable beyond grace) → `leave_alone` for the door, schedule and shading
   layers; the wind layer is still consulted unless the last known outdoor temperature was
   ≤ `frost_threshold + 1 K` (decision 19). If the wind or door opinion is `open` while
   frost is active (or unknown and near freezing), raise a persistent notification and a
   repair issue for that cover, once per episode (decisions 6, 16).
2. **Wind.** `wind_active` for this cover (§2) → `open`, or `leave_alone` if the cover's
   `wind_action` is `hold`. Wind ignores overrides, doors, schedules, quiet hours and the
   minimum interval. **Restoring move:** when wind releases and the layers below want a
   state different from actual, the first command sent within 15 minutes of the release is
   exempt from quiet hours; the exemption lapses if the layers below stop disagreeing.
3. **Door.** Door sensor `on` → `open`. Door sensor `unavailable`/`unknown` →
   `leave_alone` (never close on a dead sensor; never force open either) plus a repair
   issue. Nothing below this layer may close a cover on an open door.
4. **Quiet hours** (from the cover's profile) → `leave_alone`.
5. **Schedule** (decision 13). Two rule kinds:
   - A **close rule** that fired at T holds `closed` until the next rule of the profile
     fires or until `manual_move_at > T` (decision 22; `manual_move_at` is written only by
     manual moves and never cleared, so a release is sticky until the next rule).
   - An **open rule** that fired at T is a **one-shot**: it yields `open` while the cover has
     not yet been observed open since T, for at most 15 minutes after T, and only until the
     next rule fires or `manual_move_at > T`. Once the cover has been observed open the rule
     is satisfied and has no further opinion, so a later shading close is not undone
     (decision 24). The satisfied marker is runtime-only; a restart within the 15-minute
     window while the cover is closed may re-open it once.
   - Any rule firing clears `dam` and `dam_layer` for every cover of the profile (schedules
     are authoritative, decision 10), even when the desired state does not change.
   - Two consecutive close rules are legal and act as a re-close (documented escape hatch).
   - A fixed-time rule inside its own profile's quiet hours is rejected at config time. A
     sun-relative rule that lands inside quiet hours on a given day is **clamped** (close
     rules to one minute before quiet hours start, open rules to quiet hours end); it is
     skipped that day with a repair issue only if no valid clamp exists (decision 23).
6. **Shading** (decision 14) — only while sun elevation > 0, mode `auto` or `dark_only`,
   hub shading mode ≠ `off`. Otherwise `leave_alone`.
   Per-cover `shading_rule`:
   - `forecast_with_room` (default): `want_shade = sun_hits AND sunny AND NOT room_cold
     AND (hot_day OR room_hot)`
   - `room_only`: `want_shade = sun_hits AND sunny AND room_hot` (requires a room sensor;
     rejected at config time otherwise; an unavailable sensor makes the input unknown →
     `leave_alone`, decision 29)
   - `either`: `want_shade = sun_hits AND sunny AND (hot_day OR room_hot)`
   Hub shading mode `forced_sunlit` → `want_shade = sun_hits`; `forced_all` →
   `want_shade = elevation within the cover's range`. Forced modes bypass the comfort floor
   by design. If any input the rule needs is `unknown` (sunny, hot_day), the layer yields
   `leave_alone`.
   `auto`: `closed` if want_shade else `open`. `dark_only`: `closed` if want_shade else
   `leave_alone`.
7. **Default** → `leave_alone`.

### 1.3 Act gate

Evaluated when desired ∈ {open, closed} and desired ≠ actual. Produces `send`, `defer(t)`
or `suppress`, in this order:

1. Cover disabled → suppress.
2. Frost active → suppress; frost unknown → suppress unless the request comes from the wind
   layer and the last known temperature was not near freezing (§1.2 layer 1). Wind or door
   requests suppressed here raise the notification and repair issue once per episode.
3. Desired from the **wind** layer → send.
4. Desired from the **door** layer → if an override is active and `manual_move_at` is
   later than the door sensor's `last_changed`, suppress (a manual move while the door was
   open is respected, decision 15); otherwise send. Door moves ignore the minimum interval.
5. Override active and command target == `dam` → suppress.
6. Desired `open` from the **shading** layer, by hub reopening mode: `passive` (default) →
   send only if the engine owns the current state; `active` → send; `off` → suppress.
   Schedule-layer opens are not subject to reopening mode (schedules are authoritative).
7. Actual is `moving` → suppress until settled (wind and door excepted); the controller
   surfaces this as the pending move and re-evaluates on the settle transition.
8. Shading-layer moves only: minimum interval since the **last engine command of any layer**
   on this cover (`min_move_interval`, default 10 min) → defer to the earliest allowed time
   (decision 21; this also damps the open-rule → shading and wind-release → shading
   sequences).
9. Command backoff timer active for this cover (§5) → defer.
10. Simulation mode → log the command and fire the logbook event with `simulated: true`,
    but only when the (layer, target) pair differs from the last simulated command for this
    cover. Register **no** pending command, do not touch `engine_target`, `owner`, `dam` or
    the interval clock. Applies to every layer, wind included.
11. Send the command. Feature check at command time: `cover.open_cover`/`cover.close_cover`
    when the cover advertises `OPEN`/`CLOSE`; otherwise `cover.set_cover_position` with
    100/0 (still only the two end positions, decision 2); a cover supporting neither raises
    a repair issue and the command is suppressed.

### 1.4 Commands, pending records and transition classification

**On send:** `engine_target = target`; `owner = engine` (decision 20: ownership is claimed at
send time, so `owner == engine AND engine_target ≠ actual` is the honest representation of
a command in flight, also across restarts); create the pending record `{target, sent_at,
last_progress_at}`; update the interval clock; flush the Store immediately. `dam` handling
by layer (decision 18): a **schedule**-layer send clears `dam` and `dam_layer`; **wind** and
**door** sends leave `dam` intact (the override is merely bypassed for that episode);
**shading** sends leave `dam` intact.

**While a pending record exists:**
- classified state reaches `target` → **match**: pending cleared;
- a **contrary** settled state (§1.0) persists ≥ 10 s → **manual move** (below), pending
  cleared;
- entering `partial`, `moving` or `cover_unavailable` updates `last_progress_at` and
  extends the confirm window; the window (`confirm_window`, per cover, default 120 s, must
  exceed the cover's travel time) is measured from `last_progress_at`;
- the confirm window expires while the cover is `partial` → the user stopped it: pending
  cleared, `owner = user`, `manual_move_at = now`, `dam = the pending target` (layer other),
  no re-send (decision 27);
- the confirm window expires otherwise → status `unconfirmed`, pending cleared, backoff
  started (§5).

**Without a pending record:** a classified change to `engine_target` while `owner == engine`
is a late **match** (slow cover after `unconfirmed`), not a manual move. A settled state that
equals the **last settled state** after a `moving` or `cover_unavailable` gap is a blip and is
ignored (decision 26): a connectivity dropout never counts as a manual move. Any other
classified state change is a manual move.

**Manual move:** `owner = user`; `manual_move_at = now`; `dam` is set from the **last
completed evaluation**: if its desired ∈ {open, closed} → `dam = that desired`; else if the
new actual ∈ {open, closed} → `dam = inverse of the new actual`; else (`partial`) →
`dam = null`. `dam_layer = shading` only when the override was created against a
**sun-driven shading close**: the last completed evaluation's winning layer was shading, its
desired state was `closed`, and the sun was hitting the window at that evaluation (decision
25). Every other override is `other` (null when `dam` is null). `engine_target` is left
unchanged. Any
schedule hold on the cover is released per §1.2 layer 5. Manual moves are recorded while
the cover is disabled, but with `dam = null`.

`partial` is a manual state that is neither open nor closed. While actual is `partial` and
an override is active, the engine waits; once the override ends (§1.5) the engine may
command the cover. Status shows `partial`.

### 1.5 Override lifecycle (decisions 12, 18)

An override begins when a manual move sets `dam ≠ null`. It **ends** (`dam = null`,
`dam_layer = null`) when any of the following happens:

a. a schedule rule of the cover's profile fires;
b. reset (button or service): `owner = engine`, `engine_target = actual` if actual ∈ {open,
   closed} else null, `dam = null`. `manual_move_at` is untouched, so a released schedule
   hold stays released;
c. the engine sends a **schedule**-layer command to the cover;
d. the desired state has been ∈ {open, closed} and ≠ `dam` **continuously** for the override
   dwell (hub `override_dwell`, default 30 min). `leave_alone` pauses the dwell timer; a
   return to `dam` resets it. The timer is runtime-only and restarts after a restart;
e. `dam_layer == shading` (the override was created against a sun-driven close) and
   `sun_hits` is false for that cover. Evaluated as a level test so it also holds after a
   restart; it never applies to overrides created against a `no_shade` opinion or a
   forced-mode close while the sun was off the window (decision 25).

Wind and door commands do not end an override; they bypass it for the episode (gates 3, 4).
An override does not end merely because desired changed; that is what makes a passing cloud
harmless regardless of its length. `manual_override` sensor `since` = `manual_move_at`.

### 1.6 Confirmed defaults

Reopening mode `passive`. Door above quiet hours. Frost above wind above door.
Min interval blocks only shading-layer moves but its clock is updated by every send.

## 2. Inputs and signal processing

All timers use HA helpers: `async_track_time_change` for local midnight and daily
recomputation of sun-relative rule times (DST-correct); `async_call_later` for debounce,
dwell, wind hold, min-interval retry, command retry and confirm windows (relative,
DST-immune); `async_track_point_in_time` only for the next schedule rule, recomputed at
midnight and on `EVENT_CORE_CONFIG_UPDATE`. A fixed-time rule falling into a skipped DST
hour fires at the first valid minute after it; in a repeated hour it fires once.

- **Sun hits window** (per cover): `signed_diff = (sun_az − cover_az + 180) mod 360 − 180`;
  inside ⇔ `−tol_left < signed_diff < tol_right AND elev_min ≤ elevation ≤ elev_max`.
  Hysteresis: on when strictly inside on both axes; off when outside by the release margin
  (default 2°) on either axis, except that the elevation lower bound never releases below
  the horizon (with `elev_min = 0`, off at elevation ≤ 0). Source: `sun.sun` attributes
  (HA updates them every 2–4 min in daylight); `sun.sun` is validated at setup (§5). Seed
  at startup/reload with the strict test. The margin is a release margin, not chatter
  protection; chatter protection is the sunny debounce.
- **Sunny**: weather condition ∈ configurable set (default `sunny`, `partlycloudy`),
  debounced: on after continuously true for `sunny_on_delay` (10 min), off after
  continuously false for `sunny_off_delay` (20 min). Seeded at startup from the current
  condition as already settled. `unknown` until the weather entity has reported, and after
  it has been unavailable longer than `weather_grace` (30 min); unknown → shading yields
  `leave_alone`.
- **Hot day**: daily forecast via `weather.get_forecasts` (`type: daily`,
  `blocking=True, return_response=True`) at startup, hourly, and after a weather entity
  change (throttled to once per 10 min). "Today" = first entry whose `datetime` falls on the
  local calendar day. Within the local day the stored max only rises and the stored min only
  falls; the **flag** `hot_day` latches true until local midnight once true. `hot_day =
  unknown` until the first successful fetch of the new local day; a failed or empty fetch
  keeps the previous values and raises the weather repair issue after the grace period.
  `hot_day = max ≥ hot_high AND (NOT hot_low_enabled OR min ≥ hot_low)`.
- **Override entities**: optional `sunny_override_entity`, `hot_override_entity`; if set,
  their on/off replaces the computed value (unavailable → unknown).
- **Room temperature** (per cover, optional): `room_cold = temp < comfort_floor`,
  `room_hot = temp ≥ comfort_ceiling`, 0.5 K hysteresis and a 10-minute dwell on both
  transitions. Seeded at startup/reload from the current reading with the hysteresis band
  resolved toward the nearer state and the dwell treated as elapsed. Unavailable → neither
  cold nor hot, status attribute `degraded`. `room_only` covers with an unavailable sensor
  are not shaded and raise a repair issue.
- **Wind**: one hub sensor (speed or gust). Per cover: `wind_enabled`, `wind_upper`,
  `wind_lower`, `wind_hold` (15 min), `wind_action` (open|hold). Activation at value ≥
  upper; release after value < lower continuously for the hold time. Sensor unavailable →
  `wind_active` frozen at its last value + repair issue.
- **Frost**: outdoor temperature from a sensor or the weather entity's `temperature`
  attribute. `frost_active` at ≤ threshold (default 0 °C), release at > threshold + 1 K.
  Source unavailable → last value held for `weather_grace`, then `unknown`; the last known
  value is retained for the near-freezing test in §1.2 layer 1.
- **Units**: every threshold is stored together with the unit it was entered in and converted
  at read time to the source entity's current unit using HA's unit converters; the weather
  entity's `temperature_unit` attribute is the source unit for forecast and outdoor
  temperature. Number selectors display the source entity's unit. A wind sensor whose unit
  changes after configuration raises a repair issue.
- **Time zone contract**: schedule queries take the home's time zone explicitly; the
  controller passes `hass.config.time_zone` so a UTC `now` is converted before quiet hours and
  fixed rules are evaluated (decision 28).
- **Evaluation triggers**: state changes of covers, door sensors, room sensors, wind,
  outdoor temperature, weather entity, `sun.sun`, override entities, runtime control
  entities; timers listed above; local midnight rollover; 5-minute fallback tick.

## 3. Configuration model

One **hub config entry** (`single_config_entry: true`) + two **config subentry types**
(`cover`, `profile`), each with create and reconfigure flows.

**Reload mechanism (single rule):** one update listener on the entry calls
`hass.config_entries.async_schedule_reload`. Every hub and subentry flow step ends with
`async_update_and_abort`; `async_update_reload_and_abort` and `OptionsFlowWithReload` are
never used (they conflict with update listeners; the hub variant breaks in 2026.12).
Subentry add/update/remove all notify the same listener.

**Devices (2026.8 registry rules):** created explicitly in `async_setup_entry` before
platforms are forwarded. Hub device: `identifiers={(DOMAIN, entry.entry_id)}`,
`config_subentry_id=None`, `entry_type=SERVICE`. One device per cover subentry:
`identifiers={(DOMAIN, subentry_id)}`, `config_subentry_id=subentry_id`,
`via_device_id=<hub device id>` (never the deprecated `via_device`). Hub entities are added
without a subentry id; per-cover entities with their subentry id; a hub entity never declares
a cover device. Device lookups are always `config_entry_id`-scoped. Deleting a cover
subentry removes its device and entities automatically.

**Hub entry** (two-step config flow; hub options editable later):
`weather_entity` (required; the flow rejects entities without `FORECAST_DAILY`);
`wind_sensor` (optional; absent → wind layer disabled and wind fields omitted from cover
subentries); `outdoor_temperature_source` (sensor entity; default = weather entity
temperature attribute); `frost_threshold` (0 °C); `sunny_conditions`; `sunny_on_delay`
(10 min); `sunny_off_delay` (20 min); `weather_grace` (30 min); `hot_high` (24 °C);
`hot_low` (13 °C) + `hot_low_enabled` (true); `sunny_override_entity`,
`hot_override_entity` (optional); `sun_release_margin` (2°); `open_closed_tolerance` (5 %);
`override_dwell` (30 min).

**Cover subentry** (one per cover): `cover_entity` (required; feature check OPEN+CLOSE or
SET_POSITION runs in the flow when the cover currently reports a state, otherwise the cover
is accepted and re-checked at every setup with the §4 repair issue as fallback); `name`
(default: cover friendly name); `azimuth` (0–359); `tolerance_left`, `tolerance_right`
(60°); `elevation_min` (0°), `elevation_max` (90°); `shading_rule` (`forecast_with_room` |
`room_only` | `either`); `room_temperature_sensor` (optional; required for `room_only`);
`comfort_floor` (21 °C) < `comfort_ceiling` (25 °C); `door_sensor` (optional); wind block
only if the hub has a wind sensor: `wind_enabled`, `wind_upper` > `wind_lower`, `wind_hold`
(15 min), `wind_action` (open|hold); `schedule_profile` (select over
`entry.get_subentries_of_type("profile")`, value = `subentry_id`, plus an explicit "none");
`min_move_interval` (10 min); `confirm_window` (120 s; must exceed the cover's travel time).
Enabled and mode are runtime state (§5), not subentry data.

**Schedule profile subentry**: `name`; `rules` stored as a list, presented as four collapsed
sections `rule_1`…`rule_4` with all fields optional and cross-field validation in the step
handler: `{action: close|open, time_mode: fixed|sunrise|sunset, time (fixed) or
offset_minutes (sun-relative), earliest?, latest? (clamps for sun-relative rules)}`;
optional `quiet_hours {start, end}` (may span midnight). Validation: fixed rule times
outside quiet hours; `fixed` requires `time`, sun-relative requires `offset_minutes`.

**Entity references** (`cover_entity`, `door_sensor`, `room_temperature_sensor`, hub
sensors) are followed automatically: the integration subscribes to
`EVENT_ENTITY_REGISTRY_UPDATED` and rewrites the stored entity id when `old_entity_id`
matches. Entry data carries `version`/`minor_version`; migrations run once via
`async_migrate_entry`. A cover referencing a deleted profile behaves as if it had none and
raises a repair issue.

## 4. Entities, services, observability

Unique ids: hub `f"{entry_id}_{key}"`, per cover `f"{subentry_id}_{key}"`.
`has_entity_name = True` everywhere; the cover device is named after the cover.

**Hub device:** select `shading_mode` (off | auto | forced_sunlit | forced_all; default
auto) [CONFIG]; select `reopening_mode` (active | passive | off; default passive)
[CONFIG]; switch `simulation_mode` [CONFIG]; switch `verbose_logging` [CONFIG] (sets the
integration logger to DEBUG while on; a `logger:` YAML entry overrides); binary sensors
[DIAGNOSTIC] `hot_day`, `sunny` (debounced), `frost_active`, `any_wind_protection_active`,
`problem` (device class PROBLEM; on while any non-dismissed repair issue of this integration
is open, driven by the issue-registry update event); sensors [DIAGNOSTIC]
`forecast_max_today`, `forecast_min_today`, `next_scheduled_event` (timestamp; attributes
profile, action, covers); button `evaluate_now` [CONFIG].

**Per-cover device:** switch `enabled` [CONFIG]; select `mode` (auto | dark_only |
protection_only) [CONFIG]; sensor `status` (enum, **no category**, see services) with
values in precedence order: `cover_unavailable`, `disabled`, `command_failed`,
`unconfirmed`, `held_frost`, `protected_wind`, `door_open`, `quiet_hours`, `partial`,
`manual_override`, `schedule_hold`, `closed_shading`, `open_no_shade`, `idle`; attributes:
`desired_state`, `actual_state`, `winning_layer`, `reason`, `sun_hits`, `sunny`, `hot_day`,
`room_state`, `wind_state`, `active_rule`, `next_planned_action` (the move currently
deferred by gate 7–9 with its retry time, else the cover's next schedule rule),
`last_engine_move`, `owner`, `degraded`. The status sensor class declares a literal
`_unrecorded_attributes` frozenset naming every attribute except `desired_state`,
`actual_state`, `reason`. Binary sensor `manual_override` (**no category**; attributes
`since`, `overridden_desired`); button `reset_override` [CONFIG]; binary sensors
[DIAGNOSTIC] `sun_hits`, `wind_protection_active`.

**Services** (registered with `hass.services.async_register`): `cover_automation.reset_override`,
`cover_automation.evaluate_now`. Targets resolved with
`async_extract_referenced_entity_ids(hass, TargetSelection(call.data))`;
`has_any_target == False` → all covers. Referenced devices are mapped to cover subentries
through `device.config_subentry_id`; referenced entities through the uncategorised `status`
/ `manual_override` entities (entities with a category are excluded from device and area
expansion, which is why those two must stay uncategorised).

**Logbook:** every engine command (real or simulated) fires `cover_automation_action`
`{entity_id (cover), action, reason, layer, simulated}`. `logbook.py` describes it
("Closed Bedroom for shading: sun hits, hot day"), attributed to the cover entity. Manifest
declares `dependencies: ["sun", "weather", "logbook"]`. A cover excluded from the
recorder will not show these entries.

**Repairs** (non-fixable, auto-clearing; shared `translation_key` + `translation_placeholders`
`{"cover": name}` for per-cover issues): wind or door wanted open during frost (per cover;
plus a persistent notification, decisions 6/16); wind sensor unavailable; wind sensor unit
changed; weather unavailable beyond grace or forecast fetch failing; `sun.sun` missing at
runtime; frost source unavailable; door sensor unavailable; configured cover, door or room
sensor missing; `room_only` without usable room sensor; cover supports neither open/close
nor position; cover references a deleted profile; sun-relative rule skipped because no
quiet-hours clamp was possible; three consecutive command failures.

**Diagnostics** (`diagnostics.py`): hub config, all subentries, per-cover evaluation
snapshot, persisted state. Nothing to redact.

## 5. Persistence, startup, error handling

**Store** (`Store(version=1, minor_version=1)` with a migration function; one per hub
entry; saved immediately on every command send and ownership change, delayed 1 s for
everything else, immediately on unload, removed on entry removal):
- Per cover, eight scalars: `owner`, `engine_target`, `manual_move_at`,
  `desired_at_manual_move`, `dam_layer`, `wind_active`, `enabled`, `mode`.
- Hub: forecast latch `{date, max, min, hot_day}`; `shading_mode`, `reopening_mode`,
  `simulation_mode`, `verbose_logging`.
- Entities are views over engine state and write through the engine (decision 17).
- Recomputed, never stored: sunny debounce, frost, sun hits, room hysteresis and dwell,
  override dwell timer, command backoff, schedule hold/release, open-rule satisfied marker,
  pending records (after a
  restart a command in flight is represented by `owner == engine AND engine_target ≠
  actual` and is resolved by reconcile).

**Setup order:** validate `sun.sun` and the weather entity — either missing, or the weather
entity reporting no `FORECAST_DAILY` feature yet → `ConfigEntryNotReady` (HA retries);
the flows reject weather entities without daily forecasts up front, and the repair issue
for forecast problems is raised only when a fetch fails on an otherwise healthy entity.
Then: create devices (§3); load Store; forward platforms (entities read committed Store
values in `async_added_to_hass`); register the update listener; subscribe listeners; register
the first evaluation with `async_at_started` as a coroutine job (fires immediately when HA
is already running, so reloads work). Missing optional sensors → repair issue, continue.
The first evaluation seeds all signals (§2) and is computed with the **persisted** `owner`
and `manual_move_at` before reconcile writes anything. A cover that is `cover_unavailable`
is skipped until it reports.

**Startup order through the engine facade:** `decide()` (side-effect free) → `reconcile(actual,
decision, now)` → `evaluate()` and act. The runtime's wind edge detector is seeded from the
persisted `wind_active`, so a wind release during downtime still opens a restoring window.

**Reconcile after downtime** (per cover):
- first setup (`owner` unset) → `owner = engine`, `engine_target = actual` if ∈ {open,
  closed} else null, `dam = null`;
- `actual == engine_target` → **no change at all** (decision 20): `owner`, `manual_move_at`,
  `dam` and `dam_layer` are left exactly as persisted (covers a command that completed
  during downtime, and a user who closed a cover the engine had earlier closed);
- `actual ≠ engine_target` and `owner == engine` → a manual move happened during downtime:
  `owner = user`, `manual_move_at = now`, `dam`/`dam_layer` per §1.4 using the first
  evaluation's desired state and winning layer (or inverse of actual);
- `actual ≠ engine_target` and `owner == user` → keep `manual_move_at`, `dam` and
  `dam_layer` unchanged (a live override survives a restart during frost or quiet hours).

**Command failures:** the service call raises → log warning, status `command_failed`, one
retry after 30 s (the failed send does not advance the minimum-interval clock). Confirm window expires → status `unconfirmed`, pending cleared; a
per-cover backoff timer (starts at `min_move_interval`, doubles per repeat, max 1 h)
applies to all layers except wind. Three consecutive failures/unconfirmed → repair issue;
cleared on the next confirmed command. Per-cover exceptions are isolated.

**Unload / remove:** `async_unload_entry` cancels every timer and pending record, saves the
Store, unloads platforms. `async_remove_entry` deletes the Store.
`async_remove_config_entry_device` returns true for devices whose subentry no longer exists.

**Config changes:** subentry or hub option change → reload via the update listener.
Runtime entity change → engine state update + evaluation, no reload. Timezone/location
change → recompute sun-relative timers.

## 6. Code structure, testing, tooling

**Layout** `custom_components/cover_automation/`:
- `engine/` — pure Python, no HA imports, own `engine/const.py`: `model.py` (enums,
  dataclasses for inputs, per-cover state, decision), `layers.py` (layer stack), `gate.py`
  (act gate), `signals.py` (hysteresis, debounce, dwell, daily latch), `sun.py` (geometry
  with wrap-around and release margin), `schedule.py` (rule fire times with sunrise/sunset,
  clamps and quiet-hours clamping, holds, releases), `override.py` (lifecycle),
  `classify.py` (actual-state classification, pending/match/contrary/manual rules),
  `reconcile.py`.
- `controller.py` — HA binding: subscriptions, timers, command sending with feature check,
  pending tracking, Store, repairs, notifications, logbook event firing, entity-rename
  tracking. Exposed to platforms via typed `entry.runtime_data`.
- `config_flow.py` (hub flow + `cover` and `profile` subentry flows), `__init__.py`
  (devices, setup order, update listener, services), `const.py`, `store.py`, platforms
  `switch.py`, `select.py`, `sensor.py`, `binary_sensor.py`, `button.py`, `logbook.py`,
  `diagnostics.py`, `services.yaml`, `translations/en.json` (no `strings.json`), `manifest.json`
  (`domain`, `name`, `codeowners`, `config_flow: true`, `dependencies: ["sun", "weather",
  "logbook"]`, `documentation`, `issue_tracker`, `iot_class: "calculated"`,
  `integration_type: "hub"`, `single_config_entry: true`, `version`).
- Translation key shapes: `config_subentries.cover` / `.profile` with `entry_type` and
  `initiate_flow.user`; `entity.sensor.status.state.<value>`; `entity.select.<key>.state.<option>`;
  `services.<name>`; `issues.<translation_key>.title/description` with placeholders.
- Repo root: `hacs.json` (`homeassistant: "2026.8.0"`), `README.md`, `pyproject.toml`,
  `requirements_test.txt`, `.github/workflows/` (hassfest and HACS actions, SHA-pinned;
  tests), `tests/`. Brand icon requires a `home-assistant/brands` PR; generic icon otherwise.

**Targets and pins:** Home Assistant ≥ 2026.8 (`homeassistant==2026.8.3` for tests),
Python ≥ 3.14.2, `pytest-homeassistant-custom-component==0.13.357` (brings pytest-asyncio,
pytest-freezer, pytest-socket). Ruff for lint and format; pyright strict on `engine/`.

**Deprecations avoided by construction:** `via_device` (2027.8), cross-subentry device
moves (2027.8), `DeviceEntry.config_entries*` shims (deprecated, no removal date
announced), update-listener + reloading flows (2026.12), `TargetSelectorData` →
`TargetSelection` (2026.12), `deprecated_hass_argument` service helpers (2026.10),
`show_advanced_options` (2027.6), manual `entity_id` generation (2027.2).

**Testing:** table-driven unit tests for `engine/` covering: layer precedence (frost over
wind over door over quiet hours over schedule over shading); mode table; every hysteresis,
debounce, dwell and latch edge including seeding; override lifecycle a–e in both reopening
modes, including which sends clear `dam`; schedule close/open rule semantics as state tests,
sticky releases, re-close, quiet-hour rejection and clamping; door bypass rule; classification
with tolerance, `partial`, `moving`, pending match / contrary / late match / expiry, and the
"position-only cover without travel flags, 45 s close" case; reconcile cases (first setup,
completed-during-downtime, user-closed-then-restart, restart during frost with a live
override). The reviewer scenarios (`docs/reviews/*behavior*.md`, scenarios a–n plus N1–N6)
become named test cases with their expected outcomes. A **day-replay harness** inside
`engine/` tests feeds a synthetic sun path, weather sequence, room temperatures and manual
moves through the engine and asserts the command sequence. Controller tests with the HA test
harness (pytest-freezer for time) for config flows incl. subentries, device creation,
entities, state-change → service-call paths, reload via update listener, at-started on
reload. Test-driven development throughout.
