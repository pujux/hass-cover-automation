All probes are done and the findings are confirmed. Here is the whole-branch review.

---

# Whole-branch review: `feature/engine` (5c03b5a..51b9e87)

Verified on HEAD 51b9e87: 140 tests pass, `ruff check` clean, `pyright` 0 errors, no `homeassistant`/`asyncio` imports under `engine/`. Working tree untouched.

## Strengths

- **The module split holds.** `engine/` is pure stdlib, every input is a frozen dataclass, mutable state is confined to `CoverPersisted` (Store-shaped) and `CoverRuntime` (explicitly not persisted), and the facade returns data (`Send | Defer | Suppress | None`, `next_check_at`) instead of doing things. This is exactly the §6 shape and it makes the controller a thin adapter.
- **The decision log is traceable in code.** Decision 18 (only schedule sends clear `dam`) is one `if` in `commands.py:23-24` pinned by `test_send_claims_ownership_and_clears_dam_only_for_schedule`; decision 20 (ownership at send) is `commands.py:15-16` + `reconcile.py:21` pinned by `test_completed_during_downtime_is_unchanged` and scenario f; decision 21 (interval clock = any send) is `gate.py:83` pinned by `test_min_interval_applies_to_shading_only_and_uses_any_send`; decision 22 (sticky release) is `schedule.py:161` pinned by scenario N1; decision 24 (satisfied marker) is `cover.py:55-56` + `schedule.py:166-170` pinned by scenarios g and n5.
- **Schedule day-boundary arithmetic is careful and tested**: cross-midnight quiet hours, clamp-to-previous-day skipped, `fired_between` across days, `next_fire` across midnight, open-rule clamp to quiet end.
- **The replay harness is the right instrument.** `fired_between` drives `rule_fired`, the satisfied marker is plumbed through `view()`, and `restart()` round-trips through `to_dict/from_dict`, so restart scenarios exercise the real serialisation.
- **The scenario suite is discriminating where it matters**: a asserts §1.5(e) at 17:05 (before the dwell boundary would mask it); b vs b-active isolates gate 6; n4 gets a door bypass in first so decision 18 is exercised by a real send.
- **Per-task review rounds caught real bugs** (wind hold clock through an unavailable gap; restoring-lapse guard; frost-unknown notification gate), and the rulings are recorded with their cost-if-wrong.

## Issues

### Critical (Must Fix)

**C1. §1.5(e) is a level test on `sun_hits` applied to every shading-tagged override, so a manual close made in daylight while the sun is off the window loses its override on the next evaluation; in `active` reopening mode the engine reopens it within a minute.**
`override.py:67-70` clears `dam` whenever `dam_layer is SHADING and not sun_hits`. `override.py:33-35` tags `dam_layer = SHADING` for *any* shading-layer decision, including `no_shade` (desired OPEN). Probe (auto, daylight, `sun_hits=False`, cover open, user closes at 13:05):

```
passive: after manual close dam=open layer=shading -> 13:06 dam=None, status=open_no_shade, actual=closed (only gate 6 keeps it closed)
active:  after manual close dam=open layer=shading -> 13:06 dam=None, actual=open, commands=[('13:06','open','shading')]
```

The same mechanism fires in `forced_all` when the engine closes a cover the sun is not hitting and the user opens it: `dam=closed/shading` → cleared one evaluation later → re-closed at the min interval (probe: manual open 12:01, engine close 12:11).

Spec: §1.5(e) says "`sun_hits` **becomes** false" and decision 12 scopes it to "overrides created **against a shading opinion**" meaning the sun-driven close; §1.5 closes with "An override does not end merely because desired changed" — here it ends with nothing changing. The rereview's own trace for scenario b (rereview §3 row b) expects the 14:00 override to survive until the 15:30 dwell and `active` to open at 18:00, not at 14:01. The scenario tests sidestep this: `test_scenarios.py:69-71` and `:111-113` call `sim.day(hits=True)` between `manual(CLOSED)` and the first tick, so no evaluation ever sees `dam=open, layer=shading, sun_hits=False`.

