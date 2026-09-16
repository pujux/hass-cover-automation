# Final fix wave — implementation report (plan 2b, branch `feature/ha-binding-b`)

Worktree: `/Users/julian/Projects/hass-cover-automation/.worktrees/ha-binding-b`, base `ca4244a`.
All ten ruled items (F1, F2, F3, F5, F6, F7, F8, F9, F11, F12) are implemented. The engine
package is untouched.

## Commits

| SHA | Subject | Items |
| --- | --- | --- |
| `c70a405` | fix: stop the permanently-expired timer candidates | F1 |
| `069347e` | fix: the write API and the entity views work before the start job runs | F2, F3 |
| `d67486c` | fix: core-config trigger, entry-owned tasks and send-time persistence | F6, F8, F9, F11, F12 |
| `64f2bab` | test: cover the wired command and layer behaviours end to end | F5, F7 (+ one controller fix found by F7d) |
| `7057f94` | test: drive the frost conflict from a real outdoor temperature sensor | F7b |

## Per item

### F1 — permanently-expired timer candidate → endless 1 Hz loop

- (a) `signals_adapter.py:166` — `HubSignalSource.next_check_at(now)` now takes `now` and only
  offers a weather/forecast grace expiry while `since + grace > now`. Callers updated:
  `controller.py:612`, `tests/ha/test_signals_adapter.py:70` and `:122`.
- (b) `controller.py:497, 504, 516, 529` — `_act` pops `_retry_at[cover_id]` on every path that
  sends nothing: non-`Send` action, in-flight suppression, simulated send, unsupported cover.
  (The unsupported branch is also the "cover entity vanished" path, because `cover_supports(None)`
  is `(False, False)`; the evaluation loop already skips `_act` for `CoverState.UNAVAILABLE`.)
  The success path already popped it (`controller.py:549`), and the failure path re-sets it.
- (c) `controller.py:598-621` — `_arm_cover_timer` keeps only candidates `> now` from the signal
  set, the hub signals and `_retry_at`; `result.next_check_at` is still taken as-is (an expired
  pending deadline needs a prompt `check_pending`, which clears the pending record, so it cannot
  arm twice for the same deadline).

TDD: both tests written first and observed failing against the unfixed source
(`_cover_timers` held a 1 s re-arm; `_retry_at` still held the stale deadline).

- `tests/ha/test_controller.py:563` `test_expired_weather_grace_stops_arming_the_cover_timer` —
  weather goes unavailable (timer armed for the expiry itself), then three 31-minute advances with
  `freezer` + `async_fire_time_changed`: `_cover_timers == {}` after each, and a patched
  `async_call_later` records **0** arms over the whole outage. `async_fetch_today` is patched to
  keep succeeding inside the loop so the *forecast* grace (a legitimately new future candidate,
  re-created by the hourly refresh once the real fetch starts failing) does not mask the result.
- `tests/ha/test_controller.py:596` `test_retry_deadline_is_dropped_once_the_disagreement_resolves`
  — `close_cover` fails, then the cover is closed by hand: `_retry_at == {}` and
  `_cover_timers == {}`.

### F2 — per-cover write API before the controller starts

`controller.py:158` `_persisted(cover_id)` returns `store.data.covers[cover_id]` — the very object
`async_start` builds the engine on (`controller.py:188`), so one write serves both sides of the
start. `async_set_enabled` (`:877`) and `async_set_mode` (`:884`) write through it under the
per-cover lock, save, and then `_async_after_write` (`:863`) evaluates only when `started`.
`async_reset_override` (`:911`) returns early with a debug log before start.

- Test `tests/ha/test_controller.py:614` `test_write_api_works_before_the_controller_starts`:
  all three setters on an unstarted controller raise nothing, `enabled=False` / `mode=dark_only`
  land in `hass_storage`, and after `async_start` the engine sees them (no close command).

Addition beyond the ruling (noted deliberately): a pre-start write also re-seeds the affected
cover view and dispatches an update (`_async_after_write`), because `CoverEnabledSwitch.is_on`
reads `controller.cover_views[...]`; without it the switch would keep showing the pre-write value
until the start job runs. The same helper replaced the bare `async_evaluate()` in
`async_set_shading_mode` / `async_set_reopening_mode` / `async_set_simulation`.

