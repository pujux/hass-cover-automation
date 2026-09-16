# Final fix wave report — engine branch (`feature/engine`)

Status: **DONE_WITH_CONCERNS** (three deviations from the brief, all recorded below; no
unresolved defect).

Base: `9be3629` (spec revision 3.2). Commits listed below were later rewritten only to normalise the co-author trailer; final SHAs: c33b2f9 f80be75 a0d960d 16341ff 3561011 b325289 98405a0 f1887ae 20633a2.
before the first fix commit). Nine commits, one per brief item, in the brief's order.
Nothing under `docs/` was touched (`git diff --name-only 9be3629..HEAD` lists only
`custom_components/cover_automation/engine/` and `tests/engine/`).

| # | SHA | Subject |
|---|---|---|
| C1 | `e57d037` | fix(engine): scope §1.5(e) to overrides against a sun-driven shading close |
| C2 | `df5930b` | fix(engine): an unavailable/moving round trip is not a manual move |
| I1 | `05ec114` | fix(engine): a failed command does not start the minimum interval |
| I2 | `aa7b238` | feat(engine): explicit timezone for the schedule query functions |
| I3 | `45d542a` | fix(engine): room_only with a degraded room sensor is unknown, not "not hot" |
| I4 | `efff669` | feat(engine): side-effect-free CoverEngine.decide() for the §5 startup order |
| I5 | `3219976` | fix(engine): seed prev_wind_active from the Store; gate the release on wind_enabled |
| I6 | `eccd2e9` | fix(engine): a stop during an engine move is a manual stop (decision 27) |
| minors | `68a2d5c` | chore(engine): minor review items — disabled early return, docstrings, defaults |

Every item was verified against spec revision 3.2 (`9be3629`) after the fact: §1.2 layer 6
(room_only/degraded), §1.3 gate 7 wording, §1.4 (partial expiry, blip rule, sun-driven
`dam_layer`), §1.5(e), §2 time-zone contract and §5 (startup order, wind seeding, failed
send not advancing the interval clock) all match what was implemented. No contradiction
between the brief and the spec was found.

---

## Hand-traces (done before running anything)

### C1 — §1.5(e) scope

Setup: `CFG` (auto, passive/active), no profile, elevation 30, `sun_hits = False`, cover
open, engine owns OPEN from reconcile's first-setup branch.

| time | event | trace |
|---|---|---|
| 13:01–14:00 | ticks | layer 6: `want_shade` short-circuits on `sun_hits = False` → `Decision(OPEN, SHADING, no_shade, sun_hits=False)`. Gate: target OPEN == actual OPEN → `None`. `rt.last_evaluation` = that decision. |
| 14:00 | user closes | `on_transition(CLOSED)`: no pending; `CLOSED != rt.last_settled (OPEN)`; `engine_target (OPEN)` does not match CLOSED → manual move. `dam = last_eval.desired = OPEN`. `dam_layer`: **old** `layer is SHADING` → SHADING; **new** `_is_sun_driven_close` requires `desired is CLOSED` → False → **OTHER**. |
| 14:01 | tick | `update_dwell`: **old** `dam_layer is SHADING and not sun_hits` → `clear(p)`, dam gone one minute after the user acted. **new** dam_layer is OTHER → (e) not reached; desired OPEN == dam OPEN → dwell reset, dam kept. Gate 5 (`target is p.dam`) → `Suppress("manual_override")`, cover stays CLOSED. In *active* mode the old path reached gate 6 and sent `Send(OPEN, SHADING)` — the review's probe. |
| 15:00 | sun arrives | decision becomes `shade` (CLOSED). `cond = CLOSED is not dam(OPEN)` → True; the dwell starts at the 15:01 tick. |
| 15:31 | dwell | accumulated 1800 s → `clear(p)`; dam None. Cover still CLOSED (desired CLOSED == actual). |
| 15:31–18:00 | ticks | desired CLOSED == actual CLOSED → no action. |
| 18:00 | sun leaves | decision `no_shade` (OPEN). Passive: `p.owns(CLOSED)` is False (owner USER) → `Suppress("reopening_passive")`, CLOSED at 20:00. Active: gate 6 passes, `rt.last_send_at is None` so gate 8 is skipped → `Send(OPEN, SHADING)` at **18:01**. |

