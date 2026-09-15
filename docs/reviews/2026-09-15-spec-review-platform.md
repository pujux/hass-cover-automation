# Cover Automation Design Spec — Platform Feasibility Review

## 1. Summary

The design is implementable on Home Assistant 2026.4 with **no blockers**. Config subentries with per-subentry devices, per-subentry entities, subentry reconfigure flows, `single_config_entry`, non-fixable auto-clearing repairs, a custom logbook event described by `logbook.py`, `weather.get_forecasts` with `return_response`, and `RestoreEntity` all exist and behave as the spec assumes — I verified each against the unpacked `homeassistant==2026.4.1` wheel rather than from memory. The architecture (pure `engine/` + HA-binding `controller.py`, desired-state reconciler, five persisted scalars per cover) is sound and markedly more testable than the integration it replaces.

The significant gaps are not in the concept but in the HA-contract details: the spec's "subentry change → entry reload" has a hard API conflict that will raise `ValueError` if implemented the obvious way; every temperature and wind threshold is specified in a fixed unit even though HA hands back forecast, weather-attribute and sensor values in the *user's* display unit; the cover command path assumes `OPEN`/`CLOSE` features that position-only covers do not advertise; `EVENT_HOMEASSISTANT_STARTED` never fires on a reload; and "no target = all covers" is not how HA entity services behave. Roughly a dozen smaller items (reserved enum state name, recorder attribute bloat, translation key shapes, missing lifecycle hooks and manifest keys, two internal contradictions) need a paragraph each.

---

## 2. Findings

### F1 — Subentry reconfigure + update listener is a hard API conflict
- **Severity:** MAJOR
- **Spec section:** §5 "Config changes"; §3
- **Problem.** The spec says "subentry or hub option change → entry reload" without naming a mechanism. The two obvious implementations are mutually exclusive with the third requirement (subentry *creation* must also reload). `ConfigSubentryFlow.async_update_reload_and_abort()` raises if the entry has update listeners, and `OptionsFlowWithReload` carries the identical restriction — yet subentry *add* and *remove* are signalled **only** through `entry.update_listeners`, because `ConfigSubentryFlowManager.async_finish_flow` has no reload hook.
- **Evidence (verified in `homeassistant-2026.4.1`).**
  - `config_entries.py` `ConfigSubentryFlow.async_update_reload_and_abort`: `if entry.update_listeners: raise ValueError("Cannot update and reload entry with update listeners")`.
  - `class OptionsFlowWithReload` docstring: *"It's not allowed to use this class if the integration uses config entry update listeners."*
  - `ConfigEntries.async_add_subentry` / `async_remove_subentry` / `async_update_subentry` all funnel into `_async_update_entry` → `_async_save_and_notify`, which iterates `entry.update_listeners`. So one update listener covers add, update **and** remove.
  - `ConfigSubentryFlowManager.async_finish_flow` only calls `async_add_subentry` — it never reloads.
  - `ConfigFlow.async_update_reload_and_abort` (the top-level one) has **no** such guard — only the subentry variant does.
- **Suggested fix.** Pick exactly one mechanism and write it into §5: register `entry.async_on_unload(entry.add_update_listener(_update_listener))` where `_update_listener` calls `hass.config_entries.async_schedule_reload(entry.entry_id)`; end every subentry create/reconfigure step with `async_update_and_abort` (never `async_update_reload_and_abort`); use plain `OptionsFlow` for the hub. Canonical reference implementation in core: `homeassistant/components/bayesian/__init__.py` + `config_flow.py:555` — a helper-shaped integration with subentries that does exactly this.

### F2 — Every threshold is specified in a fixed unit; HA delivers user units
- **Severity:** MAJOR
- **Spec section:** §2 (Hot day, Room temperature, Wind, Frost); §3 (hub + cover subentry fields)
- **Problem.** The spec states thresholds as "24 °C", "13 °C", "21 °C", "25 °C", "0 °C, release at +1 K" and, for wind, "any unit; thresholds are entered in that unit". None of the four sources delivers a guaranteed unit, and the unit can change *after* configuration.
- **Evidence (verified).**
  - `components/weather/__init__.py` `WeatherEntity._convert_forecast`: `to_temp_unit = self._temperature_unit` — forecast temperatures are converted to the entity's **display** unit, which follows the user's unit system and any per-entity override. A °F household gets `86.0`, not `30.0`.
  - `components/weather/const.py`: the weather entity publishes `temperature` alongside `temperature_unit` precisely because the unit is not fixed.
  - A `sensor` entity's `unit_of_measurement` is user-overridable at runtime via entity options (`components/sensor/__init__.py` `_async_read_entity_options`), so the room/outdoor/wind unit observed at config-flow time is not stable.
  - The reference integration had to solve exactly this (`docs/reference/…-analysis.md` §4.2: "°F converted to °C").
- **Suggested fix.** Store each threshold with the unit it was entered in; convert at read time using the source entity's *current* unit (`TemperatureConverter` / `SpeedConverter` from `homeassistant.util.unit_conversion`), with the weather entity's `temperature_unit` attribute as the source unit for the forecast and outdoor-temperature paths. Drive the `NumberSelector`'s `unit_of_measurement` from the selected source entity so the form shows the right unit. For wind, snapshot the sensor's unit into the subentry and raise a repair issue if it later changes.