### F3 — entities published defaults instead of persisted state

`controller.py:144` seeds `hub_view` from `store.data` (shading mode, reopening mode, simulation,
verbose); `controller.py:162` `_seed_cover_view` seeds each `CoverView` with `enabled`, `mode`,
`status = DISABLED if not enabled else IDLE` and `actual_state` from `classify_state` of the
cover's current state.

- Test `tests/ha/test_controller.py:639` `test_views_are_seeded_from_the_store_before_start`
  (persisted `enabled=False`, `mode=protection_only`, cover `closed`, hub store non-defaults).

### F5 — `set_cover_position` fallback and `cover_unsupported`

- `tests/ha/test_controller.py:680` `test_position_only_cover_uses_set_cover_position` — a cover
  advertising only `CoverEntityFeature.SET_POSITION` gets `set_cover_position` with
  `position: 0` for a shading close and no `close_cover`.
- `tests/ha/test_controller.py:694`
  `test_cover_without_usable_features_raises_a_repair_until_it_reports_them` — `features=0` yields
  no service call and issue `cover_unsupported_<subentry_id>`; when the cover later advertises
  features 3 the state change re-evaluates, the issue is gone and the close goes out.

### F6 — `EVENT_CORE_CONFIG_UPDATE`

`controller.py:288` subscribes in `_subscribe`; `_on_core_config_update` (`:341`) spawns
`_async_core_config_updated` (`:344`), which is guarded by `started`, re-arms the schedule tracker
and evaluates every cover.

- Test `tests/ha/test_controller.py:661` `test_core_config_update_rearms_the_schedule_and_evaluates`
  — after firing the event, `ScheduleTracker._unsub` is a different handle (re-armed) and the
  cover view object was replaced (an evaluation ran). Written first, observed failing.

### F7 — controller-level tests for wired behaviours

All in `tests/ha/test_controller.py`, real engine, `async_mock_service` cover services, `freezer`:

- (a) `:712` `test_door_layer_opens_holds_and_reports_a_missing_sensor` — shading closes, the door
  goes `on` → `open_cover` (the door layer skips the minimum interval) and status `door_open`;
  the door goes `off` → no close while the 10-minute interval runs, then a close after it;
  removing the sensor raises `door_sensor_unavailable_<id>`.
- (b) `:747` `test_frost_conflict_notifies_once_and_is_dismissed_when_it_clears` — outdoor
  −5 °C with wind at 75 km/h (upper 60) and the cover closed: no command, status `held_frost`,
  issue `frost_conflict_<id>`, exactly one `persistent_notification.async_create` with id
  `cover_automation_frost_<id>` and no second one on a repeat evaluation; when the outdoor sensor
  rises to 10 °C the issue is gone, `async_dismiss` is called once with that id (F12) and wind
  opens the cover.
- (c) `:797` `test_quiet_hours_block_a_shading_close` — profile with quiet 22:00–07:00 and a
  07:00 open rule (long since released, so shading is the only opinion left), clock at 23:00:
  no command, status `quiet_hours`, winning layer `quiet_hours`.
- (d) `:820` `test_confirm_window_expiry_then_a_user_stop_part_way` — close sent, cover keeps
  reporting `open`, +121 s → status `unconfirmed`, `pending is None`, `backoff_until` set; +11 min
  → second close; the cover then reports `open`/position 50 (user stop) and +121 s later
  decision 27 fires: `owner=user`, `dam=closed`, status `partial`, override active.
- (e) `:849` `test_midnight_rollover_clears_hot_day_and_refetches_the_forecast` — `hot_day` True
  from the start forecast, clock crossed over local midnight with `async_fetch_today` patched to
  return `None`: the fetch was called again and `hub_view.hot_day is None`.