Why level semantics must stay: N3 (override surviving a restart during frost/night) relies on (e) being re-evaluable from persisted state, so a pure edge detector would re-open the resurrection bug. Fix instead at the tagging site: `dam_layer = SHADING` only when the override was created against a shading **close made while the sun was hitting** — i.e. `last_eval.layer is SHADING and last_eval.desired is CLOSED and <sun_hits at that evaluation>`. `Decision` already carries `want_shade`; add `sun_hits: bool` to it (set in `layers.evaluate`) so `on_manual_move` can read it, or pass `inputs.sun_hits` alongside `last_eval`. Then (e) stays a level test but only ever ends "user opened against a sun-driven close" overrides, which is the intended semantics and covers `forced_all` correctly. Add a scenario reproducing the rereview b trace with a tick between the close and the sun's arrival, in both reopening modes.

**C2. An `UNAVAILABLE` round trip with no pending record is recorded as a manual move, which releases schedule holds and creates a spurious override.**
`classify.py:65-66` ignores the transition *into* `MOVING`/`UNAVAILABLE`, but the return to the **same** settled state falls through `classify.py:67-73` (late match only if `owner is ENGINE and engine_target matches`) to `classify.py:74` `override.on_manual_move`. For every user-owned cover, and for engine-owned covers whose `engine_target` is `None` or stale, a connectivity blip therefore writes `manual_move_at = now` and derives a `dam` from the last evaluation. Probe (cover closed under a 21:30 close rule, `owner=user`, drops to unavailable 23:00, returns closed 23:01):

```
UNAVAILABLE -> ignored; CLOSED -> manual
manual_move_at 2026-06-30 07:30 -> 2026-07-01 23:01 ; dam=None -> closed ; hold released? True
```

Spec: §1.0 "Only **changes of the classified state** are events" is satisfied literally but §5 says an unavailable cover "is skipped until it reports", and §1.4's manual-move rule is meant for moves. Consequences the HA binding would inherit: decision 22's sticky release (`schedule.py:161`) fires on every Zigbee/RF/cloud dropout, the `manual_override` sensor lights up, and `manual_move_at` (the `since` attribute) is overwritten. The controller cannot filter `UNAVAILABLE` out because `on_transition` needs it for pending progress (`classify.py:62`). Fix: keep `rt.last_settled: CoverState | None` in `on_transition`; without a pending record, a settled state equal to `last_settled` after an `UNAVAILABLE` (and arguably `MOVING`) gap is `IGNORED`, not `MANUAL`. Add the test the T6 ledger line deferred ("MOVING/UNAVAILABLE manual-move branch not directly tested") — that is exactly where this lives.

### Important (Should Fix)

**I1. Failed shading commands retry after 10 minutes, not 30 s.** `commands.on_command_sent` (`commands.py:18`) advances `rt.last_send_at` for the failed send and `on_command_failed` (`commands.py:47-58`) does not roll it back; in `gate.decide` the min-interval check (`gate.py:83-86`) precedes backoff (`gate.py:88-89`). Probe: `on_command_failed` returns 13:00:31, the gate at 13:00:31 returns `Defer(13:10, 'min_interval')`. Spec §5: "the service call raises → ... one retry after 30 s." `test_command_failed_retries_after_30s_then_backs_off` (`test_transitions.py:122-131`) never goes through the gate, so this is untested. Fix: store the previous clock (e.g. `Pending.prev_last_send_at`) and restore it in `on_command_failed`, or let gate 8 skip when `rt.command_failed` is set; add a facade-level test.

