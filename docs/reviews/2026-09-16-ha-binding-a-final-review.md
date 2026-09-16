# Plan 2a (HA binding foundation) — final whole-branch review

Reviewer: Opus 5, range 4b03104..1e0751e (17 commits). Verdict: **ready to merge with fixes**; fixes applied in the
fix wave (see `2026-09-16-ha-binding-a-final-fix-findings.md` and `-final-fix-report.md`), re-reviewed clean at 05bd442.

## Strengths (condensed)
- Plan scope complete; every Global Constraint honoured; engine untouched; ruff/pyright clean; all commits carry the trailer.
- 2026.8 API usage verified against the pinned source: `config_subentry_id`/`via_device_id`, `async_update_and_abort` (data replace),
  `async_update_reload_and_abort` would raise with a listener attached (plan rule is load-bearing), subentry add/remove/update all
  fire the single listener, subentry removal clears devices/entities, `section(...)` signature, translation shape for subentries.
- Store matches spec §5 exactly and is tolerant in three layers. Repair-issue lifecycle created/cleared/swept with tests.
- Unit handling: bounds/defaults converted, stored values never converted; options and cover reconfigure re-stamp the stored unit.
- Tests drive real HA machinery; no lingering-timer suppressions.
- Cross-module shapes line up key for key (minutes → `_minutes`, `confirm_window` seconds, `none` sentinel, rule dicts).

## Issues found (all fixed in the wave unless marked)
Important
1. Spec §4/§6 contradicted the shipped manifest (ruling: `dependencies: ["weather"]`, `after_dependencies: ["logbook","sun"]`) → spec 3.3, decision #30, README line.
2. One malformed subentry failed the whole hub → per-cover/per-profile isolation with `broken_cover_config_*` / `broken_profile_config_*` repairs; hub-level defect → `ConfigEntryError`.
3. CI actions not SHA-pinned (spec §6) → all four pinned with version comments.
4. `hacs` CI job would fail on the brands check → `ignore: brands` until the brands PR lands.
5. No flow → `config_map` seam test → `tests/ha/test_flow_to_engine.py` (cover, profile, hub).
Minor fixed: 8 empty profile name (`name_required`), 9 stale profile default → `none`, 10 `runtime_data` guard in the deferred check, 21 README disclaimer first paragraph.

## Deferred to plan 2b (reviewer triage + controller ledger)
- `form_from_profile_data` prefill test (priority: `data=` replace means a prefill regression silently drops rules).
- Options/reconfigure `data_description`; `initiate_flow.reconfigure` labels; `rule_1` section description on reconfigure.
- Override-entity clearing test; weather `unavailable`/no-daily `ConfigEntryNotReady` tests.
- `DEFAULT_SUNNY_CONDITIONS` → tuple; `DEFAULT_*` for azimuth and wind thresholds (unit-aware); `STATE_UNAVAILABLE/UNKNOWN` constants.
- Store: prune records of removed covers; `_async_migrate_func`; real `async_migrate_entry` calling `async_update_entry(version=…)` before any bump.
- `elevation_min < elevation_max` validation; `_opt_str` `none` sentinel scoped to `schedule_profile`; `hub_config` unit fallback comment; `cover` vs `weather` manifest dependency consistency.
- Owed by the spec, not in 2a: wind-sensor-unit-changed repair (§2); entity-rename following via `EVENT_ENTITY_REGISTRY_UPDATED` (§3); a path to change a stored `temperature_unit`; re-evaluate `missing_entity` issues from the controller's state subscriptions; `missing_entities` → problem sensor or delete the field.
- Design notes for 2b: `HubConfig.temperature_unit` and `CoverBindings.temperature_unit` can differ on one install — convert per value against the unit that travels with it, never against `hass.config.units`; convert `CoverConfig.comfort_*` before any engine call; device/unique-id conventions ready (`(DOMAIN, entry_id)` hub, `(DOMAIN, subentry_id)` covers, Store keyed by `subentry_id`, `hub_device_id` on `runtime_data`); `PLATFORMS` is `[]` and already wrapped.
- Dropped by triage: `MIN_CONFIRM_WINDOW_S` (now used), `DEFAULT_SHADING_RULE/WIND_ACTION` (enum is the source), fixture int/float, `test_single_instance` scope, user/reconfigure duplication, thresholds re-validation, conftest imports, `rule_1..4` hardcoding (test loops `MAX_RULES`), brief interface drift, `already_configured` scan, Store save ordering, late import in tests.