### F3 — `cover.open_cover` / `close_cover` require features the spec never checks
- **Severity:** MAJOR
- **Spec section:** §1 act gate / §5 "Command failures"; decision 2
- **Problem.** Decision 2 mandates "commanded with open/close, never a percentage", and the spec has no `supported_features` check and no fallback. Position-only covers (common on KNX, some ESPHome and Zigbee stacks) advertise `SET_POSITION` without `OPEN`/`CLOSE`; every command to them fails, so the cover sits permanently in `command_failed` → three failures → repair issue, forever.
- **Evidence (verified).** `components/cover/__init__.py`: `component.async_register_entity_service(SERVICE_OPEN_COVER, None, "async_open_cover", [CoverEntityFeature.OPEN])` and the same for `CLOSE`. `helpers/service.py` (~line 816): an entity that lacks the required feature **and is explicitly referenced** raises `ServiceNotSupported` rather than being silently skipped.
- **Suggested fix.** Read `supported_features` at command time; use `cover.set_cover_position` with `position: 0/100` when `OPEN`/`CLOSE` is absent (this does not violate decision 2 — it is still only the two end positions); validate features in the cover subentry flow and raise a repair issue when a cover supports neither path.

### F4 — `reset_override` with no target cannot mean "all covers"
- **Severity:** MAJOR
- **Spec section:** §4 "Services"
- **Problem.** "`cover_automation.reset_override` (target: cover status entities; **no target = all covers**)" is not achievable with an entity service: an entity-service call with no `entity_id`/`device_id`/`area_id` resolves to an empty set and is a silent no-op, not a broadcast.
- **Evidence (verified).** `helpers/service.py`: `entity_service_call` builds its target from `target_helpers.async_extract_referenced_entity_ids(...)`; the only broadcast form is the literal `entity_id: all` (`target_all_entities = call.data.get(ATTR_ENTITY_ID) == ENTITY_MATCH_ALL`, line 751). Nothing maps "absent target" to "everything".
- **Suggested fix.** Register `reset_override` with `hass.services.async_register` and a schema whose target fields are optional, resolving them in the integration (empty → all covers) — this also lets you accept the per-cover *device* as a target. If you keep it an entity service, change §4 to "no target = no-op; use `entity_id: all`". (Note: with `EntityCategory` set, the entity is excluded from *indirect* device/area service calls — see F12.)

### F5 — `EVENT_HOMEASSISTANT_STARTED` never fires on reload
- **Severity:** MAJOR
- **Spec section:** §5 "Startup"
- **Problem.** "first evaluation after `EVENT_HOMEASSISTANT_STARTED`". Every reload path — the update listener from F1, a HACS upgrade, disable/enable, re-adding the entry — sets the entry up *after* startup, so a one-shot listener on that event never fires and the engine never performs its first evaluation. Given F1 makes reloads routine (one per subentry edit), this would be a frequent, silent dead state.
- **Evidence (verified).** `helpers/start.py` `async_at_started` exists for exactly this: it runs the callback immediately when `hass.state is CoreState.running`, and otherwise subscribes to `EVENT_HOMEASSISTANT_STARTED` once.
- **Suggested fix.** `entry.async_on_unload(async_at_started(hass, self._async_first_evaluation))`, and say so in §5.

### F6 — Hub device must be created before any cover device, or `via_device` is silently dropped
- **Severity:** MAJOR
- **Spec section:** §3 "Cover subentry (creates its own device linked `via_device` to the hub device)"
- **Problem.** If cover devices are created implicitly by `DeviceInfo(via_device=...)` on the first entity added, the hub device may not exist yet — platforms are forwarded concurrently and there is no ordering guarantee between `switch.py`, `sensor.py`, etc. The link is then dropped without an error the user will ever see.
- **Evidence (verified).** `helpers/device_registry.py` `async_get_or_create`: when the `via_device` identifier is not in the registry it calls `report_usage(..., core_behavior=ReportBehavior.LOG, breaks_in_ha_version="2025.12.0")` and leaves `via_device_id = UNDEFINED`. It logs; it does not raise, and it does not retry later.
- **Suggested fix.** In `async_setup_entry`, before `async_forward_entry_setups`, explicitly create the hub device with `dr.async_get_or_create(config_entry_id=entry.entry_id, config_subentry_id=None, identifiers={(DOMAIN, entry.entry_id)}, ...)` and then one device per cover subentry with `config_subentry_id=subentry_id` and `via_device=(DOMAIN, entry.entry_id)`. (Both the `None` and the per-subentry form are valid: `async_get_or_create` accepts `config_subentry_id: str | None` and validates non-`None` values against `entry.subentries`.)

