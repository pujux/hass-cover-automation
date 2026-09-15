# Design Review — Cover Automation Spec (§1, §2, §5)

*Reviewer lens: behavioural correctness and edge cases of the decision logic. Read-only; no files were modified.*

State-tuple notation used throughout:
**S = (owner, engine_target, manual_move_at, desired_at_manual_move, wind_active)** — abbreviated `dam` for `desired_at_manual_move`.

---

## 1. Summary

The layer-stack architecture is sound and the act gate is in the right shape, but the spec is not yet implementable consistently: several rules are circular or under-specified in ways that flip behaviour to the opposite outcome depending on which reading an implementer picks. The most serious problem is structural — because the schedule layer (5) sits above shading (6) and a rule's hold **always** runs until the next rule fires, any cover with the most common profile (`open 07:00` / `close 21:30`) has shading permanently suppressed; the integration's headline feature is unreachable for scheduled covers. Second, the manual-override lifecycle is defined as a pure predicate over `desired_at_manual_move` but nothing ever clears that field, so an override silently re-arms every time `desired` returns to its old value — a cover opened once against shading is never shaded again. Third, the five persisted scalars are provably insufficient: `engine_target` is only written **after** a command is confirmed, so a restart with a command in flight records the engine's own move as a manual move and permanently strands the cover in `owner = user`. Lastly a cluster of smaller but user-visible gaps: debounce/hysteresis have no defined initial value after a restart or reload, `partial` has no resolution path, reconcile destroys overrides whenever `desired` is `leave_alone`, and the door-sensor-unavailable case is undefined on a safety-critical path.

---

## 2. Findings

### BLOCKER 1 — A schedule hold permanently suppresses the shading layer

**Section:** §1 layers 5/6, §1.3; decision 7; feature-selection C3′, S1–S6.

**Problem.** §1.3: *"A rule holds from its fire time until the next rule of the profile fires."* Combined with layer 5 sitting above layer 6, a profile always has exactly one hold active from the moment its first rule fires — there is no gap. The shading layer is therefore consulted only for covers with **no profile at all**, or after a manual move has released the hold.

**Evidence.** Profile `{open 07:00, close 21:30}` (the canonical configuration): 07:00–21:30 the hold says `open`, 21:30–07:00 it says `closed`. `want_shade` is never evaluated. The status sensor reads `schedule_hold` for 24 h. Scenario (g) is the degenerate case: a single `open at sunrise+30` rule means the hold says `open` for 24 h, and the only way to get shading back is for the user to close the cover by hand — which is exactly what the automation was supposed to do.

**Suggested fix.** Make the hold direction-aware, since the asymmetry is real (see BLOCKER 3/MAJOR 8 — in mode `auto` `open` is the ground state at night, so a *close* rule genuinely must hold, while an *open* rule must not). Concretely, add a per-rule `hold: bool`, default **true for `close`, false for `open`**, and restate §1.3:

> A rule with `hold = true` holds from its fire time until the next rule of the profile fires, a manual move releases it, or the rule's hold window elapses. A rule with `hold = false` yields an opinion only until the cover has reached the rule's action (or for at most `min_move_interval` after firing); afterwards the layers below govern. Both rule kinds clear `desired_at_manual_move` on firing per §1.2.

### BLOCKER 2 — Circular/unordered evaluation: is "current desired" computed before or after the hold release?

**Section:** §1.1 (manual-move bullet), §1.3, §5 (reconcile).

**Problem.** §1.1 says, in one bullet, both `desired_at_manual_move = current desired` **and** *"Any schedule hold on that cover is thereby released"*. But `current desired` depends on whether the hold is released, and the release depends on `manual_move_at`, which the same bullet is writing. Two implementations, opposite outcomes.

**Evidence (scenario b, morning).** Profile has only `close 21:30`; hold active; S = (engine, closed, null, null, false). User opens at 07:30.
- Reading A (desired computed **before** the release): `dam = closed` → at 11:00 shading wants `closed` → predicate `desired == dam ∧ actual ≠ desired` → **override active → the cover is never shaded that day**.
- Reading B (desired computed **after** the release): the hold is gone, shading says `open` → `dam = open` → at 11:00 `desired = closed ≠ dam` → no override → **the engine closes for shading at 11:00**.

The same circularity exists in §5 reconcile (`manual_move_at = now` is written while `current desired` is being computed from it).

**Suggested fix.** Pin the order explicitly: *"On a manual move, `desired` is the value from the last completed evaluation (i.e. the state the user acted against); `manual_move_at` and the hold release take effect from the next evaluation onward."* Apply the same rule to reconcile: compute `current desired` with the **persisted** `manual_move_at`, then write the new one.

### BLOCKER 3 — The manual override never ends (and self-resurrects); nothing ever clears `desired_at_manual_move`

**Section:** §1.2; decision 5; feature D4.