Both scenarios were then run against the *old* tagging (`return last_eval.layer is
Layer.SHADING`) as a mutation check: they fail at 14:01 with `dam=None`, and in active mode
with `owner=ENGINE` — i.e. the engine had already reopened the cover.

### C2 — unavailable round trip

Setup: `CFG`, profile `(CLOSE_2130,)`, reopening **active**, start 21:00, cover CLOSED,
persisted `owner=USER`, `engine_target=OPEN`, `manual_move_at=20:30` (the user closed it by
hand half an hour before the rule).

| time | event | trace |
|---|---|---|
| 21:01–21:29 | ticks | `last_fired` = 06-30 21:30; `manual_move_at (07-01 20:30) > fired_at` → released → `desired = leave_alone`; night → `Decision(LEAVE_ALONE, NONE, night)`. No send, so `owner` stays USER. |
| 21:30 | rule fires | `rule_fired` → `on_rule_fired` clears `dam` (already None). View: `20:30 > 21:30` is False → hold armed, `desired = CLOSED`. Actual is already CLOSED → gate returns `None`. **No send, owner still USER** — this is what makes the blip reach the manual-move branch (an engine-owned cover would take the late-match path instead). |
| 23:00 | tick | `evaluate` seeds `rt.last_settled = CLOSED`. |
| 23:00 | blip | `on_transition(UNAVAILABLE)` → IGNORED, `last_settled` untouched (not settled). `on_transition(CLOSED)` → no pending; **old**: not MOVING/UNAVAILABLE, owner is USER so no late match → `on_manual_move` → `manual_move_at = 23:00`, `dam = CLOSED` (from the schedule hold that was the last evaluation). **new**: `CLOSED is rt.last_settled` → IGNORED; nothing written. |
| 07-02 07:00 | sun up, off the window | **old**: `23:00 > 21:30` → hold released → shading wants OPEN; gate 5 passes (`dam=CLOSED != OPEN`), gate 6 active → `Send(OPEN, SHADING)` at 07:01. **new**: hold still active → `desired = CLOSED` == actual → `None`. |
| 07-02 07:30 | assert | cover CLOSED, `commands == []`, `manual_move_at` still 20:30, `dam` None. |

Mutation check (blip branch removed): the scenario fails on the behavioural tail —
`sim.actual` is OPEN at 07:30 — as well as on the `manual_move_at`/`dam` assertions.

Note on scope: the spec words decision 26 as "a settled state that equals the last settled
state **after a `moving`/`cover_unavailable` gap**". The implementation does not track
whether a gap occurred, because it cannot happen otherwise: §1.0 says only *changes* of the
classified state are events, so a settled state equal to the last settled state can only
arrive after a non-settled state in between. The two readings are equivalent.

### I6 — stop at partial

Setup: `CFG`, `travel_s=120`, sun hitting, cover open.

| time | event | trace |
|---|---|---|
| 12:01 | tick | `Send(CLOSED, SHADING)`; `on_command_sent` → pending `{target=CLOSED, last_progress_at=12:01}`; harness sets actual PARTIAL (pending progress, `last_progress_at` stays 12:01 for the contrary-free branch… it is refreshed to 12:01, same instant); arrival scheduled for 12:03. |
| — | user stops | `_arrival = None`: the cover never leaves PARTIAL. |
| 12:02 | `check_pending` | `120 - 60 = 60 s` to go → `None`. Gate returns `Defer(12:11, min_interval)`. |
| 12:03 | `check_pending` | window expired. **old**: `on_unconfirmed` → `unconfirmed`, `consecutive_failures=1`, `backoff_until=12:13`; at 12:13 the gate re-sends CLOSED over the user's stop (the old scenario k asserted exactly that). **new**: `current_state is PARTIAL` → pending cleared, `owner=USER`, `manual_move_at=12:03`, `dam=CLOSED`, `dam_layer=OTHER`, `contrary_since=None`; `unconfirmed`/backoff untouched. |
| 12:03 | `evaluate` | `update_dwell`: desired CLOSED == dam CLOSED → dwell reset each tick, so the override does not expire. Gate 5 → `Suppress("manual_override")`. Status: `PARTIAL` (not `UNCONFIRMED` any more). |
| 12:04–12:19 | ticks | gate 5 fires before gate 8/9 every time → no second command. |