### F7 — `RestoreEntity` for `enabled` / `mode`: ownership and ordering are unstated
- **Severity:** MAJOR
- **Spec section:** §4 (per-cover `enabled`, `mode`; hub selects/switches); §5 "Runtime entity states use `RestoreEntity`, not the Store"
- **Problem.** The entities are described as views over engine state, yet they are also the *only* persistence for that state. Three things are undefined: (a) who writes whom at startup; (b) whether the engine may evaluate before platforms exist; (c) the durability difference versus the Store. The safety-relevant case is the `enabled` kill switch "intended for maintenance" silently returning to `on`.
- **Evidence (verified).**
  - Ordering is *achievable*: `helpers/entity_platform.py` `_async_setup_platform` drains `self._tasks` with `await asyncio.gather(*pending)` before returning, so after `await hass.config_entries.async_forward_entry_setups(...)` every entity's `async_added_to_hass` (and therefore `async_get_last_state`) has completed. But nothing enforces it — if the controller subscribes and evaluates before that await, it uses defaults.
  - Durability: `helpers/restore_state.py` `STATE_DUMP_INTERVAL = timedelta(minutes=15)`, `STATE_EXPIRATION = timedelta(days=7)`. Restore data is written every 15 minutes and on `EVENT_HOMEASSISTANT_STOP`; an unclean shutdown loses up to 15 minutes of changes.
- **Suggested fix.** State in §5 that (1) the controller registers listeners/timers and performs its first evaluation strictly after `async_forward_entry_setups` returns *and* after `async_at_started`; (2) the entity is the writer — `async_added_to_hass` restores and pushes into the engine, which holds a mirror only; (3) `enabled` (the kill switch) goes in the Store rather than `RestoreEntity`, with the entity reading from it, so a maintenance lockout survives a power cut.

---

### F8 — `unavailable` is a reserved state name and must not be an enum option
- **Severity:** MINOR
- **Spec section:** §4 per-cover `status` sensor
- **Problem.** `unavailable` in the status enum produces a state string indistinguishable from the entity actually being unavailable — breaking `states()`/template checks, availability styling and history.
- **Evidence (verified).** `helpers/entity.py` `_stringify_state`: if `available` is true, the raw string is returned verbatim, so the state literally becomes `unavailable`. Separately, `components/sensor/__init__.py` raises `ValueError("Sensor … provides state value '…', which is not in the list of options provided")`, so the option cannot simply be omitted either.
- **Suggested fix.** Rename to `cover_unavailable`. Keep `unknown` out of the option list for the same reason.

### F9 — The status sensor's 13 attributes will flood the recorder
- **Severity:** MINOR
- **Spec section:** §4 per-cover `status`; hub `next_scheduled_event`
- **Problem.** The attribute set includes volatile timestamps (`next_planned_action (+time)`, `last_engine_move`) and derived booleans that change on every evaluation. With the 5-minute fallback tick plus sun (every 2–4 min), weather and cover events, this writes a recorder row with a full attribute blob many times per hour, per cover.
- **Evidence (verified).** `helpers/entity.py` defines `_unrecorded_attributes: frozenset[str]` and the combined set is published to the recorder (`"unrecorded_attributes": self.__combined_unrecorded_attributes`). Core uses it for exactly this — e.g. `components/sun/entity.py` marks `azimuth`/`elevation` unrecorded.
- **Suggested fix.** Declare `_unrecorded_attributes` for the verbose/derived attributes (everything except `desired_state`, `actual_state`, `reason`), or drop the volatile timestamps from the status sensor since `next_scheduled_event` already carries one.

### F10 — Logbook approach is right; three prerequisites are unstated
- **Severity:** MINOR
- **Spec section:** §4 "Logbook"
- **Problem.** The chosen approach (custom event + `logbook.py::async_describe_events`) is correct and preferable to `logbook.async_log_entry` — it survives restarts and shows in history. But three things must be in the spec or it will silently not work.
- **Evidence (verified).**
  - `components/logbook/__init__.py:150-164`: `_process_logbook_platform` calls `platform.async_describe_events(hass, _async_describe_event)` and stores `external_events[event_name] = (domain, describe_callback)`. Registration happens via `async_process_integration_platforms`, which also handles components loaded later (`EVENT_COMPONENT_LOADED` listener in `helpers/integration_platform.py`).
  - `components/logbook/queries/entities.py`: `apply_event_entity_id_matchers(json_quoted_entity_ids)` — events are attached to an entity's logbook by the `entity_id` **inside the event data**. The spec's `{entity_id (cover), …}` payload is therefore correct and the entry will appear on the cover itself (a genuine improvement over the reference, which attached entries to its own status sensor — analysis §4.11).
  - `components/recorder/core.py` `_event_listener`: all events are recorded except excluded types, **but** an event carrying `entity_id` is subject to the recorder's entity filter.
- **Suggested fix.** Add to §4/§6: declare `"dependencies": ["sun", "weather", "logbook"]` in the manifest; note that a user excluding the cover from the recorder also hides these entries; and decide explicitly whether simulation-mode "commands" fire the event (see F15).

### F11 — Repairs: placeholders, ignored issues, and the persistent-notification duplicate
- **Severity:** MINOR
- **Spec section:** §4 "Repairs"; §1 layer 1
- **Problem.** Three details missing. (a) Per-cover issues need a unique `issue_id` but a shared `translation_key` plus `translation_placeholders` — the spec never mentions placeholders, so a naive implementation ends up with one translation key per cover. (b) The `problem` binary sensor "on while any repair issue of this integration is open" must exclude issues the user has dismissed. (c) §1 layer 1 raises *both* a persistent notification and a repair issue for the frost/wind conflict.
- **Evidence (verified).** `helpers/issue_registry.py`: `async_create_issue(..., is_fixable, severity, translation_key, translation_placeholders=...)`; `async_ignore()` keeps the entry in `registry.issues` with a dismissal marker rather than removing it; `EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED` (line 25) is the change signal the binary sensor should subscribe to, alongside `ir.async_get(hass).issues` for the initial read. Non-fixable issues need no `repairs.py` platform — correct as specified.
- **Suggested fix.** Specify shared `translation_key` + `translation_placeholders={"cover": name}`; have the `problem` sensor filter dismissed issues; drop the persistent notification (repairs is the current idiom and the duplicate is noise).