**Problem.** §1.2 defines the override as a *pure predicate* recomputed each evaluation. §1.2 then claims *"the override ends automatically as soon as the desired state changes"*. Under the predicate it does not **end** — it goes dormant and re-arms the moment `desired` returns to `dam`. `dam` is cleared only by a schedule rule or the reset button, so a cover **without a profile** keeps its override forever.

**Evidence (scenario a, both reopening modes — the mode is irrelevant here because the blocked action is a *close*, and act gate 4 only gates opens).**

| t | event | S | desired | override |
|---|---|---|---|---|
| 13:00 | engine closes for shading | (engine, closed, null, null, false) | closed | – |
| 14:00 | user opens | (user, **closed**, 14:00, **closed**, false) | closed | **active** |
| 14:30–14:50 | cloud (20 min ≥ off-delay) | unchanged | closed → **open** at 14:50 | dormant |
| 15:00 | sunny back (on-delay 10 min) | unchanged | **closed** | **active again** |
| 17:00 | sun leaves window | unchanged | open | dormant |
| next day 10:00 | sun hits again | (user, closed, 14:00, **closed**, false) | closed | **active again** |

The cover is never shaded again — on any subsequent day — and `owner` never returns to `engine`, so passive reopening is dead for it too. This contradicts decision 5 and feature D4's intent of a *temporary* override. The spec's own rationale (*"a passing cloud does not end it because debounce keeps desired stable"*) only holds for clouds shorter than the 20 min off-delay; the behaviour has a hard cliff at exactly the off-delay.

The opposite repair (clear `dam` whenever `desired` changes) is also defective: at 14:50 the override dies and at 15:00 the engine re-closes the cover the user opened 60 minutes earlier, because a 21-minute cloud passed.

**Suggested fix.** Give the override an explicit lifecycle with a *structural* end condition rather than "any change of desired":

> `desired_at_manual_move` is cleared (override ends permanently) when (a) a schedule rule of the cover's profile fires, (b) reset is invoked, or (c) `desired ∈ {open, closed}` and `desired ≠ desired_at_manual_move` **continuously for at least `sunny_off_delay + sunny_on_delay`** (or, for shading-originated overrides, when `sun_hits` goes false for that cover). `desired == leave_alone` never clears it and never counts toward the dwell.

Clause (c)'s dwell requirement is what makes a passing cloud — of *any* length — harmless, which is what decision 5 actually wanted.

### BLOCKER 4 — Five scalars are insufficient: an in-flight command at restart is recorded as a manual move

**Section:** §1.1 (match rule), §5 (Store, reconcile). Answers the "prove or disprove" question.

**Problem.** `engine_target` is written **only on a confirmed match**, so throughout a command's flight it holds the *previous* target. The Store holds no pending command, and the save is *delayed*. Reconcile's only test is `actual == engine_target`.

**Disproof (scenario f, part 2).** West window, hot day. S = (engine, **open**, null, null, false) from the morning opening. 19:00 the engine sends `close`; HA is killed at 19:00:01; the cover finishes closing during downtime; HA returns at 19:05.
- Reconcile: `actual = closed ≠ engine_target = open` → S = (**user**, open, 19:05, closed, false). No override (actual == desired), but `owner = user`.
- 20:00 sun leaves → `desired = open`, `actual = closed` → act gate 4 (`passive`): the engine does not own the current state → **the cover is never reopened**. It stays shut until a schedule rule fires, the user acts, or reset is pressed. For a cover with no profile, that is indefinite.

The five scalars cannot distinguish this from a genuine manual close, because the only record of the engine's intent is destroyed by its own confirmation protocol.

A second insufficiency (recoverable within five scalars, but only with a rule the spec does not state): reconcile's mismatch branch unconditionally overwrites `manual_move_at`/`dam`, so a restart while `desired == leave_alone` destroys a live override (see MAJOR 2).

**Suggested fix.** Either (i) persist a sixth field `pending_target {state, sent_at}` and have reconcile treat `actual == pending_target` as an engine-owned move; or (ii) write `engine_target` **at send time** and redefine ownership as *"`owner = engine` iff the last command the engine sent is the state the cover reached"* — then §1.1's match rule only flips `owner` to `user`, never sets `engine_target`. Also state that the Store is flushed immediately when a command is sent, not on the delayed-save schedule.

---

### MAJOR 5 — Debounce and hysteresis have no defined initial value after restart or reload

**Section:** §2 (sunny debounce, sun hysteresis), §5 (*"Recomputed from live sensors, never stored"*).

**Problem.** "Recomputed from live sensors" admits two readings: (A) start the debounce/Schmitt trigger from `false`/`off` and let it settle, or (B) seed it from the current raw value as already settled. Reading A is harmful.

**Evidence.** Sunny midday, covers closed for shading, S = (engine, closed, …). HA restarts (or the entry reloads after any option change — §5 reloads the entry for every subentry/hub change). First evaluation: `sunny = false` (needs 10 min of continuous truth) → `want_shade = false` → mode `auto` → `desired = open`; reconcile keeps `owner = engine`; act gate 4 `passive` passes (engine owns); min interval has long expired → **every shaded cover opens, then closes again 10 minutes later.** The same limbo problem applies to `sun_hits`: the hysteresis band has no defined "previous value" at startup.

