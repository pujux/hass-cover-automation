# Final whole-branch review — fix wave findings (plan 2b, branch feature/ha-binding-b, FIX_BASE ca4244a)

Fix every item in ONE pass in /Users/julian/Projects/hass-cover-automation/.worktrees/ha-binding-b, a few logical commits. Rulings are binding. Cite item ids in commit bodies. Engine package stays untouched (engine items go to a follow-up).

## F1 (Critical) Permanently-expired timer candidate → endless 1 Hz evaluation loop
- `controller._arm_cover_timer` floors the delay at 1 s over `min(candidates)`. Two candidates can stay in the past indefinitely: `HubSignalSource.next_check_at()` returns `_weather_unavailable_since + grace` / `_forecast_failed_since + grace` for the whole outage once the grace has elapsed; `self._retry_at[cover_id]` is only popped on a successful send, so a failed command whose disagreement later resolves leaves it forever.
- Ruling: (a) `HubSignalSource.next_check_at(now)` takes `now` and omits grace expiries that are already ≤ now (update its tests and the controller call); (b) `_act` pops `_retry_at[cover_id]` whenever it returns without a real send (non-Send action, in-flight suppression, simulated, unsupported, unavailable); (c) `_arm_cover_timer` keeps only candidates `> now` from the hub/signal/retry sources; `result.next_check_at` is kept as-is (an expired pending deadline legitimately needs a prompt `check_pending`), but if it is the only candidate and it is in the past, arm it once (1 s) — `_async_check_pending` resolves it, and a re-arm on the same past deadline must not happen because `check_pending` clears the pending record.
- Tests: weather unavailable, advance past the grace with `freezer` + `async_fire_time_changed` several times → the per-cover timer is armed at most once per real trigger (count `async_call_later` calls via a patch, or assert `_cover_timers` is empty after the grace expiry evaluation); a failed command followed by the disagreement resolving (set the cover to the desired state by hand) → `_retry_at` empty and no 1 s re-arms.

## F2 (Important) Per-cover write API crashes before the controller starts
- `async_set_enabled` / `async_set_mode` / `async_reset_override` index `self._engines`, which is empty until `async_start` runs (platforms are up before `async_at_started` fires; on reload during the forecast fetch too).
- Ruling: `async_set_enabled`/`async_set_mode` mutate `self._store.data.covers[cover_id]` (the same `CoverPersisted` object the engines are later built from), `await store.async_save()`, and only evaluate when `self.started`; `async_reset_override` before start is a no-op that logs at debug (a reset needs the live actual). Test: construct the controller, call the three setters before `async_start` → no exception, `enabled` persisted, then `async_start` → engine sees `enabled=False`.

## F3 (Important) Entities publish defaults instead of persisted state until start
- `_cover_views` is seeded with `CoverView(name, cover_entity)` defaults (`enabled=True`, `mode=AUTO`, `status=IDLE`), so a disabled cover shows its switch ON after every restart until `async_at_started`.
- Ruling: seed `enabled`, `mode` from `store.data.covers[cover_id]` and `status=Status.DISABLED if not enabled else Status.IDLE`, `actual_state` from `classify_state` of the current state in the constructor; seed `hub_view` from `store.data` (shading_mode, reopening_mode, simulation, verbose). Test: persisted `enabled=False` → `cover_views[id].enabled is False` right after construction (before start).

## F5 (Important) `set_cover_position` fallback and `cover_unsupported` never executed
- Add controller tests: cover with `features=4` (SET_POSITION only) → `set_cover_position` called with `position: 0` for a close; cover with `features=0` → no service call, issue `cover_unsupported_<subentry_id>` present, and it clears when the cover later advertises features 3 and an evaluation runs.

## F6 (Important) No `EVENT_CORE_CONFIG_UPDATE` handling (spec §2/§5)
- Ruling: in `_subscribe`, `hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, ...)` → task that re-arms the schedule tracker and evaluates all (guarded by `started`). Test: fire the event → `ScheduleTracker.async_arm` called (patch or observe `_schedule._unsub` replaced) and an evaluation ran.

## F7 (Important) Controller-level tests for wired behaviours
- Add one controller test each (real engine, mocked cover services, `freezer`): (a) door layer — door `on` while the engine closed the cover → `open_cover`; door `off` → shading may close again after the min interval; door sensor removed → `door_sensor_unavailable_<id>` issue; (b) frost conflict — frost active (outdoor −5 °C) + wind above upper wanting open → no command, `persistent_notification` created once (check `hass.states` for `persistent_notification.*` or patch `persistent_notification.async_create`), `frost_conflict_<id>` issue; frost clears → issue gone and notification dismissed (see F12); (c) quiet hours — profile with quiet 22:00–07:00, time 23:00, shading would close → no command, status `quiet_hours`; (d) confirm-window expiry — command sent, cover never moves, advance `confirm_window_s + 1` → status `unconfirmed`, pending cleared, backoff set; then cover reports partial (position 50) after a second send and the window expires → decision 27 user stop: `owner user`, `dam` = pending target, status `partial`; (e) midnight rollover — `hot_day` True from a forecast, cross local midnight with the forecast patched to return None → `hub_view.hot_day is None` and the forecast fetch was called again.

## F8 (Minor, upgraded) `_async_cover_timer` checks `started` before the lock
- Move the check inside the per-cover lock (re-check after acquiring) so a straggler cannot save the Store after `async_stop`'s final save.

## F9 (Minor, upgraded) Untracked tasks from callbacks
- Replace `self.hass.async_create_task(...)` in the controller's callbacks with `self.entry.async_create_background_task(self.hass, coro, name=..., eager_start=True)`; if a unit test constructs the controller on an entry that is not loaded and this raises, fall back to `hass.async_create_task` for that path and say so in the report.

## F11 (Minor, upgraded) Store save timing on send
- Save immediately after `engine.on_command_sent(...)` and BEFORE the blocking service call (decision 20: ownership is claimed at send time, also across restarts); keep the save after `on_command_failed`.

## F12 (Minor, upgraded) Frost-conflict notification never dismissed
- In the branch that clears the `frost_conflict` repair, call `persistent_notification.async_dismiss(hass, f"cover_automation_frost_{cover_id}")`.

## Not in this wave (recorded for follow-ups)
- Important 4 (reload while a position-only cover travels → spurious override) needs a spec §5 decision and persisted send time — the user decides after checking whether their covers report opening/closing.
- Engine follow-up: in-flight suppression as a gate condition (spec §1.3 gate 7), `enabled` guard in check_pending's decision-27 branch, prev_wind_active while disabled, persisted last_send_at.
- Minors 10, 15–19 and the "fix later" triage rows go to the plan-3 backlog.

## Verification
`.venv/bin/pytest`, `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/pyright`; no lingering timers. Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
