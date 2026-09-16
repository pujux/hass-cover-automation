# Scoped re-review of the engine final fix wave (9be3629..20633a2)

### Finding Verdicts

**C1 — §1.5(e) scope — ADDRESSED.**
`model.py:289` adds `sun_hits` to `Decision`; `layers.py:62` populates it from `inputs.sun_hits` in the single `dec(...)` helper through which *every* return path of `layers.evaluate` passes (verified: lines 66–101 have no bare `Decision(...)`). `override.py:25-36` adds `_is_sun_driven_close` (`layer is SHADING and desired is CLOSED and sun_hits`) and `override.py:49` uses it for the tag; `update_dwell` (`override.py:81`) is untouched and stays a level test, so N3 survives. Tests: `test_override.py:55-68` (both new cases), `test_scenarios.py:124-165` (`_c1_sim` with `sim.advance(1)` between the manual close and the sun's arrival, then passive and active tails asserting CLOSED at 20:00 / the single `18:01` OPEN command). Mutation-checked in memory (patched `_is_sun_driven_close` back to `layer is SHADING`): all four new tests fail. Scenario a still passes, which is positive proof the rule did not over-narrow — its 17:05 assertion can only be satisfied by (e) firing on a still-SHADING-tagged override.

**C2 — blip round trips — ADDRESSED.**
`model.py:322` adds `last_settled`; `classify.py:67-69` returns IGNORED for a settled state equal to `rt.last_settled` without a pending record; `classify.py:81-88` records `last_settled` for every settled state *after* classification (so the test reads the previous value); `cover.py:76-77` seeds the baseline in `evaluate`. Tests: `test_transitions.py:120-146` (three cases incl. the T6 ledger line), scenario `test_scenarios.py:173-201` with `Sim.blip()` (`replay.py:79-87`). Mutation-checked: dropping the branch fails the scenario and both round-trip unit tests.

**I1 — failed-command retry — ADDRESSED.** `model.py:298` (`Pending.prev_last_send_at`), `commands.py:15-21` stores it, `commands.py:56-59` restores it (guarded for a failure with no pending). Facade test `test_cover_engine.py:290-300` asserts `Defer` is gone and `rt.last_send_at is None`; gate test `test_gate.py:222-236` covers the generic gate-9 backoff path (T8).

**I2 — explicit tz — ADDRESSED.** `schedule.py:62, 85, 138, 147, 156, 174` all take `*, tz: tzinfo | None = None` and `astimezone` their datetime argument(s); the module docstring (`schedule.py:1-8`) states the contract and names the silent failure. Inner calls are safe (`view` converts `now` once, then delegates; `_candidates` reads the already-converted `tzinfo`). Test `test_schedule.py:150-165`.

**I3 — room_only + degraded — ADDRESSED.** `layers.py:38-41`. Test `test_layers.py:181-191` asserts both `want_shade(...) is None` and the full `LEAVE_ALONE/SHADING/shading_unknown` decision, plus the healthy counterpart.

**I4 — side-effect-free `decide()` — ADDRESSED.** `cover.py:65-69`; class docstring `cover.py:29-41` carries the startup order and the persistence contract verbatim from the brief; `replay.py:193` `restart()` now calls `decide()` and feeds it to `reconcile`. Test `test_cover_engine.py:273-286` pins `p.to_dict()` unchanged, three runtime fields untouched, and equality with the following `evaluate().decision`.

**I5 — wind across restart / `wind_enabled` — ADDRESSED.** `cover.py:56-61` seeds `prev_wind_active=persisted.wind_active` only for a freshly constructed runtime; `cover.py:87` gates the falling edge on `cfg.wind_enabled`. Tests `test_cover_engine.py:130-147`.

**I6 — stop during an engine move — ADDRESSED.** `classify.py:115-127` implements the decision-27 branch exactly as briefed (owner USER, `manual_move_at`, `dam = pending.target`, `dam_layer = OTHER`, `contrary_since = None`, pending cleared, `unconfirmed`/backoff untouched, returns `"manual"`); the non-PARTIAL path is unchanged. Unit test `test_transitions.py:107-121` covers both branches; scenario renamed `test_k_user_stop_at_partial_is_respected` (`test_scenarios.py:368-381`) with the mandated tail.

**Minors (one commit) — ALL ADDRESSED.** `classify.py:1` docstring; `model.py:313-315` uses `OVERRIDE_DWELL_S`; `cover.py:53-54` keeps an injected runtime's dwell; `cover.py:79-82` disabled early return; `cover.py:83-84` clears `last_simulated` off-simulation; `gate.py:79-81` gate-7 comment. Tests `test_cover_engine.py:150-190`.

**Deferred-minor "fix before merge" rows:** T6 — addressed as the brief scoped it (the UNAVAILABLE manual-move branch is now covered by three direct tests); the row's other half, `override.reset()` with `CoverState.OPEN`, is still untested (`test_override.py:87-90` covers CLOSED and PARTIAL only) — the brief did not ask for it, so this is a residual note, not an open finding. T8 (backoff), T9 (docstring), T11 (injected dwell, `prev_wind_active`, disabled early return, hardcoded 1800) — all addressed.

Suite state on HEAD `20633a2`: `159 passed`, `ruff check` clean, `pyright` 0 errors, no `homeassistant`/`asyncio` imports under `engine/` — matches the report.

### New Breakage in the Fix Diff

**None Critical or Important.** Four Minor items, all introduced by this diff:

1. **Minor — `classify.py:121-124`: the decision-27 branch writes `dam` on a disabled cover.** §1.1 (spec:53-54) and §1.4 (spec:194-195) both say manual moves are recorded while disabled *with `dam = null`*; this branch assigns unconditionally, unlike `override.on_manual_move` (`override.py:44-46`). The implementer parked this as "unreachable today" — it is in fact reachable through the facade: probe (cover disabled mid-flight, then `check_pending` at window expiry) yields `enabled=False, dam=closed, dam_layer=other, owner=user`. The dam is inert until re-enable, then live. One-line fix (`if p.enabled:` around the two dam writes, else `override.clear(p)`).
2. **Minor — `cover.py:76-77`: the `last_settled` seed couples C2 to controller call order.** If a controller calls `evaluate()` with an already-updated `actual` *before* `on_transition()`, the seed makes the subsequent real manual move classify as a blip. Probe: evaluate-first → `kind=ignored`, `owner`/`manual_move_at`/`dam` untouched; transition-first → `kind=manual`, `dam=closed`. The new class docstring documents the startup order and the persistence contract but not "`on_transition` must precede any `evaluate` that sees the new actual". Add that line.
3. **Minor — `classify.py:115-127` vs. a slow position-only cover.** With travel > `confirm_window` and no intermediate progress reports, the engine's *own* completed move is now recorded as a user stop and then, on arrival, as a second manual move: probe (`travel_s=300`, window 120 s) ends at `owner=user, dam=closed, manual_move_at=12:06` with a sticky override, where the old path self-healed via `unconfirmed` → late match. This is guarded by spec:172/327 ("`confirm_window` … must exceed the cover's travel time") and by `last_progress_at` being refreshed on every `partial`/`moving` report, so it is a configuration contract, not an engine defect — but it should become a config-flow validation, since the failure mode changed from transient to persistent.
4. **Minor — `cover.py:79-82`: the disabled early return also skips `override.on_rule_fired`.** An override that existed before a cover was disabled is no longer ended by §1.5(a) while disabled (nor by (d)/(e), which is what "no timers run for it" intends). Defensible — arguably better — but it is an unannounced divergence from §1.5(a).

Direct answers to the three targeted questions: **C1** leaves no override unfinished — the tag now matches the spec's own wording (spec:188-194) one-for-one, every `Decision` carries a real `sun_hits`, and `reconcile.py:24` inherits the same rule via `decide()`'s decision. **C2** only swallows a move that ends in the same *classified* state it started in, i.e. one the engine has nothing to act on; decision 26 accepts that trade (the residual loss is a user "re-asserting" the current state to release a hold). The real edge is item 2 above, not the blip rule itself. **I6** does interact with slow position-only covers — item 3.

### Deviations judged

- **Disabled early return uses `status()` — correct, accept.** §4's precedence list (spec:361-362) puts `cover_unavailable` above `disabled`; a hardcoded `Status.DISABLED` would have broken it. `status()` returns DISABLED for every other case (`cover.py:135-138`). Pinned by `test_cover_engine.py:171-174`.
- **`last_settled` seeded before the early return — correct, accept, and necessary.** Without it, `rt.last_settled` stays stale on a disabled cover, so a dropout during the disabled period takes the manual-move branch and rewrites `manual_move_at`, releasing schedule holds the moment the cover is re-enabled. The brief's ordering words were about timers; a baseline observation is not one.
- **Parked observation A (`prev_wind_active` not updated while disabled) — does not block merge.** Worst case is one restoring window (one quiet-hours exemption for one command) on the first evaluation after re-enabling. §1.1 supports either reading. The one-line move is worth taking for symmetry with `p.wind_active`, which *is* recorded above the early return — but not now.
- **Parked observation B (decision-27 dam write on a disabled cover) — does not block merge, but the report's "unreachable today" is wrong.** See breakage item 1: it is reachable via `check_pending` on a cover disabled mid-flight, and the enable switch can be flipped at any moment. Fix it before the HA binding wires the switch; it is one line and needs no spec change.

### Out-of-Scope Observations

- `commands.on_command_sent` also clears `rt.restoring_until` (and, for schedule sends, the dam); I1's rollback restores only `last_send_at`, so a failed command still destroys a wind restoring exemption and the 30 s retry can then be blocked by quiet hours. Pre-existing, now conspicuous — a one-line `prev_restoring_until` would close it.
- Gate 1 (`Suppress("disabled")`, `gate.py:51-53`) is now unreachable through `CoverEngine.evaluate`. It remains correct and independently tested (`test_gate.py:76-87`); a controller that logged the suppress reason for disabled covers now sees `action=None` plus `status=disabled`.
- `override.reset()` with `CoverState.OPEN` is still untested (T6's other half).
- Tests sit outside pyright's `include`, so the 24 new/changed tests are type-checked by ruff only.

### Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.** Nine commits, one per item, content matching the report; C1, C2 and I1 independently mutation-checked here. Four Minor follow-ups are recommended before the HA binding lands (disabled-cover `dam` guard in `check_pending`; document the `on_transition`-before-`evaluate` ordering; reject `confirm_window ≤ travel_time` in the config flow; note or restore §1.5(a) for disabled covers) — none of them blocks this merge.