### F12 — Entity categories, unique IDs and the indirect-service-call side effect
- **Severity:** MINOR
- **Spec section:** §4
- **Problem.** The spec assigns `EntityCategory.DIAGNOSTIC` to some entities and nothing to `enabled`, `mode`, the hub selects/switches or the buttons. It also never states the unique_id scheme, which is the thing that makes §3's "renaming does not lose settings" true.
- **Evidence (verified).** `const.py` `class EntityCategory` docstring: an entity with a category *"Not be exposed to cloud, Alexa, or Google Assistant components / Not be included in indirect service calls to devices or areas."* So a `reset_override` button marked `CONFIG` will be skipped by a `device_id`-targeted service call — directly relevant to F4.
- **Suggested fix.** `CONFIG` for `enabled`, `mode`, `shading_mode`, `reopening_mode`, `simulation_mode`, `verbose_logging`, and both buttons; `DIAGNOSTIC` for `sun_hits`, `wind_protection_active`, `hot_day`, `sunny`, `frost_active`, `any_wind_protection_active`, `problem` (+ `BinarySensorDeviceClass.PROBLEM`), `forecast_*`, `next_scheduled_event`; leave `status` and `manual_override` uncategorised as the device's primary entities. Unique IDs: `f"{subentry.subentry_id}_{key}"` per cover (the subentry_id is a stable ULID, so cover renames are safe) and `f"{entry.entry_id}_{key}"` for hub entities. `_attr_has_entity_name = True` throughout, with the device named after the cover.

### F13 — §3 claims renames are safe, but the fix is manual
- **Severity:** MINOR
- **Spec section:** §3 "Renaming a cover entity does not lose settings"
- **Problem.** The spec's own remedy is "update the entity field in its reconfigure flow" — i.e. the *settings* survive but the automation is broken (cover permanently `unavailable`, per §5) until the user notices and edits the subentry. That is only a partial fix of the reference integration's known defect (analysis §2: "renaming a cover's entity_id silently orphans its settings").
- **Evidence (verified).** `helpers/entity_registry.py`: `EVENT_ENTITY_REGISTRY_UPDATED` fires with `action: "update"` and `old_entity_id` in the payload on rename (`_EventEntityRegistryUpdatedData_Update`, `data["old_entity_id"] = old.entity_id`).
- **Suggested fix.** Subscribe to `EVENT_ENTITY_REGISTRY_UPDATED` and rewrite the subentry's `cover_entity` / `door_sensor` / `room_temperature_sensor` when `old_entity_id` matches (several core integrations do this), or store the immutable entity registry `id` instead of `entity_id` and resolve to an `entity_id` at read time.

### F14 — Forecast: "today" selection, `SupportsResponse.ONLY`, and daily-capable entities
- **Severity:** MINOR
- **Spec section:** §2 "Hot day"; §3 `weather_entity`
- **Problem.** The spec does not say how "today's" forecast entry is picked, nor what happens when the weather entity has no daily forecast, nor what `hot_day` is when the list has no entry for the local day (late-evening providers frequently roll to tomorrow).
- **Evidence (verified).** `components/weather/__init__.py`: the service is registered with `supports_response=SupportsResponse.ONLY` — the call **must** pass `blocking=True, return_response=True`; the response is `{entity_id: {"forecast": [...]}}` (`helpers/service.py:842`). `required_features` is an **any-of** test (`any(entity.supported_features & fs == fs for fs in required_features)`, line 809), so an hourly-only weather entity is *not* filtered out — it reaches `async_get_forecasts_service`, which then raises `HomeAssistantError("Weather entity '…' does not support 'daily' forecast")`. Forecast entries carry a `datetime` field.
- **Suggested fix.** Validate `WeatherEntityFeature.FORECAST_DAILY` in the hub config flow (and raise a repair issue if it disappears later); specify "today" as the first entry whose `datetime` falls on the local calendar day; specify `hot_day = unknown` (→ shading yields `leave_alone`, consistent with the sunny-unknown rule) when no such entry exists and no latch for today is stored.

### F15 — Simulation mode will manufacture `unconfirmed` statuses and failure counts
- **Severity:** MINOR
- **Spec section:** §1 act gate 6; §5 "Command failures"
- **Problem.** In simulation the engine logs a command instead of sending it, so no settled transition ever arrives; §5's "No settled transition observed within 120 s → status `unconfirmed`, pending record dropped" then fires for every simulated move, feeding backoff and eventually the three-failures repair issue. This is the same class of bug the reference integration has (analysis §4.4: simulation "has the inferred side effect of self-triggering overrides").
- **Evidence.** Spec-internal; corroborated by the reference analysis. From memory on HA behaviour (no API involved).
- **Suggested fix.** State in §1/§5 that simulation mode registers no pending command at all — no confirmation timer, no retry, no failure counting, no repair issue — and decide whether it still fires the logbook event (recommend yes, with a `simulated: true` field, so the dry run is auditable).