Final state asserted: one command, `dam=CLOSED`, `owner=USER`, actual PARTIAL.

---

## Per item

### C1 — scope of §1.5(e) (`e57d037`)

- `model.Decision`: `sun_hits: bool = False`.
- `layers.evaluate`: the local `dec(...)` helper passes `inputs.sun_hits` into every
  `Decision` it builds.
- `override.on_manual_move`: new `_is_sun_driven_close(last_eval)` — `layer is SHADING and
  desired is CLOSED and sun_hits` — decides `DamLayer.SHADING`; everything else is `OTHER`.
  `update_dwell` (e) is unchanged and stays a level test, so N3 (override surviving a
  restart during frost/night) keeps working.

Tests: `tests/engine/test_override.py::test_manual_move_against_a_shading_open_is_other_layer`,
`::test_manual_move_against_a_shading_close_without_sun_is_other_layer`, and
`tests/engine/test_scenarios.py::test_c1_manual_close_without_sun_survives_until_the_dwell`
/ `::test_c1_active_reopens_only_after_the_override_ended_and_the_sun_left`.

**Existing-test change:** `test_override.py`'s module-level `SHADE` constant became
`Decision(Desired.CLOSED, Layer.SHADING, "shade", sun_hits=True)`. This is a fixture
change, not an expectation change: the test is named "manual move *against shading* records
dam and layer" and its assertion (`dam_layer is DamLayer.SHADING`) is unchanged — the
fixture now states the precondition (the sun was on the window) that the assertion always
assumed. The complementary `sun_hits=False` case is covered by the new
`SHADE_NO_SUN` test. No scenario expectation was weakened anywhere in this wave except the
one the brief explicitly mandates (scenario k, see I6).

### C2 — unavailable/moving round trips (`df5930b`)

- `model.CoverRuntime`: `last_settled: CoverState | None = None`.
- `classify.on_transition` split into `_kind(...)` (the classification) plus a wrapper that
  records `rt.last_settled` for every settled state *after* classifying, so the blip test
  reads the previous value. Without a pending record: MOVING/UNAVAILABLE → IGNORED, a
  settled state identical to `last_settled` → IGNORED, then the unchanged late-match /
  manual logic.
- `cover.evaluate` seeds `rt.last_settled` from `inputs.actual` when settled, so the
  baseline exists after a restart before any transition.
- `tests/engine/replay.py`: new `Sim.blip()` (UNAVAILABLE and straight back to the current
  state, no tick in between — a controller skips an unavailable cover, §5).

Tests: `test_transitions.py::test_unavailable_round_trip_to_the_same_state_is_not_a_manual_move`,
`::test_moving_round_trip_to_the_same_state_is_not_a_manual_move`,
`::test_unavailable_round_trip_to_a_different_state_is_a_manual_move` (the T6 ledger line),
`test_scenarios.py::test_c2_unavailable_blip_does_not_release_a_schedule_hold`.

`test_late_match_without_pending_is_not_manual` still passes: its runtime has
`last_settled = None`, so the blip branch is skipped and the late match still fires.

### I1 — failed command retries after 30 s (`05ec114`)

- `model.Pending`: `prev_last_send_at: datetime | None = None`.
- `commands.on_command_sent` stores the previous `rt.last_send_at` in the new pending
  record; `commands.on_command_failed` restores it (reading the pending record before
  clearing it, and guarding for a failure reported with no pending record).