**Suggested fix.** State: *"At startup and after a reload, the sunny debounce is seeded from the current raw condition as already settled; `sun_hits` is seeded with the strict-inside test; `room_hot`/`room_cold` are seeded from the current reading with the hysteresis band resolved toward the nearer state. If the weather entity has not yet reported, `sunny = unknown` (shading yields `leave_alone`) until it does."* The `unknown` state already exists in §2 and is the safe default.

### MAJOR 6 — Reconcile destroys a live override whenever `desired` is `leave_alone` (violates D4 "survives restart")

**Section:** §5 reconcile; §1.1; feature D4.

**Problem.** Reconcile's mismatch branch sets `dam = current desired`, and §1.1's rule maps `leave_alone → null`. §5 does not repeat the `null` clause at all — a second inconsistency between §1.1 and §5.

**Evidence.** Frosty morning, frost layer wins → `desired = leave_alone` **for every cover**. User had opened a cover against shading yesterday: S = (user, closed, 14:00, closed, false). Restart at 08:00 during frost → mismatch branch → S = (user, closed, 08:00, **null**, false) → override gone for *every* cover simultaneously. When frost releases at 11:00 and shading wants `closed`, the engine closes covers the user had deliberately opened. Same effect for a restart during quiet hours or a `hold`-action wind episode.

**Suggested fix.** *"In the mismatch branch, if `owner` is already `user`, keep `manual_move_at` and `desired_at_manual_move` unchanged. Only when ownership transitions engine→user does reconcile set them, and if `current desired` is `leave_alone`, defer the write until the first evaluation that yields `open` or `closed`."* Also add: *"On first setup (`engine_target == null`), set `owner = engine`, `engine_target = actual` rather than synthesising a manual override for every cover."* (As written, a fresh install shows `manual_override` on every closed cover.)

### MAJOR 7 — Manual moves made while `desired == leave_alone` get no override protection at all; the predicate contradicts the "frozen" prose

**Section:** §1.1, §1.2.

**Problem.** Two issues in one place. (a) `dam = null` when `desired` is `leave_alone`, so a manual move during quiet hours, frost, a wind `hold`, an unknown-sunny period, or on a `dark_only`/`protection_only` cover produces **zero** override. (b) §1.2's prose says *"while desired is `leave_alone` the override status is frozen"*, but the formal predicate requires `desired ∈ {open, closed}` and therefore evaluates to **false**. These are different: the binary sensor, the `status` enum and any "has desired changed?" logic all behave differently under the two readings.

**Evidence.** Quiet hours 22:00–07:00. User opens a cover at 23:00 → S = (user, closed, 23:00, **null**, false). 11:00 next day shading wants `closed` → no override → the engine closes it. The user's deliberate night-time move bought nothing.

**Suggested fix.** Replace the `null` rule with: *"If `desired` is `leave_alone` at the time of a manual move, `desired_at_manual_move` is set to the last `open`/`closed` desired state from the current 24 h, else to the inverse of the new actual state."* And restate §1.2 as: *"While `desired` is `leave_alone`, the override predicate is not evaluated; the last computed override status is retained (that is what 'frozen' means) and `desired_at_manual_move` is not cleared."*

### MAJOR 8 — Mode `auto`'s unconditional `open` makes "open" the 24/7 ground state; reset opens covers at night

**Section:** §1 layer 6, §1.2 (reset), act gate 4.

**Problem.** *"Mode `auto`: `closed` if want_shade else `open`"* holds at 03:00 as much as at 13:00. The only thing stopping night-time openings is `owner == user` (passive) or an active override — both of which the reset button deliberately removes.

**Evidence (continues scenario d).** Door opened at 22:00, engine opened the cover, user re-closed it at 22:05 → S = (user, open, 22:05, open, false), hold released, override active, door closes at 22:30. At 23:00 the user presses `reset_override` → S = (**engine**, **closed**, –, null, false). Next evaluation: `desired = open` (no shade), act gate 4 `passive` → *the engine owns the current state* → **the cover opens at 23:00.** The reset button is documented as "hand ownership back to the engine", not "open my cover now".

**Suggested fix.** Make the shading layer's `open` a *release of its own close* rather than a ground state:

> Mode `auto`: `closed` if `want_shade`; `open` if `NOT want_shade` **and** (the cover is engine-owned at `engine_target = closed`, **or** reopening mode is `active`); otherwise `leave_alone`.

This preserves the 17:00 reopening of scenario (a), makes `off`/`passive`/`active` differences explicit in one place, and removes every night-time opening path.

### MAJOR 9 — Reopening mode `active` is nearly a no-op (contradicts D6)

**Section:** act gate 3 vs 4; feature D6; reference §4.3.