### F16 — Two internal contradictions in the override model
- **Severity:** MINOR
- **Spec section:** §1.1 / §1.2 vs §5 "Reconcile after downtime"; §1 act gate 1 vs feature D1/D2
- **Problem.** (a) §1.1 stores `desired_at_manual_move = current desired` *"or `null` when desired is `leave_alone`"*, while §5 reconcile stores it unconditionally and asserts "so an override exists iff actual ≠ desired". With `desired == leave_alone`, §1.2's definition (`desired ∈ {open, closed}`) makes an override impossible, so the §5 invariant does not hold and the two rules disagree about what is written. (b) Act-gate rule 1 ("frost active → never move") sits above the door layer, so on a sub-zero morning an opened terrace door will not lift its cover — a lockout that decision 6 (frost vs *wind*) does not cover.
- **Evidence.** Spec-internal reading; `docs/design-decisions.md` #5, #6; `docs/feature-selection.md` D1/D2.
- **Suggested fix.** (a) Make §5 match §1.1 explicitly and restate the invariant as "an override exists iff actual ≠ desired **and** desired is not `leave_alone`". (b) Add one sentence stating the intended frost-vs-door outcome either way, plus a test — it is a safety-visible behaviour.

### F17 — Sun cadence is sufficient; the 2° hysteresis is a margin, not chatter protection
- **Severity:** MINOR
- **Spec section:** §2 "Sun hits window"; §2 "Evaluation triggers"
- **Problem.** The spec presents the 2° hysteresis as boundary protection (feature S2). Because astral output is monotonic and noiseless within a day, there is nothing to flap: the margin only delays the "sun left" edge by roughly 8 minutes. The real anti-chatter mechanism is the sunny debounce. Worth correcting so the wrong knob isn't tuned later.
- **Evidence (verified).** `components/sun/entity.py`: `_PHASE_UPDATES = {night: 20 min, astronomical_twilight: 8, nautical_twilight: 4, twilight: 2, small_day: 2, day: 4}`; `update_sun_position` recomputes azimuth/elevation and calls `async_write_ha_state()` on that cadence, then re-arms `async_track_point_in_utc_time`. Attribute-only writes do produce `EVENT_STATE_CHANGED` (`core.py` only downgrades to `EVENT_STATE_REPORTED` when `same_state and same_attr`), so `async_track_state_change_event` on `sun.sun` is sufficient and needs no polling. ~4-minute samples ≈ 1° of azimuth at mid-latitudes, so a 2° margin is ~2 samples. Also verified: azimuth/elevation are in `Sun._unrecorded_attributes`, so a replay harness cannot be driven from recorder history — it must synthesise the path, which §6 already plans.
- **Suggested fix.** Reword §2 to call the 2° value a release margin; keep the 5-minute fallback tick (it is cheap and covers wind/room/forecast staleness, not the sun).

### F18 — Timer helpers: name them, and separate wall-clock from monotonic
- **Severity:** MINOR
- **Spec section:** §2 "Timers"; §5 "Timezone/location change"
- **Problem.** The spec lists six timer kinds but no helpers, and DST is called out as a concern without a resolution.
- **Evidence (verified).** `helpers/event.py`: `async_track_time_change(hass, cb, hour=0, minute=0, second=0)` delegates to `async_track_utc_time_change(..., local=True)` and is the DST-correct way to hit local midnight; `async_call_later` (line 1599) is relative and therefore DST-immune; `async_track_point_in_time` (line 1464) on a wall-clock target can fire twice or not at all across a fold. `const.py:263`: `EVENT_CORE_CONFIG_UPDATE = "core_config_updated"` exists as the spec assumes.
- **Suggested fix.** In §2: `async_track_time_change` for the midnight rollover and daily recomputation of sunrise/sunset rule times; `async_call_later` for debounce, wind hold, min-interval retry, command retry and the 120 s confirmation timeout; `async_track_point_in_time` only for the *next* schedule rule, recomputed at rollover and on `EVENT_CORE_CONFIG_UPDATE`. Add a sentence on what a fixed-time rule at 02:30 does on a spring-forward day.

### F19 — Profile dropdown and the 4-rule form: feasible, with two constraints
- **Severity:** MINOR
- **Spec section:** §3 "Cover subentry `schedule_profile`"; "Schedule profile subentry `rules`"
- **Problem.** Both are buildable but the spec should record the constraints so the UX isn't designed around a capability that doesn't exist.
- **Evidence (verified).** `ConfigSubentryFlow._get_entry()` returns the parent `ConfigEntry`, so `entry.subentries` can be filtered by `subentry_type` to build a `SelectSelector` — the sibling-profile dropdown works, and storing the `subentry_id` as the value (with the title as the label) keeps profile renames safe. But the HA developer docs state *"a subentry flow can only be initiated via the `user` or `reconfigure` steps"* — you cannot spawn a profile-creation flow from inside the cover flow, so the very first cover has no profile to pick. For rules, `data_entry_flow.section` (`data_entry_flow.py:921`) is the only grouping primitive — there is no repeater selector, and sections cannot be shown/hidden conditionally on another field's value within the same step.
- **Suggested fix.** Make `schedule_profile` optional with an explicit "none" option and handle zero profiles; build the 4 rules as four collapsed `section`s (`rule_1`…`rule_4`) with all fields optional, validated in the step handler (e.g. reject `offset_minutes` with `time_mode: fixed` rather than trying to hide it). Conditional *hub-dependent* fields — hiding wind settings when the hub has no wind sensor — do work, since the schema is built at flow time from `self._get_entry().data`.