**I2. `schedule.*` infers the local day and clock from `now.tzinfo`; a UTC-aware `now` silently evaluates quiet hours and fixed rules in UTC.** `schedule.py:58` (`tz = at.tzinfo`) and `:116` (`tz = now.tzinfo`). Probe: 23:00 local → `quiet_active=True`; the same instant as UTC → `quiet_active=False`. HA code commonly uses `dt_util.utcnow()`; the plan's "caller passes now in the home's local zone" is a documented contract, but the failure is silent and off by the UTC offset. `fire_times` already takes an explicit `tz`; give `quiet_window/quiet_active/last_fired/next_fire/fired_between/view` the same parameter (or `now.astimezone(tz)` internally) so the controller cannot get it wrong. Add one test passing a UTC `now`.

**I3. `room_only` with a degraded room sensor actively opens the cover, and `CoverInputs.room_degraded` is dead.** `model.py:275` declares it; `layers.py` never reads it. With the sensor unavailable the controller yields `room_hot=False` → `want_shade=False` (`layers.py:38-39`) → `auto` → `Desired.OPEN` (`layers.py:98`). Spec §2 says such covers "are not shaded and raise a repair issue"; §1.2 layer 6 says a needed input that is unknown → `leave_alone`. "Not shaded" and "opened because it is not hot" differ for a cover that was already closed. This is partly a spec ambiguity; the consistent reading is `want_shade=None` for `ROOM_ONLY and room_degraded`. Either implement that or remove the field.

**I4. The §5 startup protocol is not expressible safely through the facade.** Spec §5: the first evaluation is computed with the persisted `owner`/`manual_move_at` "before reconcile writes anything", and reconcile consumes its decision. `CoverEngine.evaluate` (`cover.py:44-96`) has side effects (dwell may clear `dam`, sets the satisfied marker, opens restoring windows, sets `last_evaluation`) and runs the gate, so a controller that follows the natural "evaluate → act → reconcile" order will send a command with `owner=engine` and then have `reconcile` (`reconcile.py:21-25`) record a manual move against the engine's own in-flight command. The harness avoids this only because `Sim.restart()` ignores the first result's action. Add a side-effect-free `CoverEngine.decide(inputs, signals) -> Decision` (a wrapper over `layers.evaluate`) for the reconcile step and document the order in the class docstring.

