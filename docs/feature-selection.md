# Feature Selection — FINAL (2026-09-15)

## Base conditions
- No tilt covers.
- Covers react to sun, weather, and inside temperature.
- Wind / storm protection with thresholds per cover.
- Covers on doors stay open while the door is open, but a manual override is respected.
- **Covers are driven 0/100 only: fully closed or fully open. No intermediate positions.**
  Position-capable covers are still commanded with open/close, never with a percentage.

## Consequences of the 0/100 rule
- S3 / S4 (gradual or banded shading positions) are out.
- S5 collapses to "shade = closed, no-shade = open"; no per-cover positions.
- C1 "own position" for evening closure collapses to closed.
- X7 "minimum position delta" becomes an open/closed tolerance only: a cover reporting
  e.g. 97 % is treated as open, 3 % as closed, so drift is never mistaken for a manual move.
- Manual override detection = unexpected open↔closed transition (plus tolerance above).

## Chosen

### S. Sun
- [x] S1 Per-cover sun geometry: window azimuth, tolerance left/right, elevation min/max
- [x] S2 Hysteresis on angle boundaries + minimum time between moves
- [x] S5 Never open a cover the user closed (ownership rule; positions collapsed to 0/100)
- [x] S6 Shading mode select: off / auto / forced for sunlit windows / forced for all

### W. Weather
- [x] W1 Hot day from forecast daily max + min thresholds, latched per day, persisted
- [x] W2 Sunny from weather condition
- [x] W3 Sunny debounce: minimum sunny duration before closing, minimum cloudy duration before opening
- [x] W7 Escape hatch: external boolean override for sunny / hot

### T. Inside temperature
- [x] T1 Indoor temperature sensor per cover (or shared per room) with close/open thresholds and hysteresis
- [x] T2 Configurable combination rule per cover: indoor temp as extra condition / as replacement for forecast hot / as override that closes regardless of sun

### P. Protection
- [x] P1 Wind sensor (speed or gust) with per-cover upper/lower thresholds (hysteresis)
- [x] P2 Per-cover protection action: force open and lock, or hold position
- [x] P3 Minimum hold time after wind calms before releasing
- [x] P4 Protection overrides everything: manual override, schedules, door state
- [x] P6 Frost protection only: do not move covers while the outside temperature is below freezing

### D. Doors and manual interaction
- [x] D1 Door sensor per cover: never close while the door is open
- [x] D2 Door opens → actively reopen a cover the automation had closed
- [x] D3 Manual move while door is open is respected: no forced reopen, stays until override expires
- [x] D4 Manual override detection with tolerance, duration, extends on repeated manual moves, survives restart
- [x] D5 Per-cover override binary sensor + reset button
- [x] D6 Reopening mode: active / passive (only what automation closed) / off, ownership persisted
- [x] D7 Per-cover modes: auto / dark-only (may only ever close) / off; optional global vacation mode later

### C. Schedules (revised per decision 7)
- [x] C1' Schedule profiles: named lists of timed rules; each cover references one profile
- [x] C2' Rule = time (fixed, or sunrise/sunset ± offset) → desired state closed or open
- [x] C3' A rule holds until the next rule or until manual interaction; then shading logic governs
- [x] C4' Quiet-hours rule: no shading or schedule movements in a time range; protection still acts
- [ ] ~~C2 Keep closed overnight~~ dropped: manual open releases the hold

### X. Platform and transparency
- [x] X1 Event-driven engine: react instantly to cover, door, sensor, sun, weather changes; timer fallback
- [x] X2 Per-cover configuration via config subentries (edit one cover at a time)
- [x] X3 Per-cover enable switch
- [x] X4 Per-cover status sensor: current reason, next planned action, sun hitting, protection active
- [x] X5 Simulation mode without side effects
- [x] X6 Logbook entries with reason
- [x] X7 Open/closed tolerance (see consequences above)
- [x] X8 HA platform hygiene: diagnostics download, repairs, reconfigure flow, setup retry, versioned migrations
- [x] X9 Binary open/close-only covers supported (and all covers driven as such)
- [x] X12 Global diagnostic sensors: today's max/min as used, hot?, sunny?, wind protection state, next scheduled event
- [x] X13 Verbose logging switch

## Declined
S3, S4 (positions), W4 (lux sensor), W5 (hourly lookahead), W6 (outdoor temp sensor for heat),
T3 (absorbed by T2), T4 (temp trend), P5 (storm warning entity), P6 rain/hail parts,
P7 (manual lock modes), D8 (presence), C5 (pre-close), C6 (workday), C7 (calendar),
C9 (external times), X10 (stagger), X11 (read-back), X14 (aggregate entity), X15 (translations).

Reference for how the original does things: `docs/reference/smart-cover-automation-analysis.md`.