### F20 — Missing lifecycle hooks and manifest keys
- **Severity:** MINOR
- **Spec section:** §5; §6
- **Problem.** The spec never names `async_unload_entry`, `async_remove_entry` (though §5 says the Store is "deleted on entry removal"), `Store` version/migration, `async_remove_config_entry_device`, or the manifest contents beyond `single_config_entry`.
- **Evidence (verified).** `helpers/storage.py`: `Store(hass, version, key, ..., minor_version=1)` plus `_async_migrate_func(old_major, old_minor, old_data)` — parallel to the entry's `version`/`minor_version`, and the spec should say both exist. `config_entries.py:4018`: device-removal support is detected by `hasattr(component, "async_remove_config_entry_device")`; with subentries it is optional, because `async_remove_subentry` already calls `dr.async_clear_config_subentry` and `er.async_clear_config_subentry` — but it is the only way to clear a stale device. Manifest, verified against `loader.py` and core examples (`components/sun/manifest.json` shows the shape): `version` is required for custom integrations; `single_config_entry` is read at `loader.py:958`; `integration_type` defaults to `hub` for custom integrations. Minimum-HA is **not** a manifest key — `hacs.json`'s `homeassistant` field carries it (the reference repo's `hacs.json`: `{"name": …, "homeassistant": "2026.4.1", "hacs": "2.0.5"}`).
- **Suggested fix.** Add to §5/§6: `async_unload_entry` → `async_unload_platforms` + cancel every timer and pending command + immediate Store save; `async_remove_entry` → `Store.async_remove()`; `async_remove_config_entry_device` returning `True` for devices whose subentry no longer exists; `Store(version=1, minor_version=1)` with an explicit migration function. Manifest: `domain`, `name`, `codeowners`, `config_flow: true`, `dependencies: ["sun", "weather", "logbook"]`, `documentation`, `issue_tracker`, `iot_class: "calculated"`, `integration_type: "hub"`, `single_config_entry: true`, `version`. Put "minimum 2026.4" in `hacs.json`. Skip `quality_scale` (it drives core review, not HACS). Note that brand icons need a PR to `home-assistant/brands` or the integration shows a generic icon.

### F21 — Translations: `strings.json` is dead weight; three key shapes are load-bearing
- **Severity:** MINOR
- **Spec section:** §6 layout (`strings.json`, `translations/en.json`)
- **Problem.** Shipping both means hand-maintaining two copies of the same content with no generator, and the spec does not say where the subentry / enum-state / select-option / issue / service strings live.
- **Evidence (verified).** `helpers/translation.py:104`: translations are loaded from `integration.file_path / "translations" / f"{lang}.json"` only — `strings.json` is never read at runtime. The reference custom integration ships `translations/` with **no** `strings.json` and passes `home-assistant/actions/hassfest`. Key shapes confirmed from `components/bayesian/translations/en.json`, whose `config_subentries.observation` contains exactly `entry_type`, `initiate_flow.user`, `step`, `error`, `abort` — without `entry_type` and `initiate_flow.user` the "Add cover" / "Add profile" buttons render raw keys.
- **Suggested fix.** Drop `strings.json` from §6 (or generate it), and specify: `config_subentries.cover` / `.profile` with `entry_type` + `initiate_flow.user`; `entity.sensor.status.state.<value>` for the enum states; `entity.select.mode.state.<option>` and `entity.select.shading_mode.state.<option>`; `services.<name>.name/description/fields.*` (with `services.yaml` supplying selectors/target); `issues.<translation_key>.title/description` with placeholders.

### F22 — `engine/` purity, test pinning and CI action names
- **Severity:** MINOR
- **Spec section:** §6
- **Problem.** No circular-import risk in the proposed layout, but one trap and three unpinned facts.
- **Evidence (verified).** The reference repo, which targets the same HA version, pins `homeassistant==2026.4.1` + `pytest-homeassistant-custom-component==0.13.322` on Python `3.14.2` (`requirements-test.txt`, `requirements-test-lock.txt`, `.python-version`) — that is the concrete triple to use. Its `.github/workflows/validate.yml` uses `home-assistant/actions/hassfest@<sha>` and `hacs/action@<sha>` (SHA-pinned), which are the current working action refs. Subentry flows are testable: `ConfigEntry.__init__` accepts `subentries_data` (`config_entries.py:447`), and flows start via `hass.config_entries.subentries.async_init((entry.entry_id, "cover"), context={"source": "user"})` / `{"source": "reconfigure", "subentry_id": …}`.
- **Suggested fix.** Give `engine/` its own constants module — a shared root `const.py` will import `homeassistant.const.Platform` and quietly break the "no HA imports" rule that pyright-strict-on-`engine/` is meant to protect. Platforms should reach the controller via a typed `ConfigEntry[CoverAutomationData].runtime_data`, never by importing `controller` from `engine/`. Keep the day-replay harness entirely inside `engine/` (plain pytest, no `hass`, no `freezegun`); reserve `async_fire_time_changed` + `freezegun` for controller-level timer tests. Add `category: integration` to the HACS action step.

---

## 3. Verification log

Ground truth was the actual `homeassistant==2026.4.1` wheel, downloaded and unpacked to `/private/tmp/claude-501/.../a458e59e-.../scratchpad/ha/homeassistant`. Everything below was read in that source unless marked otherwise.

- **Subentries exist and are stable in 2026.4.1** — `config_entries.py`: `ConfigSubentryFlow` (class def), `ConfigSubentryFlowManager`, `ConfigFlow.async_get_supported_subentry_types`, `ConfigEntry.supported_subentry_types` exposing per-type `{"supports_reconfigure": hasattr(handler, "async_step_reconfigure")}`, `_get_entry()`, `_get_reconfigure_subentry()`, `_reconfigure_subentry_id`.
- **Subentry add/update/remove all fire the entry's update listeners** — `async_add_subentry`, `async_remove_subentry`, `async_update_subentry` → `_async_update_entry` → `_async_save_and_notify`, which iterates `entry.update_listeners`.
- **`async_update_reload_and_abort` is forbidden with update listeners** — `ConfigSubentryFlow.async_update_reload_and_abort` raises `ValueError`; `OptionsFlowWithReload` docstring states the same restriction; `ConfigFlow.async_update_reload_and_abort` has no such guard.
- **Devices per subentry + entities per subentry** — `helpers/device_registry.py` `async_get_or_create(config_subentry_id=...)` with validation against `entry.subentries`; `helpers/entity_platform.py` `async_add_entities(..., config_subentry_id=...)` and the registry write at `entity_registry.async_get_or_create(..., config_subentry_id=...)`.
- **`via_device` failure mode** — `device_registry.py` `async_get_or_create`: unresolved `via_device` → `report_usage(..., ReportBehavior.LOG)`, `via_device_id = UNDEFINED`. Logs only.
- **Subentry removal cleans up** — `async_remove_subentry` calls `dr.async_clear_config_subentry` and `er.async_clear_config_subentry`.
- **`single_config_entry`** — `loader.py:958` `Integration.single_config_entry`; enforced in `config_entries.py` via `_support_single_config_entry_only` with abort reason `single_instance_allowed`.
- **Entity addition is awaited by platform setup** — `entity_platform.py` `_async_setup_platform`: `while self._tasks: … await asyncio.gather(*pending)`.
- **RestoreEntity durability** — `helpers/restore_state.py`: `STATE_DUMP_INTERVAL = 15 min`, `STATE_EXPIRATION = 7 days`, `RestoreEntity.async_get_last_state` / `async_get_last_extra_data`.
- **`weather.get_forecasts`** — `components/weather/__init__.py`: registered with `supports_response=SupportsResponse.ONLY` and `required_features=[FORECAST_DAILY, FORECAST_HOURLY, FORECAST_TWICE_DAILY]`; `async_get_forecasts_service` raises `HomeAssistantError` for an unsupported type; `_convert_forecast` converts to `self._temperature_unit` (user display unit). Response shape `{entity_id: {"forecast": [...]}}` — `helpers/service.py:842`.
- **`required_features` is any-of** — `helpers/service.py:809-813`; explicit reference to a non-supporting entity raises `ServiceNotSupported`.
- **Cover service feature gates** — `components/cover/__init__.py`: `SERVICE_OPEN_COVER` → `[CoverEntityFeature.OPEN]`, `SERVICE_CLOSE_COVER` → `[CoverEntityFeature.CLOSE]`.
- **Entity-service targeting** — `helpers/service.py`: `ENTITY_MATCH_ALL` handling at line 751; no "absent target = all" path.
- **Logbook** — `components/logbook/__init__.py:144-164` (`async_process_integration_platforms` → `platform.async_describe_events`); `components/logbook/queries/entities.py` `apply_event_entity_id_matchers` (entity match by `entity_id` in event JSON); `components/logbook/const.py` `LOGBOOK_ENTRY_{NAME,MESSAGE,ENTITY_ID,DOMAIN,ICON}`.
- **Recorder records custom events** — `components/recorder/core.py` `_event_listener`: listens on `MATCH_ALL`, drops only `exclude_event_types`, applies the entity filter when the event carries `entity_id`.
- **Repairs** — `helpers/issue_registry.py`: `async_create_issue(is_fixable, severity, translation_key, translation_placeholders)`, `async_delete_issue`, `async_ignore`, `IssueRegistry.issues`, `EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED` (line 25).
- **Enum sensor** — `components/sensor/__init__.py:650-666`: `ValueError` if the value is not in `options`, and if `options` is set without `SensorDeviceClass.ENUM`.
- **Reserved-state collision** — `helpers/entity.py` `_stringify_state`: available entity returns the raw string, so `"unavailable"` becomes the literal state.
- **`_unrecorded_attributes`** — `helpers/entity.py:550` + `__combined_unrecorded_attributes` published to the recorder.
- **`sun.sun` cadence** — `components/sun/entity.py`: `_PHASE_UPDATES` (day 4 min, small_day/twilight 2 min, night 20 min), `update_sun_position` → `async_write_ha_state()` → re-arm; azimuth/elevation listed in `Sun._unrecorded_attributes`. Attribute-only writes are real state changes per `core.py` (`same_state and same_attr` → `EVENT_STATE_REPORTED`, otherwise `EVENT_STATE_CHANGED`).
- **Start/time/event helpers** — `helpers/start.py` `async_at_started`; `helpers/event.py` `async_track_state_change_event` (309), `async_track_point_in_time` (1464), `async_call_later` (1599), `async_track_time_change` (1905, local-time); `const.py:263` `EVENT_CORE_CONFIG_UPDATE`.
- **Entity rename signal** — `helpers/entity_registry.py`: `EVENT_ENTITY_REGISTRY_UPDATED` with `old_entity_id` on update.
- **`EntityCategory` side effects** — `const.py:953` docstring (excluded from cloud/voice and from indirect device/area service calls).
- **Store versioning** — `helpers/storage.py:233-269` (`version`, `minor_version`, `_async_migrate_func` with 2- or 3-arg signature detection).
- **Translations load path + subentry key shape** — `helpers/translation.py:104` (`translations/<lang>.json` only); `components/bayesian/translations/en.json` `config_subentries.observation` keys `['abort','entry_type','error','initiate_flow','step']`.
- **Canonical subentry+reload pattern** — `components/bayesian/__init__.py` (update listener → `async_schedule_reload`) with `config_flow.py:555` (`async_update_and_abort`); `components/kitchen_sink/sensor.py:119` (`config_subentry_id=subentry_id`) for the device/entity-per-subentry pattern.
- **Version pinning and CI action names** — from the cloned reference integration at `/private/tmp/claude-501/.../7da038c0-.../scratchpad/ha-smart-cover-automation`: `requirements-ha.txt` (`homeassistant==2026.4.1`), `requirements-test.txt` / `requirements-test-lock.txt` (`pytest-homeassistant-custom-component==0.13.322`), `.python-version` (`3.14.2`), `hacs.json`, `.github/workflows/validate.yml` (`home-assistant/actions/hassfest@<sha>`, `hacs/action@<sha>`), `custom_components/smart_cover_automation/` (ships `translations/` and `brand/`, no `strings.json`).
- **From HA developer docs (WebFetch)** — `developers.home-assistant.io/docs/config_entries_config_flow_handler/`: subentry flows support `async_step_reconfigure`, translations live under `config_subentries`, and *"a subentry flow can only be initiated via the `user` or `reconfigure` steps"*. `developers.home-assistant.io/docs/creating_integration_manifest/`: `version` required for custom integrations; `integration_type` defaults to `hub`; `dependencies` vs `after_dependencies`; `single_config_entry`.
- **From memory (not verified here)** — `has_entity_name` naming composition; `services.yaml` selector/target syntax; `freezegun` + `async_fire_time_changed` test patterns; the exact set of hassfest checks applied to custom integrations (inferred from the reference repo passing without `strings.json`); HA's `logger` integration overriding programmatic log levels (F-note in §4 `verbose_logging`).

---

## 4. Things that are well designed

- **The desired-state reconciler with a pure `engine/` layer.** Splitting the layer stack, act gate, hysteresis/debounce primitives and schedule arithmetic into HA-free modules, with the HA binding isolated in `controller.py`, is the single biggest improvement over the reference's 60-second `DataUpdateCoordinator` polling loop and per-cycle object churn. It also makes the day-replay harness a plain pytest fixture instead of an HA integration test.
- **Event-driven with a bounded fallback tick.** Verified as sufficient: `sun.sun` genuinely emits state-change events every 2–4 minutes during daylight, so no polling is needed for geometry, and the 5-minute tick is a cheap backstop for staleness rather than the primary mechanism.
- **Five persisted scalars per cover.** A deliberately minimal Store schema with an explicit "recomputed, never stored" list is unusually disciplined, and it makes the restart-reconciliation rule statable in one line.
- **Subentry-as-identity.** Keying configuration to the subentry (with the cover entity as a mutable *field*) is the correct fix for the reference's flat `"{entity_id}_{suffix}"` keys, and the unique_id scheme it implies survives renames — F13 only asks that the entity-pointer update be automatic rather than manual.
- **Logbook attributed to the cover entity.** Firing a custom event carrying the cover's `entity_id` and describing it in `logbook.py` puts the reason on the cover's own history page — verified to work via the logbook's entity matcher, and a real improvement over the reference, which attached entries to its own status sensor.
- **Per-cover transparency entities.** `status` with a winning-layer/reason attribute, a `manual_override` binary sensor and a reset button directly answer the top user complaints recorded in the reference analysis (§6: "confusion about how manual override works", "no per-cover entity of any kind").
- **Explicit precedence with an act gate separate from the opinion stack.** Separating "what should this cover be" from "may I move it now" is what makes frost-over-wind, quiet hours, min-interval and simulation mode each expressible as one rule instead of nested special cases — and it is what makes the table-driven test plan in §6 realistic.