Tests: `test_cover_engine.py::test_failed_command_retries_after_30s_without_a_min_interval_defer`
(send at T0 → `on_command_failed(T0+1s)` returns T0+31s → `evaluate` at T0+31s yields
`Send(CLOSED, SHADING)`, previously `Defer(T0+10m, "min_interval")`), and
`test_gate.py::test_backoff_defers_a_shading_move_after_the_min_interval_expired` for the
generic backoff path (T8 ledger line). Mutation check: removing the rollback fails the
facade test at `e.rt.last_send_at is None`.

### I2 — explicit timezone for schedule queries (`aa7b238`)

`quiet_window`, `quiet_active`, `last_fired`, `next_fire`, `fired_between` and `view` take
`*, tz: tzinfo | None = None` and convert their datetime argument(s) with `astimezone(tz)`
before use; with `tz=None` the behaviour is unchanged. The module docstring states the
contract and names the silent failure mode. `fire_time`/`fire_times` already took an
explicit `tz` and are untouched.

Test: `test_schedule.py::test_utc_now_needs_an_explicit_tz` — 23:00 Vienna expressed as
21:00Z; with `tz=TZ` quiet hours are active and the 21:00 rule's `rule_fired_at` is 21:00
local, without it quiet hours read as inactive and the rule "fires" at 21:00Z (= 23:00
local). The tz-less behaviour is asserted as documentation, with a comment saying so.

### I3 — room_only with a degraded sensor (`45d542a`)

`layers.want_shade` returns `None` for `ShadingRule.ROOM_ONLY` when `inputs.room_degraded`,
else `inputs.room_hot`. The caller already maps `None` → `leave_alone` /
`shading_unknown`. `CoverInputs.room_degraded` is no longer dead.

Test: `test_layers.py::test_room_only_with_a_degraded_sensor_is_unknown` — both
`want_shade(...) is None` and the full `evaluate` result (`LEAVE_ALONE`, `SHADING`,
`shading_unknown`), plus the healthy-sensor counterpart still yielding `no_shade`/OPEN.

### I4 — side-effect-free `decide()` (`efff669`)

`CoverEngine.decide(inputs, signals) -> Decision` reads `rt.restoring_until` and returns
`layers.evaluate(...)`, nothing else. The class docstring states the startup order
(`decide` → `reconcile` → `evaluate`/act) and the persistence contract (delayed Store save
after every `evaluate`; immediate after `on_command_sent`, `reset`, `reconcile` and a MANUAL
transition). `Sim.restart()` uses `decide()` for the first decision.

Test: `test_cover_engine.py::test_decide_is_side_effect_free_and_matches_evaluate` —
`p.to_dict()` unchanged, `rt.last_evaluation`/`open_rule_satisfied_at`/`last_settled` still
None, `restoring_until` untouched, and the returned decision equals the `decision` of a
subsequent `evaluate()` with the same inputs.

### I5 — wind state across restart, `wind_enabled` (`3219976`)

`CoverEngine.__init__` builds a fresh `CoverRuntime` with
`prev_wind_active=persisted.wind_active` (an injected runtime is left alone, which is also
the T11 "injected dwell" item, finished in the minors commit); the falling-edge detector in
`evaluate` now requires `cfg.wind_enabled`.

Tests: `test_cover_engine.py::test_wind_episode_survives_a_restart` (persisted
`wind_active=True`, first evaluate with `wind_active=False` → `restoring_until = now +
900 s`) and `::test_wind_disabled_cover_gets_no_restoring_window`.

### I6 — a stop during an engine move is a manual stop (`eccd2e9`)

`classify.check_pending`: at confirm-window expiry with `current_state is PARTIAL` the
pending record is cleared and `owner=USER`, `manual_move_at=now`, `dam=pending.target`,
`dam_layer=OTHER`, `contrary_since=None` are written; `rt.unconfirmed` and the backoff timer
are left alone and `"manual"` is returned. A non-partial state keeps the `on_unconfirmed`
path.

Tests: `test_transitions.py::test_confirm_window_expiry_is_a_user_stop_only_at_partial`
covers both expiry branches in one test. Scenario k is renamed
`test_k_user_stop_at_partial_is_respected` with the tail the brief specifies.

