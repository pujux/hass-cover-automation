# Plan 2b (HA binding behaviour) — final whole-branch review

Reviewer: Opus 5, range 86d3c05..ca4244a (26 commits). Verdict: **ready to merge with fixes**; fix wave applied afterwards
(see `2026-09-16-ha-binding-b-final-fix-findings.md` / `-final-fix-report.md`).

## Strengths (condensed)
- Plan scope complete; every Global Constraint honoured (timer helpers per spec §2, unique ids, `config_subentry_id`, `_unrecorded_attributes`,
  entity categories, engine untouched).
- Startup order decide → reconcile → evaluate → act implemented once per cover; C2 blip rule, decisions 26/27 intact through the binding.
- Unit handling: hub thresholds vs `HubConfig.temperature_unit`, cover comfort vs `CoverBindings.temperature_unit`, wind readings converted
  into the cover's stored unit; °F hub with °C covers works and is tested.
- Lifecycle: per-cover locks with re-checks, stop drains locks before the final save, `_stopped` beats an uncancellable `async_at_started` job,
  scheduler generation counter — each raced by a test.
- Translations enforced by tests derived from code; `tests/ha/` drives real HA machinery with no lingering-timer suppressions.

## Issues (fixed in the wave unless marked)
Critical
1. Expired timer candidates (weather/forecast grace expiry, stale retry time) + the 1 s floor → endless 1 Hz evaluation loop during any
   weather outage longer than the grace. → `next_check_at(now)` drops past expiries; retry time popped when nothing is sent; candidates filtered `> now`.
Important
2. Per-cover write API raised `KeyError` before the controller started → setters mutate the persisted record and defer evaluation.
3. Entities published default values (enabled/mode/status) until start → views seeded from the Store.
4. **Deferred (user decision):** a reload while a position-only cover is travelling (`partial`) makes reconcile record a spurious manual
   override — spec §5 writes the downtime rule for restarts, not one-second reloads. Needs a persisted send time and a spec amendment;
   irrelevant if the covers report `opening`/`closing`.
5. `set_cover_position` fallback and `cover_unsupported` untested → tests added.
6. No `EVENT_CORE_CONFIG_UPDATE` re-arm (spec §2/§5) → listener added.
7. Door layer, frost-conflict notification, quiet hours, confirm-window expiry (decision 27), midnight rollover untested through the
   controller → tests added.
Minor fixed: started re-check inside the timer lock (8); tracked background tasks (9); Store saved between `on_command_sent` and the
service call (11); frost notification dismissed when the conflict ends (12).

## Deferred to plan 3 / follow-ups
- Engine follow-up (one small task): in-flight suppression as a gate condition (spec §1.3 gate 7) replacing the controller guard;
  `enabled` guard in `check_pending`'s decision-27 branch; `prev_wind_active` not updated while disabled; decide whether `last_send_at`
  and the pending record belong in the Store (settles Important 4 and the min-interval reset on reload).
- Sun missing → no evaluation at all (protection layers pause); cache the last sun position.
- `last_engine_move` and the min-interval clock are runtime-only (reset on reload); `_publish` clock; `log_missing()` for service targets;
  silent no-op when no controller is loaded; `_iso` and `missing_entity_` id format duplicated; wind defaults/bounds not unit-aware.
- Still open from 2a: entity-rename following (`EVENT_ENTITY_REGISTRY_UPDATED`, spec §3); a path to change a stored temperature unit;
  re-evaluating `missing_entity` issues from state subscriptions; unused `missing_entities` field.
- Triage "fix later": naive-datetime forecast fallback test; polar sun fallback test; `wind_unit is None` mismatch; wind freeze-while-active
  test; `_sync_actual` coalescing comment; diagnostics started-snapshot test; literal entity id in an init test.

## Shakedown checklist (simulation mode first)
1. Weather outage > 30 min: exactly one evaluation per real trigger (no 1 Hz log storm) — regression check for Critical 1.
2. HA restart: `Automation enabled` / `Mode` never flip for a disabled cover.
3. Edit a hub option while a cover is physically moving: does `Manual override` turn on? If yes, the covers are position-only → Important 4.
4. First hot day: read the logbook (`cover_automation_action` with layer + reason) against the `status` sensor attributes.
5. Dusk/dawn: `next_planned_action`/`next_planned_at`/`active_rule` explain deferrals and schedule holds.
6. First wind event: cover opens, `wind_protection_active` and hub `any_wind_protection_active` agree, release after 15 min does not re-close immediately.
7. Download diagnostics once while everything looks right (baseline for `covers[*].runtime`).

## Fix-wave re-review (Opus) — residual observations for plan 3
- `ScheduleTracker._fired` is spawned by `async_track_point_in_time` as a plain `hass` task; make it entry-owned like the controller's tasks.
- The "no 1 Hz loop" guarantee rests on an engine invariant (only the pending deadline in `StepResult.next_check_at` may lie in the past); add a debug log or assertion on sub-second arms so a regression is visible.
- `hass.async_block_till_done()` does not await entry background tasks; future tests with genuinely awaiting Store/service mocks need `wait_background_tasks=True` (note this in `tests/ha/conftest.py`).
- The frost notification dismiss runs on every non-frost evaluation (cheap no-op); controller construction `setdefault`s empty `CoverPersisted` records before start (defaults only).
