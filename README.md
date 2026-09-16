# Cover Automation for Home Assistant

Drives roller covers fully open or fully closed from sun position, weather forecast, room
temperature, wind, frost, door sensors and per-cover schedule profiles. Event-driven, with
per-cover configuration through Home Assistant config subentries.

Minimum Home Assistant: 2026.8.

## Install

1. Add this repository to HACS as a custom repository (category: integration), or copy
   `custom_components/cover_automation` into your `config/custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Cover Automation**.

## Configure

1. **Hub**: pick your weather entity (must provide a daily forecast), optionally a wind sensor
   and an outdoor temperature sensor, then the thresholds.
2. **Schedule profiles** (optional): Add a *schedule profile* subentry with up to four rules
   (close at a time or relative to sunset, open once at a time or relative to sunrise) and
   optional quiet hours.
3. **Covers**: Add one *cover* subentry per cover: window azimuth and sun tolerances, shading
   rule, room temperature sensor, door sensor, wind thresholds and a schedule profile.

Every subentry can be edited on its own from the integration page. Behaviour, entities and
services arrive with the next release; this version installs and configures.

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/pyright
```

Design spec: `docs/superpowers/specs/2026-09-15-cover-automation-design.md`.