**Expectation change (mandated):** the old scenario k asserted
`sim.engine.rt.unconfirmed` and a *second* CLOSED command after backoff. Both assertions
are gone, replaced by `len(sim.commands) == 1`, `dam is CLOSED`, `owner is USER`, actual
PARTIAL. This is the point of decision 27 and of the review's I6 (the old assertions were
the spec defect); it is the only existing expectation this wave reverses.

### Minors (`68a2d5c`)

- `classify.py` docstring: "Transition rules are added in Task 9" → "Actual-state
  classification and transition rules (spec §1.0, §1.4)".
- `model.CoverRuntime.override_dwell` default now uses `OVERRIDE_DWELL_S` from `.const`.
- `CoverEngine.__init__` only builds a `ContinuousCondition` when `runtime is None`; an
  injected runtime keeps its own dwell.
- `cover.evaluate` returns early for a disabled cover: `Decision(LEAVE_ALONE, NONE,
  "disabled")`, `action=None`, no notification, `next_check_at=None`, after recording
  `p.wind_active`. No dwell, no restoring window, no frost bookkeeping, no rule bookkeeping.
- `cover.evaluate` clears `rt.last_simulated` whenever `not signals.simulation`.
- `gate.py` gate 7 comment: `Suppress("moving")` is what the controller surfaces as §4's
  `next_planned_action`, re-evaluated on the settle transition (review I7; the spec's gate 7
  wording was changed to match in revision 3.2).

Tests: `test_cover_engine.py::test_disabled_cover_returns_early_and_runs_no_timers`,
`::test_simulation_duplicate_suppression_resets_when_simulation_is_switched_off`,
`::test_injected_runtime_keeps_its_own_dwell` (which also pins the `OVERRIDE_DWELL_S`
default).

---

## Deviations from the brief

1. **Commit trailer.** The brief asks for `Co-Authored-By: Claude Fable 5.1
   <noreply@anthropic.com>` (which the earlier branch commits carry). A system-level
   instruction in this session mandates `Co-Authored-By: Claude Opus 5 (1M context)
   <noreply@anthropic.com>` for every commit created from here on, explicitly replacing
   earlier attribution guidance. All nine commits use the Opus 5 trailer. If the branch
   must be uniform, the nine messages can be rewritten with `git rebase`/`filter-branch`.
2. **Disabled early return uses `self.status(...)` instead of a hardcoded
   `Status.DISABLED`.** The brief says "status DISABLED". §4's precedence list puts
   `cover_unavailable` *above* `disabled`, and `status()` returns `DISABLED` for every case
   except an unavailable cover, so calling it preserves both the brief's intent and the §4
   ordering. A test pins both (`Status.DISABLED` normally, `Status.COVER_UNAVAILABLE` when
   the cover is unavailable).
3. **`rt.last_settled` is seeded before the disabled early return.** The brief's ordering
   ("after still recording `p.wind_active`") places the early return before the rest of
   `evaluate`. The C2 baseline is an observation, not a timer, so it is recorded first —
   otherwise a blip on a disabled cover would take the manual-move path the moment the cover
   is re-enabled. `p.wind_active` is recorded immediately after, exactly as the brief says.

## Concerns / observations (no action taken)

- **`rt.prev_wind_active` is not updated while a cover is disabled.** The brief's list of
  what the disabled path must skip ("no dwell, no restoring window, no frost bookkeeping")
  was followed literally, so `prev_wind_active` keeps its pre-disable value. A wind episode
  that releases while the cover is disabled therefore opens a restoring window on the first
  evaluation after re-enabling. The consequence is one quiet-hours exemption for one
  command; it is not a spec violation either way (§1.1 says "no timers run for it"), but if
  the re-review prefers the episode to be swallowed, moving `rt.prev_wind_active =
  inputs.wind_active` above the early return is a one-line change.