**Problem.** Gate 3 (override) precedes gate 4 (reopening mode), and — per BLOCKER 3 — a user-closed cover's override persists as long as `desired` stays `open`. In the reference integration `active` means "reopen user-closed covers *after the override duration*"; with no timer here, `active` can only differ from `passive` after some *other* event has cleared the override. On an overcast day `desired` never leaves `open`, so `active` and `passive` are indistinguishable for the whole day.

**Suggested fix.** State the intended precedence explicitly. Either add to gate 3: *"…unless reopening mode is `active` and the override originated from the shading layer"*, or document in §1.4 that `active` only takes effect once the override has ended per §1.2, and that there is deliberately no timer.

### MAJOR 10 — "The engine owns the current state" is undefined; `engine_target` is never invalidated

**Section:** act gate 4, §1.1, §5.

**Problem.** Two candidate definitions — `owner == engine`, or `engine_target == actual` — and the spec uses the phrase without choosing. They diverge because a manual move sets `owner = user` but leaves `engine_target` stale. Example: engine closes (target = closed); user stops the cover at 50 % then closes it fully by hand → `engine_target == actual == closed` but `owner == user`.

**Suggested fix.** Define once in §1.1: *"'The engine owns the current state' means `owner == engine`. On any manual move, `engine_target` is set to `null`."* (With BLOCKER 4's fix, `engine_target` becomes "last commanded target" and the definition becomes `owner == engine AND engine_target == actual`.)

### MAJOR 11 — `partial` has no resolution path; a stopped cover is overridden forever

**Section:** §1.1, §1.2, §4 status enum, §5.

**Problem.** `partial` is produced by classification but (i) cannot be stored in `engine_target` (open|closed|null), (ii) satisfies `actual ≠ desired` for **both** possible desired values, so the §1.2 predicate makes it a permanent override, and (iii) has no value in the `status` enum.

**Evidence (scenario k).** Engine sends `close`; user presses stop at 50 %; the settled state is `partial`, no match → S = (user, closed, t, **closed**, false). Engine wants `closed` → predicate: `owner == user ∧ desired == dam ∧ actual(partial) ≠ closed` → **override active → the engine never finishes the close.** Defensible as "the user stopped it deliberately", but the spec never says so, and it never says how the cover gets out of `partial` (nothing ends the override, since `desired` stays `closed` all afternoon).

**Suggested fix.** State explicitly: *"`partial` is treated as a manual state; an override created at `actual == partial` ends as soon as `desired` changes or the cover leaves `partial`. `status` gains a `partial` value (or reuses `manual_override` with `actual_state = partial` in the attributes). `engine_target` is set to `null` when the cover settles `partial`."*

### MAJOR 12 — Transition classification: raw vs classified state, and the pending-record lifecycle

**Section:** §1.1, §5 (command failures).

**Problem.** Three under-specified points on the same path:
1. *"Every settled cover state change"* — raw HA state change or **classified** state change? A drift 100 % → 97 % is a raw change that matches no pending command and would be classified as a manual move, defeating X7 (*"drift is never mistaken for a manual move"*).
2. Covers that update `current_position` without ever reporting `opening`/`closing` emit intermediate values (`partial`) mid-move; under §1.1 each one is a manual move that clobbers `owner`/`dam` while the engine's own command is in flight.
3. When a settled state change **does not** match the pending command, is the pending record dropped, or does it stay open until the 120 s window (so a slow cover that eventually reaches the target is still credited to the engine)? §5 only defines the "no transition at all" case.

**Suggested fix.** *"Classification operates on the classified state (§1.1) and only when it changes. While a command is pending, non-matching classified states do not create a manual move: the pending record is retained until (a) the target state is reached — match; (b) a settled state contrary to the target persists for 10 s — manual move; or (c) the confirm window (default 120 s, per-cover configurable for slow covers) expires — `unconfirmed`, record dropped."*

### MAJOR 13 — Door sensor unavailable is undefined on a safety path; frost source unavailable likewise

**Section:** §1 layer 3, §2, §4 repairs.

**Problem.** §4 raises a repair for a *missing* door sensor, but §1/§2 never define the layer's opinion when the sensor is `unavailable`/`unknown` (flat battery on an open terrace door is the realistic case). The natural implementation `state == "on"` evaluates to false → the cover may close on an open door, breaking D1, which is the one hard guarantee in the base conditions. Same gap for the frost source: if the outdoor-temperature sensor or weather entity is unavailable, is `frost_active` false (covers move at −8 °C) or held?

**Suggested fix.** *"A door sensor that is `unavailable`/`unknown` is treated as `on` for the purpose of blocking closes (the layer yields `leave_alone` rather than `open`, so it does not force a cover open on a dead sensor), plus a repair issue. A frost source that is unavailable holds the last known `frost_active` value for the grace period and then yields `frost_active = unknown`, which blocks shading- and schedule-layer moves but not wind protection, plus a repair issue."*

### MAJOR 14 — Wind release inside quiet hours, and rules that fire inside quiet hours, have no defined catch-up

**Section:** §1 layers 2/4/5, §1.3.

**Evidence (scenario c).** Profile `{close 21:30}`, quiet hours 22:00–07:00.
| t | S | desired | action |
|---|---|---|---|
| 21:30 | (engine, closed, null, null, false) | closed (hold) | engine closes |
| 22:00 | (engine, **open**, null, null, **true**) | open (wind) | act gate 2 → opens immediately ✔ |
| 22:45 | wind < lower, hold timer runs | open | – |
| 23:00 | (engine, open, null, null, **false**) | **leave_alone** (quiet hours) | **cover stays open all night** |
| 07:00 | quiet hours end; hold from 21:30 still active (owner == engine, so §1.3 never released it) | closed | **engine closes at 07:00** |
| 07:00–21:30 | hold `closed` persists (BLOCKER 1) | closed | **cover stays closed all day** |

So the storm costs the user a night of privacy and then a day of daylight. Without quiet hours configured the cover simply re-closes at 23:00, which is fine. Scenario (i) is the same defect from the other side: a rule at 23:00 inside quiet hours 22:00–07:00 fires (clearing `dam` on every cover of the profile per §1.2) but cannot move anything, and the spec never says whether the move is executed at 07:00 or dropped — only the hold's continued existence implies 07:00.

**Suggested fix.** Add to §1.3/§1.4: *"When wind protection releases and a lower layer's opinion still differs from the actual state, the restoring move is part of the protection episode and is exempt from quiet hours."* And: *"A schedule rule whose fire time falls inside its own profile's quiet hours is rejected at config time (sun-relative rules that drift into the window raise a repair issue and are skipped, not deferred)."*

### MAJOR 15 — `hot_day` is not latched; the min latch can retract it mid-day, and it is undefined after midnight

**Section:** §2 (hot day); feature W1 (*"latched per day"*).

**Problem.** The spec latches the *inputs* asymmetrically (max only rises, min only falls) but not the *flag*. Since `hot_day = max ≥ 24 ∧ (min ≥ 13)`, a forecast whose daily min drops from 14 °C to 12 °C during the afternoon flips `hot_day` **off**, irreversibly for that day → `want_shade` false → in mode `auto` the engine **opens** every shaded cover at the hottest hour. This contradicts W1 and defeats the stated purpose of the latch.

Scenario (m): between local midnight and the first successful forecast fetch, `{date, max, min}` is stale-dated and the spec says nothing. Three plausible implementations (false / carry over yesterday / unknown) with different behaviour for east-facing covers at sunrise. `weather.get_forecasts` failing or returning an empty daily list is also undefined (the reference falls back to the last snapshot).

**Suggested fix.** *"`hot_day` is latched per local day: once true it stays true until the local-midnight rollover. Until the first daily forecast of the new local day has been fetched, `hot_day = unknown`, which makes the shading layer yield `leave_alone` (identical to the sunny-unknown path). A failed or empty forecast keeps the previous fetch's values and raises the weather repair issue after the grace period."*

### MAJOR 16 — The act gate mixes blocking and bypassing rules; several scopes undefined

**Section:** §1 act gate, §1.4.

**Problem.** Rules 1, 3, 4, 5 block; rule 2 (*"move immediately"*) bypasses; rule 6 transforms. The spec never says how far the bypass reaches. Specifically: does a wind move bypass rule 6, i.e. does **simulation mode still simulate during a storm**? (It must — that is the entire point of simulation, and it is also a safety claim in the other direction.) Also undefined: does rule 5's clock restart on *every* engine move, or only on shading moves? §1.4 says the min interval *exempts* wind/door/schedule moves, which answers "can they be blocked" but not "do they restart the clock" — so after a wind move at 12:00 a shading close at 12:05 may or may not be delayed to 12:10. Finally, the act gate is silent about acting while `actual == moving`.

**Suggested fix.** Restate the gate as an ordered decision producing one of `{send, defer(t), suppress}`: *"1 frost → suppress. 2 wind-layer desired → send (subject to 6). 3 override → suppress. 4 reopening mode → suppress opens as specified. 5 min interval — evaluated only for shading-layer moves, measured from the last **shading-layer** engine move → defer. 6 simulation → log instead of send, for every layer including wind. While `actual == moving`, only wind- and door-layer desires are sent; other layers defer until the cover settles."*

### MAJOR 17 — `protection_only` is not defined beyond the shading layer

**Section:** §1 (mode select), layer 6; feature D7 (which calls the third mode "off").

**Problem.** Only layer 6 is gated on mode. As written, a `protection_only` cover still obeys its door sensor, quiet hours and its full schedule profile — which is not what the name says, and D7's "off" implies neither. Combined with BLOCKER 1, a `protection_only` cover with a profile is fully schedule-driven.

**Suggested fix.** State explicitly which layers each mode enables, e.g. as a table: `auto` = 1–7; `dark_only` = 1–6 with the shading layer emitting `closed` only; `protection_only` = 1–3 + 7 (frost, wind, door) with schedule, quiet hours and shading skipped. If schedules *are* meant to apply in `protection_only`, rename the mode.

---

### MINOR 18 — Undefined or under-specified terms

- **`next_planned_action` (+time)** (§4) is never defined. Candidates: the cover's next schedule rule; the move currently deferred by the min interval with its retry time; a prediction of the next shading transition. The hub already has `next_scheduled_event` for the first reading, which suggests the per-cover one means something else. Define it.
- **`status` precedence** (§4): the enum has 14 values but no ordering. What does a cover in quiet hours, with an active override and a dead room sensor, report? Define the precedence (suggest: `unavailable > disabled > command_failed > unconfirmed > held_frost > protected_wind > door_open > quiet_hours > manual_override > schedule_hold > closed_shading/open_no_shade > idle`, with `degraded` as an attribute rather than a status).
- **`manual_override.since`** is `manual_move_at`, which reconcile resets to the restart time — the sensor will report a freshly-started override after every restart. Note it or preserve the original timestamp.
- **"Frozen" for the wind protection state** when the wind sensor is unavailable (§2): frozen at the last value, and for how long? If frozen at `true`, the cover stays open indefinitely — safe but worth stating; if frozen at `false`, there is no protection during the storm that killed the sensor.

### MINOR 19 — Sun-hits geometry gaps

- **Azimuth wrap-around is not specified.** *"azimuth inside [az − tol_left, az + tol_right]"* is ill-defined for `azimuth = 350, tolerance = 60`. Specify the signed-difference form: `signed_diff = (sun_az − cover_az + 180) mod 360 − 180`, `hit ⇔ −tol_left < signed_diff < tol_right`.
- **Scenario (n):** with a window narrower than 2 × margin (e.g. tolerances 1°/1°, margin 2°) there is no deadlock, but the cover keeps shading up to 2° beyond its configured window — harmless, worth documenting. With `elevation_min = 0` (the default) the off-condition requires elevation < −2°, so `sun_hits` stays true for roughly 10–15 minutes after sunset; if `sunny` has not yet debounced off, a cover can close *after* sunset. Consider clamping the elevation hysteresis at the horizon (`elevation_min = 0` → off at elevation ≤ 0).
- **Hysteresis limbo has no defined initial value** after a restart — see MAJOR 5.

### MINOR 20 — Shading-rule and mode combinations that silently do nothing

- `shading_rule = room_only` with no `room_temperature_sensor` configured, or with the sensor unavailable, means the cover is **never** shaded (`room_hot` is false). §2 only says the status shows `degraded`. Reject the combination in the config flow and state the per-rule degraded fallback explicitly (`forecast_with_room` → drop the comfort floor; `room_only` → no shading + repair; `either` → forecast only).
- Hub shading mode **`forced_*` bypasses the comfort floor** (`room_cold`), which decision 4 states as an invariant of the default rule. Say so explicitly so it is not read as a contradiction.
- Hub shading mode **`off`** leaves layer 6 silent, so covers already closed for shading stay closed with no release path. Expected, but users will report it; consider a status/repair hint.
- `forced_all` + per-cover `dark_only` (scenario j) = permanently closed (closes at sunrise, never opens, and stays closed after the hub returns to `auto`, since `dark_only` may only close). Per D7 this is correct, but it deserves a documented note; `forced_all` + `protection_only` correctly does nothing.

### MINOR 21 — Timing, flapping and lifecycle details

- **Scenario (l):** `sunny` cannot flip faster than `on_delay + off_delay` (30 min) and `sun_hits`/`hot_day` are slow, but **`room_hot` has only a 0.5 K hysteresis and no dwell time** — a noisy room sensor can drive `want_shade` at the full min-interval rate (6 moves/hour with the 10 min default). Add a minimum dwell to the room-temperature hysteresis, or state that `min_move_interval` is the intended and sole rate limit.
- The min-interval **retry timer** can fire a move for a condition that began Δ minutes earlier; this is benign for shading but should be stated so the logbook reason includes the original trigger time.
- **Command backoff** (*"subsequent re-sends of the same target back off by doubling the min interval up to 1 h"*) has no base for wind/door/schedule moves, which are exempt from the min interval. Define the backoff as its own per-cover timer applying to all layers.
- **First evaluation after a reload:** §5 ties it to `EVENT_HOMEASSISTANT_STARTED`, which does not fire when an entry reloads on a running instance (and §5 reloads on every config change). Add "…or immediately if `hass.is_running`". Also ensure `RestoreEntity` state (notably `enabled = off`) is restored before the first evaluation, or a disabled cover may be moved once at startup.
- **Simulation mode** never produces a match, so `desired ≠ actual` persists and the command is "sent" on every evaluation — the min interval only damps shading moves, so a simulated wind/door/schedule move logs on every trigger. Add a dedup (log a simulated command only when the desired/actual pair changes), and state that simulation never writes `engine_target`.
- **Delayed save** (§5) can lose the most recent manual move on an unclean shutdown, which then feeds the reconcile path in MAJOR 6/BLOCKER 4. Flush on every ownership change.
- **Scenario (h)** — two consecutive `close` rules (20:00, 22:00) are legal and produce a working "keep closed" re-close, because the 22:00 rule clears `dam` on every cover of the profile and re-closes what the user opened at 20:30. Decision 7 explicitly *dropped* C2 "keep closed"; this is a user-reachable re-implementation. Not necessarily a defect, but it should be a documented escape hatch rather than an accident, and the second rule causes no move and no logbook entry when the cover is already closed.

### MINOR 22 — Door layer vs a *pre-existing* override (D3 wording)

Feature D3 says *"Manual move **while door is open** is respected"*, but act gate 3 blocks the door layer's `open` for **any** active override, including one created before the door opened. Concretely: the user closes the cover at 21:00 (override active, `dam = open`), then opens the terrace door at 22:00 → `desired = open == dam` → override still active → the cover does **not** lift, and the user steps out behind a closed roller shutter. This is consistent with the base condition ("a manual override is respected"), so it may be intended — but it should be a deliberate, documented choice. If not intended, the fix needs no new persisted state: *"The door layer's `open` request bypasses the override when `manual_move_at < door_sensor.last_changed`."*

---

## 3. Scenario walk-through table

| # | Scenario | Outcome per spec | Verdict |
|---|---|---|---|
| a | Open against shading 14:00; cloud 14:30–14:50; sun leaves 17:00; sun returns next day 10:00 | Trace in BLOCKER 3. Override active 14:00; dormant 14:50; **re-arms 15:00**; dormant 17:00; **re-arms next day 10:00** → cover never shaded again, `owner = user` forever. Passive and active are identical here (the blocked action is a close; gate 4 only gates opens). A 19-min cloud is harmless, a 21-min cloud changes everything. | **Wrong** (BLOCKER 3) |
| b | Close 14:00 vs desired open; sun 15:00–18:00; rule close 21:30; no morning rule; user opens 07:30 | 14:00 → (user, open, 14:00, **open**, false), override. 15:00 desired=closed → dormant, no move. 18:00 desired=open → **re-arms**, cover stays closed (right outcome, wrong mechanism). 21:30 rule → `dam = null`, hold=closed, no move, `owner` stays `user`. 07:30 user opens → hold released; `dam` = **closed or open depending on evaluation order** → the cover is either never shaded that day or shaded normally at 11:00. Hold otherwise runs to 21:30 the next day → shading suppressed all day. | **Wrong** (BLOCKER 1, 2, 3) |
| c | Wind forces open under hold "closed"; releases 23:00; quiet hours? | Trace in MAJOR 14. Without quiet hours: re-closes at 23:00 — acceptable. With quiet hours 22:00–07:00: **open all night, closes at 07:00, then held closed all day.** Quiet hours do matter, and the spec does not define the catch-up. | **Wrong** (MAJOR 14 + BLOCKER 1) |
| d | Door opens under hold "closed"; user closes while door open; door closes | 21:30 (engine, closed, –, null, false). Door on → desired=open, gate 4 exempts the door layer → engine opens → (engine, open, –, null, false). User closes 22:05 → (user, open, 22:05, open, false), override active, hold released. Door closes 22:30 → desired = shading = `open` (night!) → override still active → cover stays closed ✔. But pressing reset at 23:00 → engine opens it at 23:00. | **OK, with a surprising reset path** (MAJOR 8) |
| e | Frost + cover closed + wind > upper; frost releases 2 h later with wind still high; wind then drops | Frost → desired=`leave_alone` all covers, act gate 1 never reached; `wind_active = true` persisted; notification + repair raised ✔. Cover stays closed through the storm (decision 6, accepted). Frost releases → wind layer → gate 2 → opens immediately ✔. Wind < lower for 15 min → `wind_active = false` → falls through to door/quiet/schedule/shading ✔. Note: any manual move made during the frost window gets `dam = null` → no override afterwards (MAJOR 7). | **OK** (per decision 6) |
| f | (1) Restart with override active. (2) Restart with a command in flight that completed during downtime | (1) Normally preserved (`dam` re-derived = same desired), but `manual_move_at` jumps to the restart time and the override is **destroyed** if `desired` is `leave_alone` at reconcile (frost/quiet hours) → MAJOR 6. (2) Trace in BLOCKER 4: engine's own move recorded as manual → `owner = user` → in passive mode the cover is never reopened. | **Wrong** (BLOCKER 4, MAJOR 6) |
| g | Profile with one rule `open at sunrise+30`, no close rule | The hold means `open` from sunrise+30 until the same rule fires 24 h later. Layer 5 is above layer 6, so **shading never runs for that cover, all day, every day** — unless the user closes it by hand, which releases the hold. | **Wrong** (BLOCKER 1) |
| h | Two `close` rules (20:00, 22:00) | 20:00 hold closed; user opens 20:30 → hold released, override active; 22:00 rule fires → `dam = null` for every cover of the profile → override cleared → engine re-closes. Effectively re-implements the "keep closed" feature decision 7 dropped. If the cover is already closed at 22:00, no move and no logbook entry. | **Surprising** (MINOR 21) |
| i | Quiet hours 22:00–07:00 with rule `close 23:00` | Layer 4 outranks layer 5 → the rule fires (clearing `dam` on every cover of the profile) but cannot move anything; the hold persists, so the close lands at **07:00**, then holds closed until the next rule. Whether deferring or dropping is intended is undefined. | **Surprising / undefined** (MAJOR 14) |
| j | `forced_all` + `dark_only`; `forced_all` + `protection_only` | `dark_only`: `want_shade` = sun elevation in range (default 0–90 → "sun is up") → closes at sunrise, `leave_alone` at sunset → **permanently closed**, including after the hub returns to `auto`. Per D7 this is what `dark_only` means. `protection_only`: layer 6 skipped → `forced_all` correctly has no effect — but doors/schedules/quiet hours still apply, which the mode's name contradicts. Forced modes also bypass the comfort floor. | **OK but trap-like** (MAJOR 17, MINOR 20) |
| k | Position 50 after a user stop; engine then wants closed | Settles `partial`, no match → (user, closed, t, **closed**, false) → override predicate true (`actual ≠ desired`) → **the engine never finishes the close**; nothing ends the override while `desired` stays `closed`; `status` has no `partial` value; `engine_target` cannot represent it. | **Wrong / undefined** (MAJOR 11) |
| l | Min interval (shading only) × debounce — can shading flap? | `sunny` cannot flip faster than 30 min and `sun_hits`/`hot_day` are slow, so the shading path is well damped — **except `room_hot`**, whose only guard is a 0.5 K hysteresis: a noisy sensor flaps `want_shade` at the min-interval rate (6 moves/h). The deferred re-evaluation itself is safe (desired is recomputed), but the min-interval **clock origin** is undefined (any engine move vs shading moves only), so a shading close can be delayed 10 min after an unrelated wind or schedule move. | **Mostly OK, two gaps** (MAJOR 16, MINOR 21) |
| m | `hot_day` latch across midnight before the new forecast is fetched | Undefined — false, carried over, or unknown, with materially different behaviour for east-facing covers at sunrise. Separately, the min-only-falls latch can **retract** `hot_day` mid-afternoon and open every shaded cover at peak heat, contradicting W1's "latched per day". | **Wrong / undefined** (MAJOR 15) |
| n | Window narrower than 2 × margin; `elevation_min = 0` | Narrow window: no deadlock — `on` requires strictly inside, `off` requires 2° outside, so shading merely extends up to 2° past the configured window. `elevation_min = 0`: `sun_hits` stays true until elevation < −2°, i.e. ~10–15 min after sunset, so a cover can close after sunset if `sunny` has not yet debounced off. The real defect here is the missing azimuth wrap-around rule and the undefined initial value of the hysteresis after a restart. | **OK with caveats** (MINOR 19, MAJOR 5) |

---

## 4. Things that are well designed

- **The desired-state reconciler with a fixed layer stack plus a separate act gate** is the right decomposition: precedence is a total order that can be unit-tested exhaustively, and the "who may move now" question is cleanly separated from "what should the state be". The pure-`engine/` + `controller.py` split (§6) makes the whole of §1 table-testable, and the day-replay harness is exactly the right instrument for the flapping and latch questions.
- **Act gate ordering 3 → 4 (override before reopening mode)** is what makes feature D3 fall out for free (scenario d): a manual close while a door is open is respected without any special case.
- **Wind bypassing the min interval, overrides, doors and quiet hours, while frost outranks wind** matches decisions 6 and P4 exactly, and the "wind wanted open during frost → notification + repair" escape valve is the right way to surface an unresolvable conflict instead of guessing.
- **Simulation mode cannot self-trigger overrides**, because ownership is only claimed on a confirmed match and no command is sent — a direct, deliberate fix for the reference integration's §4.4 bug.
- **"A schedule rule firing clears `desired_at_manual_move` for every cover in the profile, even when the desired state does not change"** (§1.2, decision 10) is a genuinely subtle insight and correctly placed; it is the one mechanism that reliably breaks override deadlocks.
- **Event-driven with an explicit trigger inventory** (§2, last bullet) including midnight rollover, `EVENT_CORE_CONFIG_UPDATE` and a 5-minute fallback tick — a complete list, and a decisive improvement over the reference's 60 s polling.
- **Subentry-as-identity** (renaming a cover entity does not lose settings), `ConfigEntryNotReady`, versioned migrations, diagnostics, per-cover status entities and logbook attribution to the cover itself all directly address named pain points in the reference analysis.
- **Open/closed tolerance and the `partial`/`moving`/`unavailable` classification vocabulary** are the right primitive for X7; the remaining work is in the transition rules (MAJOR 11/12), not in the model.