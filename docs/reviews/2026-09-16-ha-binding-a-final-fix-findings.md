# Final whole-branch review — fix wave findings (plan 2a, branch feature/ha-binding-a, FIX_BASE 1e0751e)

Fix every item below in ONE pass, one or a few commits, in /Users/julian/Projects/hass-cover-automation/.worktrees/ha-binding-a.
Controller rulings are binding where given. Cite the item number in each commit message body.

## F1 (Important) Spec contradicts the shipped manifest
- `docs/superpowers/specs/2026-09-15-cover-automation-design.md` §4 and §6 still say `dependencies: ["sun", "weather", "logbook"]`.
- Ruling: amend BOTH places to `dependencies: ["weather"]`, `after_dependencies: ["logbook", "sun"]` with one sentence of rationale: no core integration hard-depends on sun or logbook; logbook discovers `logbook.py` describers itself via `async_process_integration_platforms`; a hard logbook dependency would drag in http/frontend/recorder and break recorder-less installs; setup raises ConfigEntryNotReady while `sun.sun` is missing (sun is part of default_config). Bump the spec revision line to 3.3 and add a short changelog entry if the spec has one.
- Also append decision #30 to `docs/design-decisions.md` in the table's existing format: "Manifest dependencies: hard on `weather` only; `sun` and `logbook` are after_dependencies" with that rationale.
- README: add one line under Install: the integration needs the `sun` integration (included in `default_config`) and a weather integration that provides a daily forecast.

## F2 (Important) One malformed subentry fails the whole hub
- `custom_components/cover_automation/__init__.py`: `cover_config(subentry, hub)` raises ValueError (unknown shading_rule/wind_action), KeyError (missing azimuth/cover_entity) or TypeError; `profile(subentry)` raises ValueError on a bad time string. Nothing catches these, so `async_setup_entry` ends in SETUP_ERROR and every cover stops.
- Ruling: isolate per subentry. Wrap the per-cover mapping in `try/except (ValueError, KeyError, TypeError)`: log a warning, raise a non-fixable repair issue `broken_cover_config_<subentry_id>` (translation_key `broken_cover_config`, placeholders `cover` = subentry title, `error` = str(err)), and skip that cover (no device? — keep creating the device so the user can still find/delete it; just no runtime cover). Do the same for profiles with `broken_profile_config_<subentry_id>` (translation_key `broken_profile_config`, placeholders `profile`, `error`); a skipped profile is simply absent from `profiles`, so covers referencing it get the existing missing_profile handling. Include these two issue-id families in the owned-issue set so the stale sweep and `async_remove_entry` clean them up. Add both issues to `translations/en.json` (`issues.broken_cover_config`, `issues.broken_profile_config`, title + description) keeping recursive key sort.
- Hub-level `hub_config` failures (missing weather_entity) stay fatal — that is the hub's identity; convert the bare KeyError into `ConfigEntryError` with a clear message.
- Tests in `tests/ha/test_init.py`: a cover subentry with `shading_rule: "bogus"` plus a valid one → entry LOADED, only the valid cover in `runtime_data.covers`, `broken_cover_config_<id>` issue present; removing that subentry → issue gone. A profile subentry with rule time `"25:99"` → entry LOADED, profile absent, `broken_profile_config_<id>` present.

## F3 (Important) CI actions not SHA-pinned (spec §6 requires SHA pinning)
- `.github/workflows/ci.yml`: pin `actions/checkout`, `actions/setup-python`, `home-assistant/actions/hassfest`, `hacs/action` to full commit SHAs with a trailing `# vX.Y.Z` / `# master @ YYYY-MM-DD` comment. Resolve SHAs with `git ls-remote https://github.com/<owner>/<repo> <ref>` (tags: `refs/tags/v4`, peel `^{}` if annotated; branches: `refs/heads/master`). If the network is unavailable, STOP and report BLOCKED for this item only (do the rest).

## F4 (Important) `hacs` CI job will fail on the `brands` check
- Ruling: add `ignore: brands` under the hacs/action step's `with:` (the brand PR to home-assistant/brands is a later task), with a comment saying to remove it once the brands PR lands.

## F5 (Important) No end-to-end test of the flow → config_map seam
- Add `tests/ha/test_flow_to_engine.py`: (a) run the cover subentry flow to CREATE_ENTRY with the defaults (hub with wind sensor), take the created `ConfigSubentry` from `hub_entry.subentries`, call `config_map.cover_config(subentry, config_map.hub_config(hub_entry))` and assert engine values: `cfg.cover_id == subentry.subentry_id`, `wind_hold_s == 900`, `min_move_interval_s == 600`, `confirm_window_s == 120`, `profile_id is None`, `comfort_floor == 21.0`, `comfort_ceiling == 25.0`, `shading_rule is ShadingRule.FORECAST_WITH_ROOM`, `wind_action is WindAction.OPEN`, `bind.temperature_unit == "°C"`, `bind.wind_unit == "km/h"`; (b) run the profile subentry flow with one fixed close rule at 21:30 and quiet hours 22:00–07:00, feed the subentry to `config_map.profile()` and assert `rules[0].action is Target.CLOSED`, `rules[0].time == time(21, 30)`, `quiet_hours == QuietHours(time(22, 0), time(7, 0))`; (c) run the hub user flow to CREATE_ENTRY with defaults and assert `hub_config(entry).sunny_on_delay_s == 600`, `weather_grace_s == 1800`, `override_dwell_s == 1800`, `hot_low == 13.0`, `temperature_unit == "°C"`.

## F6 (Minor 8) Empty profile name accepted
- `ProfileSubentryFlow`: an all-whitespace/empty name must be rejected with a field error `errors[const.CONF_NAME] = "name_required"`; add `config_subentries.profile.error.name_required` to en.json. Test it.

## F7 (Minor 9) Stale profile default renders an unselectable value
- `cover_schema`: if the stored `schedule_profile` is not `none` and not among the current profile subentry ids, use `const.PROFILE_NONE` as the selector default. Test: cover subentry referencing a deleted profile id → reconfigure form renders and submitting with the default succeeds.

## F8 (Minor 10) `async_at_started` callback can touch a deleted `runtime_data`
- In the deferred optional-entity check, guard with `if getattr(entry, "runtime_data", None) is None: return` before writing `missing_entities`.

## F9 (Minor 21) README opens with a capability claim
- Move the "this version installs and configures; behaviour, entities and services arrive with the next release" disclaimer into the first paragraph (right after the one-line description), and phrase the opening as the project's purpose.

## Verification
- `.venv/bin/pytest` (all), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/pyright`, and the translation test must stay green (en.json recursively sorted). Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
