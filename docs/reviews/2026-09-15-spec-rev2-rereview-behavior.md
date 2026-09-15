# Behavior re-review of spec revision 2

*Read-only. Behavioural lens on §1.0–1.6, §2, §5, against decisions 1–17.*

State tuple used below: **S = (owner, engine_target, manual_move_at, dam)**; `wind_active`/`enabled`/`mode` named only where they matter.

---

## 1. Summary verdict

Revision 2 is a large step forward: 4 of 4 blockers, 12 of 13 majors and 4 of 5 minors from the first review are fully or substantially resolved, and the structural fixes (act gate as an ordered `send`/`defer`/`suppress` pipeline, `engine_target` written at send time with an immediate Store flush, `dam` pinned to the last completed evaluation, one-shot open rules, shading only above the horizon, explicit override lifecycle) are the right ones. **§1/§2/§5 are close to implementable consistently, but not yet there.** One new blocker remains: the close-rule release predicate carries an `owner == user` conjunct, so the *first engine command after a manual release re-arms the hold* — which reinstates the original BLOCKER 1 for exactly the profile shape decision 7 was designed around (a single evening close rule, opened by hand in the morning). Four further new majors: `frost = unknown` now silently disables wind protection with no time limit; override end condition (e) depends on a fact (`which layer created the override`) that §5 does not persist, so the resurrection bug returns after any restart; §1.4 ("clear `dam` on every send") directly contradicts §1.5(c) ("door, wind, schedule"), and the §1.4 reading lets a routine shading close destroy an unrelated user override; and one-shot open rules with the min interval measured from shading commands only produce an open-then-immediately-close flap on hot mornings. All five are local fixes — none require rethinking the model.

---

## 2. Resolution of the original findings

