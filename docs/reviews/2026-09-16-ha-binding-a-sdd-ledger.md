# SDD ledger — plan: docs/superpowers/plans/2026-09-16-ha-binding-a.md
Spec: docs/superpowers/specs/2026-09-15-cover-automation-design.md (rev 3.2). Worktree: .worktrees/ha-binding-a, branch feature/ha-binding-a from main 4b03104. Root .venv symlinked into each worktree.
Execution shape (user's standing instruction: parallelize independent tasks in worktrees, reconcile crossovers before merge):
  wave 1: Task 0 + Task 1 (one implementer, serial, feature branch)
  wave 2 (parallel): worktree A = Tasks 2+3 (config_map, store) | worktree B = Task 4 (hub flow, conftest, en.json)
  wave 3 (parallel): worktree C = Task 5 (subentry flows; config_flow.py + en.json) | worktree D = Task 6 (__init__; en.json issues) — crossover: en.json top-level keys, reconcile at merge
  wave 4: Task 7 (translations test + README), then final review

## Preflight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T0/T1 | T0 widens pyright include to whole component + venv config; T1 adds const.py importing HA | ok; T1 dispatch: keep existing pythonVersion/ruff config, only add venvPath/venv/include |
| T1→T2 | CONF_*/DEFAULT_* keys used by config_map | all defined in T1 const.py |
| T1→T3 | STORAGE_VERSION/MINOR, storage_key | defined |
| T3↔engine | DailyLatch.to_dict/from_dict/.date | exist; from_dict raises ValueError on bad date (test relies on it) |
| T2→T5 | parse_time, quiet_from_data, rules_from_data; schedule.validate -> list[str] | consistent |
| T4→T5 | _entity, _number, _clean_optional_entities in config_flow.py; _select defined in T5 | consistent |
| T4→T6 | conftest set_weather(supported_features=1), hub_entry fixture with sensor.wind, cover/profile_subentry_data | consistent with T6 tests |
| T5↔T6 | both add top-level keys to en.json (config_subentries+selector vs issues) | crossover, disjoint keys; reconcile at merge keeping recursive sort (T7 test) |
| T6 self | async_remove_config_entry_device semantics vs test | consistent (hub False, cover False, stale True) |
| T6 self | dr.async_get_device_by_identifier(identifier, config_entry_id) | signature exists in 2026.8.3 |
| T2 self | test_wind_disabled_when_hub_has_no_wind_sensor uses HubConfig.__dict__ but HubConfig is slots=True | DEFECT → Ruling below |
| T4 self | options-flow test posts hub_options() incl. temperature_unit, schema has no such key → vol extra-keys error | DEFECT → Ruling below |
| T5 self | confirm_window selector min=10 would reject the test's value 5 before validate_cover_input runs | DEFECT → Ruling below |
| T3 self | _enum_or_default[E] calls type[E](raw) → pyright call-arg | cosmetic → Ruling below |
| T5/T7 | en.json section objects in T5 snippet not key-sorted; T7 test requires recursive sort | T5 dispatch: write en.json with keys sorted recursively |

Ruling: T2 test builds the no-wind hub with dataclasses.replace(hub, wind_sensor=None) instead of HubConfig(**hub.__dict__) — slots dataclasses have no __dict__ — cost if wrong: none, test-only.
Ruling: T4 options-flow test posts hub_options() minus CONF_TEMPERATURE_UNIT (the unit is recorded by _complete_options, never a form field) — cost if wrong: none, test-only.
Ruling: T5 confirm_window NumberSelector uses min=1 (step 1); the ≥10 s rule is enforced by validate_cover_input so the user sees the translated error instead of a raw selector rejection — cost if wrong: a slightly laxer widget range.
Ruling: T3 _enum_or_default binds E to Enum ([E: Enum]) and drops the type-ignore — cost if wrong: none.
Task 0+1: BASE 4b031046aa58f9cc2cce30446a4afbe8b6043c2c dispatched together (one implementer)
Task 0+1: review (sonnet) — spec ❌ one Important plan-mandated finding: const.py lacks DEFAULT_SHADING_RULE / DEFAULT_WIND_ACTION. ⚠️ trailers verified by controller (3/3 commits carry Co-Authored-By: Claude Fable 5.1); homeassistant 2026.8.3 verified via pip show.
Ruling: no DEFAULT_SHADING_RULE/DEFAULT_WIND_ACTION constants — the plan deliberately sources these two defaults from the engine enums (ShadingRule.FORECAST_WITH_ROOM, WindAction.OPEN) in config_map (T2) and config_flow (T5) so the enum stays the single source of the strings; only numeric defaults without an engine counterpart get DEFAULT_* constants — cost if wrong: two call sites name the enum member instead of one constant (minor DRY nit for the final review to triage).
Task 0+1: minor (deferred): MIN_CONFIRM_WINDOW_S unused until T5; hassfest action pinned to @master (plan text).
Task 0+1: complete (commits 4b03104..94ff1d1 + housekeeping 726d0d3 '.venv' gitignore, review clean after ruling)
wave 2: BASE 726d0d31b22420c157ca73a12936b71110f0ee39 — worktree .worktrees/task-2-3 (branch task/2-3, Tasks 2+3, one implementer) | .worktrees/task-4 (branch task/4, Task 4) dispatched in parallel
Task 2+3: implementer DONE (071333a, 80f38b7; 175 passed); review (sonnet) dispatched on review-726d0d3..80f38b7.diff
Task 2+3: review (sonnet) — spec ❌ one Important plan-mandated: StoreData.from_dict only guards per-record parsing; a malformed `covers` container (or non-mapping raw) raises out of async_load. Minor (deferred): KeyError/AttributeError unreachable at per-record scope; report says 15 HubConfig fields (16).
Ruling: fix the container-level tolerance in this task (spec §5 "never fail setup" is binding; the brief's reference code was incomplete) — cost if wrong: none, strictly more tolerant.
Task 2+3: fix round 1/5 dispatched (resume implementer) — finding: container-level tolerance in StoreData.from_dict.
Task 4: implementer DONE_WITH_CONCERNS (636fbbd; 168 passed) — manifest hard dependency on `logbook` pulls frontend/http/recorder (not installable in the test env, and would make the integration fail on installs without logbook); `sun` hard dependency leaves lingering timers (sun's HassJobs lack cancel_on_shutdown) so the implementer overrode expected_lingering_timers=True for all tests/ha and patched logbook as loaded.
Ruling: manifest becomes dependencies ["weather"], after_dependencies ["sun", "logbook"] — no core integration lists logbook or sun as a hard dependency; logbook discovers describer platforms itself via async_process_integration_platforms, and setup already raises ConfigEntryNotReady while sun.sun is missing (sun is always present via default_config). Both conftest workaround fixtures are removed so the lingering-timer check stays armed for plan 2b's own timers. Spec §6 manifest line to be amended at plan end — cost if wrong: on an install with sun disabled the integration retries with a clear message instead of auto-loading sun.
Task 4: pre-review fix dispatched (resume implementer) — manifest deps ruling + remove both autouse fixtures + adjust test_const_manifest dependency assertion.
Task 2+3: fix round 1/5 done (a593f67; 177 passed) — scoped re-review dispatched. Task 4: fix done (8a14903; 168 passed) — full task review dispatched on 726d0d3..8a14903
Task 2+3: fix round 1/5 (1 addressed, 0 open — StoreData.from_dict container tolerance; commits 80f38b7..a593f67)
Task 2+3: complete (commits 726d0d3..a593f67, review clean after fix round 1); task/2-3 merged into feature/ha-binding-a (fast-forward, disjoint files)
Task 4: review (opus) — spec ✅ ; 3 Important (2 plan-mandated): (1) temperature NumberSelector bounds hard-coded in °C while the label follows the unit system → unusable for °F; (2) options flow re-stamps temperature_unit from the current unit system although the form rendered stored values in the stored unit; (3) _HUB_OPTION_KEYS allowlist duplicated by hand, silent drop of unknown options, no round-trip test.
Ruling (1): temperature fields get a `_temperature(min_c, max_c, default_c, unit)` helper that converts bounds/defaults from °C to the form's unit (TemperatureConverter, rounded to 0.5) — spec §3 "stored with the unit entered" implies entry in the user's unit; the helper is reused by Task 5 for comfort floor/ceiling — cost if wrong: °F ranges slightly off, no data impact.
Ruling (2): options flow passes the displayed unit into _complete_options(user_input, unit) so the stored unit always matches the unit the values were entered in — cost if wrong: none.
Ruling (3): derive _HUB_OPTION_KEYS from thresholds_schema markers; add `set(hub_entry.options) == set(hub_options())` round-trip assertion — cost if wrong: none.
Task 4: minor (deferred): schema default shares the mutable DEFAULT_SUNNY_CONDITIONS list; hub_entry fixture stores ints where the flow stores floats; test_single_instance exercises the manifest gate not the handler guard; options.init lacks data_description (13-key duplicate of thresholds); user/reconfigure steps duplicate ~12 lines; thresholds step doesn't re-validate weather, override-entity clearing untested; helpers imported from conftest.
Task 4: carry to T5: hub_data_schema(defaults) has no hass param; conftest has cover_subentry_data/profile_subentry_data but no add_* helpers; use _temperature helper for comfort floor/ceiling.
Task 4: fix round 1/5 dispatched (resume implementer) — findings 1–3.
Task 4: fix round 1/5 done (5053fa3; 170 passed) — scoped re-review (sonnet) dispatched on 8a14903..5053fa3
Task 4: fix round 1/5 (3 addressed, 0 open — unit-aware bounds, options unit, derived allowlist; commits 8a14903..5053fa3)
Task 4: complete (commits 726d0d3..5053fa3, review clean after fix round 1); task/4 merged into feature/ha-binding-a (merge commit, disjoint files vs task/2-3); full suite + ruff + pyright green on the merged branch
wave 3: BASE 77ff0d0050f44b40b063c09ea6d9797b1e5fa197 — worktree .worktrees/task-5 (branch task/5, Task 5) | .worktrees/task-6 (branch task/6, Task 6) dispatched in parallel; crossover: translations/en.json (T5 adds config_subentries+selector keys, T6 adds issues)
Task 6: implementer DONE (6235942; 193 passed) — review (opus) dispatched on review-77ff0d0..6235942.diff
Task 5: implementer DONE (82f7c0e; 194 passed) — review (opus) dispatched on review-77ff0d0..82f7c0e.diff
Task 6: review (opus) — spec ✅ ; 4 Important: (1) repair issues orphaned when a reference/subentry/entry disappears (delete paths only iterate still-configured refs; nothing in async_remove_entry); (2) optional entities checked only once at setup → spurious missing_entity repairs on restart for slow integrations; (3) plan-mandated: missing profile is logged/repaired but CoverConfig/CoverBindings still carry the dangling profile_id; (4) repair lifecycle untested (clear-on-present, missing_profile, subentry removal → reload).
Ruling (1): setup computes the set of issue ids it owns (missing_entity_<entry_id>_* and missing_profile_<subentry_id> for this entry's cover subentries) and deletes every other such issue of this domain; async_remove_entry deletes them all — cost if wrong: none.
Ruling (2): the optional-entity check runs through homeassistant.helpers.start.async_at_started (immediate when HA is already running) so slow integrations have finished startup; plan 2b's controller must re-evaluate missing_entity issues when it subscribes to the entities (ledger note for 2b) — cost if wrong: a repair may lag until the next reload.
Ruling (3): missing profile → dataclasses.replace(cfg, profile_id=None) and replace(bindings, profile_id=None) before storing in covers — cost if wrong: none.
Task 6: minor (deferred): weather unavailable / no-daily ConfigEntryNotReady branches untested; Store keeps records of removed cover subentries (prune to live covers); Store saved before platform-unload result known; missing_entities on runtime_data unused so far; late import + unannotated fixtures in test_init.py.
Task 6: fix round 1/5 dispatched (resume implementer) — findings 1–4.
Note for plan 2b: re-evaluate missing_entity repair issues from the controller's state subscriptions; prune stale Store cover records.
Task 5: review (opus) — spec ❌ 3 Important: (1) cover reconfigure re-stamps temperature_unit from the current unit system (stored comfort values relabelled without conversion); (2) wind_unit dropped on reconfigure when the wind sensor has no state; (3) plan-mandated: quiet-hours consistency check unreachable — profile_data_from_form drops a half-entered pair before validate_profile sees it.
Ruling (1): reconfigure renders and stamps the subentry's stored temperature_unit (fallback: current unit system) — mirrors the options-flow ruling — cost if wrong: none.
Ruling (2): wind_unit falls back to the stored value when the sensor has no unit right now — cost if wrong: none.
Ruling (3): profile_data_from_form keeps a half-entered quiet-hours pair (None for the missing half) so validate_profile reports "quiet hours need both a start and an end" and the flow refuses to create/update; both empty → no quiet keys stored — cost if wrong: none.
Task 5: minor (deferred): form_from_profile_data prefill unasserted; string literals for unavailable/unknown; magic defaults azimuth 180 / wind 60/50; en.json hardcodes rule_1..4 while _RULE_KEYS derives from MAX_RULES; reconfigure steps lack data_description; empty profile name accepted; brief Interfaces drift (validate_profile returns list[str], cover_schema has no editing_subentry_id); already_configured scan keeps iterating after a match.
Task 5: fix round 1/5 dispatched (resume implementer) — findings 1–3.
Task 6: fix round 1/5 done (84a297e; 199 passed) — scoped re-review (sonnet) dispatched on 6235942..84a297e
Task 5: fix round 1/5 done (e9354ff; 197 passed) — scoped re-review (sonnet) dispatched on 82f7c0e..e9354ff
Task 6: fix round 1/5 (4 addressed, 0 open — stale-issue sweep + async_at_started + profile_id cleared + lifecycle tests; commits 6235942..84a297e)
Task 6: complete (commits 77ff0d0..84a297e, review clean after fix round 1)
Task 5: fix round 1/5 (3 addressed, 0 open — stored units on reconfigure, wind_unit fallback, half quiet-hours rejected; commits 82f7c0e..e9354ff)
Task 5: complete (commits 77ff0d0..e9354ff, review clean after fix round 1)
wave 3 merge: task/6 merged cleanly, task/5 conflicted on translations/en.json as predicted; reconciled by scripted JSON union (base 77ff0d0 + task/5 config_subentries/selector + task/6 issues, sorted recursively) in merge commit 6cf663e; merged branch: 211 passed, ruff clean, pyright 0 errors, en.json recursively sorted.
Task 7: BASE 6cf663e — dispatched on feature/ha-binding-a directly (last task, serial)
Task 7: implementer DONE (b9449e9; 217 passed, no en.json gaps) — review (sonnet) dispatched on review-6cf663e..b9449e9.diff
Task 7: review (sonnet) — spec ✅, quality Approved but 1 Important plan-mandated: test_selector_options_and_issues_are_translated hardcodes 4 selector option sets instead of deriving them from ShadingRule/TimeMode/WindAction/Target (a new enum member without translation would pass). Minor (deferred): README opening line reads as present-tense capability before the "installs and configures only" disclaimer (brief text); config_subentries.*.abort keys not asserted.
Ruling: derive the four option sets from the engine enums (brief text was weaker than its own intent: "guaranteeing every ... selector option used in code has a translation") — cost if wrong: none.
Task 7: fix round 1/5 dispatched (resume implementer).
Task 7: fix round 1/5 done (1e0751e; 217 passed) — scoped re-review (haiku) dispatched on b9449e9..1e0751e; final whole-branch package prepared review-4b03104..1e0751e.diff
Task 7: fix round 1/5 (1 addressed, 0 open — enum-derived selector sets + subentry abort assertions; commits b9449e9..1e0751e)
Task 7: complete (commits 6cf663e..1e0751e, review clean after fix round 1)
All 8 tasks complete. Branch feature/ha-binding-a: 17 commits over main 4b03104, HEAD 1e0751e, 217 tests, ruff + pyright clean.
Final whole-branch review (opus) dispatched on review-4b03104..1e0751e.diff with this ledger's deferred/parked lines for triage.
cleanup: task worktrees/branches for tasks 2-6 removed after merge; feature branch remains
Final review (opus): Ready to merge WITH FIXES. 0 Critical; 5 Important: (1) spec §4/§6 not amended for the manifest ruling; (2) one malformed subentry fails the whole hub (no per-subentry isolation); (3) CI actions not SHA-pinned (spec §6; plan defect); (4) hacs CI job will fail on brands check; (5) no flow→config_map seam test. 16 minors; deferred-minor triage table recorded in the review output (kept in the fix-wave findings file where actionable).
Ruling (fix wave): one fix dispatch covering Important 1–5 plus minors 8 (empty profile name), 9 (stale profile default), 10 (runtime_data guard), 21 (README opening) — all small and merge-relevant; everything triaged "Fix in 2b" goes to the plan 2b backlog: mutable DEFAULT_SUNNY_CONDITIONS → tuple; options/reconfigure data_description; override-entity clearing test; form_from_profile_data prefill test (priority); STATE_UNAVAILABLE/UNKNOWN constants; DEFAULT_ constants for azimuth/wind (unit-aware); weather-unavailable/no-daily ConfigEntryNotReady tests; Store pruning of removed covers; Store migrate func + real async_migrate_entry before any version bump; elevation_min < elevation_max validation; initiate_flow.reconfigure labels; `cover` in manifest deps consistency; _opt_str none-sentinel scoping; hub_config unit fallback comment; wind-sensor-unit-changed repair (§2); entity-rename following (§3); temperature_unit change path; missing_entities → problem sensor.
Ruling (manifest sun half): accepted — installs without default_config and without sun: get a clear ConfigEntryNotReady retry instead of auto-loading sun; README states the requirement — cost if wrong: manual `sun:` for such installs.
Final fix wave dispatched (sonnet) with findings file final-fix-wave-findings.md; FIX_BASE 1e0751e.
Final fix wave DONE (92b07e5, 31949ad, 05bd442; 224 passed, F3 SHAs resolved) — scoped re-review (sonnet) dispatched on 1e0751e..05bd442
Final fix wave re-review (sonnet): F1–F9 all ADDRESSED, no new breakage, all four action SHAs independently re-resolved and matching. Final review clean. Branch head 05bd442, 224 tests.
