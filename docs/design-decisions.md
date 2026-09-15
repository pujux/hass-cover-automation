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