| # | Original finding | Status in rev 2 | Resolving clause / residue |
|---|---|---|---|
| **B1** | Schedule hold permanently suppresses shading | **Partially resolved** | §1.2 layer 5 makes open rules one-shot (decision 13) — the `{open 07:00, close 21:30}` case is fixed. **Residue:** the close-hold release is `owner == user AND manual_move_at > T`, which un-releases on the next engine command → **new finding N1**. |
| **B2** | Circular "current desired" on a manual move | **Resolved** | §1.4: *"`dam` is set from the last completed evaluation"* with the explicit fallback chain; §5 reconcile: *"using the persisted `manual_move_at` for the release computation before any write"*. (One gap: the persisted **`owner`** should be named there too — see §5 of this report.) |
| **B3** | Override never ends / self-resurrects | **Partially resolved** | §1.0 (`override ⇔ dam ≠ null`, blocks only commands whose target == `dam`) + §1.5 (a)–(e) give a real lifecycle; §1.4 clears `dam` on send; ownership recovers automatically on match. **Residue:** (e) needs the originating layer, which §5 does not persist → **new finding N3**. |
| **B4** | In-flight command at restart becomes a manual move; 5 scalars insufficient | **Resolved** | §1.4 *"On send: write `engine_target = target` … flush the Store immediately"* + §5 reconcile branch 2 (`actual == engine_target` → engine keeps ownership). Store now carries 7 fields. (Branch 2's effect on `owner` is under-specified → **N6**.) |
| **M5** | Debounce/hysteresis seeding at startup/reload | **Mostly resolved** | §2: sunny *"Seeded at startup from the current condition as already settled"*; sun hits *"Seed at startup/reload with the strict test"*. **Residue:** the new 10-minute room-temperature dwell has no stated seeding rule. |
| **M6** | Reconcile destroys a live override | **Resolved** | §5 reconcile: *"if `owner` is already `user` → keep `manual_move_at` and `dam` unchanged (a live override survives a restart during frost or quiet hours)"*. |
| **M7** | Manual move under `leave_alone` gets no override; "frozen" vs predicate | **Resolved** | §1.4 fallback *"else if the new actual ∈ {open, closed} → `dam` = inverse of the new actual"*; the contradictory "frozen" prose is gone, replaced by §1.5(d) *"`leave_alone` pauses the dwell timer"*. |
| **M8** | `auto`'s unconditional `open` is the 24/7 ground state; reset opens at night | **Resolved** | §1.2 layer 6 *"only while sun elevation > 0"* (decision 14); §1.5(b) reset now lands on `leave_alone` at night. |
| **M9** | Reopening mode `active` is a no-op | **Resolved** | The dwell-based lifecycle means overrides actually end, so gate 6 `active` vs `passive` now diverges (see scenario b, 18:00). |
| **M10** | "Engine owns the current state" undefined | **Resolved** | §1.0: *"⇔ `owner == engine AND engine_target == actual`"*. |
| **M11** | `partial` has no resolution path | **Resolved** | §1.4 *"While actual is `partial` and an override is active, the engine waits; once the override ends the engine may command the cover"*; §4 adds a `partial` status value. (Definition of "contrary" still open — §5 of this report.) |
| **M12** | Raw vs classified transitions; pending lifecycle | **Mostly resolved** | §1.0 *"Only changes of the classified state are events … 100 → 97 is ignored"*; §1.4 match / contrary-≥10 s / confirm-window rules. **Residue:** "contrary" undefined; a late arrival at `engine_target` after `unconfirmed` is classified as a manual move. |
| **M13** | Door/frost unavailable undefined (safety) | **Resolved, with a new hazard** | §1.2 layer 3 (door unavailable → `leave_alone` + repair) and layer 1 (`frost = unknown` → `leave_alone`). The frost half introduces **N2**. |
| **M14** | Quiet hours: wind release, rules inside quiet hours | **Resolved** | §1.2 layer 2 *"that single restoring move is exempt from quiet hours"*; layer 5 *"rejected at config time … skipped that day and raises a repair issue"*. |
| **M15** | `hot_day` retraction / undefined after midnight | **Resolved** | §2: *"the **flag** `hot_day` latches true until local midnight once true"*; *"`unknown` until the first successful fetch of the new local day"*; failed fetch keeps previous values. |
| **M16** | Act gate mixes block/bypass; scopes undefined | **Resolved** | §1.3 rewritten as an ordered `send`/`defer`/`suppress` pipeline; gate 10 simulation *"Applies to every layer, wind included"*; gate 8 *"since the last **shading-layer** command"*; gate 7 handles `moving`. |
| **M17** | `protection_only` undefined beyond shading | **Resolved** | §1.1 mode table (quiet hours, schedule, shading all "–" for `protection_only`). |
| **m18** | `next_planned_action`, status precedence, `since`, "frozen" | **Resolved** | §4 defines `next_planned_action` and lists status values *"in precedence order"*; §2 *"`wind_active` frozen at its last value"*. |
| **m19** | Sun geometry: wrap-around, horizon, limbo seeding | **Resolved** | §2 `signed_diff = (sun_az − cover_az + 180) mod 360 − 180`; *"the elevation lower bound never releases below the horizon"*; seeding stated. |
| **m20** | Rule/mode combinations that silently do nothing | **Mostly resolved** | §1.2 layer 6 (`room_only` *"rejected at config time otherwise"*, forced modes *"bypass the comfort floor by design"*), §2 (`room_only` + dead sensor → repair). **Open:** hub `shading_mode = off` still leaves shaded covers closed with no release path; `forced_all` + `dark_only` (permanently closed) is still undocumented. |
| **m21** | Timing/flapping/lifecycle details | **Mostly resolved** | §2 room *"10-minute dwell"*; §5 backoff *"applies to all layers except wind"*; §5 *"`async_at_started` (fires immediately when HA is already running, so reloads work)"*; decision 17 moves runtime state into the Store; per-cover `confirm_window`; units converted at read time; two close rules documented. **Open:** simulation fires a logbook event on every evaluation (no dedup, no interval bookkeeping). |
| **m22** | Door vs pre-existing override | **Resolved** | §1.3 gate 4 + decision 15. |

---

## 3. Scenario walk-throughs against revision 2

| # | Scenario | Outcome per revision 2 | Verdict |
|---|---|---|---|
| **a** | Open against shading 14:00; cloud 14:30–14:50; sun leaves 17:00; sun returns next day 10:00 | 13:00 S=(engine, closed, –, null). 14:00 manual open → S=(user, closed, 14:00, **closed**); gate 5 blocks every `closed` command. 14:50 sunny off → desired=open ≠ dam → dwell starts; 15:00 sunny on → desired=closed → **dwell resets**, override survives — cloud length no longer matters. 17:00 `sun_hits` false → §1.5(e) ends the override (dam=null); desired=open==actual, no move. Next day 10:00: desired=closed, dam=null → gate 5 passes, gate 6 not applicable to closes → **engine shades the cover**; match → owner=engine; 17:00 gate 6 `passive` → engine owns → reopens. Identical in `active`. | **OK** (but see N3 if a restart intervenes) |
| **b** | Close 14:00 vs desired open; sun 15:00–18:00; rule `close 21:30`; no open rule; user opens 07:30 | 14:00 → S=(user, open, 14:00, **open**). 15:00 desired=closed ≠ dam → dwell → 15:30 override ends. 18:00 desired=open, actual=closed → gate 5 passes → gate 6 `passive`: owner==user → **suppress** ✔ (S5); in `active` → the cover opens at 18:00 (per decision 12, but worth knowing). 21:30 rule → dam=null, hold closed, no move. 07:30 manual open → dam = **closed** (last completed evaluation = the hold; B2's pinning works), hold released. 07:30–08:00 desired=open ≠ dam → dwell → override ends. 11:00 shading closes → match → **owner = engine → the release predicate goes false → the 21:30 hold is active again** → 18:00 shading wants open but layer 5 holds `closed` → **the cover never reopens; it stays shut until the user opens it by hand the next morning.** | **Wrong** (N1) |
| **c** | Wind opens under a `closed` hold; releases 23:00 | 21:30 engine closes, S=(engine, closed, –, null). 22:00 wind → gate 3 send → opens, `wind_active=true`. 23:00 release → §1.2 layer 2 restoring move **exempt from quiet hours** → layers below: the hold is intact (owner==engine, no manual move) → closes at 23:00. Quiet hours no longer strand the cover open all night, and the 07:00 surprise close is gone. | **OK** (noise at 23:00 is the accepted trade) |
| **d** | Door opens under a `closed` hold; user closes while the door is open; door closes | 22:00 door → gate 4 (no override) → send open. 22:05 manual close → S=(user, open, 22:05, **open**), hold released. 22:30 door closes → night → shading silent (decision 14) → desired=`leave_alone` → cover stays closed ✔; dwell paused → the override survives the night ✔. Reset at 23:00 → desired still `leave_alone` → **no night-time opening** ✔. Next morning desired=open==dam → gate 5 keeps it closed; passive also blocks (owner==user). | **OK** |
| **e** | Frost + closed cover + wind > upper; frost releases; wind then drops | Layer 1 → `leave_alone`; gate 2 suppress + notification/repair *"once per episode"* ✔. Frost releases → gate 3 → opens immediately ✔. Wind release → restoring move ✔. Unchanged and correct — **unless the frost source itself goes `unknown`**, in which case protection is suppressed indefinitely (N2). | **OK** (N2 is the adjacent hazard) |
| **f** | (1) Restart with an override active (2) restart with a command in flight | (1) Reconcile branch 3 *"if `owner` is already `user` → keep `manual_move_at` and `dam` unchanged"* → the override survives a restart during frost/quiet hours ✔. **But** the dwell timer restarts (stated, acceptable) and end condition (e) is lost because the originating layer is not persisted → N3. (2) `engine_target` was written and flushed at send → `actual == engine_target` → branch 2 → ownership retained ✔ **B4 resolved**. Branch 2's effect on `owner`/`dam` when `owner == user` is unstated → N6. | **OK with residue** (N3, N6) |
| **g** | Single `open at sunrise+30` rule, no close rule | Decision 13: the rule *"yields `open` until the cover's actual state is `open`"* → one-shot → afterwards *"the layers below govern"* → **shading runs normally all day**. The hold now means "open it once". | **OK — fully resolved** |
| **h** | `close 20:00` + `close 22:00` | 20:00 hold; a manual open at 20:30 releases it; 22:00 the second rule fires, clears `dam` on every cover of the profile, re-closes. §1.2 documents it as *"a re-close (documented escape hatch)"*. | **OK (documented)** |
| **i** | Quiet hours 22:00–07:00 with a rule at 23:00 | Rejected at config time; a sun-relative rule that drifts in is *"skipped that day"* + repair issue. No more invisible 07:00 catch-up. Residual: `close at sunset+30` with quiet hours from 22:00 is simply skipped every midsummer night rather than clamped (see §5 of this report). | **OK** |
| **j** | `forced_all` + `dark_only`; + `protection_only` | `protection_only` now skips quiet hours, schedule **and** shading per the §1.1 table → `forced_all` provably cannot touch it ✔. `dark_only` + `forced_all` still closes at sunrise and never reopens (per D7) — correct but undocumented. Note the mode table gives `dark_only` the **schedule** layer, so an `open` rule *will* open a "may only ever close" cover. | **OK, two doc traps** |
| **k** | Stop at 50 % during an engine close; engine wants closed | If `partial` counts as *"a settled classified state contrary to the target"*: manual move at +10 s → dam=`closed` → gate 5 suppresses; §1.4 *"the engine waits; once the override ends … the engine may command the cover"*; status `partial`. Defined and sane. If `partial` is **not** "contrary", the pending record runs to the 120 s window → `unconfirmed` + backoff → the engine re-sends `close` and overrides the user's stop. Both readings are available. | **OK if "contrary" is defined** |
| **l** | Min interval (shading only) × debounce | `sunny` 10/20 min, `sun_hits` hysteresis, `hot_day` latched, and now `room_hot/room_cold` with a **10-minute dwell** → no realistic flap path from the signals; gate 8's clock is pinned to *"the last shading-layer command"*. **New flap instead comes from the schedule boundary**: a satisfied open rule hands straight to shading with the shading clock untouched → open then close within seconds (N5). | **Surprising** (N5) |
| **m** | `hot_day` latch across midnight before the new forecast | Flag latches true until local midnight; `unknown` until the first successful fetch of the new day; `unknown` → shading `leave_alone`. Because shading also requires elevation > 0, the unknown window is harmless in practice. Failed/empty fetch keeps the previous values + repair. | **OK — fully resolved** |
| **n** | Narrow azimuth window; `elevation_min = 0` | Wrap-around formula specified; *"the elevation lower bound never releases below the horizon"* removes the post-sunset shading window; seeding specified; and the margin is explicitly *"a release margin, not chatter protection"*. A window narrower than 2× margin still shades up to 2° past its edges — now an understood consequence. | **OK** |

---

## 4. New findings (regressions and newly introduced gaps)

### N1 — BLOCKER — The close-rule release is not sticky: the next engine command re-arms the hold

**Section:** §1.2 layer 5 (*"or until released by a manual move (`owner == user AND manual_move_at > T`)"*).

**Problem.** The release is a predicate over `owner`, but `owner` reverts to `engine` on the very next confirmed engine command (§1.4 match). The release therefore evaporates the first time any layer moves the cover, and the hold — which for a close rule can span a full 24 h — comes back and outranks shading (layer 5 > 6) for the rest of the day.

**Evidence (scenario b).** Profile `{close 21:30}`.
| t | event | S | layer 5 |
|---|---|---|---|
| 21:30 | engine closes | (engine, closed, –, null) | hold `closed` |
| 07:30 | user opens | (user, closed, **07:30**, closed) | **released** (owner==user, 07:30 > 21:30) |
| 08:00 | dwell ends override | (user, closed, 07:30, null) | released |
| 11:00 | shading closes, match | (**engine**, closed, 07:30, null) | **hold `closed` again** |
| 18:00 | shading wants `open` | – | layer 5 wins → **cover stays closed until the user opens it tomorrow** |

This reinstates the core of BLOCKER 1 for the exact usage decision 7 describes ("no keep-closed re-closing; a manual open releases the hold").

**Fix.** Drop the `owner` conjunct — `manual_move_at` is written only by manual moves and never cleared, so it is already a monotone witness:

> A close rule that fired at T holds `closed` until the next rule of the profile fires or until `manual_move_at > T`.

(If reset should also re-arm the hold, say so explicitly; today §1.5(b) leaves `manual_move_at` untouched, which keeps the release in force — that is the right default.)

### N2 — MAJOR — `frost = unknown` disables wind protection indefinitely

**Section:** §1.2 layer 1, §1.3 gate 2, §2 (frost).

**Problem.** Decisions 6 and 16 say *frost* wins over wind and door. Revision 2 extends that to `frost = unknown`, and §2 makes `unknown` reachable after just `weather_grace` (30 min) of an unavailable source — the **default** source being the weather entity's `temperature` attribute. There is no upper bound on how long `unknown` lasts and no fallback to the last known value.

**Evidence.** July, weather entity unavailable for 40 min (a common cloud-API hiccup) → `frost = unknown` → layer 1 `leave_alone`, gate 2 suppress → a gust above `wind_upper` produces a repair issue and a notification instead of opening the awning. The failure mode is "fail to open during a storm", which P1/P2 exist to prevent, and it is triggered by an input that has nothing to do with frost.

**Fix.** Split the unknown case from the active case:

> `frost = unknown` yields `leave_alone` for the shading, schedule and door layers, but does **not** suppress the wind layer unless the last known outdoor temperature (before the source became unavailable) was ≤ `frost_threshold + 1 K`. If no value was ever seen, wind protection proceeds and the repair issue records that frost could not be verified.

### N3 — MAJOR — Override end condition (e) depends on a fact that is not persisted

**Section:** §1.5(e) (*"for an override created while the shading layer was the winning layer"*), §5 Store.

**Problem.** The Store holds `owner, engine_target, manual_move_at, dam, wind_active, enabled, mode` — not the layer that created the override. After a restart the engine cannot evaluate (e), so a shading-originated override can only end via (a)–(d). Condition (d)'s dwell is *paused* whenever desired is `leave_alone`, and after sunset the shading layer is silent (decision 14) → desired is `leave_alone` all night → the dwell never completes.

**Evidence.** West-facing cover whose azimuth window stays satisfied until the sun drops below the horizon. 14:00 user opens against shading → dam=`closed`. Restart at 15:00 (reconcile branch 3/`owner == user` keeps the override ✔, but the originating layer is gone). Sunset: `sun_hits` goes false — (e) would have ended the override, but it is unavailable; desired becomes `leave_alone` → dwell paused → **the override survives the night**. Next morning desired returns to `closed == dam` → gate 5 suppresses → **the cover is never shaded again** until a schedule rule, a reset, or an unrelated engine command. This is the revision-1 resurrection bug, reachable through any restart.

**Fix.** Add an eighth per-cover scalar `dam_layer` (or a boolean `dam_from_shading`) to the Store and to §1.0's state list; it is written with `dam` in §1.4 and cleared with it in §1.5. Alternatively, make (d) run its dwell while desired is `leave_alone` *because the shading layer is out of season* — but the extra field is cheaper and matches the existing pattern.

### N4 — MAJOR — "Clear `dam` on send" (§1.4) contradicts §1.5(c), and the §1.4 reading lets routine shading moves destroy a user override

**Section:** §1.4 (*"clear `dam` (the engine has taken control)"*) vs §1.5(c) (*"the engine sends a command to the cover (door, wind, schedule)"*).

**Problem.** Two clauses, two scopes: every send, or only door/wind/schedule sends. The difference is user-visible.

**Evidence (shading case, only §1.4 applies).** Bedroom, no profile. 23:00 the user closes the cover; last evaluation said `leave_alone` (night) → dam = inverse(closed) = `open` → the override protects against opening. 13:00 next day shading wants `closed` → target ≠ dam → gate 5 passes → **send → §1.4 clears `dam`** and match sets owner=engine, engine_target=closed. 17:00 shading wants `open` → dam=null, gate 6 `passive` → the engine owns the current state → **the cover opens at 17:00**, undoing a manual close the user never revisited. Under §1.5(c)'s scope the override survives, target `open == dam` and gate 5 suppresses — the better behaviour, and the one S5 implies.

**Evidence (protection case).** User closes a cover at 14:00 (dam=`open`); a 10-minute gust at 15:00 opens it via gate 3 → dam cleared permanently. "Wind ignores overrides" (decision 5) is about the episode; destroying the override outlives it.

**Fix.** Make §1.4 defer to §1.5: *"On send: write `engine_target`, create the pending record, flush the Store. `dam` is cleared only per §1.5(c), i.e. for schedule-layer commands (schedules are authoritative) — wind and door commands **suspend** the override for the duration of the episode and leave `dam` intact; shading commands never clear it."* Also state explicitly that **simulation (gate 10) never clears `dam`**, or simulation acquires the exact side effect the reference integration was criticised for.

### N5 — MAJOR — A satisfied open rule hands straight to shading with no interval damping → morning open-then-close flap

**Section:** §1.2 layer 5 (open rules, decision 13) × §1.3 gate 8 (*"minimum interval since the last shading-layer command"*).

**Problem.** An open rule releases as soon as actual is `open`; the next evaluation consults shading, whose min-interval clock was never touched by the schedule command. On a hot morning with the sun already on the window, the shading layer immediately wants `closed`.

**Evidence.** East bedroom, `open at sunrise+30` (07:00), hot day, `sun_hits` true at 07:00. 07:00 rule fires → gate 6 exempts schedule opens → cover opens; 07:00:30 rule satisfied → shading: `want_shade` true → gate 8 sees no prior shading command → **cover closes again ~30 s later**. Every hot morning, two moves and a visibly pointless cycle.

**Fix.** Either (i) *"gate 8's clock is set by every engine command on the cover, not only shading commands"* (one sentence, also removes the last unstated interaction between layers), or (ii) *"a satisfied open rule suppresses shading-layer `closed` for that cover until `min_move_interval` has elapsed since the rule fired"*. Option (i) is simpler and also damps the wind-release → shading sequence.

### N6 — MAJOR — Reconcile branch 2 does not say what happens to `owner` and `dam`; read as "owner := engine" it breaks S5 across restarts

**Section:** §5 reconcile (*"`actual == engine_target` → engine keeps ownership"*).

**Problem.** The branch is silent on the case `owner == user AND actual == engine_target`, which is reachable without any downtime: the engine closes at 13:00; the user opens at 14:00 (`engine_target` stays `closed`); the user closes again at 15:00 → S=(user, closed, 15:00, closed) with `actual == engine_target`. Read as "set `owner = engine`", a restart silently converts a **user-closed** cover into an engine-owned one.

**Evidence.** Without a restart, 17:00 → gate 6 `passive`: owner==user → suppress → the cover stays closed (S5 ✔). With a restart at 16:00 → branch 2 → owner=engine, engine_target==actual → *engine owns the current state* → **the cover the user closed is reopened at 17:00**. Behaviour that depends on whether HA restarted is exactly the class of bug D4/S5 were written against. The branch is also silent on the stale `dam` it leaves behind.

**Fix.** Two lines: (1) *"Set `owner = engine` at **send** time (§1.4), not at match time"* — this makes `owner == engine ∧ engine_target ≠ actual` the honest representation of a pending command that §5 already claims, and covers a send issued while `owner == user`. (2) *"Reconcile branch 2 confirms that no reconciliation is needed and modifies nothing; in particular it never changes `owner`, `manual_move_at` or `dam`."*

---

## 5. Still ambiguous — an implementer would have to guess

1. **"a settled classified state contrary to the target"** (§1.4) — does `partial` count as contrary, or only the opposite end state? Scenario (k) resolves to "user's stop is respected" under the first reading and "engine re-sends `close` after the confirm window" under the second. Define: *"contrary = any classified state ∈ {open, closed, partial} that is not the target"*.
2. **"An open rule yields `open` until the cover's actual state is `open`"** (§1.2 layer 5) — a *state test re-evaluated each pass* (correct) or a *transition test*? Under the transition reading, a cover that is already open when the rule fires is never satisfied and holds `open` all day, re-creating BLOCKER 1 for the commonest profile. Add: *"evaluated as a state test, including in the evaluation in which the rule fires; a rule whose action already matches the actual state is satisfied immediately"*.
3. **The "restoring move" exemption** (§1.2 layer 2) — how long does it last, and what if the move is deferred by gate 7/8/9 or the layers below change their mind before it is sent? State a bound: *"the exemption applies to the first command sent within N minutes of the wind release; it lapses if the layers below no longer disagree with actual"*.
4. **Reconcile branch 3 pins `manual_move_at` "before any write" but not `owner`** — layer 5's release predicate reads both. Say *"the first evaluation's desired state is computed with the persisted `owner` and `manual_move_at`"* (this becomes moot if N1's fix drops the `owner` conjunct).
5. **Late arrival at `engine_target` after `unconfirmed`** (§1.4 *"Without a pending record: any classified state change is a manual move"*) — a cover slower than `confirm_window` lands on the engine's target and is recorded as a manual move, which costs it passive reopening for the rest of the day. Add: *"a classified state change to `engine_target` while `owner == engine` is a match, even without a pending record"*.
6. **Room-temperature seeding** (§2) — `sunny` and `sun_hits` have explicit seeding rules; the new 10-minute room dwell does not. If `room_cold` seeds to false, the comfort floor is disabled for the first 10 minutes after every restart or reload.
7. **Simulation output volume** (§1.3 gate 10) — `desired ≠ actual` persists forever in simulation, no pending record is created and no interval bookkeeping is recorded, so the logbook event fires on every evaluation (5-minute tick plus every `sun.sun` update). Add a dedup: *"a simulated command is logged only when (layer, target) changes"*.
8. **Sun-relative rules drifting into quiet hours are skipped** (§1.2 layer 5) — `close at sunset+30` with quiet hours from 22:00 is skipped every midsummer night, leaving the cover open, plus a daily repair issue. The rule schema already has `latest`; prefer *"clamped to one minute before quiet hours start; skipped only if the clamp is impossible"*.
9. **Mode table vs mode names** — `dark_only` has the schedule layer enabled, so an `open` rule will open a "may only ever close" cover; `forced_all` + `dark_only` closes at sunrise and never reopens; hub `shading_mode = off` leaves already-shaded covers closed with no release path. All three are defensible, none is documented; one sentence each in §1.1/§1.2 would prevent the bug reports.
10. **Layer 1's "evaluated for notification purposes only"** (§1.2 layer 1 ↔ §1.3 gate 2) — the act gate is otherwise defined over the *winning* layer's desired state; here it must inspect a non-winning layer's opinion. Say explicitly that an evaluation computes the wind and door opinions unconditionally and carries them alongside the winning desired state.