**I5. `p.wind_active` is write-only; `rt.prev_wind_active` is not seeded from it, and the release detector ignores `cfg.wind_enabled`.** `cover.py:51-54`. §5 persists `wind_active` precisely so the wind episode survives a restart. A restart inside the 15-minute restoring window (wind already released, restoring command deferred) yields no restoring window afterwards and the cover stays open through quiet hours. Separately, a controller that hands hub-wide `wind_active` to a wind-disabled cover would grant it quiet-hour exemptions. Two-line fix: `self.rt.prev_wind_active = persisted.wind_active` in `__init__`, and `cfg.wind_enabled and ...` on line 51. (Ledger has the first half as a deferred minor; I'd take it now.)

**I6. Scenario k asserts the opposite of its name, and the underlying behaviour is a spec defect.** `test_k_user_stop_at_partial_is_respected_until_override_ends` (`test_scenarios.py:298-307`) asserts the engine **re-sends close** after the confirm window and backoff. Under rev 3.1 (`partial` is never contrary, §1.0) that is what the spec produces, but §1.4's "while actual is partial and an override is active, the engine waits" never applies to a stop *during* an engine move, because no manual move is recorded. The rereview (§5 item 1) flagged both readings and the spec chose the one that overrides the user's stop after ~12 minutes. Spec defect, not implementation: needs a decision (e.g. "a settled `partial` while pending is a manual move with `dam = target`"). Rename the test to describe what it asserts either way.

**I7. Gate 7 returns `Suppress("moving")` where §1.3 says `defer until settled`.** `gate.py:80-81`; `test_moving_defers_non_protection_layers` (`test_gate.py:185-196`) asserts `Suppress`. Behaviourally fine because the settle transition re-evaluates, but §4 defines `next_planned_action` as "the move currently deferred by gate 7–9", which the controller cannot render from a `Suppress`. Either add a `Wait`-style action (no `until`) or document that `Suppress("moving")` is to be surfaced as a pending move.

### Minor (Nice to Have)

- `classify.py:1` stale docstring "Transition rules are added in Task 9".
- `model.py:310` hardcodes `ContinuousCondition(1800)` instead of `OVERRIDE_DWELL_S`; `cover.py:40` unconditionally replaces an injected runtime's dwell.
- `rt.last_simulated` (`gate.py:27`) is never cleared when simulation is switched off, so the first command of a later simulation session is suppressed as a duplicate.
- `schedule.view` builds `ScheduleView` positionally (`schedule.py:163,165,170`); the pre-flight scan already had to check field order by hand — use keywords.
- `Graceful`/`FrostSignal` (`signals.py:50-69, 280-306`) expose no `next_check_at` for the grace expiry, unlike `Debounce`/`RoomTemperature`/`WindProtection`; the known→unknown frost transition therefore waits for the 5-minute tick.
- Open-rule window expiry (`fired_at + 900 s`) is not a `next_check_at` candidate (`cover.py:89-95`); status can be stale for up to one tick.
- `CoverPersisted.from_dict` raises on an unknown enum value or naive datetime (`model.py:201-211, 253-265`); one corrupt or forward-version field fails the whole Store load. Consider tolerant parsing with a logged fallback at the controller boundary.
- `DailyLatch.update` with `min is None` and `hot_low` set can never be hot (`signals.py:143`); providers without a daily min exist and the spec formula is silent — spec gap.
- `reconcile.py:21` treats `engine_target is None` as "unchanged" even when a PARTIAL cover reached an end state during downtime; `owns()` stays false so passive reopening is blocked until the first engine command.
- `layers.py:57,65` lets a `wind_action=HOLD` cover through frost-unknown as `Layer.WIND` (status `protected_wind` instead of `held_frost`); harmless.
- `rt.unconfirmed`/`rt.command_failed` survive a user's manual move (`classify.py:74` does not touch them), so status stays `unconfirmed` until the next engine send.
- A clamped open rule that coincides with the next day's native fire time yields two identical `fired_between` entries; benign double `rule_fired`.
- DST: `_combine` uses `replace(tzinfo=)` so a fixed rule in the skipped hour fires an hour late (02:30 → 03:30) rather than at "the first valid minute after it" (§2). Once a year.
- Disabled covers still run the dwell and report `next_check_at` (`cover.py:69, 89-95`) against §1.1 "no timers run for it" — an early return in `evaluate` when `not p.enabled` would make the contract explicit (decide what happens to a pending record when a cover is disabled mid-flight).
- `Decision.reason` strings (e.g. `"door_unavailable"`) are the controller's hook for repairs and logbook text; document them as stable or make them an enum.
- Test gaps worth closing before the HA binding: (e) after a restart (extend f2 with `sim.day(hits=False)` post-restart — N3's core); a manual close with `sun_hits=False` surviving an evaluation (C1); an UNAVAILABLE round trip (C2); a failed command through the gate (I1); a UTC `now` guard (I2); the `room_only`+degraded path (I3).

## Deferred-minor triage

| Ledger line | Verdict | Reasoning |
|---|---|---|
| T0: ruff py314 preview warning; newer pyright | drop | Informational; pins are deliberate. |
| T2: no exact-boundary test for `position == 100 − tolerance`; `is_contrary` for UNAVAILABLE / matching pair | defer to HA plan | Cheap but not on a spec-risk path; the classification table already covers 94/97. |
| T3: release-margin exact boundaries; hysteresis only on left edge; inverted elevation not via `SunHits` | defer to HA plan | Geometry is symmetric by construction; low risk. |
| T6: `reset()` OPEN branch and MOVING/UNAVAILABLE manual-move branch untested | **fix before merge** | The UNAVAILABLE branch is where C2 lives; write the test with the fix. |
| T7: reason strings unasserted; `protection_only`/`dark_only` × frost/wind; wind-over-everything bundled | defer to HA plan | Layer precedence is well pinned; reasons become important once the logbook consumes them. |
| T8: generic backoff path untested; moving-vs-defer precedence; door guard could use `p.override_active` | **fix before merge** (backoff) / drop (rest) | The gate-level failed-retry test for I1 covers the generic backoff path; the other two are cosmetic. |
| T4: `RoomTemperature.next_check_at` untested | defer to HA plan | Controller-timer test belongs with the controller. |
| T5: `unsatisfied` conflates two conditions; redundant sort in `fire_times`; `quiet_window`/`fire_times` not tested directly | defer to HA plan | Behaviour pinned via `view()` tests; the sort is harmless. |
| T10: `engine_target`-None branch; first-setup with moving untested | defer to HA plan | Low-impact edge (see Minor list). |
| T9: stale "Task 9" docstring | **fix before merge** | Trivial. |
| T9: `check_pending` inlines pending clearing | drop | Two lines; a helper would not pay for itself. |
| T9: `CONTRARY_PERSIST_S` vs `confirm_window < 10 s` | defer to HA plan | Reject `confirm_window < 10 s` in the config flow. |
| T11: `__init__` discards an injected runtime's dwell | **fix before merge** | Trivial; only construct a new `ContinuousCondition` when `runtime is None`. |
| T11: `prev_wind_active` not seeded from `p.wind_active` | **fix before merge** | Promoted to I5: the persisted scalar is otherwise dead. |
| T11: disabled cover still returns `next_check_at`/opens restoring windows | **fix before merge** | An early return makes the facade contract explicit rather than relying on the controller to know. |
| T11: weak assertion in restoring test | drop | Tightened in the fix round. |
| T11: dwell-regression test could be tighter | defer to HA plan | Adequate as is. |
| T11: `model.py` hardcodes 1800 | **fix before merge** | Trivial. |
| T11: several statuses and delegations untested | defer to HA plan | Status precedence is pinned by `test_status_precedence_order` and one facade test. |
| T11 addendum: lapse guard for `protection_only` (`Layer.NONE`) | drop | Inert, as the ledger notes. |
| T12: mutation "schedule send fails to clear dam" survives replay | drop | Pinned by `test_transitions`. |

## Recommendations

1. Fix C1 and C2 with the scenario tests named above before merging; both are local (one tagging site in `override.py` plus a `sun_hits` field on `Decision`; one `last_settled` field in `CoverRuntime`).
2. Take I1, I2 and I5 in the same pass — each is a handful of lines and each closes a silent-failure path the controller would otherwise have to know about.
3. Add `CoverEngine.decide()` (I4) and a class docstring stating the startup order (`decide` → `reconcile` → `evaluate`/act), and state the persistence contract: the controller schedules a delayed Store save after every `evaluate` (dwell/(e)/rule-fired can mutate `p`) and an immediate one after `on_command_sent`, `reset`, `reconcile` and a `MANUAL` transition.
4. Spec follow-ups to record in `design-decisions.md`: the precise scope of §1.5(e) ("created against a sun-driven shading close"); the partial-stop-during-pending semantics (I6); `room_only` with a degraded sensor (I3); `hot_day` when the forecast has no daily minimum.
5. When the HA binding starts, consider making `Decision.reason` an enum, since it becomes the logbook/repairs API.

## Assessment

**Ready to merge into main?** With fixes

**Reasoning:** The architecture, the layer stack, the gate, the schedule arithmetic and the reconcile/ownership model are faithful to §1/§2/§5 and are well-tested, and the module split will carry the HA binding. Two cross-module defects that no per-task review could see — §1.5(e) applied as a level test to every shading-tagged override (the engine undoes a daylight manual close within a minute in `active` mode) and unavailability round trips being recorded as manual moves that release schedule holds — are foundation behaviours the controller would inherit and must be fixed first; the remaining Important items are small and mostly harden the facade against predictable controller mistakes.