**Controller fix found by (d)** (`controller.py:615-618`): `_arm_cover_timer` was handed a
`StepResult` computed *before* `_act` ran, so the `pending` record the send had just created was
invisible and no confirm-window timer was armed — an unresponsive cover only got checked at the
next five-minute fallback tick. The deadline is now re-read from the engine runtime after acting
via `engine.classify.pending_deadline(rt, cfg)` (read-only use of the engine; no engine change).
Without this the ruled expectation "advance `confirm_window_s + 1` → status `unconfirmed`" is
false; the engine behaviour itself is correct (the pending deadline only exists after
`on_command_sent`, which the engine's own test documents).

### F8 — `started` re-checked under the per-cover lock

`controller.py:632-640`: `_async_cover_timer` keeps the cheap pre-check and re-checks `started`
after acquiring the lock, so a straggler cannot save the Store behind `async_stop`'s final save.
Covered indirectly by the existing stop tests (`test_stop_cancels_everything`,
`test_evaluation_waiting_on_a_lock_does_nothing_after_stop`).

### F9 — untracked tasks from callbacks

`controller.py:233` `_create_task` wraps `entry.async_create_background_task(hass, coro,
name=..., eager_start=True)`; all six callback spawn sites use it (state change, weather change,
midnight, forecast tick, fallback tick, cover timer, core-config update). No fallback to
`hass.async_create_task` was needed: `ConfigEntry.async_create_background_task` performs no
entry-state check, and every test that constructs a controller does so on a loaded entry.

### F11 — Store save timing on send

`controller.py:538-541`: the save now happens immediately after `engine.on_command_sent(...)` and
before the blocking `services.async_call`; the save after `on_command_failed` stays (`:547`). The
old post-success save was removed as redundant — nothing mutates persisted state between the send
and the return.

### F12 — frost-conflict notification dismissal

`controller.py:573`: the branch that clears the `frost_conflict` repair also calls
`persistent_notification.async_dismiss(hass, f"cover_automation_frost_{cover_id}")` (a no-op when
no such notification exists). Asserted in the F7b test.

## Verification

```
.venv/bin/pytest                 323 passed in 3.34s   (311 before; +12 new tests, run 3x, stable)
.venv/bin/ruff check .           All checks passed!
.venv/bin/ruff format --check .  74 files already formatted
.venv/bin/pyright                0 errors, 0 warnings, 0 informations
```

No lingering-timer or lingering-task failures from the harness.

## Deviations and notes

1. **F2 (addition).** A pre-start write also re-seeds the cover view and dispatches, so entities
   follow it immediately; the same helper is used by the hub-level setters. Rationale above.
2. **F7d (extra controller fix).** `_arm_cover_timer` now also considers the post-`_act` pending
   deadline. This was required for the ruled expectation and is the controller's fault, not the
   engine's. Risk of a new 1 Hz loop was checked: whenever that deadline has passed,
   `check_pending` clears `rt.pending` (both the contrary branch and the confirm-window branch),
   so the candidate disappears after one arm.
3. **F7b (test shape).** The hub fixture has no outdoor temperature sensor, so the test binds
   `sensor.outdoor` to the entry before setup rather than driving frost through the weather
   entity's temperature fallback — closer to the ruling's "outdoor −5 °C".
4. **Test helper split.** `start_controller` is now `build_controller` (set up the entry, build an
   unstarted controller) plus the patched start; it gained `store_overrides` and `cover_state`
   parameters. Existing call sites are unchanged.
5. **F11 / test environment.** With the save moved before the service call, an evaluation that
   sends a command now awaits the Store first. In tests PHACC's `mock_storage` makes that write
   suspension-free, so the eager background tasks from F9 still complete synchronously and
   `hass.async_block_till_done()` (which does *not* wait for background tasks) keeps working.
   Worth remembering if a future test introduces a genuinely awaiting Store or service mock:
   such a test would need `async_block_till_done(wait_background_tasks=True)`.

## Concerns

- `result.next_check_at` is still allowed to be in the past by design (F1 ruling). Today only the
  pending deadline can be expired there, and `check_pending` always clears it; if a future engine
  change adds another expirable source to `StepResult.next_check_at`, the 1 Hz loop could come
  back. A `_LOGGER.debug` on a sub-second arm would make that visible cheaply.
- Items recorded as out of scope by the findings (Important 4, the engine follow-ups, minors 10
  and 15–19) were not touched.
