# Platform re-review of spec revision 2 (HA 2026.8)

Read: the revised spec (revision 2) in full, and `docs/design-decisions.md` (#1–#17). All API claims re-verified against the unpacked `homeassistant==2026.8.3` tree at `…/scratchpad/ha8/homeassistant`; every `file:line` below is from that tree.

## 1. Summary verdict

**Yes — implementable as written on HA 2026.8**, with one real platform defect to fix before implementation starts. All 32 findings from the previous two rounds are addressed: 30 are fully resolved by a named clause, F28 is partially resolved (the helper is named, but the companion class it needs was itself deprecated in this window), and F31 was informational. The three structurally risky areas — the reload rule (§3), the 2026.8 device-registry rules (§3), and the Store-over-`RestoreEntity` move (decision 17 + §5) — are now stated precisely enough to implement directly, and the setup order in §5 has **no race**: the Store is loaded before platforms are forwarded, so entities acting as views over `enabled`/`mode` read committed values in `async_added_to_hass`, and `async_at_started` is registered last, after `async_forward_entry_setups` has returned.

The defect is in §1.4. The "settled classified state contrary to the target persists ≥ 10 s → manual move" rule fires spuriously on an entire class of covers: HA's `CoverEntity.state` returns `open` for any non-zero position unless the integration implements `is_opening`/`is_closing`, so a position-reporting cover without travel flags is classified `partial` for the whole of a 20–60 s travel — contrary to the target, well past 10 s. That silently flips `owner` to `user` mid-command and, under the default `passive` reopening mode, permanently disables automatic reopening for that cover. The same rule misfires on covers that drop to `unavailable` briefly while moving. Four smaller items follow: one deprecation the spec's own "avoided" list misses, one deprecation date the spec asserts that does not exist in the code, and two validation clauses that cannot always run when the spec says they do.

## 2. Resolution of F1–F32

| Id | Status | Resolving clause / note |
|----|--------|--------------------------|
| F1 | **Resolved** | §3 "Reload mechanism (single rule)". Both guards re-verified: `config_entries.py:3858` (subentry) and `:3944-3948` (`OptionsFlowWithReload`). `ConfigFlow.async_update_and_abort` exists for the hub path (`config_entries.py:3489`, class `ConfigFlow`). |
| F2 | **Resolved** | §2 "Units" bullet — stored with unit, converted at read time, `temperature_unit` as source unit, selector shows the source unit, unit-change repair. |
| F3 | **Resolved** | §1.3 gate 11 feature check; §3 `cover_entity` "must support OPEN+CLOSE or SET_POSITION"; §4 repair. Flags verified: `components/cover/const.py` `CoverEntityFeature.OPEN=1, CLOSE=2, SET_POSITION=4`. (See G5 on *when* validation can run.) |
| F4 | **Resolved** | §4 Services — `hass.services.async_register` + `async_extract_referenced_entity_ids`, "no reference → all covers". Verified `helpers/target.py:158`. (See G2 on the companion class.) |
| F5 | **Resolved** | §5 setup order — "`async_at_started` (fires immediately when HA is already running, so reloads work)". Verified `helpers/start.py:33-35`. |
| F6 | **Resolved** | §3 "Devices … created explicitly in `async_setup_entry` before platforms are forwarded", hub first. |
| F7 | **Resolved** | Decision 17 + §5 Store — `enabled`/`mode` and the hub selects/switches are Store-held; "Entities are views over engine state and write through the engine". `RestoreEntity` is gone, so the 15-minute dump window no longer applies. Setup order loads the Store before forwarding platforms. |
| F8 | **Resolved** | §1.0 and §4 status enum use `cover_unavailable`; no reserved state name remains in the option list. |
| F9 | **Resolved** | §4 "All attributes except `desired_state`, `actual_state`, `reason` are `_unrecorded_attributes`". (See G7 on the mechanics.) |
| F10 | **Resolved** | §4 Logbook — `dependencies: ["sun", "weather", "logbook"]`, `simulated` field, explicit recorder-exclusion caveat. |
| F11 | **Resolved** | §4 Repairs — shared `translation_key` + `translation_placeholders {"cover": name}`; `problem` sensor is "on while any **non-dismissed** repair issue … is open, driven by the issue-registry update event". Fields verified: `helpers/issue_registry.py:58,62` (`active`, `dismissed_version`). |
| F12 | **Resolved** | §4 category assignments, `has_entity_name = True`, unique-id scheme `f"{entry_id}_{key}"` / `f"{subentry_id}_{key}"`. (Interacts with G2.) |
| F13 | **Resolved** | §3 "Entity references … subscribes to `EVENT_ENTITY_REGISTRY_UPDATED` and rewrites the stored entity id when `old_entity_id` matches". Payload verified: `helpers/entity_registry.py:1999-2008` — `old_entity_id` is set only when `old.entity_id != entity_id`. |
| F14 | **Resolved** | §2 "Hot day" ("today" = first entry on the local calendar day; `unknown` until the first successful fetch) + §3 `weather_entity` daily-forecast validation. `WeatherEntityFeature.FORECAST_DAILY` verified at `components/weather/const.py:31`. (See G6.) |
| F15 | **Resolved** | §1.3 gate 10 — simulation registers **no** pending command and does not touch `engine_target`; applies to every layer. |
| F16 | **Resolved** | §1.4 pins the `dam` ordering explicitly; §1.2 layer 1 + gate 2 make frost-over-door a stated decision (16) with notification + repair. §5 reconcile now enumerates all three cases. |
| F17 | **Resolved** | §2 — "The margin is a release margin, not chatter protection; chatter protection is the sunny debounce." Cadence "every 2–4 min in daylight" matches `components/sun/entity.py:80-87`. |
| F18 | **Resolved** | §2 opening paragraph names `async_track_time_change` / `async_call_later` / `async_track_point_in_time` correctly and states both DST edge cases. Verified `helpers/event.py:1552,1853`. |
| F19 | **Resolved** | §3 — profile select over `entry.get_subentries_of_type("profile")` with an explicit "none"; four collapsed `rule_N` sections with cross-field validation in the step handler. |
| F20 | **Resolved** | §5 "Unload / remove" (`async_unload_entry`, `async_remove_entry`, `async_remove_config_entry_device`); §6 manifest key list; `Store(version=1, minor_version=1)` with migration; `hacs.json homeassistant: "2026.8.0"`. Verified `helpers/storage.py:464,480,624`; `config_entries.py:4164` (`hasattr(component, "async_remove_config_entry_device")`). (See G3, G4.) |
| F21 | **Resolved** | §6 — `translations/en.json` only, "no `strings.json`", plus the four key shapes. All four verified against `components/bayesian/translations/en.json` and a core select example. |
| F22 | **Resolved** | §6 — `engine/const.py`, typed `entry.runtime_data`, pins `homeassistant==2026.8.3` / Python ≥ 3.14.2 / `pytest-homeassistant-custom-component==0.13.357`, SHA-pinned hassfest + HACS actions. Pins re-verified on PyPI. |
| F23 | **Resolved** | §3 — `via_device_id=<hub device id>` "(never the deprecated `via_device`)". |
| F24 | **Resolved** | §3 — one device per subentry, hub entities without a subentry id, "a hub entity never declares a cover device". |
| F25 | **Resolved** | §3 — "the hub variant breaks in 2026.12" is called out explicitly and `async_update_reload_and_abort` is banned on both paths. |
| F26 | **Resolved** | §3 four collapsed sections; nothing in the spec marks fields `advanced`. |
| F27 | **Resolved** | §3 uses `entry.get_subentries_of_type(...)`. Verified `config_entries.py:643-649`. |
| F28 | **Partially resolved** | §4 names `async_extract_referenced_entity_ids` correctly, and §6 lists `deprecated_hass_argument` (2026.10) as avoided — verified `helpers/deprecation.py:156` and the five decorated helpers. But the class that feeds the function was deprecated in this same window and is not mentioned → **G2**. |
| F29 | **Resolved** | §3 "Deleting a cover subentry removes its device and entities automatically"; §5 keeps `async_remove_config_entry_device` as the stale-device escape hatch. |
| F30 | **Resolved** | §0 non-goals: "integration-provided automation triggers/conditions (mature in 2026.8 but not selected; the event model in §4 does not preclude adding a `trigger.py` later)". Matches decision 11. |
| F31 | n/a (informational) | Spec reads attributes by name; the `ATTR_*` constants are unchanged and still exported. No action was needed. |
| F32 | **Resolved** | §3 "Device lookups are always `config_entry_id`-scoped". |

## 3. New findings

### G1 — The "contrary state ≥ 10 s" rule misfires on position-only covers and on transient unavailability
- **Severity:** MAJOR
- **Spec section:** §1.4 "While a pending record exists"; §1.0 classification
- **Problem.** §1.4 clears the pending record and declares a manual move when "a settled classified state contrary to the target persists ≥ 10 s". Three of the five classified states can legitimately hold for longer than 10 s *during a commanded move*:
  - **`partial`.** HA derives a cover's state from `is_opening` / `is_closing` / `is_closed`. An integration that reports `current_position` but leaves `is_opening` and `is_closing` at `None` — a large, common class (many MQTT, KNX, ESPHome and template covers) — keeps state `open` at every intermediate position. Closing from 100 to 0 therefore classifies as `partial` (state `open`, position < 100 − tolerance) for the entire 20–60 s travel of a roller shutter. Under §1.4 this fires at 10 s: `owner = user`, `manual_move_at = now`, pending cleared. `dam` ends up `null` (actual is `partial`, third branch), so no override is created — but ownership has flipped, and §1.3 gate 6 `passive` then means the shading layer may never reopen that cover again. When the cover finally settles at `closed` there is no pending record left, so "any classified state change is a manual move" fires a *second* time, this time setting `dam = closed`. A correct, confirmed close is thus recorded as two manual moves plus a spurious override.
  - **`cover_unavailable`.** Battery and mains-powered radio covers commonly drop out for a few seconds while the motor runs. Same false manual move.
  - **`moving`** is excluded in practice by gate 7, but §1.4 does not say so.
- **Evidence.** `components/cover/__init__.py`, `CoverEntity.state`: `if self.is_opening: return CoverState.OPENING` / `if self.is_closing: return CoverState.CLOSING` / `if (closed := self.is_closed) is None: return None` / `return CoverState.CLOSED if closed else CoverState.OPEN` — there is no third "moving" signal, so an integration that does not implement the travel flags reports `open` at position 99 and position 1 alike. `CoverEntityFeature` (`components/cover/const.py`) has no feature bit that tells you whether travel flags are implemented, so this cannot be detected from `supported_features`.
- **Fix.** Make "contrary" mean only a **settled `open` or `closed` opposite the pending target**. Specifically, in §1.4: `moving`, `partial` and `cover_unavailable` never count as contrary and never clear a pending record; while a pending record exists, entering `partial` or `moving` **extends** the confirm window rather than terminating it; and the confirm window is measured from the last observed progress, not only from `sent_at`. Add a test case for "position-only cover, no travel flags, 45 s close" to the §6 classification list, and note in §3 that `confirm_window` (default 120 s) must exceed the slowest cover's travel time.

### G2 — `TargetSelectorData` was deprecated in this window, and device targeting only resolves uncategorised entities
- **Severity:** MINOR
- **Spec section:** §4 "Services"; §6 "Deprecations avoided"
- **Problem.** Two details behind the (correct) choice of `async_extract_referenced_entity_ids`. First, the obvious companion class is deprecated with removal in **2026.12** — the same short clock as the reload rule the spec already guards against, yet it is missing from the "Deprecations avoided" list. Second, the extractor's default excludes entities that have an `entity_category` from device/area expansion. The spec's §4 allows "per-cover devices" as a target; that works only because `status` and `manual_override` are deliberately left uncategorised. The `reset_override` button, `enabled` switch, `mode` select and the two diagnostic binary sensors on the same device will *not* be returned. That is fine — the integration only needs to identify the cover — but it makes an apparently cosmetic future change (marking `status` as DIAGNOSTIC) silently break device targeting.
- **Evidence.** `helpers/target.py:106` — `@deprecated_class("TargetSelection", breaks_in_ha_version="2026.12.0")` on `class TargetSelectorData`. `helpers/target.py:65-101` — `class TargetSelection.__init__(self, config: ConfigType)`; core calls it as `TargetSelection(service_call.data)` (`helpers/service.py:379,411,423,700`; `components/homeassistant/__init__.py:118`). `helpers/target.py:158-176` — `async_extract_referenced_entity_ids(hass, target_selection, expand_group=True, *, primary_entities_only: bool = True)`, whose docstring states that with the default "entities with an `entity_category` … are excluded from indirect expansion via device, area, and floor". `helpers/deprecation.py:156` — `deprecated_hass_argument`, confirming the 2026.10 item the spec already lists.
- **Fix.** In §4, name the construction explicitly: `async_extract_referenced_entity_ids(hass, TargetSelection(call.data))`, treat `TargetSelection.has_any_target == False` as "all covers", and state that a resolved entity is mapped back to its cover through the uncategorised `status` / `manual_override` entities (or, more robustly, resolve `SelectedEntities.referenced_devices` directly to subentry ids via `device.config_subentry_id`). Add `TargetSelectorData → TargetSelection (2026.12)` to the §6 list, and add a note that `status` must stay uncategorised for device targeting to work.

### G3 — The 2027.10 removal date for the device-registry shims is not in the code
- **Severity:** MINOR
- **Spec section:** §6 "Deprecations avoided by construction"
- **Problem.** The list asserts "`DeviceEntry.config_entries*` shims (2027.10)". The shims exist and are documented as deprecated, but they carry no `breaks_in_ha_version` and no removal release is stated anywhere. Six of the seven entries in that list are exact; this one invents a date, which will read as authoritative to whoever maintains the file.
- **Evidence.** `helpers/device_registry.py:454-491` — `config_entries`, `config_entries_subentries` and `primary_config_entry` are plain `@property` shims whose docstrings say only "Deprecated compatibility shim: a device now belongs to a single config entry…"; none is wrapped in `report_usage` or `deprecated_*`. `grep -rn "2027\.10" helpers/ config_entries.py` over the 2026.8.3 tree returns nothing.
- **Fix.** Change the entry to "`DeviceEntry.config_entries*` shims (deprecated, no removal date announced)". The design already avoids them via §3's "Device lookups are always `config_entry_id`-scoped", so nothing else changes.

### G4 — `dependencies: ["sun"]` does not guarantee that `sun.sun` exists
- **Severity:** MINOR
- **Spec section:** §6 manifest; §5 "Setup order"; §2 sun-hits seeding
- **Problem.** §5 validates the weather entity at setup and raises `ConfigEntryNotReady` when it is missing, but says nothing about `sun.sun`, apparently relying on the manifest dependency. In 2026.8 `sun` is a config-entry integration: the manifest dependency guarantees the *component* is set up, not that a sun config entry exists. A user who removed the Sun entry (or whose entry has not finished setting up when this integration loads on a slow boot) leaves `hass.states.get("sun.sun")` as `None`, and §2's "Seed at startup/reload with the strict test" has nothing to read — the shading layer would silently behave as if the sun never hit any window.
- **Evidence.** `components/sun/manifest.json` in 2026.8.3: `"config_flow": true`, `"single_config_entry": true`, `"integration_type": "service"` — i.e. the sun entity comes from a config entry the user can delete.
- **Fix.** Add `sun.sun` to §5's setup validation on the same footing as the weather entity: missing or `unavailable` at setup → `ConfigEntryNotReady` (HA retries), and a repair issue if it disappears at runtime. §2's seeding step then has a defined precondition.

### G5 — Cover feature validation cannot always run at config time
- **Severity:** MINOR
- **Spec section:** §3 "Cover subentry — `cover_entity` (required; must support OPEN+CLOSE or SET_POSITION)"
- **Problem.** `supported_features` is an attribute of the live state object, not of the entity registry entry. A cover that is `unavailable` or `unknown` when the user runs the subentry flow — a battery shutter asleep, an integration still starting — exposes no state object and therefore no feature bits, so the stated validation would either reject a perfectly good cover or has to be skipped. §4 already defines the runtime repair issue ("cover supports neither open/close nor position"), so the fallback exists; §3 just over-promises.
- **Evidence.** `components/cover/__init__.py` registers `SERVICE_OPEN_COVER` / `SERVICE_CLOSE_COVER` with `[CoverEntityFeature.OPEN]` / `[CoverEntityFeature.CLOSE]`; the check in `helpers/service.py:744-753` reads `entity.supported_features` from the live entity, and an explicitly referenced entity without the feature raises `ServiceNotSupported`. Registry entries do carry a cached `supported_features`, but it is `None` until the entity has been added at least once.
- **Fix.** Reword §3 to "validated when the cover currently reports a state; otherwise accepted and re-checked at every setup, raising the §4 repair issue if the cover supports neither path". Gate 11 already handles the runtime branch.

### G6 — §5 mixes flow-time and setup-time weather validation
- **Severity:** MINOR
- **Spec section:** §5 "Setup order"
- **Problem.** "validate weather entity (missing → `ConfigEntryNotReady`; lacks daily forecast → abort with error in the flow, repair issue later)" describes two different lifecycles in one clause. There is no flow running during `async_setup_entry`, so "abort with error in the flow" cannot happen there; and at setup the *entity exists but has no `FORECAST_DAILY` bit yet* case is indistinguishable from "not finished starting", which argues for retry rather than a repair issue.
- **Evidence.** `components/weather/__init__.py:214-222` — the service is registered with `required_features=[FORECAST_DAILY, FORECAST_HOURLY, FORECAST_TWICE_DAILY]` (an any-of test, `helpers/service.py:744-750`), and `async_get_forecasts_service` then raises `HomeAssistantError("… does not support 'daily' forecast")` for an hourly-only entity — a runtime error, not something the setup path can pre-empt reliably. `exceptions.py` `ConfigEntryNotReady` is the retry mechanism.
- **Fix.** Split the clause: (a) the config and reconfigure flows reject a weather entity lacking `WeatherEntityFeature.FORECAST_DAILY`; (b) `async_setup_entry` raises `ConfigEntryNotReady` while the entity is missing **or** reports no `FORECAST_DAILY`, and only creates the repair issue once the first forecast fetch fails with `HomeAssistantError` after the entity is otherwise healthy.

### G7 — `_unrecorded_attributes` must be a literal class attribute
- **Severity:** MINOR
- **Spec section:** §4 status sensor
- **Problem.** §4 expresses the rule as a subtraction ("all attributes except `desired_state`, `actual_state`, `reason`"). HA resolves the set once, at class-definition time, in `__init_subclass__`; it cannot be built per instance or per subentry. The rule is fine as written because the attribute list is fixed, but the implementation must spell the frozenset out literally in `sensor.py` rather than derive it from the attribute dict at runtime.
- **Evidence.** `helpers/entity.py:551-559` — `_unrecorded_attributes: frozenset[str] = frozenset()` and `__combined_unrecorded_attributes … set automatically by __init_subclass__`; the combined set is published to the recorder once per entity class.
- **Fix.** One sentence in §4 or §6: the excluded set is a literal `_unrecorded_attributes` frozenset on the status sensor class, listing the eleven excluded attribute names. (The rule correctly excludes `next_planned_action`, whose retry timestamp would otherwise write a recorder row per deferral.)

### G8 — §1.4 and §1.5c disagree about which sends clear `dam`
- **Severity:** MINOR (spec-internal; flagged for the behaviour reviewer)
- **Spec section:** §1.4 "On send"; §1.5 c
- **Problem.** §1.4 clears `dam` on *every* send; §1.5 c lists the ending condition as "the engine sends a command to the cover (door, wind, schedule)" — shading omitted. In the default `passive` reopening mode the difference is mostly unreachable, because gate 5 already suppresses any command whose target equals `dam`. It becomes reachable under `reopening_mode: active`, where a shading-layer `open` with target ≠ `dam` is sent and would, per §1.4, end the override that §1.5 says only those three layers end.
- **Evidence.** Spec-internal; no platform API involved.
- **Fix.** Pick one and make §1.5 c match §1.4 (or exclude shading sends in §1.4). Either way it needs a named test case alongside the §6 "override lifecycle a–e in both reopening modes" entry.

## 4. Verification log (all `file:line` from `homeassistant==2026.8.3`)

**Claims the spec names, confirmed**
- `helpers/target.py:158-176` — `async_extract_referenced_entity_ids(hass, target_selection, expand_group=True, *, primary_entities_only=True)`; docstring states the entity-category exclusion for indirect expansion.
- `helpers/target.py:65-101` — `TargetSelection(config: ConfigType)`; `has_any_target`. `:106` — `@deprecated_class("TargetSelection", breaks_in_ha_version="2026.12.0")` on `TargetSelectorData`. Core usage: `helpers/service.py:379,411,423,700`, `components/homeassistant/__init__.py:118`.
- `config_entries.py:643-649` — `ConfigEntry.get_subentries_of_type`.
- `config_entries.py:3489` (`ConfigFlow.async_update_and_abort`), `:3531` (`ConfigFlow.async_update_reload_and_abort`, with `report_usage(..., breaks_in_ha_version="2026.12.0")` at `:3570-3576`), `:3798` (`ConfigSubentryFlow.async_update_and_abort`), `:3827` + `:3858` (`ConfigSubentryFlow.async_update_reload_and_abort` → `ValueError`), `:3944-3948` (`OptionsFlowWithReload` → `ValueError`).
- `helpers/start.py:33-35` — `async_at_started` runs the job immediately via `hass.async_run_hass_job` when the core is already running.
- `helpers/storage.py:238` (`minor_version: int = 1`), `:464` (`async_save`), `:480` (`async_delay_save`), `:620` (`_async_migrate_func`), `:624` (`async_remove`).
- `helpers/entity.py:551-559` — `_unrecorded_attributes` / `__combined_unrecorded_attributes` via `__init_subclass__`.
- `config_entries.py:4164` — `hasattr(component, "async_remove_config_entry_device")`.
- `helpers/entity_registry.py:1999-2008` — `EVENT_ENTITY_REGISTRY_UPDATED` update payload; `old_entity_id` present only when `old.entity_id != entity_id`.
- `components/weather/const.py:28,31` — `WeatherEntityFeature.FORECAST_DAILY = 1`. `components/weather/__init__.py:214-222` — `required_features`, `supports_response=SupportsResponse.ONLY`. `:707-708` — forecast converted to the entity's display unit.
- `components/cover/const.py` — `CoverEntityFeature.OPEN=1, CLOSE=2, SET_POSITION=4`; `CoverState`; `CoverEntityStateAttribute`.
- `components/cover/__init__.py` — `CoverEntity.state` (opening/closing → `OPENING`/`CLOSING`; else `CLOSED if is_closed else OPEN`); `:103,107` service registration with feature gates.
- `helpers/deprecation.py:156` — `deprecated_hass_argument`; `:89` — `deprecated_class`. `helpers/service.py:351,364,403,418,1011` — the five decorated helpers, `breaks_in_ha_version="2026.10"`.
- `helpers/issue_registry.py:23` (`EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED`), `:58` (`active`), `:62` (`dismissed_version`), `:339`/`:415` (`async_create_issue` / `async_delete_issue`).
- `helpers/device_registry.py:1740-1771` (`async_get_or_create` with `config_subentry_id` and `via_device_id`; via_device removal 2027.8), `:1863-1871` (`DeviceInfoError` on unresolvable `via_device_id`), `:1937-1955` (cross-subentry move `report_usage`, 2027.8), `:3069-3080` (`async_clear_config_subentry` removes devices), `:3153` (`async_get_device_id_by_identifier`), `:1558` (`async_get_device_by_identifier`, entry-scoped).
- `components/sun/entity.py:80-87` — `_PHASE_UPDATES` (day 4 min, small_day/twilight 2 min); `:93` — `_unrecorded_attributes`.
- `helpers/event.py:1552` (`async_call_later`), `:1853` (`async_track_time_change`, local-time). `const.py:273` — `EVENT_CORE_CONFIG_UPDATE`.
- `loader.py:966` — `single_config_entry`. `helpers/translation.py:101` — translations loaded only from `translations/<lang>.json`.
- Translation key shapes: `components/bayesian/translations/en.json` — top-level keys `['config','config_subentries','issues','options','selector','services']`; `config_subentries.observation` keys `['abort','entry_type','error','initiate_flow','step']`; `issues.<key>.{title,description}`. Select option shape `entity.select.<key>.state.<option>` confirmed from a core example.

**Claims the spec makes that the code does not support**
- No occurrence of `2027.10` in `helpers/` or `config_entries.py`; `helpers/device_registry.py:454-491` — the `config_entries` / `config_entries_subentries` / `primary_config_entry` shims carry no `breaks_in_ha_version` (→ G3).
- `components/sun/manifest.json` — `"config_flow": true`, `"single_config_entry": true`; a manifest `dependencies: ["sun"]` therefore does not guarantee the `sun.sun` entity (→ G4).

**Setup-order race check (§5), negative result**
- `helpers/entity_platform.py:507` — `_async_setup_platform` drains pending add tasks with `await asyncio.gather(*pending)`, so `await async_forward_entry_setups(...)` returns only after every `async_added_to_hass` has completed. Combined with the spec's order (create devices → load Store → forward platforms → register update listener → subscribe → `async_at_started`), entities read committed Store values and the first evaluation cannot precede platform setup. One implementation note: `async_at_started` runs the job **synchronously** if the callback is a plain `@callback` (`helpers/start.py:33-35`); making the first-evaluation callback a coroutine keeps it a task, which is the safer default but is not required given the ordering.

**Tooling pins, re-confirmed**
- `homeassistant-2026.8.3.dist-info/METADATA` — `Requires-Python: >=3.14.2`.
- PyPI: `pytest-homeassistant-custom-component==0.13.357` pins `homeassistant==2026.8.3`; requires Python `>=3.14`; brings `pytest-asyncio==1.4.0`, `pytest-freezer==0.4.9`, `pytest-socket`, `pytest-timeout`, `pytest-unordered` — matching §6.
- `api.github.com/repos/home-assistant/actions` → 200 with `hassfest/action.yml`; `api.github.com/repos/hacs/action` → 200. SHA pinning as stated in §6 remains correct.