- **Decision 27 and the disabled switch.** `check_pending`'s new partial branch writes
  `dam` unconditionally, per the brief's literal assignment list. §1.1 says manual moves are
  recorded while disabled but with `dam = null`. The case is unreachable today (a disabled
  cover never gets a pending record, and `evaluate` now returns before the gate), but if a
  cover is disabled *mid-flight* the stop would record an override. Worth a `p.enabled`
  guard if the controller can disable a cover with a command outstanding.
- **Tests are outside pyright's scope** (`include = ["custom_components/.../engine"]`), so
  the new test code is type-checked only by ruff.

## Verification

Run on the final HEAD `68a2d5c`, from `/Users/julian/Projects/hass-cover-automation`:

```
$ .venv/bin/ruff format .
31 files left unchanged

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/pyright
0 errors, 0 warnings, 0 informations

$ .venv/bin/pytest
159 passed in 0.13s

$ grep -rn "import homeassistant\|from homeassistant\|import asyncio" custom_components/cover_automation/engine/
(no matches)

$ git status --short
(clean)
```

Baseline was 140 tests; 19 were added and none removed (scenario k was renamed, not
dropped). The same four commands were run before each of the nine commits.

The 24 tests that are new or changed in this wave:

```
tests/engine/test_cover_engine.py::test_wind_episode_survives_a_restart PASSED
tests/engine/test_cover_engine.py::test_wind_disabled_cover_gets_no_restoring_window PASSED
tests/engine/test_cover_engine.py::test_injected_runtime_keeps_its_own_dwell PASSED
tests/engine/test_cover_engine.py::test_disabled_cover_returns_early_and_runs_no_timers PASSED
tests/engine/test_cover_engine.py::test_simulation_duplicate_suppression_resets_when_simulation_is_switched_off PASSED
tests/engine/test_cover_engine.py::test_decide_is_side_effect_free_and_matches_evaluate PASSED
tests/engine/test_cover_engine.py::test_failed_command_retries_after_30s_without_a_min_interval_defer PASSED
tests/engine/test_gate.py::test_backoff_defers_a_shading_move_after_the_min_interval_expired PASSED
tests/engine/test_layers.py::test_room_only_with_a_degraded_sensor_is_unknown PASSED
tests/engine/test_override.py::test_manual_move_against_shading_records_dam_and_layer PASSED
tests/engine/test_override.py::test_manual_move_against_a_shading_open_is_other_layer PASSED
tests/engine/test_override.py::test_manual_move_against_a_shading_close_without_sun_is_other_layer PASSED
tests/engine/test_scenarios.py::test_c1_manual_close_without_sun_survives_until_the_dwell PASSED
tests/engine/test_scenarios.py::test_c1_active_reopens_only_after_the_override_ended_and_the_sun_left PASSED
tests/engine/test_scenarios.py::test_c2_unavailable_blip_does_not_release_a_schedule_hold PASSED
tests/engine/test_schedule.py::test_utc_now_needs_an_explicit_tz PASSED
tests/engine/test_scenarios.py::test_k_user_stop_at_partial_is_respected PASSED
tests/engine/test_transitions.py::test_confirm_window_expiry_is_a_user_stop_only_at_partial PASSED
tests/engine/test_transitions.py::test_unavailable_round_trip_to_the_same_state_is_not_a_manual_move PASSED
tests/engine/test_transitions.py::test_moving_round_trip_to_the_same_state_is_not_a_manual_move PASSED
tests/engine/test_transitions.py::test_unavailable_round_trip_to_a_different_state_is_a_manual_move PASSED
```

(plus `test_shade_close_round_trip_gives_engine_ownership`,
`test_manual_move_against_schedule_hold_is_other_layer` and
`test_wind_disabled_cover_ignores_wind`, which the `-k` filter picked up unchanged.)

Each of C1, C2 and I1 was additionally mutation-checked by reverting the production change
and confirming the new tests fail for the right reason; those runs are quoted in the
hand-trace sections above.

## History note

The I3 commit was amended after the fact (and the four commits after it replayed with
`git cherry-pick`) to correct a decision number in a code comment — `layers.py` cited
decision 28 where decision 29 is the `room_only`/degraded ruling. Only that comment changed;
the SHAs in the table above are the final ones.
