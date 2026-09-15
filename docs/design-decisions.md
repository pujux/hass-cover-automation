# Design Decisions Log

Running log of decisions taken during brainstorming (2026-09-15). The spec will
consolidate these; until then this file is the source of truth.

| # | Topic | Decision |
|---|-------|----------|
| 1 | Feature set | See `docs/feature-selection.md` (final). |
| 2 | Cover positions | 0/100 only. Covers are commanded with open/close, never a percentage. |
| 3 | Cover hardware | Two-way: remotes and wall switches report state back to HA. Manual detection = any open/closed transition not commanded by the engine. |
| 4 | Shading rule (default) | **Option A:** close when sun hits AND weather sunny AND forecast hot day; never close while room below comfort floor (e.g. 21 °C); close regardless of forecast when room above ceiling (e.g. 25 °C). Per-cover rule override remains possible (T2). |
| 5 | Manual override end | **Option B, no timer.** Override = cover state differs from engine's desired state because of a manual move. It ends when the engine's *desired state* for that cover changes (sun leaves/arrives after debounce, room crosses threshold, schedule event). Raw input flicker does not end it. Reset button ends it early. Wind protection ignores overrides. |
| 6 | Frost vs wind | **Frost wins.** Below 0 °C outside no cover moves, even if its wind threshold is crossed. Engine raises a persistent notification + repair issue instead. |
| 7 | Schedules | No fixed evening-closure/morning-opening pair. A **schedule profile** is a list of timed rules (fixed time or sunrise/sunset ± offset → desired state closed/open, or quiet-hours range). Each cover references one profile. **Option C:** a rule holds until the next rule in the profile or until manual interaction, whichever comes first; a released cover is governed by shading logic. Consequence: no "keep closed" re-closing. Quiet hours block shading + schedule moves, never protection. Schedule moves are blocked only by protection, frost and door state. |
| 8 | Architecture | **Approach 1: desired-state reconciler.** Pure per-cover function computes desired state from a fixed layer stack; event-driven controller recomputes on relevant state changes + timers; acts only when desired ≠ actual and no gate blocks. One config entry (house-wide inputs) + subentries per cover and per schedule profile. |
| 9 | Domain name | `cover_automation`. |
| 10 | Override vs schedule | A schedule rule firing clears manual overrides on all covers of its profile, even when the desired *state* does not change (only the reason). Reset button/service hands ownership back to the engine. Min move interval applies to shading-layer moves only. Per-cover persisted state reduced to five scalars (see spec §5). |
| 11 | Minimum HA version | **2026.8** (device registry: one config entry + at most one subentry per device, `via_device_id` API). Nothing above 2026.8 is required. Integration-provided triggers/conditions (2026.7) deferred until HA declares the API stable. |
| 12 | Override end (refined) | Explicit lifecycle. An override (`desired_at_manual_move ≠ null`) ends when: a schedule rule of the cover's profile fires; reset is invoked; the engine's desired state has differed from the overridden value continuously for the override dwell (default 30 min; `leave_alone` pauses the dwell); for overrides created against a shading opinion, additionally when `sun_hits` becomes false; or when the engine itself commands the cover (door/wind/schedule). |
| 13 | Schedule rule kinds | `close` rules hold (until next rule or manual release). `open` rules are one-shot: they open the cover once, then release it to the layers below. |
| 14 | Shading in daylight only | The shading layer has an opinion only while sun elevation > 0. Nothing opens at night for shading reasons, including after a reset. |
| 15 | Door vs earlier manual close | A door-open request bypasses an override whose manual move predates the door's last opening. Manual moves made while the door is open are respected. |
| 16 | Frost vs door | Frost wins: door-open request cannot move a cover while frost is active; notification + repair issue, as for wind. |
| 17 | Runtime state storage | Per-cover `enabled` and `mode` and the hub selects/switches live in the Store (engine owns state; entities are views), not RestoreEntity. |
| 18 | Which sends clear an override | Only **schedule**-layer sends clear `dam` (schedules are authoritative). Wind and door sends bypass the override for the episode but leave it intact; shading sends never clear it. Refines decision 12(c). |
| 19 | Frost unknown vs wind | `frost = unknown` (source unavailable beyond grace) blocks door, schedule and shading moves but not wind protection, unless the last known outdoor temperature was ≤ threshold + 1 K. Refines decision 16 for the unknown case. |
| 20 | Ownership at send time | `owner = engine` is written when a command is **sent**, not when it is confirmed. Reconcile with `actual == engine_target` changes nothing. |
| 21 | Min interval clock | The minimum interval blocks only shading-layer moves, but its clock is updated by every engine command on the cover (prevents open-rule → shading and wind-release → shading flaps). |
| 22 | Close-rule release | A close rule's hold is released by `manual_move_at > fire time` alone (no `owner` conjunct), so the release is sticky until the next rule. |
| 23 | Sun-relative rules in quiet hours | Clamped to the quiet-hours boundary (close → 1 min before start, open → end) instead of skipped; skipped with a repair issue only if no valid clamp exists. |
| 24 | Open rules are satisfied once | An open rule yields `open` only while the cover has not been observed open since it fired, for at most 15 minutes, until the next rule or a manual release. A satisfied rule never re-asserts itself after a later shading close (found while writing the engine plan: the earlier state-test wording re-created blocker 1). |
