# Final fix wave report — branch feature/ha-binding-a

Base: `1e0751e`. Worked in `/Users/julian/Projects/hass-cover-automation/.worktrees/ha-binding-a`.
Commits: `92b07e5` (docs+ci), `31949ad` (code), `05bd442` (tests).

## F1 — Spec/manifest dependency contradiction

The manifest (`custom_components/cover_automation/manifest.json:2,5`) already had
`dependencies: ["weather"]` / `after_dependencies: ["logbook", "sun"]`; only the spec and
docs still said otherwise.

- `docs/superpowers/specs/2026-09-15-cover-automation-design.md:384-388` (§4) and
  `:474-476` (§6): rewrote the `dependencies`/`after_dependencies` statements plus the
  one-sentence rationale from the ruling (no core integration hard-depends on sun/logbook;
  logbook self-discovers via `async_process_integration_platforms`; a hard logbook
  dependency drags in http/frontend/recorder; setup raises `ConfigEntryNotReady` while
  `sun.sun` is missing).
- `docs/superpowers/specs/2026-09-15-cover-automation-design.md:3`: revision bumped
  3.2 → 3.3. The spec has no changelog section, so none was added (per the ruling's
  "if the spec has one").
- `docs/superpowers/specs/2026-09-15-cover-automation-design.md:4`: also updated the
  stale `#1–#29` decision-log cross-reference to `#1–#30` while touching this line (not
  explicitly requested by F1, but left stale by adding decision #30 in the same commit).
- `docs/design-decisions.md`: appended row `| 30 | Manifest dependencies | ... |` in the
  existing table format with the same rationale.
- `README.md:17-18`: added "Needs the `sun` integration (included in `default_config`) and
  a weather integration that provides a daily forecast." under Install.

## F2 — One malformed subentry no longer fails the whole hub

`custom_components/cover_automation/__init__.py`:
- Added `_broken_cover_config_issue_id`/`_broken_profile_config_issue_id` (lines 54-59)
  and prefixes `_BROKEN_COVER_ISSUE_PREFIX`/`_BROKEN_PROFILE_ISSUE_PREFIX` (lines 29-30).
- `async_setup_entry` (lines 206-280): `hub_config(entry)` is now wrapped in
  `try/except KeyError` and re-raised as `ConfigEntryError` (hub-level failure stays
  fatal, per the ruling). The profile-building loop and the cover-building loop each wrap
  their per-subentry call in `try/except (ValueError, KeyError, TypeError)`: log a
  warning, create a non-fixable repair issue (`broken_profile_config_<id>` /
  `broken_cover_config_<id>`, translation keys `broken_profile_config` /
  `broken_cover_config`, placeholders `profile`/`cover` = subentry title, `error` =
  `str(err)`), and skip just that subentry (`continue` for covers; the profile is simply
  omitted from `profiles`, so a cover pointing at it falls through to the existing
  `missing_profile` handling as specified). `ensure_devices` (unchanged, lines 178-203)
  still iterates `get_subentries_of_type(SUBENTRY_COVER)` independently of the cfg
  mapping, so a broken cover's device is still created.
- `_entry_issue_ids` (lines 94-109) now also includes
  `broken_cover_config_<id>`/`broken_profile_config_<id>` for every currently-configured
  subentry, and `_delete_stale_issues` (lines 112-141) sweeps both new prefixes — so
  removing a broken subentry clears its issue, and `async_remove_entry` (unchanged, calls
  `_delete_stale_issues` with `owned_issue_ids=set()`) clears it on entry removal too.
- `custom_components/cover_automation/translations/en.json`: added
  `issues.broken_cover_config` and `issues.broken_profile_config` (title + description),
  recursively key-sorted (verified programmatically, see Verification).

Tests added in `tests/ha/test_init.py`:
- `test_broken_cover_config_is_isolated_and_repaired`: a cover subentry with
  `shading_rule: "bogus"` plus a valid one → entry `LOADED`, only the valid cover in
  `runtime_data.covers`, `broken_cover_config_<id>` issue present with
  `translation_key == "broken_cover_config"`; removing the broken subentry → entry stays
  `LOADED` and the issue is gone.
- `test_broken_profile_config_is_isolated_and_repaired`: a profile subentry with rule
  time `"25:99"` → entry `LOADED`, profile id absent from `runtime_data.profiles`,
  `broken_profile_config_<id>` present with `translation_key == "broken_profile_config"`.

## F3 — CI actions SHA-pinned

Network was available; resolved with `git ls-remote`:

| Action | Ref | SHA | Comment |
|---|---|---|---|
| `actions/checkout` | `refs/tags/v4` (lightweight, = v4.4.0) | `11d5960a326750d5838078e36cf38b85af677262` | `# v4.4.0` |
| `actions/setup-python` | `refs/tags/v5` (lightweight, = v5.6.0) | `a26af69be951a213d495a4c3e4e4022e16d87065` | `# v5.6.0` |
| `home-assistant/actions/hassfest` | `refs/heads/master` | `58bff37c8947f690ace498be413a9b78d6f30f93` | `# master @ 2026-09-10` |
| `hacs/action` | `refs/heads/main` | `1ebf01c408f29afcb6406bd431bc98fd8cbb15aa` | `# main @ 2026-06-08` |

Neither tag was annotated (no `^{}` peel entries), so the listed SHA is the commit SHA
directly. Branch dates came from the GitHub API commit lookup. Applied in
`.github/workflows/ci.yml` (all four `uses:` lines) with trailing comments as above.
**Not BLOCKED** — network was available throughout.

## F4 — hacs job `brands` check

`.github/workflows/ci.yml`: added `ignore: brands` under the `hacs/action` step's `with:`,
with a `# TODO: remove once the home-assistant/brands PR lands for this integration.`
comment directly above it.

## F5 — Flow → config_map seam tests

New file `tests/ha/test_flow_to_engine.py` (122 lines), three tests:
- `test_cover_subentry_defaults_map_to_engine_values`: runs the cover subentry flow (hub
  fixture already has a wind sensor) to `CREATE_ENTRY` with only `cover_entity` supplied
  (letting the schema fill in every other default), then calls
  `config_map.cover_config(subentry, config_map.hub_config(hub_entry))` and asserts
  `cfg.cover_id == subentry.subentry_id`, `wind_hold_s == 900`,
  `min_move_interval_s == 600`, `confirm_window_s == 120`, `profile_id is None`,
  `comfort_floor == 21.0`, `comfort_ceiling == 25.0`,
  `shading_rule is ShadingRule.FORECAST_WITH_ROOM`, `wind_action is WindAction.OPEN`,
  `bind.temperature_unit == "°C"`, `bind.wind_unit == "km/h"`.
- `test_profile_subentry_defaults_map_to_engine_values`: runs the profile subentry flow
  with one enabled fixed-close rule at 21:30 and quiet hours 22:00–07:00, feeds the
  subentry to `config_map.profile()`, asserts `rules[0].action is Target.CLOSED`,
  `rules[0].time == time(21, 30)`, `quiet_hours == QuietHours(time(22, 0), time(7, 0))`.
- `test_hub_flow_defaults_map_to_engine_values`: runs the hub user flow to
  `CREATE_ENTRY` with an empty thresholds submission, asserts
  `hub_config(entry).sunny_on_delay_s == 600`, `weather_grace_s == 1800`,
  `override_dwell_s == 1800`, `hot_low == 13.0`, `temperature_unit == "°C"`.

Confirmed empirically (not just assumed) that voluptuous fills in `Required(key,
default=...)` fields absent from the submitted `user_input` dict — all three tests passed
on the first run supplying only the fields with no real default.

## F6 — Empty profile name

`custom_components/cover_automation/config_flow.py` (`ProfileSubentryFlow._step`,
~line 671): after `profile_data_from_form` (which already `.strip()`s the name), an empty
result now short-circuits with `errors[const.CONF_NAME] = "name_required"` before rule
validation runs. Added `config_subentries.profile.error.name_required` to `en.json`.

Test added in `tests/ha/test_subentry_flows.py`:
`test_profile_empty_name_is_rejected` — submits `CONF_NAME: "   "` with all rule slots
disabled, asserts `result["errors"] == {const.CONF_NAME: "name_required"}`.

## F7 — Stale profile default in cover_schema

`custom_components/cover_automation/config_flow.py` (`cover_schema`, ~lines 333-334,
418-423): computed `profile_ids = {p.subentry_id for p in profiles}`; the
`schedule_profile` field's default now falls back to `const.PROFILE_NONE` when the stored
value is neither `PROFILE_NONE` nor a currently-existing profile id.

Test added in `tests/ha/test_subentry_flows.py`:
`test_cover_reconfigure_offers_none_when_profile_deleted` — a cover subentry referencing
`schedule_profile="ghost_profile"` (never created) → reconfigure form renders, the
`schedule_profile` schema marker's `.default()` is `const.PROFILE_NONE`, and submitting
with that default succeeds (`ABORT`/`reconfigure_successful`,
`updated.data[CONF_SCHEDULE_PROFILE] == const.PROFILE_NONE`).

## F8 — async_at_started callback vs. deleted runtime_data

`custom_components/cover_automation/__init__.py`
(`_async_check_optional_entities`, line ~171): added
`if getattr(entry, "runtime_data", None) is None: return` immediately before
`entry.runtime_data.missing_entities = missing`, exactly as ruled. No new test was
written for this (not in the F2/F5/F6/F7 test list the job called out); existing tests
(`test_missing_optional_sensor_deferred_until_started_then_present`,
`test_missing_optional_sensor_still_missing_after_started`) continue to exercise the
non-removed path and still pass.

## F9 — README opening

`README.md`: merged "This version installs and configures the integration; behaviour,
entities and services arrive with the next release." into the first paragraph, right
after the one-line capability description, and removed the old trailing disclaimer
sentence at the end of the Configure section.

## Tests written (new files/tests) and their output

- `tests/ha/test_flow_to_engine.py` (new, 3 tests) — F5
- `tests/ha/test_init.py` +2 tests — F2
- `tests/ha/test_subentry_flows.py` +2 tests — F6, F7

Focused runs during iteration (all green on first pass after implementation):
```
.venv/bin/pytest tests/ha/test_flow_to_engine.py -q   → 3 passed
.venv/bin/pytest tests/ha/test_init.py -q             → 16 passed
.venv/bin/pytest tests/ha/test_subentry_flows.py -q   → 14 passed
.venv/bin/pytest tests/ha/ -q                         → 67 passed
```

## Full verification (final, post-commit)

```
.venv/bin/pytest            → 224 passed (was 217; +7 new tests), exit code 0
.venv/bin/ruff check .      → All checks passed!
.venv/bin/ruff format --check .  → 46 files already formatted
.venv/bin/pyright           → 0 errors, 0 warnings, 0 informations
```

Translation sort verified programmatically (recursive `list(obj) == sorted(obj)` check
over the whole `en.json`, same assertion `test_translation_keys_are_sorted_recursively`
makes) — passes.

`custom_components/cover_automation/engine/` was not touched (`git diff --stat -- .../engine/`
empty).

## Files changed

- `.github/workflows/ci.yml` (F3, F4)
- `README.md` (F1, F9)
- `custom_components/cover_automation/__init__.py` (F2, F8)
- `custom_components/cover_automation/config_flow.py` (F6, F7)
- `custom_components/cover_automation/translations/en.json` (F2, F6)
- `docs/design-decisions.md` (F1)
- `docs/superpowers/specs/2026-09-15-cover-automation-design.md` (F1)
- `tests/ha/test_flow_to_engine.py` (new, F5)
- `tests/ha/test_init.py` (F2 tests)
- `tests/ha/test_subentry_flows.py` (F6, F7 tests)

## Self-review notes

Went through the findings file item by item against the final diff (`git diff
1e0751e..HEAD`):
- F1: manifest itself needed no change (it was already correct); spec §4/§6, decision
  log, README all updated with the exact ruled dependency lists and rationale. Confirmed
  no other mention of the stale `["sun", "weather", "logbook"]` triple remains anywhere
  in the repo (`grep -rn '"sun", "weather", "logbook"'` → no hits after the edit).
- F2: verified the device-creation guarantee holds without code changes, because
  `ensure_devices` never depended on `cover_config` succeeding. Verified both new issue
  prefixes are covered by both the "owned" set (created-this-cycle protection) and the
  stale sweep (cleanup on removal), symmetrically with the existing `missing_profile_*`
  pattern.
- F3: confirmed both tag SHAs are direct commit SHAs (no annotated-tag peel needed) by
  checking for `^{}` refs; cross-checked against the exact point-release tags (`v4.4.0`,
  `v5.6.0`) pointing at the same commits for accurate comments.
- F5: deliberately ran the tests before assuming voluptuous default-fill behavior, to
  avoid over-specifying input dicts that would have masked a real defaults mismatch.
- F6/F7: kept both changes minimal and local to the exact code paths named in the
  ruling; did not touch `validate_cover_input`/`validate_profile` semantics otherwise.
- F8: applied the guard exactly where and how the ruling specified (only before the
  `missing_entities` write), rather than guarding the whole function, since the ruling
  was specific about the guard's placement and issue create/delete calls are safe to run
  regardless of `runtime_data`.
- Confirmed `custom_components/cover_automation/engine/` has zero diff.
- Confirmed commit messages cite finding numbers and end with the required
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer.

## Concerns

None outstanding. One small unrequested addition: while editing the spec's revision
line I also corrected the now-stale `docs/design-decisions.md (decision log, #1–#29)`
cross-reference to `#1–#30` in the same file, since I was the one making it stale by
adding decision #30 in this pass.
