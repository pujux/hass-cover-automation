# HA Binding Part A (foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cover_automation` a loadable, UI-configurable Home Assistant integration: manifest and HACS metadata, hub config flow with options and reconfigure, cover and schedule-profile subentry flows, a Store for the engine's persisted state, config-to-engine mapping, devices per cover, and English translations. No entities or automation behaviour yet (that is plan 2b).

**Architecture:** The engine package from plan 1 is untouched. This plan adds the Home Assistant shell around it: `const.py` (keys/defaults), `config_flow.py` (hub flow + two `ConfigSubentryFlow` handlers), `config_map.py` (subentry/option dicts → `CoverConfig`, `schedule.Profile`, `HubConfig`), `store.py` (one `Store` per hub entry holding `CoverPersisted` per cover, the `DailyLatch`, and hub runtime selects), and `__init__.py` (setup order from spec §5: validate → devices → Store → platforms (none yet) → update listener). Everything follows the 2026.8 rules the platform review verified: one update listener that schedules reloads, flows ending with `async_update_and_abort`, devices created explicitly with `via_device_id`.

**Tech Stack:** Python 3.14, Home Assistant 2026.8.3 via `pytest-homeassistant-custom-component==0.13.357`, voluptuous, pytest (asyncio auto mode), ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-09-15-cover-automation-design.md` (revision 3.2) §3, §4 (devices/unique ids only), §5 (setup order, Store, config changes), §6 (layout, tooling). Decision log `docs/design-decisions.md`. Platform facts verified against the 2026.8.3 source: `docs/reviews/2026-09-15-spec-review-platform.md`, `docs/reviews/2026-09-15-spec-review-platform-delta-2026.8.md`, `docs/reviews/2026-09-15-spec-rev2-rereview-platform.md`.

## Global Constraints

- Minimum Home Assistant **2026.8** (`hacs.json` `"homeassistant": "2026.8.0"`); tests pin `homeassistant==2026.8.3` through `pytest-homeassistant-custom-component==0.13.357`; Python ≥ 3.14.2.
- `custom_components/cover_automation/engine/` keeps zero Home Assistant imports; pyright strict on it stays green. Everything new in this plan lives outside `engine/`.
- Reload rule (spec §3): exactly one update listener per entry calling `hass.config_entries.async_schedule_reload`; every hub/subentry flow ends with `async_update_and_abort`/`async_create_entry`; never `async_update_reload_and_abort` or `OptionsFlowWithReload`.
- Devices (spec §3, 2026.8): hub device `identifiers={(DOMAIN, entry.entry_id)}`, `config_subentry_id=None`, `entry_type=SERVICE`; one device per cover subentry `identifiers={(DOMAIN, subentry_id)}`, `config_subentry_id=subentry_id`, `via_device_id=<hub device id>`; never `via_device`; lookups always `config_entry_id`-scoped.
- Unique ids: hub entities `f"{entry_id}_{key}"`, per-cover `f"{subentry_id}_{key}"` (used by plan 2b; the Store keys covers by `subentry_id`).
- Spec §3 defaults (verbatim): frost threshold 0 °C; sunny conditions `sunny`, `partlycloudy`; sunny on-delay 10 min, off-delay 20 min; weather grace 30 min; hot high 24 °C, hot low 13 °C (enabled); sun release margin 2°; open/closed tolerance 5 %; override dwell 30 min; cover: tolerance left/right 60°, elevation 0–90°, shading rule `forecast_with_room`, comfort floor 21 °C < ceiling 25 °C, wind hold 15 min, wind action `open`, min move interval 10 min, confirm window 120 s; profile: up to 4 rules in the UI, optional quiet hours.
- Validation (spec §3): weather entity must support `WeatherEntityFeature.FORECAST_DAILY`; cover must support OPEN+CLOSE or SET_POSITION when it currently reports a state (otherwise accepted, re-checked at setup); `room_only` requires a room sensor; `comfort_floor < comfort_ceiling`; `wind_lower < wind_upper` when wind is enabled; `confirm_window ≥ 10 s`; profile rules validated with `engine.schedule.validate`.
- Thresholds are stored with the unit they were entered in (`temperature_unit` from `hass.config.units.temperature_unit`, `wind_unit` from the wind sensor's `unit_of_measurement`); conversion happens at read time in plan 2b.
- Manifest keys (spec §6): `domain`, `name`, `codeowners`, `config_flow: true`, `dependencies: ["sun", "weather", "logbook"]`, `documentation`, `issue_tracker`, `iot_class: "calculated"`, `integration_type: "hub"`, `single_config_entry: true`, `version`. No `strings.json`; only `translations/en.json`.
- Commit after every task; messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File structure

```
pyproject.toml                                  asyncio_mode auto; pyright venv + include widened
requirements_test.txt                           pytest-homeassistant-custom-component pin
hacs.json                                       HACS metadata (min HA 2026.8.0)
.github/workflows/engine.yml                    renamed job set: lint+types+tests, hassfest, HACS
custom_components/cover_automation/manifest.json
custom_components/cover_automation/const.py     DOMAIN, subentry types, CONF_* keys, defaults, weather conditions
custom_components/cover_automation/config_map.py  dict → HubConfig / CoverConfig+CoverBindings / Profile
custom_components/cover_automation/store.py     StoreData + CoverAutomationStore (Store v1.1)
custom_components/cover_automation/config_flow.py hub ConfigFlow (+options, reconfigure), CoverSubentryFlow, ProfileSubentryFlow
custom_components/cover_automation/__init__.py  setup/unload/remove/migrate, devices, update listener, runtime_data
custom_components/cover_automation/translations/en.json
tests/conftest.py                               enable custom integrations
tests/ha/__init__.py
tests/ha/conftest.py                            hass state helpers (weather, sun, cover, sensors), MockConfigEntry builders
tests/ha/test_const_manifest.py
tests/ha/test_config_map.py
tests/ha/test_store.py
tests/ha/test_config_flow.py
tests/ha/test_subentry_flows.py
tests/ha/test_init.py
tests/ha/test_translations.py
```

---

### Task 0: Home Assistant test tooling

**Files:**
- Modify: `pyproject.toml`, `requirements_test.txt`, `.github/workflows/engine.yml`
- Create: `hacs.json`, `tests/conftest.py`, `tests/ha/__init__.py`

**Interfaces:**
- Produces: a venv where `import homeassistant` works (2026.8.3), pytest in asyncio auto mode, pyright resolving HA types, CI running hassfest and HACS validation.

- [ ] **Step 1: Update the tooling files**

`requirements_test.txt` (replace the pytest pin; the plugin pins pytest, pytest-asyncio, pytest-freezer, pytest-socket and `homeassistant==2026.8.3`):
```
pytest-homeassistant-custom-component==0.13.357
ruff==0.13.0
pyright==1.1.405
```

`pyproject.toml` — replace the `[tool.pytest.ini_options]` and `[tool.pyright]` tables:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
asyncio_mode = "auto"

[tool.pyright]
venvPath = "."
venv = ".venv"
include = ["custom_components/cover_automation"]
strict = ["custom_components/cover_automation/engine"]
typeCheckingMode = "standard"
pythonVersion = "3.14"
reportMissingImports = true
```

`hacs.json`:
```json
{
  "name": "Cover Automation",
  "homeassistant": "2026.8.0",
  "hacs": "2.0.5",
  "render_readme": true
}
```

`.github/workflows/ci.yml` (run `git mv .github/workflows/engine.yml .github/workflows/ci.yml` first, then replace its content with):
```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - run: python -m pip install -r requirements_test.txt
      - run: ruff check .
      - run: ruff format --check .
      - run: pyright
      - run: pytest
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

`tests/conftest.py`:
```python
"""Test session configuration: enable loading custom integrations."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable the custom_components/ directory for every test."""
    return
```

Empty file: `tests/ha/__init__.py`.

- [ ] **Step 2: Install and verify**

```bash
cd /Users/julian/Projects/hass-cover-automation
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -c "import homeassistant.const as c; print(c.__version__)"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pyright
```
Expected: version prints `2026.8.3`; all 159 engine tests pass under asyncio auto mode; ruff clean; pyright 0 errors (the engine is the only code so far). If pip resolves a different homeassistant version, stop and report — the pin is a global constraint.

- [ ] **Step 3: Commit**

```bash
git add -A pyproject.toml requirements_test.txt hacs.json .github tests/conftest.py tests/ha/__init__.py
git commit -m "chore: Home Assistant test tooling, HACS metadata and CI jobs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Constants and manifest

**Files:**
- Create: `custom_components/cover_automation/const.py`, `custom_components/cover_automation/manifest.json`
- Test: `tests/ha/test_const_manifest.py`

**Interfaces:**
- Produces: `DOMAIN`, `SUBENTRY_COVER`, `SUBENTRY_PROFILE`, `PROFILE_NONE`, all `CONF_*` keys, `DEFAULT_*` values, `WEATHER_CONDITIONS`, `STORAGE_VERSION`, `STORAGE_MINOR_VERSION`, `storage_key(entry_id)`; the manifest recognised by HA's loader.

- [ ] **Step 1: Write the failing test**

`tests/ha/test_const_manifest.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.cover_automation import const

MANIFEST = Path("custom_components/cover_automation/manifest.json")


async def test_integration_is_discoverable(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, const.DOMAIN)
    assert integration.domain == "cover_automation"
    assert integration.config_flow is True
    assert integration.single_config_entry is True
    assert set(integration.dependencies) == {"sun", "weather", "logbook"}
    assert integration.iot_class == "calculated"
    assert integration.integration_type == "hub"


def test_manifest_keys() -> None:
    data = json.loads(MANIFEST.read_text())
    assert list(data) == sorted(data), "hassfest requires alphabetically sorted keys"
    assert data["domain"] == const.DOMAIN
    assert data["version"] == "0.1.0"


def test_defaults_match_spec() -> None:
    assert const.DEFAULT_FROST_THRESHOLD == 0.0
    assert const.DEFAULT_SUNNY_CONDITIONS == ["sunny", "partlycloudy"]
    assert (const.DEFAULT_SUNNY_ON_DELAY_MIN, const.DEFAULT_SUNNY_OFF_DELAY_MIN) == (10, 20)
    assert const.DEFAULT_WEATHER_GRACE_MIN == 30
    assert (const.DEFAULT_HOT_HIGH, const.DEFAULT_HOT_LOW) == (24.0, 13.0)
    assert const.DEFAULT_SUN_RELEASE_MARGIN == 2.0
    assert const.DEFAULT_TOLERANCE == 5.0
    assert const.DEFAULT_OVERRIDE_DWELL_MIN == 30
    assert (const.DEFAULT_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_RIGHT) == (60.0, 60.0)
    assert (const.DEFAULT_COMFORT_FLOOR, const.DEFAULT_COMFORT_CEILING) == (21.0, 25.0)
    assert const.DEFAULT_WIND_HOLD_MIN == 15
    assert const.DEFAULT_MIN_MOVE_INTERVAL_MIN == 10
    assert const.DEFAULT_CONFIRM_WINDOW_S == 120
    assert const.MAX_RULES == 4
    assert const.storage_key("abc") == "cover_automation.abc"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_const_manifest.py -v`
Expected: FAIL (`ModuleNotFoundError` for `const`, integration not found).

- [ ] **Step 3: Write the implementation**

`custom_components/cover_automation/manifest.json` (keys sorted alphabetically, as hassfest requires):
```json
{
  "codeowners": ["@julianpufler"],
  "config_flow": true,
  "dependencies": ["sun", "weather", "logbook"],
  "documentation": "https://github.com/julianpufler/hass-cover-automation",
  "domain": "cover_automation",
  "integration_type": "hub",
  "iot_class": "calculated",
  "issue_tracker": "https://github.com/julianpufler/hass-cover-automation/issues",
  "name": "Cover Automation",
  "single_config_entry": true,
  "version": "0.1.0"
}
```
(If the owner's GitHub handle differs, the codeowners/documentation URLs are edited in a later task; they do not affect behaviour.)

`custom_components/cover_automation/const.py`:
```python
"""Constants for the cover_automation integration (spec §3)."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "cover_automation"
PLATFORMS: Final[list[Platform]] = []  # entity platforms arrive with plan 2b

SUBENTRY_COVER: Final = "cover"
SUBENTRY_PROFILE: Final = "profile"
PROFILE_NONE: Final = "none"

# Hub entry data (immutable identity of the hub)
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_WIND_SENSOR: Final = "wind_sensor"
CONF_OUTDOOR_TEMPERATURE_SENSOR: Final = "outdoor_temperature_sensor"

# Hub options (thresholds and behaviour)
CONF_FROST_THRESHOLD: Final = "frost_threshold"
CONF_SUNNY_CONDITIONS: Final = "sunny_conditions"
CONF_SUNNY_ON_DELAY: Final = "sunny_on_delay"  # minutes
CONF_SUNNY_OFF_DELAY: Final = "sunny_off_delay"  # minutes
CONF_WEATHER_GRACE: Final = "weather_grace"  # minutes
CONF_HOT_HIGH: Final = "hot_high"
CONF_HOT_LOW: Final = "hot_low"
CONF_HOT_LOW_ENABLED: Final = "hot_low_enabled"
CONF_SUNNY_OVERRIDE_ENTITY: Final = "sunny_override_entity"
CONF_HOT_OVERRIDE_ENTITY: Final = "hot_override_entity"
CONF_SUN_RELEASE_MARGIN: Final = "sun_release_margin"
CONF_TOLERANCE: Final = "open_closed_tolerance"
CONF_OVERRIDE_DWELL: Final = "override_dwell"  # minutes
CONF_TEMPERATURE_UNIT: Final = "temperature_unit"
CONF_WIND_UNIT: Final = "wind_unit"

# Cover subentry data
CONF_COVER_ENTITY: Final = "cover_entity"
CONF_NAME: Final = "name"
CONF_AZIMUTH: Final = "azimuth"
CONF_TOLERANCE_LEFT: Final = "tolerance_left"
CONF_TOLERANCE_RIGHT: Final = "tolerance_right"
CONF_ELEVATION_MIN: Final = "elevation_min"
CONF_ELEVATION_MAX: Final = "elevation_max"
CONF_SHADING_RULE: Final = "shading_rule"
CONF_ROOM_SENSOR: Final = "room_temperature_sensor"
CONF_COMFORT_FLOOR: Final = "comfort_floor"
CONF_COMFORT_CEILING: Final = "comfort_ceiling"
CONF_DOOR_SENSOR: Final = "door_sensor"
CONF_WIND_ENABLED: Final = "wind_enabled"
CONF_WIND_UPPER: Final = "wind_upper"
CONF_WIND_LOWER: Final = "wind_lower"
CONF_WIND_HOLD: Final = "wind_hold"  # minutes
CONF_WIND_ACTION: Final = "wind_action"
CONF_SCHEDULE_PROFILE: Final = "schedule_profile"
CONF_MIN_MOVE_INTERVAL: Final = "min_move_interval"  # minutes
CONF_CONFIRM_WINDOW: Final = "confirm_window"  # seconds

# Profile subentry data
CONF_RULES: Final = "rules"
CONF_RULE_ACTION: Final = "action"
CONF_RULE_TIME_MODE: Final = "time_mode"
CONF_RULE_TIME: Final = "time"
CONF_RULE_OFFSET: Final = "offset_minutes"
CONF_RULE_EARLIEST: Final = "earliest"
CONF_RULE_LATEST: Final = "latest"
CONF_RULE_ENABLED: Final = "enabled"
CONF_QUIET_START: Final = "quiet_start"
CONF_QUIET_END: Final = "quiet_end"
MAX_RULES: Final = 4

# Defaults (spec §3)
DEFAULT_FROST_THRESHOLD: Final = 0.0
DEFAULT_SUNNY_CONDITIONS: Final[list[str]] = ["sunny", "partlycloudy"]
DEFAULT_SUNNY_ON_DELAY_MIN: Final = 10
DEFAULT_SUNNY_OFF_DELAY_MIN: Final = 20
DEFAULT_WEATHER_GRACE_MIN: Final = 30
DEFAULT_HOT_HIGH: Final = 24.0
DEFAULT_HOT_LOW: Final = 13.0
DEFAULT_HOT_LOW_ENABLED: Final = True
DEFAULT_SUN_RELEASE_MARGIN: Final = 2.0
DEFAULT_TOLERANCE: Final = 5.0
DEFAULT_OVERRIDE_DWELL_MIN: Final = 30
DEFAULT_TOLERANCE_LEFT: Final = 60.0
DEFAULT_TOLERANCE_RIGHT: Final = 60.0
DEFAULT_ELEVATION_MIN: Final = 0.0
DEFAULT_ELEVATION_MAX: Final = 90.0
DEFAULT_COMFORT_FLOOR: Final = 21.0
DEFAULT_COMFORT_CEILING: Final = 25.0
DEFAULT_WIND_HOLD_MIN: Final = 15
DEFAULT_MIN_MOVE_INTERVAL_MIN: Final = 10
DEFAULT_CONFIRM_WINDOW_S: Final = 120
MIN_CONFIRM_WINDOW_S: Final = 10

WEATHER_CONDITIONS: Final[list[str]] = [
    "clear-night",
    "cloudy",
    "exceptional",
    "fog",
    "hail",
    "lightning",
    "lightning-rainy",
    "partlycloudy",
    "pouring",
    "rainy",
    "snowy",
    "snowy-rainy",
    "sunny",
    "windy",
    "windy-variant",
]

STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_const_manifest.py -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 3 passed; clean. (`async_get_integration` needs a `custom_components/cover_automation/__init__.py`; the empty one from plan 1 is enough.)

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: integration manifest and configuration constants

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Config-to-engine mapping

**Files:**
- Create: `custom_components/cover_automation/config_map.py`
- Test: `tests/ha/test_config_map.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) HubConfig` with fields `weather_entity: str`, `wind_sensor: str | None`, `outdoor_temperature_sensor: str | None`, `frost_threshold: float`, `sunny_conditions: frozenset[str]`, `sunny_on_delay_s: int`, `sunny_off_delay_s: int`, `weather_grace_s: int`, `hot_high: float`, `hot_low: float | None`, `sunny_override_entity: str | None`, `hot_override_entity: str | None`, `sun_release_margin: float`, `tolerance: float`, `override_dwell_s: int`, `temperature_unit: str`.
  - `@dataclass(frozen=True) CoverBindings` with `subentry_id: str`, `name: str`, `cover_entity: str`, `door_sensor: str | None`, `room_sensor: str | None`, `profile_id: str | None`, `temperature_unit: str`, `wind_unit: str | None`.
  - `hub_config(entry: ConfigEntry) -> HubConfig`
  - `cover_config(subentry: ConfigSubentry, hub: HubConfig) -> tuple[CoverConfig, CoverBindings]`
  - `profile(subentry: ConfigSubentry) -> Profile`
  - `parse_time(value: str | None) -> time | None` (accepts `HH:MM` and `HH:MM:SS`)
  - `rules_from_data(data: Mapping[str, Any]) -> tuple[Rule, ...]`, `quiet_from_data(data) -> QuietHours | None`
- Consumes: engine `CoverConfig`, `ShadingRule`, `WindAction`, `schedule.Rule/QuietHours/Profile/TimeMode`, `Target`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_config_map.py`:
```python
from __future__ import annotations

from datetime import time
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry

from custom_components.cover_automation import const
from custom_components.cover_automation.config_map import (
    HubConfig,
    cover_config,
    hub_config,
    parse_time,
    profile,
)
from custom_components.cover_automation.engine.model import ShadingRule, Target, WindAction
from custom_components.cover_automation.engine.schedule import TimeMode
from pytest_homeassistant_custom_component.common import MockConfigEntry


def make_hub_entry(**options) -> MockConfigEntry:
    opts = {
        const.CONF_FROST_THRESHOLD: 0.0,
        const.CONF_SUNNY_CONDITIONS: ["sunny", "partlycloudy"],
        const.CONF_SUNNY_ON_DELAY: 10,
        const.CONF_SUNNY_OFF_DELAY: 20,
        const.CONF_WEATHER_GRACE: 30,
        const.CONF_HOT_HIGH: 24.0,
        const.CONF_HOT_LOW: 13.0,
        const.CONF_HOT_LOW_ENABLED: True,
        const.CONF_SUN_RELEASE_MARGIN: 2.0,
        const.CONF_TOLERANCE: 5.0,
        const.CONF_OVERRIDE_DWELL: 30,
        const.CONF_TEMPERATURE_UNIT: "°C",
    }
    opts.update(options)
    return MockConfigEntry(
        domain=const.DOMAIN,
        data={const.CONF_WEATHER_ENTITY: "weather.home", const.CONF_WIND_SENSOR: "sensor.wind"},
        options=opts,
    )


def subentry(kind: str, data: dict, title: str = "x", subentry_id: str = "sub1") -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType(data), subentry_type=kind, title=title, unique_id=None, subentry_id=subentry_id
    )


def test_hub_config_converts_minutes_and_optional_low():
    hub = hub_config(make_hub_entry())
    assert hub.weather_entity == "weather.home" and hub.wind_sensor == "sensor.wind"
    assert hub.outdoor_temperature_sensor is None
    assert (hub.sunny_on_delay_s, hub.sunny_off_delay_s, hub.weather_grace_s) == (600, 1200, 1800)
    assert hub.override_dwell_s == 1800 and hub.hot_low == 13.0
    assert hub.sunny_conditions == frozenset({"sunny", "partlycloudy"})
    hub2 = hub_config(make_hub_entry(**{const.CONF_HOT_LOW_ENABLED: False}))
    assert hub2.hot_low is None


def test_hub_config_uses_defaults_for_missing_options():
    entry = MockConfigEntry(domain=const.DOMAIN, data={const.CONF_WEATHER_ENTITY: "weather.home"}, options={})
    hub = hub_config(entry)
    assert hub.hot_high == 24.0 and hub.tolerance == 5.0 and hub.temperature_unit == "°C"
    assert hub.wind_sensor is None


def test_cover_config_maps_all_fields_and_units():
    hub = hub_config(make_hub_entry())
    se = subentry(
        const.SUBENTRY_COVER,
        {
            const.CONF_COVER_ENTITY: "cover.bedroom",
            const.CONF_NAME: "Bedroom",
            const.CONF_AZIMUTH: 170,
            const.CONF_TOLERANCE_LEFT: 50,
            const.CONF_TOLERANCE_RIGHT: 70,
            const.CONF_ELEVATION_MIN: 5,
            const.CONF_ELEVATION_MAX: 80,
            const.CONF_SHADING_RULE: "room_only",
            const.CONF_ROOM_SENSOR: "sensor.bedroom_temp",
            const.CONF_COMFORT_FLOOR: 20,
            const.CONF_COMFORT_CEILING: 26,
            const.CONF_DOOR_SENSOR: "binary_sensor.terrace",
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_HOLD: 20,
            const.CONF_WIND_ACTION: "hold",
            const.CONF_SCHEDULE_PROFILE: "prof1",
            const.CONF_MIN_MOVE_INTERVAL: 15,
            const.CONF_CONFIRM_WINDOW: 90,
            const.CONF_TEMPERATURE_UNIT: "°C",
            const.CONF_WIND_UNIT: "km/h",
        },
        title="Bedroom",
    )
    cfg, bind = cover_config(se, hub)
    assert cfg.cover_id == "sub1" and cfg.name == "Bedroom" and cfg.azimuth == 170.0
    assert (cfg.tolerance_left, cfg.tolerance_right, cfg.elevation_min, cfg.elevation_max) == (50.0, 70.0, 5.0, 80.0)
    assert cfg.shading_rule is ShadingRule.ROOM_ONLY and cfg.has_room_sensor and cfg.has_door_sensor
    assert (cfg.comfort_floor, cfg.comfort_ceiling) == (20.0, 26.0)
    assert cfg.wind_enabled and (cfg.wind_upper, cfg.wind_lower, cfg.wind_hold_s) == (60.0, 50.0, 1200)
    assert cfg.wind_action is WindAction.HOLD and cfg.profile_id == "prof1"
    assert (cfg.min_move_interval_s, cfg.confirm_window_s) == (900, 90)
    assert bind.cover_entity == "cover.bedroom" and bind.door_sensor == "binary_sensor.terrace"
    assert bind.room_sensor == "sensor.bedroom_temp" and bind.profile_id == "prof1"
    assert (bind.temperature_unit, bind.wind_unit) == ("°C", "km/h")


def test_cover_config_defaults_and_none_profile():
    hub = hub_config(make_hub_entry())
    se = subentry(const.SUBENTRY_COVER, {const.CONF_COVER_ENTITY: "cover.x", const.CONF_AZIMUTH: 180,
                                          const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE}, title="X")
    cfg, bind = cover_config(se, hub)
    assert cfg.name == "X" and not cfg.has_room_sensor and not cfg.wind_enabled and cfg.profile_id is None
    assert (cfg.tolerance_left, cfg.elevation_max, cfg.min_move_interval_s, cfg.confirm_window_s) == (60.0, 90.0, 600, 120)
    assert bind.profile_id is None and bind.temperature_unit == hub.temperature_unit


def test_wind_disabled_when_hub_has_no_wind_sensor():
    hub = HubConfig(**{**hub_config(make_hub_entry()).__dict__, "wind_sensor": None})
    se = subentry(const.SUBENTRY_COVER, {const.CONF_COVER_ENTITY: "cover.x", const.CONF_AZIMUTH: 180,
                                          const.CONF_WIND_ENABLED: True, const.CONF_WIND_UPPER: 60, const.CONF_WIND_LOWER: 50})
    cfg, _ = cover_config(se, hub)
    assert cfg.wind_enabled is False


def test_parse_time():
    assert parse_time("21:30") == time(21, 30)
    assert parse_time("06:15:30") == time(6, 15, 30)
    assert parse_time(None) is None and parse_time("") is None


def test_profile_mapping_rules_and_quiet_hours():
    se = subentry(
        const.SUBENTRY_PROFILE,
        {
            const.CONF_NAME: "Bedroom",
            const.CONF_RULES: [
                {const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_TIME: "21:30:00"},
                {const.CONF_RULE_ACTION: "open", const.CONF_RULE_TIME_MODE: "sunrise", const.CONF_RULE_OFFSET: 30,
                 const.CONF_RULE_EARLIEST: "07:00:00", const.CONF_RULE_LATEST: None},
            ],
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
        },
        title="Bedroom",
        subentry_id="prof1",
    )
    p = profile(se)
    assert p.profile_id == "prof1" and p.name == "Bedroom" and len(p.rules) == 2
    assert p.rules[0].action is Target.CLOSED and p.rules[0].time_mode is TimeMode.FIXED and p.rules[0].time == time(21, 30)
    assert p.rules[1].action is Target.OPEN and p.rules[1].time_mode is TimeMode.SUNRISE
    assert p.rules[1].offset_minutes == 30 and p.rules[1].earliest == time(7, 0) and p.rules[1].latest is None
    assert p.quiet_hours is not None and (p.quiet_hours.start, p.quiet_hours.end) == (time(22, 0), time(7, 0))


def test_profile_without_quiet_hours_or_rules():
    se = subentry(const.SUBENTRY_PROFILE, {const.CONF_NAME: "Empty", const.CONF_RULES: []}, title="Empty", subentry_id="p")
    p = profile(se)
    assert p.rules == () and p.quiet_hours is None


def test_invalid_shading_rule_raises():
    hub = hub_config(make_hub_entry())
    se = subentry(const.SUBENTRY_COVER, {const.CONF_COVER_ENTITY: "cover.x", const.CONF_AZIMUTH: 1, const.CONF_SHADING_RULE: "bogus"})
    with pytest.raises(ValueError):
        cover_config(se, hub)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_config_map.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`custom_components/cover_automation/config_map.py`:
```python
"""Map config entry / subentry dictionaries onto engine models (spec §3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import UnitOfTemperature

from . import const
from .engine.model import CoverConfig, ShadingRule, Target, WindAction
from .engine.schedule import Profile, QuietHours, Rule, TimeMode


@dataclass(frozen=True, slots=True)
class HubConfig:
    weather_entity: str
    wind_sensor: str | None
    outdoor_temperature_sensor: str | None
    frost_threshold: float
    sunny_conditions: frozenset[str]
    sunny_on_delay_s: int
    sunny_off_delay_s: int
    weather_grace_s: int
    hot_high: float
    hot_low: float | None
    sunny_override_entity: str | None
    hot_override_entity: str | None
    sun_release_margin: float
    tolerance: float
    override_dwell_s: int
    temperature_unit: str


@dataclass(frozen=True, slots=True)
class CoverBindings:
    """Home Assistant entity references and units for one cover subentry."""

    subentry_id: str
    name: str
    cover_entity: str
    door_sensor: str | None
    room_sensor: str | None
    profile_id: str | None
    temperature_unit: str
    wind_unit: str | None


def _opt_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value in (None, "", const.PROFILE_NONE):
        return None
    return str(value)


def _minutes(data: Mapping[str, Any], key: str, default_min: int) -> int:
    return int(round(float(data.get(key, default_min)) * 60))


def parse_time(value: str | None) -> time | None:
    if value in (None, ""):
        return None
    parts = [int(p) for p in str(value).split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1])
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    msg = f"invalid time: {value!r}"
    raise ValueError(msg)


def hub_config(entry: ConfigEntry) -> HubConfig:
    data, opts = entry.data, entry.options
    hot_low_enabled = bool(opts.get(const.CONF_HOT_LOW_ENABLED, const.DEFAULT_HOT_LOW_ENABLED))
    return HubConfig(
        weather_entity=str(data[const.CONF_WEATHER_ENTITY]),
        wind_sensor=_opt_str(data, const.CONF_WIND_SENSOR),
        outdoor_temperature_sensor=_opt_str(data, const.CONF_OUTDOOR_TEMPERATURE_SENSOR),
        frost_threshold=float(opts.get(const.CONF_FROST_THRESHOLD, const.DEFAULT_FROST_THRESHOLD)),
        sunny_conditions=frozenset(opts.get(const.CONF_SUNNY_CONDITIONS, const.DEFAULT_SUNNY_CONDITIONS)),
        sunny_on_delay_s=_minutes(opts, const.CONF_SUNNY_ON_DELAY, const.DEFAULT_SUNNY_ON_DELAY_MIN),
        sunny_off_delay_s=_minutes(opts, const.CONF_SUNNY_OFF_DELAY, const.DEFAULT_SUNNY_OFF_DELAY_MIN),
        weather_grace_s=_minutes(opts, const.CONF_WEATHER_GRACE, const.DEFAULT_WEATHER_GRACE_MIN),
        hot_high=float(opts.get(const.CONF_HOT_HIGH, const.DEFAULT_HOT_HIGH)),
        hot_low=float(opts.get(const.CONF_HOT_LOW, const.DEFAULT_HOT_LOW)) if hot_low_enabled else None,
        sunny_override_entity=_opt_str(opts, const.CONF_SUNNY_OVERRIDE_ENTITY),
        hot_override_entity=_opt_str(opts, const.CONF_HOT_OVERRIDE_ENTITY),
        sun_release_margin=float(opts.get(const.CONF_SUN_RELEASE_MARGIN, const.DEFAULT_SUN_RELEASE_MARGIN)),
        tolerance=float(opts.get(const.CONF_TOLERANCE, const.DEFAULT_TOLERANCE)),
        override_dwell_s=_minutes(opts, const.CONF_OVERRIDE_DWELL, const.DEFAULT_OVERRIDE_DWELL_MIN),
        temperature_unit=str(opts.get(const.CONF_TEMPERATURE_UNIT, UnitOfTemperature.CELSIUS)),
    )


def cover_config(subentry: ConfigSubentry, hub: HubConfig) -> tuple[CoverConfig, CoverBindings]:
    d = subentry.data
    room_sensor = _opt_str(d, const.CONF_ROOM_SENSOR)
    door_sensor = _opt_str(d, const.CONF_DOOR_SENSOR)
    profile_id = _opt_str(d, const.CONF_SCHEDULE_PROFILE)
    wind_enabled = bool(d.get(const.CONF_WIND_ENABLED, False)) and hub.wind_sensor is not None
    name = str(d.get(const.CONF_NAME) or subentry.title)
    cfg = CoverConfig(
        cover_id=subentry.subentry_id,
        name=name,
        azimuth=float(d[const.CONF_AZIMUTH]),
        tolerance_left=float(d.get(const.CONF_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_LEFT)),
        tolerance_right=float(d.get(const.CONF_TOLERANCE_RIGHT, const.DEFAULT_TOLERANCE_RIGHT)),
        elevation_min=float(d.get(const.CONF_ELEVATION_MIN, const.DEFAULT_ELEVATION_MIN)),
        elevation_max=float(d.get(const.CONF_ELEVATION_MAX, const.DEFAULT_ELEVATION_MAX)),
        shading_rule=ShadingRule(d.get(const.CONF_SHADING_RULE, ShadingRule.FORECAST_WITH_ROOM.value)),
        has_room_sensor=room_sensor is not None,
        comfort_floor=float(d.get(const.CONF_COMFORT_FLOOR, const.DEFAULT_COMFORT_FLOOR)),
        comfort_ceiling=float(d.get(const.CONF_COMFORT_CEILING, const.DEFAULT_COMFORT_CEILING)),
        has_door_sensor=door_sensor is not None,
        wind_enabled=wind_enabled,
        wind_upper=float(d.get(const.CONF_WIND_UPPER, 0.0)),
        wind_lower=float(d.get(const.CONF_WIND_LOWER, 0.0)),
        wind_hold_s=_minutes(d, const.CONF_WIND_HOLD, const.DEFAULT_WIND_HOLD_MIN),
        wind_action=WindAction(d.get(const.CONF_WIND_ACTION, WindAction.OPEN.value)),
        profile_id=profile_id,
        min_move_interval_s=_minutes(d, const.CONF_MIN_MOVE_INTERVAL, const.DEFAULT_MIN_MOVE_INTERVAL_MIN),
        confirm_window_s=int(d.get(const.CONF_CONFIRM_WINDOW, const.DEFAULT_CONFIRM_WINDOW_S)),
    )
    bindings = CoverBindings(
        subentry_id=subentry.subentry_id,
        name=name,
        cover_entity=str(d[const.CONF_COVER_ENTITY]),
        door_sensor=door_sensor,
        room_sensor=room_sensor,
        profile_id=profile_id,
        temperature_unit=str(d.get(const.CONF_TEMPERATURE_UNIT, hub.temperature_unit)),
        wind_unit=_opt_str(d, const.CONF_WIND_UNIT),
    )
    return cfg, bindings


def rules_from_data(data: Mapping[str, Any]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for raw in data.get(const.CONF_RULES, []):
        rules.append(
            Rule(
                action=Target(raw[const.CONF_RULE_ACTION]),
                time_mode=TimeMode(raw[const.CONF_RULE_TIME_MODE]),
                time=parse_time(raw.get(const.CONF_RULE_TIME)),
                offset_minutes=int(raw.get(const.CONF_RULE_OFFSET, 0) or 0),
                earliest=parse_time(raw.get(const.CONF_RULE_EARLIEST)),
                latest=parse_time(raw.get(const.CONF_RULE_LATEST)),
            )
        )
    return tuple(rules)


def quiet_from_data(data: Mapping[str, Any]) -> QuietHours | None:
    start = parse_time(data.get(const.CONF_QUIET_START))
    end = parse_time(data.get(const.CONF_QUIET_END))
    if start is None or end is None:
        return None
    return QuietHours(start=start, end=end)


def profile(subentry: ConfigSubentry) -> Profile:
    return Profile(
        profile_id=subentry.subentry_id,
        name=str(subentry.data.get(const.CONF_NAME) or subentry.title),
        rules=rules_from_data(subentry.data),
        quiet_hours=quiet_from_data(subentry.data),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/ha/test_config_map.py -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 9 passed; pyright clean (fix any `reportUnknownMemberType`-style complaints from `MappingProxyType` by keeping the `Mapping[str, Any]` annotations shown).

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: map config entry and subentry data onto engine models

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Store

**Files:**
- Create: `custom_components/cover_automation/store.py`
- Test: `tests/ha/test_store.py`

**Interfaces:**
- Produces:
  - `@dataclass StoreData` with `covers: dict[str, CoverPersisted]`, `latch: DailyLatch`, `shading_mode: ShadingMode`, `reopening_mode: ReopeningMode`, `simulation: bool`, `verbose: bool`; methods `to_dict()`, `StoreData.from_dict(raw)` (tolerant: a bad cover record is replaced by a default and logged; unknown enum values fall back to defaults).
  - `class CoverAutomationStore(hass, entry_id)` with `async_load() -> StoreData`, `data` property, `schedule_save()` (1 s delayed), `async_save()` (immediate), `async_remove()`.
- Consumes: engine `CoverPersisted`, `DailyLatch`, `ShadingMode`, `ReopeningMode`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_store.py`:
```python
from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.core import HomeAssistant

from custom_components.cover_automation import const
from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    Owner,
    ReopeningMode,
    ShadingMode,
    Target,
)
from custom_components.cover_automation.store import CoverAutomationStore, StoreData


def test_store_data_defaults_and_roundtrip():
    data = StoreData()
    assert data.covers == {} and data.latch.date is None
    assert data.shading_mode is ShadingMode.AUTO and data.reopening_mode is ReopeningMode.PASSIVE
    assert data.simulation is False and data.verbose is False
    data.covers["sub1"] = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED,
                                         manual_move_at=datetime(2026, 7, 1, 12, tzinfo=UTC))
    data.shading_mode = ShadingMode.FORCED_ALL
    raw = data.to_dict()
    again = StoreData.from_dict(raw)
    assert again.covers["sub1"] == data.covers["sub1"]
    assert again.shading_mode is ShadingMode.FORCED_ALL


def test_from_dict_is_tolerant_of_garbage(caplog):
    raw = {
        "covers": {"good": CoverPersisted().to_dict(), "bad": {"owner": "martian"}},
        "latch": {"date": "not-a-date"},
        "shading_mode": "nonsense",
        "reopening_mode": None,
        "simulation": "yes",
    }
    data = StoreData.from_dict(raw)
    assert "good" in data.covers and data.covers["bad"] == CoverPersisted()
    assert data.latch.date is None
    assert data.shading_mode is ShadingMode.AUTO and data.reopening_mode is ReopeningMode.PASSIVE
    assert data.simulation is True
    assert "bad" in caplog.text


async def test_store_load_save_remove(hass: HomeAssistant, hass_storage: dict) -> None:
    store = CoverAutomationStore(hass, "entry1")
    data = await store.async_load()
    assert data == StoreData()
    data.covers["sub1"] = CoverPersisted(owner=Owner.USER)
    await store.async_save()
    key = const.storage_key("entry1")
    assert hass_storage[key]["version"] == const.STORAGE_VERSION
    assert hass_storage[key]["data"]["covers"]["sub1"]["owner"] == "user"

    store2 = CoverAutomationStore(hass, "entry1")
    loaded = await store2.async_load()
    assert loaded.covers["sub1"].owner is Owner.USER

    await store2.async_remove()
    assert key not in hass_storage


async def test_schedule_save_is_delayed(hass: HomeAssistant, hass_storage: dict, freezer) -> None:
    store = CoverAutomationStore(hass, "entry2")
    await store.async_load()
    store.data.simulation = True
    store.schedule_save()
    await hass.async_block_till_done()
    key = const.storage_key("entry2")
    assert key not in hass_storage  # not yet written
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["simulation"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`custom_components/cover_automation/store.py`:
```python
"""Persisted engine state for one hub entry (spec §5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import const
from .engine.model import CoverPersisted, ReopeningMode, ShadingMode
from .engine.signals import DailyLatch

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY_S = 1


def _enum_or_default[E](enum: type[E], raw: Any, default: E) -> E:
    try:
        return enum(raw)  # type: ignore[call-arg]
    except (ValueError, TypeError):
        return default


@dataclass(slots=True)
class StoreData:
    covers: dict[str, CoverPersisted] = field(default_factory=dict)
    latch: DailyLatch = field(default_factory=DailyLatch)
    shading_mode: ShadingMode = ShadingMode.AUTO
    reopening_mode: ReopeningMode = ReopeningMode.PASSIVE
    simulation: bool = False
    verbose: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "covers": {cover_id: p.to_dict() for cover_id, p in self.covers.items()},
            "latch": self.latch.to_dict(),
            "shading_mode": self.shading_mode.value,
            "reopening_mode": self.reopening_mode.value,
            "simulation": self.simulation,
            "verbose": self.verbose,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> StoreData:
        raw = raw or {}
        covers: dict[str, CoverPersisted] = {}
        for cover_id, record in (raw.get("covers") or {}).items():
            try:
                covers[str(cover_id)] = CoverPersisted.from_dict(dict(record))
            except (ValueError, TypeError, KeyError, AttributeError) as err:
                _LOGGER.warning("Discarding persisted state for cover %s: %s", cover_id, err)
                covers[str(cover_id)] = CoverPersisted()
        try:
            latch = DailyLatch.from_dict(dict(raw.get("latch") or {}))
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Discarding persisted forecast latch: %s", err)
            latch = DailyLatch()
        return cls(
            covers=covers,
            latch=latch,
            shading_mode=_enum_or_default(ShadingMode, raw.get("shading_mode"), ShadingMode.AUTO),
            reopening_mode=_enum_or_default(ReopeningMode, raw.get("reopening_mode"), ReopeningMode.PASSIVE),
            simulation=bool(raw.get("simulation", False)),
            verbose=bool(raw.get("verbose", False)),
        )


class CoverAutomationStore:
    """One HA Store per hub entry; the engine owns the state, entities are views (decision 17)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            const.STORAGE_VERSION,
            const.storage_key(entry_id),
            minor_version=const.STORAGE_MINOR_VERSION,
        )
        self._data: StoreData | None = None

    @property
    def data(self) -> StoreData:
        if self._data is None:
            msg = "store not loaded"
            raise RuntimeError(msg)
        return self._data

    async def async_load(self) -> StoreData:
        raw = await self._store.async_load()
        self._data = StoreData.from_dict(raw)
        return self._data

    def _to_save(self) -> dict[str, Any]:
        return self.data.to_dict()

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._to_save, SAVE_DELAY_S)

    async def async_save(self) -> None:
        await self._store.async_save(self._to_save())

    async def async_remove(self) -> None:
        await self._store.async_remove()
```
`DailyLatch.from_dict` raises `ValueError` on a malformed date (`date.fromisoformat`) — that is the path the tolerance test exercises.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/ha/test_store.py -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 4 passed. (`hass_storage` and `freezer` are fixtures from the HA test plugin.)

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: Store for persisted engine state and hub runtime selects

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Hub config flow, options and reconfigure

**Files:**
- Create: `custom_components/cover_automation/config_flow.py` (hub part; subentry handlers added in Task 5), `custom_components/cover_automation/translations/en.json` (config + options sections; extended later)
- Test: `tests/ha/conftest.py`, `tests/ha/test_config_flow.py`

**Interfaces:**
- Produces: `CoverAutomationConfigFlow(ConfigFlow, domain=DOMAIN)` with steps `user` (hub entities) → `thresholds` (options), `reconfigure` (hub entities), `async_get_options_flow` → `CoverAutomationOptionsFlow` (step `init` = thresholds), `async_get_supported_subentry_types` (filled in Task 5, returns `{}` for now); helpers `hub_data_schema(hass, defaults)`, `thresholds_schema(defaults)`, `validate_weather(hass, entity_id) -> str | None` (error key or None). Entry: `data` = hub entities, `options` = thresholds + `temperature_unit`.
- Test helpers (`tests/ha/conftest.py`): `set_weather(hass, entity_id="weather.home", *, daily=True, condition="sunny")`, `set_sun(hass, elevation=30.0, azimuth=180.0)`, `set_cover(hass, entity_id, state="open", position=100, features=3)`, `set_sensor(hass, entity_id, value, unit, device_class)`, `hub_entry(hass, **overrides) -> MockConfigEntry` (added to hass, not set up), `add_cover_subentry(...)`, `add_profile_subentry(...)`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/conftest.py`:
```python
"""Home Assistant test helpers for the cover_automation integration."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_automation import const

WEATHER = "weather.home"
SUN = "sun.sun"


def set_weather(hass: HomeAssistant, entity_id: str = WEATHER, *, daily: bool = True,
                condition: str = "sunny", temperature: float = 20.0, unit: str = "°C") -> None:
    hass.states.async_set(entity_id, condition, {
        "supported_features": 1 if daily else 2,
        "temperature": temperature,
        "temperature_unit": unit,
        "friendly_name": "Home",
    })


def set_sun(hass: HomeAssistant, *, elevation: float = 30.0, azimuth: float = 180.0) -> None:
    hass.states.async_set(SUN, "above_horizon" if elevation > 0 else "below_horizon",
                          {"elevation": elevation, "azimuth": azimuth})


def set_cover(hass: HomeAssistant, entity_id: str, *, state: str = "open", position: int | None = 100,
              features: int = 3, name: str | None = None) -> None:
    attrs: dict[str, Any] = {"supported_features": features, "friendly_name": name or entity_id}
    if position is not None:
        attrs["current_position"] = position
    hass.states.async_set(entity_id, state, attrs)


def set_sensor(hass: HomeAssistant, entity_id: str, value: float | str, *, unit: str | None = None,
               device_class: str | None = None) -> None:
    attrs: dict[str, Any] = {}
    if unit:
        attrs["unit_of_measurement"] = unit
    if device_class:
        attrs["device_class"] = device_class
    hass.states.async_set(entity_id, str(value), attrs)


def hub_options(**overrides: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        const.CONF_FROST_THRESHOLD: 0.0,
        const.CONF_SUNNY_CONDITIONS: ["sunny", "partlycloudy"],
        const.CONF_SUNNY_ON_DELAY: 10,
        const.CONF_SUNNY_OFF_DELAY: 20,
        const.CONF_WEATHER_GRACE: 30,
        const.CONF_HOT_HIGH: 24.0,
        const.CONF_HOT_LOW: 13.0,
        const.CONF_HOT_LOW_ENABLED: True,
        const.CONF_SUN_RELEASE_MARGIN: 2.0,
        const.CONF_TOLERANCE: 5.0,
        const.CONF_OVERRIDE_DWELL: 30,
        const.CONF_TEMPERATURE_UNIT: "°C",
    }
    opts.update(overrides)
    return opts


def cover_subentry_data(entity_id: str = "cover.bedroom", **overrides: Any) -> ConfigSubentryData:
    data: dict[str, Any] = {
        const.CONF_COVER_ENTITY: entity_id,
        const.CONF_NAME: "Bedroom",
        const.CONF_AZIMUTH: 180,
        const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE,
        const.CONF_TEMPERATURE_UNIT: "°C",
    }
    data.update(overrides)
    return ConfigSubentryData(data=data, subentry_type=const.SUBENTRY_COVER,
                              title=str(data[const.CONF_NAME]), unique_id=None)


def profile_subentry_data(name: str = "Bedroom", rules: list[dict[str, Any]] | None = None,
                          quiet: tuple[str, str] | None = ("22:00:00", "07:00:00")) -> ConfigSubentryData:
    data: dict[str, Any] = {const.CONF_NAME: name, const.CONF_RULES: rules or [
        {const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_TIME: "21:30:00"}]}
    if quiet:
        data[const.CONF_QUIET_START], data[const.CONF_QUIET_END] = quiet
    return ConfigSubentryData(data=data, subentry_type=const.SUBENTRY_PROFILE, title=name, unique_id=None)


@pytest.fixture
def hub_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A hub entry with weather + wind sensor, added to hass but not set up."""
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Cover Automation",
        data={const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"},
        options=hub_options(),
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry
```

`tests/ha/test_config_flow.py`:
```python
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cover_automation import const
from tests.ha.conftest import WEATHER, hub_options, set_sensor, set_weather


async def test_user_flow_two_steps_creates_entry(hass: HomeAssistant) -> None:
    set_weather(hass)
    set_sensor(hass, "sensor.wind", 12, unit="km/h", device_class="wind_speed")
    result = await hass.config_entries.flow.async_init(const.DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "thresholds"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_HOT_HIGH: 26.0, const.CONF_SUNNY_ON_DELAY: 5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Cover Automation"
    assert entry.data == {const.CONF_WEATHER_ENTITY: WEATHER, const.CONF_WIND_SENSOR: "sensor.wind"}
    assert entry.options[const.CONF_HOT_HIGH] == 26.0 and entry.options[const.CONF_SUNNY_ON_DELAY] == 5
    assert entry.options[const.CONF_HOT_LOW] == 13.0  # default filled in
    assert entry.options[const.CONF_TEMPERATURE_UNIT] == "°C"


async def test_user_flow_rejects_weather_without_daily_forecast(hass: HomeAssistant) -> None:
    set_weather(hass, daily=False)
    result = await hass.config_entries.flow.async_init(const.DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {const.CONF_WEATHER_ENTITY: WEATHER})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {const.CONF_WEATHER_ENTITY: "weather_no_daily"}


async def test_user_flow_rejects_missing_weather_entity(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(const.DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {const.CONF_WEATHER_ENTITY: "weather.nope"})
    assert result["errors"] == {const.CONF_WEATHER_ENTITY: "entity_not_found"}


async def test_single_instance(hass: HomeAssistant, hub_entry) -> None:
    result = await hass.config_entries.flow.async_init(const.DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"


async def test_options_flow_updates_thresholds(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**hub_options(), const.CONF_FROST_THRESHOLD: -1.0, const.CONF_HOT_LOW_ENABLED: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hub_entry.options[const.CONF_FROST_THRESHOLD] == -1.0
    assert hub_entry.options[const.CONF_HOT_LOW_ENABLED] is False


async def test_reconfigure_flow_changes_hub_entities(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_weather(hass, "weather.other")
    result = await hub_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.CONF_WEATHER_ENTITY: "weather.other"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert hub_entry.data[const.CONF_WEATHER_ENTITY] == "weather.other"
    assert const.CONF_WIND_SENSOR not in hub_entry.data or hub_entry.data[const.CONF_WIND_SENSOR] in (None, "")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_config_flow.py -v`
Expected: FAIL (no config flow handler → `UnknownHandler`).

- [ ] **Step 3: Write the implementation**

`custom_components/cover_automation/config_flow.py`:
```python
"""Config, options, reconfigure and subentry flows (spec §3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.weather import WeatherEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from . import const

_HUB_OPTION_KEYS: tuple[str, ...] = (
    const.CONF_FROST_THRESHOLD,
    const.CONF_SUNNY_CONDITIONS,
    const.CONF_SUNNY_ON_DELAY,
    const.CONF_SUNNY_OFF_DELAY,
    const.CONF_WEATHER_GRACE,
    const.CONF_HOT_HIGH,
    const.CONF_HOT_LOW,
    const.CONF_HOT_LOW_ENABLED,
    const.CONF_SUNNY_OVERRIDE_ENTITY,
    const.CONF_HOT_OVERRIDE_ENTITY,
    const.CONF_SUN_RELEASE_MARGIN,
    const.CONF_TOLERANCE,
    const.CONF_OVERRIDE_DWELL,
)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    config: selector.NumberSelectorConfig = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _entity(domain: str, *, device_class: str | None = None) -> selector.EntitySelector:
    config: selector.EntitySelectorConfig = {"domain": domain}
    if device_class:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


def hub_data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(const.CONF_WEATHER_ENTITY, default=d.get(const.CONF_WEATHER_ENTITY, vol.UNDEFINED)): _entity("weather"),
            vol.Optional(const.CONF_WIND_SENSOR, description={"suggested_value": d.get(const.CONF_WIND_SENSOR)}): _entity("sensor", device_class="wind_speed"),
            vol.Optional(const.CONF_OUTDOOR_TEMPERATURE_SENSOR, description={"suggested_value": d.get(const.CONF_OUTDOOR_TEMPERATURE_SENSOR)}): _entity("sensor", device_class="temperature"),
        }
    )


def thresholds_schema(defaults: Mapping[str, Any], temperature_unit: str) -> vol.Schema:
    d = defaults

    def dflt(key: str, fallback: Any) -> Any:
        return d.get(key, fallback)

    return vol.Schema(
        {
            vol.Required(const.CONF_FROST_THRESHOLD, default=dflt(const.CONF_FROST_THRESHOLD, const.DEFAULT_FROST_THRESHOLD)): _number(-30, 30, 0.5, temperature_unit),
            vol.Required(const.CONF_HOT_HIGH, default=dflt(const.CONF_HOT_HIGH, const.DEFAULT_HOT_HIGH)): _number(-30, 60, 0.5, temperature_unit),
            vol.Required(const.CONF_HOT_LOW_ENABLED, default=dflt(const.CONF_HOT_LOW_ENABLED, const.DEFAULT_HOT_LOW_ENABLED)): selector.BooleanSelector(),
            vol.Required(const.CONF_HOT_LOW, default=dflt(const.CONF_HOT_LOW, const.DEFAULT_HOT_LOW)): _number(-30, 60, 0.5, temperature_unit),
            vol.Required(const.CONF_SUNNY_CONDITIONS, default=dflt(const.CONF_SUNNY_CONDITIONS, const.DEFAULT_SUNNY_CONDITIONS)): selector.SelectSelector(
                {"options": const.WEATHER_CONDITIONS, "multiple": True, "mode": selector.SelectSelectorMode.LIST, "translation_key": "weather_condition"}
            ),
            vol.Required(const.CONF_SUNNY_ON_DELAY, default=dflt(const.CONF_SUNNY_ON_DELAY, const.DEFAULT_SUNNY_ON_DELAY_MIN)): _number(0, 120, 1, "min"),
            vol.Required(const.CONF_SUNNY_OFF_DELAY, default=dflt(const.CONF_SUNNY_OFF_DELAY, const.DEFAULT_SUNNY_OFF_DELAY_MIN)): _number(0, 240, 1, "min"),
            vol.Required(const.CONF_WEATHER_GRACE, default=dflt(const.CONF_WEATHER_GRACE, const.DEFAULT_WEATHER_GRACE_MIN)): _number(1, 720, 1, "min"),
            vol.Required(const.CONF_SUN_RELEASE_MARGIN, default=dflt(const.CONF_SUN_RELEASE_MARGIN, const.DEFAULT_SUN_RELEASE_MARGIN)): _number(0, 15, 0.5, "°"),
            vol.Required(const.CONF_TOLERANCE, default=dflt(const.CONF_TOLERANCE, const.DEFAULT_TOLERANCE)): _number(0, 30, 1, "%"),
            vol.Required(const.CONF_OVERRIDE_DWELL, default=dflt(const.CONF_OVERRIDE_DWELL, const.DEFAULT_OVERRIDE_DWELL_MIN)): _number(1, 720, 1, "min"),
            vol.Optional(const.CONF_SUNNY_OVERRIDE_ENTITY, description={"suggested_value": d.get(const.CONF_SUNNY_OVERRIDE_ENTITY)}): _entity("binary_sensor"),
            vol.Optional(const.CONF_HOT_OVERRIDE_ENTITY, description={"suggested_value": d.get(const.CONF_HOT_OVERRIDE_ENTITY)}): _entity("binary_sensor"),
        }
    )


def validate_weather(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return an error key, or None when the weather entity is usable."""
    state = hass.states.get(entity_id)
    if state is None:
        return "entity_not_found"
    features = int(state.attributes.get("supported_features", 0) or 0)
    if not features & WeatherEntityFeature.FORECAST_DAILY:
        return "weather_no_daily"
    return None


def _clean_optional_entities(user_input: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Drop optional entity keys the user cleared so they read as 'not configured'."""
    cleaned = dict(user_input)
    for key in keys:
        if cleaned.get(key) in (None, ""):
            cleaned.pop(key, None)
    return cleaned


def _complete_options(user_input: Mapping[str, Any], hass: HomeAssistant) -> dict[str, Any]:
    """Fill defaults for every hub option and record the unit thresholds were entered in."""
    unit = str(hass.config.units.temperature_unit)
    filled = {
        const.CONF_FROST_THRESHOLD: const.DEFAULT_FROST_THRESHOLD,
        const.CONF_SUNNY_CONDITIONS: list(const.DEFAULT_SUNNY_CONDITIONS),
        const.CONF_SUNNY_ON_DELAY: const.DEFAULT_SUNNY_ON_DELAY_MIN,
        const.CONF_SUNNY_OFF_DELAY: const.DEFAULT_SUNNY_OFF_DELAY_MIN,
        const.CONF_WEATHER_GRACE: const.DEFAULT_WEATHER_GRACE_MIN,
        const.CONF_HOT_HIGH: const.DEFAULT_HOT_HIGH,
        const.CONF_HOT_LOW: const.DEFAULT_HOT_LOW,
        const.CONF_HOT_LOW_ENABLED: const.DEFAULT_HOT_LOW_ENABLED,
        const.CONF_SUN_RELEASE_MARGIN: const.DEFAULT_SUN_RELEASE_MARGIN,
        const.CONF_TOLERANCE: const.DEFAULT_TOLERANCE,
        const.CONF_OVERRIDE_DWELL: const.DEFAULT_OVERRIDE_DWELL_MIN,
    }
    filled.update({k: v for k, v in user_input.items() if k in _HUB_OPTION_KEYS})
    filled = _clean_optional_entities(filled, (const.CONF_SUNNY_OVERRIDE_ENTITY, const.CONF_HOT_OVERRIDE_ENTITY))
    filled[const.CONF_TEMPERATURE_UNIT] = unit
    return filled


class CoverAutomationConfigFlow(ConfigFlow, domain=const.DOMAIN):
    """Hub config flow: entities, then thresholds."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._hub_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CoverAutomationOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {}  # filled in Task 5

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_weather(self.hass, user_input[const.CONF_WEATHER_ENTITY])
            if error:
                errors[const.CONF_WEATHER_ENTITY] = error
            else:
                self._hub_data = _clean_optional_entities(
                    user_input, (const.CONF_WIND_SENSOR, const.CONF_OUTDOOR_TEMPERATURE_SENSOR)
                )
                return await self.async_step_thresholds()
        return self.async_show_form(step_id="user", data_schema=hub_data_schema(user_input), errors=errors)

    async def async_step_thresholds(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="Cover Automation",
                data=self._hub_data,
                options=_complete_options(user_input, self.hass),
            )
        return self.async_show_form(
            step_id="thresholds",
            data_schema=thresholds_schema({}, str(self.hass.config.units.temperature_unit)),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_weather(self.hass, user_input[const.CONF_WEATHER_ENTITY])
            if error:
                errors[const.CONF_WEATHER_ENTITY] = error
            else:
                data = _clean_optional_entities(
                    user_input, (const.CONF_WIND_SENSOR, const.CONF_OUTDOOR_TEMPERATURE_SENSOR)
                )
                return self.async_update_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure", data_schema=hub_data_schema(user_input or entry.data), errors=errors
        )


class CoverAutomationOptionsFlow(OptionsFlow):
    """Thresholds and behaviour; a plain OptionsFlow because the entry has an update listener."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_complete_options(user_input, self.hass))
        unit = str(self.config_entry.options.get(const.CONF_TEMPERATURE_UNIT, self.hass.config.units.temperature_unit))
        return self.async_show_form(step_id="init", data_schema=thresholds_schema(self.config_entry.options, unit))
```

`custom_components/cover_automation/translations/en.json` (first version; Task 5 adds `config_subentries`, Task 7 completes the rest):
```json
{
  "config": {
    "abort": {
      "reconfigure_successful": "Hub settings updated.",
      "single_instance_allowed": "Only one Cover Automation hub is allowed."
    },
    "error": {
      "entity_not_found": "That entity does not exist.",
      "weather_no_daily": "This weather entity does not provide a daily forecast."
    },
    "step": {
      "reconfigure": {
        "data": {
          "outdoor_temperature_sensor": "Outdoor temperature sensor",
          "weather_entity": "Weather entity",
          "wind_sensor": "Wind sensor"
        },
        "data_description": {
          "outdoor_temperature_sensor": "Used for frost protection. Leave empty to use the weather entity's temperature.",
          "weather_entity": "Provides the current condition and the daily forecast.",
          "wind_sensor": "Wind speed or gust sensor. Leave empty to disable wind protection."
        },
        "title": "Hub entities"
      },
      "thresholds": {
        "data": {
          "frost_threshold": "Frost threshold",
          "hot_high": "Hot day: minimum forecast high",
          "hot_low": "Hot day: minimum forecast low",
          "hot_low_enabled": "Require the forecast low as well",
          "hot_override_entity": "Hot-day override entity",
          "open_closed_tolerance": "Open/closed position tolerance",
          "override_dwell": "Manual override dwell",
          "sun_release_margin": "Sun release margin",
          "sunny_conditions": "Weather conditions that count as sunny",
          "sunny_off_delay": "Sunny: off delay",
          "sunny_on_delay": "Sunny: on delay",
          "sunny_override_entity": "Sunny override entity",
          "weather_grace": "Weather unavailable grace period"
        },
        "data_description": {
          "frost_threshold": "Below this outdoor temperature no cover moves.",
          "open_closed_tolerance": "A cover within this many percent of an end position counts as open or closed.",
          "override_dwell": "How long the engine must want something else before a manual override ends.",
          "sun_release_margin": "Degrees past the window edges before the sun counts as gone.",
          "sunny_off_delay": "How long the weather must be non-sunny before shading may reopen.",
          "sunny_on_delay": "How long the weather must be sunny before shading may close."
        },
        "title": "Thresholds"
      },
      "user": {
        "data": {
          "outdoor_temperature_sensor": "Outdoor temperature sensor",
          "weather_entity": "Weather entity",
          "wind_sensor": "Wind sensor"
        },
        "data_description": {
          "outdoor_temperature_sensor": "Used for frost protection. Leave empty to use the weather entity's temperature.",
          "weather_entity": "Provides the current condition and the daily forecast.",
          "wind_sensor": "Wind speed or gust sensor. Leave empty to disable wind protection."
        },
        "title": "Hub entities"
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "data": {
          "frost_threshold": "Frost threshold",
          "hot_high": "Hot day: minimum forecast high",
          "hot_low": "Hot day: minimum forecast low",
          "hot_low_enabled": "Require the forecast low as well",
          "hot_override_entity": "Hot-day override entity",
          "open_closed_tolerance": "Open/closed position tolerance",
          "override_dwell": "Manual override dwell",
          "sun_release_margin": "Sun release margin",
          "sunny_conditions": "Weather conditions that count as sunny",
          "sunny_off_delay": "Sunny: off delay",
          "sunny_on_delay": "Sunny: on delay",
          "sunny_override_entity": "Sunny override entity",
          "weather_grace": "Weather unavailable grace period"
        },
        "title": "Thresholds"
      }
    }
  },
  "selector": {
    "weather_condition": {
      "options": {
        "clear-night": "Clear night",
        "cloudy": "Cloudy",
        "exceptional": "Exceptional",
        "fog": "Fog",
        "hail": "Hail",
        "lightning": "Lightning",
        "lightning-rainy": "Lightning, rainy",
        "partlycloudy": "Partly cloudy",
        "pouring": "Pouring",
        "rainy": "Rainy",
        "snowy": "Snowy",
        "snowy-rainy": "Snowy, rainy",
        "sunny": "Sunny",
        "windy": "Windy",
        "windy-variant": "Windy, variant"
      }
    }
  }
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/ha/test_config_flow.py -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 6 passed. Notes: `MockConfigEntry.start_reconfigure_flow(hass)` exists in the 2026.8 test helpers (fallback if the plugin lacks it: `hass.config_entries.flow.async_init(const.DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": hub_entry.entry_id})`); the options flow test passes the full options dict because every threshold field is `Required` in the schema; `hass.config.units.temperature_unit` is `°C` under the default metric test config.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: hub config flow with thresholds, options and reconfigure

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Cover and profile subentry flows

**Files:**
- Modify: `custom_components/cover_automation/config_flow.py`, `custom_components/cover_automation/translations/en.json`
- Test: `tests/ha/test_subentry_flows.py`

**Interfaces:**
- Produces: `CoverSubentryFlow(ConfigSubentryFlow)` with `async_step_user` and `async_step_reconfigure`; `ProfileSubentryFlow(ConfigSubentryFlow)` with the same two steps; `async_get_supported_subentry_types` returning `{"cover": CoverSubentryFlow, "profile": ProfileSubentryFlow}`; helpers `cover_schema(hass, entry, defaults, *, editing_subentry_id)`, `validate_cover_input(hass, entry, data, editing_subentry_id) -> dict[str, str]`, `profile_schema(defaults)`, `profile_data_from_form(user_input) -> dict`, `form_from_profile_data(data) -> dict`, `validate_profile(data) -> dict[str, str]`.
- Consumes: `config_map.rules_from_data`, `quiet_from_data`, `parse_time`; engine `schedule.validate`, `Profile`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_subentry_flows.py`:
```python
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cover_automation import const
from tests.ha.conftest import cover_subentry_data, profile_subentry_data, set_cover, set_sensor, set_weather

COVER_INPUT = {
    const.CONF_COVER_ENTITY: "cover.bedroom",
    const.CONF_NAME: "Bedroom",
    const.CONF_AZIMUTH: 170,
    const.CONF_TOLERANCE_LEFT: 60,
    const.CONF_TOLERANCE_RIGHT: 60,
    const.CONF_ELEVATION_MIN: 0,
    const.CONF_ELEVATION_MAX: 90,
    const.CONF_SHADING_RULE: "forecast_with_room",
    const.CONF_COMFORT_FLOOR: 21,
    const.CONF_COMFORT_CEILING: 25,
    const.CONF_WIND_ENABLED: True,
    const.CONF_WIND_UPPER: 60,
    const.CONF_WIND_LOWER: 50,
    const.CONF_WIND_HOLD: 15,
    const.CONF_WIND_ACTION: "open",
    const.CONF_SCHEDULE_PROFILE: const.PROFILE_NONE,
    const.CONF_MIN_MOVE_INTERVAL: 10,
    const.CONF_CONFIRM_WINDOW: 120,
}


async def start(hass: HomeAssistant, entry, kind: str):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": config_entries.SOURCE_USER}
    )


async def test_cover_subentry_created_with_units_and_profile_none(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    set_cover(hass, "cover.bedroom", features=3)
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(hub_entry.subentries.values()))
    assert sub.subentry_type == const.SUBENTRY_COVER and sub.title == "Bedroom"
    assert sub.data[const.CONF_TEMPERATURE_UNIT] == "°C" and sub.data[const.CONF_WIND_UNIT] == "km/h"
    assert sub.data[const.CONF_SCHEDULE_PROFILE] == const.PROFILE_NONE


async def test_cover_subentry_validation_errors(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.tiltonly", features=16)  # OPEN_TILT only
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    bad = {**COVER_INPUT, const.CONF_COVER_ENTITY: "cover.tiltonly", const.CONF_COMFORT_FLOOR: 26,
           const.CONF_WIND_LOWER: 70, const.CONF_SHADING_RULE: "room_only", const.CONF_CONFIRM_WINDOW: 5}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        const.CONF_COVER_ENTITY: "cover_unsupported",
        const.CONF_COMFORT_FLOOR: "floor_not_below_ceiling",
        const.CONF_WIND_LOWER: "wind_lower_not_below_upper",
        const.CONF_ROOM_SENSOR: "room_sensor_required",
        const.CONF_CONFIRM_WINDOW: "confirm_window_too_short",
    }


async def test_cover_without_state_is_accepted(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_COVER_ENTITY: "cover.asleep"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_cover_twice_is_rejected(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(hub_entry, config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], COVER_INPUT)
    assert result["errors"] == {const.CONF_COVER_ENTITY: "already_configured"}


async def test_cover_reconfigure_keeps_identity_and_offers_profiles(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night")))
    hass.config_entries.async_add_subentry(hub_entry, config_entries.ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    cover_sub = next(s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER)
    prof_sub = next(s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE)
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_COVER),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": cover_sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_NAME: "Bedroom East", const.CONF_SCHEDULE_PROFILE: prof_sub.subentry_id}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[cover_sub.subentry_id]
    assert updated.title == "Bedroom East" and updated.data[const.CONF_SCHEDULE_PROFILE] == prof_sub.subentry_id


async def test_wind_fields_hidden_without_hub_wind_sensor(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.ha.conftest import WEATHER, hub_options

    set_weather(hass)
    entry = MockConfigEntry(domain=const.DOMAIN, data={const.CONF_WEATHER_ENTITY: WEATHER}, options=hub_options())
    entry.add_to_hass(hass)
    result = await start(hass, entry, const.SUBENTRY_COVER)
    keys = {str(k) for k in result["data_schema"].schema}
    assert const.CONF_WIND_UPPER not in keys and const.CONF_AZIMUTH in keys


async def test_profile_subentry_created_from_rule_sections(hass: HomeAssistant, hub_entry) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Bedroom",
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
            "rule_1": {const.CONF_RULE_ENABLED: True, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_TIME: "21:30:00", const.CONF_RULE_OFFSET: 0},
            "rule_2": {const.CONF_RULE_ENABLED: True, const.CONF_RULE_ACTION: "open", const.CONF_RULE_TIME_MODE: "sunrise", const.CONF_RULE_OFFSET: 30, const.CONF_RULE_EARLIEST: "07:00:00"},
            "rule_3": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
            "rule_4": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(hub_entry.subentries.values()))
    assert sub.title == "Bedroom" and len(sub.data[const.CONF_RULES]) == 2
    assert sub.data[const.CONF_RULES][0] == {const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed",
                                             const.CONF_RULE_TIME: "21:30:00", const.CONF_RULE_OFFSET: 0,
                                             const.CONF_RULE_EARLIEST: None, const.CONF_RULE_LATEST: None}
    assert sub.data[const.CONF_RULES][1][const.CONF_RULE_EARLIEST] == "07:00:00"
    assert (sub.data[const.CONF_QUIET_START], sub.data[const.CONF_QUIET_END]) == ("22:00:00", "07:00:00")


async def test_profile_validation_uses_engine(hass: HomeAssistant, hub_entry) -> None:
    result = await start(hass, hub_entry, const.SUBENTRY_PROFILE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Bad",
            const.CONF_QUIET_START: "22:00:00",
            const.CONF_QUIET_END: "07:00:00",
            "rule_1": {const.CONF_RULE_ENABLED: True, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_TIME: "23:00:00", const.CONF_RULE_OFFSET: 0},
            "rule_2": {const.CONF_RULE_ENABLED: True, const.CONF_RULE_ACTION: "open", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
            "rule_3": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
            "rule_4": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_rules"}
    assert "rule 1 fires inside quiet hours" in result["description_placeholders"]["problems"]
    assert "rule 2 needs a time" in result["description_placeholders"]["problems"]


async def test_profile_reconfigure_prefills_and_updates(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(hub_entry, config_entries.ConfigSubentry(**profile_subentry_data("Night")))
    sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_PROFILE),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"]
    suggested = {str(k): k.description.get("suggested_value") for k in schema.schema if getattr(k, "description", None)}
    assert suggested[const.CONF_NAME] == "Night"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_NAME: "Night 2",
            "rule_1": {const.CONF_RULE_ENABLED: True, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "sunset", const.CONF_RULE_OFFSET: 15},
            "rule_2": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
            "rule_3": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
            "rule_4": {const.CONF_RULE_ENABLED: False, const.CONF_RULE_ACTION: "closed", const.CONF_RULE_TIME_MODE: "fixed", const.CONF_RULE_OFFSET: 0},
        },
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    updated = hub_entry.subentries[sub.subentry_id]
    assert updated.title == "Night 2" and updated.data[const.CONF_RULES][0][const.CONF_RULE_TIME_MODE] == "sunset"
    assert const.CONF_QUIET_START not in updated.data  # cleared quiet hours are dropped
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_subentry_flows.py -v`
Expected: FAIL (`UnknownSubEntry`/`UnknownHandler` because no subentry types are registered).

- [ ] **Step 3: Write the implementation**

Append to `custom_components/cover_automation/config_flow.py` (and add the imports shown to the top import block):
```python
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.config_entries import ConfigSubentry, SubentryFlowResult
from homeassistant.data_entry_flow import section

from .config_map import parse_time, quiet_from_data, rules_from_data
from .engine.model import ShadingRule, Target, WindAction
from .engine.schedule import Profile, TimeMode, validate as validate_rules


# --- cover subentry -------------------------------------------------------------------

_SHADING_RULES = [r.value for r in ShadingRule]
_WIND_ACTIONS = [a.value for a in WindAction]


def _select(options: list[str], translation_key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        {"options": options, "mode": selector.SelectSelectorMode.DROPDOWN, "translation_key": translation_key}
    )


def cover_schema(hass: HomeAssistant, entry: ConfigEntry, defaults: Mapping[str, Any]) -> vol.Schema:
    d = defaults
    hub_has_wind = bool(entry.data.get(const.CONF_WIND_SENSOR))
    unit = str(hass.config.units.temperature_unit)
    profiles = entry.get_subentries_of_type(const.SUBENTRY_PROFILE)
    profile_options: list[selector.SelectOptionDict] = [{"value": const.PROFILE_NONE, "label": "—"}]
    profile_options += [{"value": p.subentry_id, "label": p.title} for p in profiles]

    def dflt(key: str, fallback: Any) -> Any:
        return d.get(key, fallback)

    fields: dict[Any, Any] = {
        vol.Required(const.CONF_COVER_ENTITY, default=dflt(const.CONF_COVER_ENTITY, vol.UNDEFINED)): _entity("cover"),
        vol.Optional(const.CONF_NAME, description={"suggested_value": d.get(const.CONF_NAME)}): selector.TextSelector(),
        vol.Required(const.CONF_AZIMUTH, default=dflt(const.CONF_AZIMUTH, 180)): _number(0, 359, 1, "°"),
        vol.Required(const.CONF_TOLERANCE_LEFT, default=dflt(const.CONF_TOLERANCE_LEFT, const.DEFAULT_TOLERANCE_LEFT)): _number(0, 180, 1, "°"),
        vol.Required(const.CONF_TOLERANCE_RIGHT, default=dflt(const.CONF_TOLERANCE_RIGHT, const.DEFAULT_TOLERANCE_RIGHT)): _number(0, 180, 1, "°"),
        vol.Required(const.CONF_ELEVATION_MIN, default=dflt(const.CONF_ELEVATION_MIN, const.DEFAULT_ELEVATION_MIN)): _number(0, 90, 1, "°"),
        vol.Required(const.CONF_ELEVATION_MAX, default=dflt(const.CONF_ELEVATION_MAX, const.DEFAULT_ELEVATION_MAX)): _number(0, 90, 1, "°"),
        vol.Required(const.CONF_SHADING_RULE, default=dflt(const.CONF_SHADING_RULE, ShadingRule.FORECAST_WITH_ROOM.value)): _select(_SHADING_RULES, "shading_rule"),
        vol.Optional(const.CONF_ROOM_SENSOR, description={"suggested_value": d.get(const.CONF_ROOM_SENSOR)}): _entity("sensor", device_class="temperature"),
        vol.Required(const.CONF_COMFORT_FLOOR, default=dflt(const.CONF_COMFORT_FLOOR, const.DEFAULT_COMFORT_FLOOR)): _number(-10, 40, 0.5, unit),
        vol.Required(const.CONF_COMFORT_CEILING, default=dflt(const.CONF_COMFORT_CEILING, const.DEFAULT_COMFORT_CEILING)): _number(-10, 40, 0.5, unit),
        vol.Optional(const.CONF_DOOR_SENSOR, description={"suggested_value": d.get(const.CONF_DOOR_SENSOR)}): _entity("binary_sensor"),
    }
    if hub_has_wind:
        wind_state = hass.states.get(str(entry.data[const.CONF_WIND_SENSOR]))
        wind_unit = str(wind_state.attributes.get("unit_of_measurement", "")) if wind_state else ""
        fields.update(
            {
                vol.Required(const.CONF_WIND_ENABLED, default=dflt(const.CONF_WIND_ENABLED, False)): selector.BooleanSelector(),
                vol.Required(const.CONF_WIND_UPPER, default=dflt(const.CONF_WIND_UPPER, 60)): _number(0, 300, 1, wind_unit or None),
                vol.Required(const.CONF_WIND_LOWER, default=dflt(const.CONF_WIND_LOWER, 50)): _number(0, 300, 1, wind_unit or None),
                vol.Required(const.CONF_WIND_HOLD, default=dflt(const.CONF_WIND_HOLD, const.DEFAULT_WIND_HOLD_MIN)): _number(0, 240, 1, "min"),
                vol.Required(const.CONF_WIND_ACTION, default=dflt(const.CONF_WIND_ACTION, WindAction.OPEN.value)): _select(_WIND_ACTIONS, "wind_action"),
            }
        )
    fields.update(
        {
            vol.Required(const.CONF_SCHEDULE_PROFILE, default=dflt(const.CONF_SCHEDULE_PROFILE, const.PROFILE_NONE)): selector.SelectSelector(
                {"options": profile_options, "mode": selector.SelectSelectorMode.DROPDOWN}
            ),
            vol.Required(const.CONF_MIN_MOVE_INTERVAL, default=dflt(const.CONF_MIN_MOVE_INTERVAL, const.DEFAULT_MIN_MOVE_INTERVAL_MIN)): _number(0, 240, 1, "min"),
            vol.Required(const.CONF_CONFIRM_WINDOW, default=dflt(const.CONF_CONFIRM_WINDOW, const.DEFAULT_CONFIRM_WINDOW_S)): _number(10, 900, 5, "s"),
        }
    )
    return vol.Schema(fields)


def validate_cover_input(
    hass: HomeAssistant, entry: ConfigEntry, data: Mapping[str, Any], editing_subentry_id: str | None
) -> dict[str, str]:
    errors: dict[str, str] = {}
    cover_entity = str(data[const.CONF_COVER_ENTITY])
    for sub in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        if sub.subentry_id != editing_subentry_id and sub.data.get(const.CONF_COVER_ENTITY) == cover_entity:
            errors[const.CONF_COVER_ENTITY] = "already_configured"
    state = hass.states.get(cover_entity)
    if state is not None and state.state not in ("unavailable", "unknown") and const.CONF_COVER_ENTITY not in errors:
        features = int(state.attributes.get("supported_features", 0) or 0)
        open_close = features & CoverEntityFeature.OPEN and features & CoverEntityFeature.CLOSE
        if not (open_close or features & CoverEntityFeature.SET_POSITION):
            errors[const.CONF_COVER_ENTITY] = "cover_unsupported"
    if float(data[const.CONF_COMFORT_FLOOR]) >= float(data[const.CONF_COMFORT_CEILING]):
        errors[const.CONF_COMFORT_FLOOR] = "floor_not_below_ceiling"
    if data.get(const.CONF_WIND_ENABLED) and float(data.get(const.CONF_WIND_LOWER, 0)) >= float(data.get(const.CONF_WIND_UPPER, 0)):
        errors[const.CONF_WIND_LOWER] = "wind_lower_not_below_upper"
    if data[const.CONF_SHADING_RULE] == ShadingRule.ROOM_ONLY.value and not data.get(const.CONF_ROOM_SENSOR):
        errors[const.CONF_ROOM_SENSOR] = "room_sensor_required"
    if int(data[const.CONF_CONFIRM_WINDOW]) < const.MIN_CONFIRM_WINDOW_S:
        errors[const.CONF_CONFIRM_WINDOW] = "confirm_window_too_short"
    return errors


class CoverSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one cover."""

    async def _step(self, user_input: dict[str, Any] | None, *, reconfigure: bool) -> SubentryFlowResult:
        entry = self._get_entry()
        current: ConfigSubentry | None = self._get_reconfigure_subentry() if reconfigure else None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_cover_input(self.hass, entry, user_input, current.subentry_id if current else None)
            if not errors:
                data = _clean_optional_entities(user_input, (const.CONF_ROOM_SENSOR, const.CONF_DOOR_SENSOR))
                cover_state = self.hass.states.get(str(data[const.CONF_COVER_ENTITY]))
                friendly = cover_state.name if cover_state else str(data[const.CONF_COVER_ENTITY])
                title = str(data.get(const.CONF_NAME) or friendly)
                data[const.CONF_NAME] = title
                data[const.CONF_TEMPERATURE_UNIT] = str(self.hass.config.units.temperature_unit)
                wind_sensor = entry.data.get(const.CONF_WIND_SENSOR)
                wind_state = self.hass.states.get(str(wind_sensor)) if wind_sensor else None
                if wind_state is not None and wind_state.attributes.get("unit_of_measurement"):
                    data[const.CONF_WIND_UNIT] = str(wind_state.attributes["unit_of_measurement"])
                if current is not None:
                    return self.async_update_and_abort(entry, current, title=title, data=data)
                return self.async_create_entry(title=title, data=data)
        defaults: Mapping[str, Any] = user_input or (current.data if current else {})
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=cover_schema(self.hass, entry, defaults),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=True)


# --- profile subentry -----------------------------------------------------------------

_RULE_KEYS = tuple(f"rule_{i}" for i in range(1, const.MAX_RULES + 1))
_TIME_MODES = [m.value for m in TimeMode]
_ACTIONS = [Target.CLOSED.value, Target.OPEN.value]


def _rule_section(defaults: Mapping[str, Any]) -> section:
    d = defaults
    return section(
        vol.Schema(
            {
                vol.Required(const.CONF_RULE_ENABLED, default=d.get(const.CONF_RULE_ENABLED, False)): selector.BooleanSelector(),
                vol.Required(const.CONF_RULE_ACTION, default=d.get(const.CONF_RULE_ACTION, Target.CLOSED.value)): _select(_ACTIONS, "rule_action"),
                vol.Required(const.CONF_RULE_TIME_MODE, default=d.get(const.CONF_RULE_TIME_MODE, TimeMode.FIXED.value)): _select(_TIME_MODES, "time_mode"),
                vol.Optional(const.CONF_RULE_TIME, description={"suggested_value": d.get(const.CONF_RULE_TIME)}): selector.TimeSelector(),
                vol.Required(const.CONF_RULE_OFFSET, default=d.get(const.CONF_RULE_OFFSET, 0)): _number(-720, 720, 1, "min"),
                vol.Optional(const.CONF_RULE_EARLIEST, description={"suggested_value": d.get(const.CONF_RULE_EARLIEST)}): selector.TimeSelector(),
                vol.Optional(const.CONF_RULE_LATEST, description={"suggested_value": d.get(const.CONF_RULE_LATEST)}): selector.TimeSelector(),
            }
        ),
        {"collapsed": not bool(d.get(const.CONF_RULE_ENABLED, False))},
    )


def form_from_profile_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Stored profile data → form values (one section per rule slot)."""
    form: dict[str, Any] = {
        const.CONF_NAME: data.get(const.CONF_NAME),
        const.CONF_QUIET_START: data.get(const.CONF_QUIET_START),
        const.CONF_QUIET_END: data.get(const.CONF_QUIET_END),
    }
    rules = list(data.get(const.CONF_RULES, []))
    for index, key in enumerate(_RULE_KEYS):
        if index < len(rules):
            form[key] = {const.CONF_RULE_ENABLED: True, **rules[index]}
        else:
            form[key] = {const.CONF_RULE_ENABLED: False}
    return form


def profile_schema(form: Mapping[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(const.CONF_NAME, description={"suggested_value": form.get(const.CONF_NAME)}): selector.TextSelector(),
        vol.Optional(const.CONF_QUIET_START, description={"suggested_value": form.get(const.CONF_QUIET_START)}): selector.TimeSelector(),
        vol.Optional(const.CONF_QUIET_END, description={"suggested_value": form.get(const.CONF_QUIET_END)}): selector.TimeSelector(),
    }
    for key in _RULE_KEYS:
        fields[vol.Required(key)] = _rule_section(form.get(key) or {})
    return vol.Schema(fields)


def profile_data_from_form(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Form values → stored profile data: only enabled rule slots are kept, in slot order."""
    rules: list[dict[str, Any]] = []
    for key in _RULE_KEYS:
        raw = user_input.get(key) or {}
        if not raw.get(const.CONF_RULE_ENABLED):
            continue
        rules.append(
            {
                const.CONF_RULE_ACTION: raw[const.CONF_RULE_ACTION],
                const.CONF_RULE_TIME_MODE: raw[const.CONF_RULE_TIME_MODE],
                const.CONF_RULE_TIME: raw.get(const.CONF_RULE_TIME) or None,
                const.CONF_RULE_OFFSET: int(raw.get(const.CONF_RULE_OFFSET, 0) or 0),
                const.CONF_RULE_EARLIEST: raw.get(const.CONF_RULE_EARLIEST) or None,
                const.CONF_RULE_LATEST: raw.get(const.CONF_RULE_LATEST) or None,
            }
        )
    data: dict[str, Any] = {const.CONF_NAME: str(user_input[const.CONF_NAME]).strip(), const.CONF_RULES: rules}
    start, end = user_input.get(const.CONF_QUIET_START), user_input.get(const.CONF_QUIET_END)
    if start and end:
        data[const.CONF_QUIET_START], data[const.CONF_QUIET_END] = str(start), str(end)
    return data


def validate_profile(data: Mapping[str, Any]) -> list[str]:
    """Problems as human-readable strings (engine.schedule.validate plus time parsing)."""
    try:
        rules = rules_from_data(data)
        quiet = quiet_from_data(data)
    except ValueError as err:
        return [str(err)]
    problems = validate_rules(Profile(profile_id="draft", name=str(data[const.CONF_NAME]), rules=rules, quiet_hours=quiet))
    start, end = parse_time(data.get(const.CONF_QUIET_START)), parse_time(data.get(const.CONF_QUIET_END))
    if (start is None) != (end is None):
        problems.append("quiet hours need both a start and an end")
    return problems


class ProfileSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one schedule profile."""

    async def _step(self, user_input: dict[str, Any] | None, *, reconfigure: bool) -> SubentryFlowResult:
        entry = self._get_entry()
        current: ConfigSubentry | None = self._get_reconfigure_subentry() if reconfigure else None
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"problems": ""}
        if user_input is not None:
            data = profile_data_from_form(user_input)
            problems = validate_profile(data)
            if problems:
                errors["base"] = "invalid_rules"
                placeholders["problems"] = "; ".join(problems)
            else:
                title = data[const.CONF_NAME]
                if current is not None:
                    return self.async_update_and_abort(entry, current, title=title, data=data)
                return self.async_create_entry(title=title, data=data)
        form = user_input or form_from_profile_data(current.data if current else {})
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=profile_schema(form),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=False)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step(user_input, reconfigure=True)
```
Then change `CoverAutomationConfigFlow.async_get_supported_subentry_types` to:
```python
        return {const.SUBENTRY_COVER: CoverSubentryFlow, const.SUBENTRY_PROFILE: ProfileSubentryFlow}
```

Add to `translations/en.json` a top-level `config_subentries` object:
```json
  "config_subentries": {
    "cover": {
      "abort": {"reconfigure_successful": "Cover updated."},
      "entry_type": "Cover",
      "error": {
        "already_configured": "This cover is already configured.",
        "confirm_window_too_short": "The confirm window must be at least 10 seconds.",
        "cover_unsupported": "This cover supports neither open/close nor position commands.",
        "floor_not_below_ceiling": "The comfort floor must be below the comfort ceiling.",
        "room_sensor_required": "The room-only rule needs a room temperature sensor.",
        "wind_lower_not_below_upper": "The wind release threshold must be below the activation threshold."
      },
      "initiate_flow": {"user": "Add cover"},
      "step": {
        "reconfigure": {
          "data": {
            "azimuth": "Window azimuth", "comfort_ceiling": "Comfort ceiling", "comfort_floor": "Comfort floor",
            "confirm_window": "Confirm window", "cover_entity": "Cover", "door_sensor": "Door sensor",
            "elevation_max": "Sun elevation: maximum", "elevation_min": "Sun elevation: minimum",
            "min_move_interval": "Minimum interval between shading moves", "name": "Name",
            "room_temperature_sensor": "Room temperature sensor", "schedule_profile": "Schedule profile",
            "shading_rule": "Shading rule", "tolerance_left": "Sun tolerance: left", "tolerance_right": "Sun tolerance: right",
            "wind_action": "Wind protection action", "wind_enabled": "Wind protection", "wind_hold": "Wind hold time",
            "wind_lower": "Wind release threshold", "wind_upper": "Wind activation threshold"
          },
          "title": "Cover"
        },
        "user": {
          "data": {
            "azimuth": "Window azimuth", "comfort_ceiling": "Comfort ceiling", "comfort_floor": "Comfort floor",
            "confirm_window": "Confirm window", "cover_entity": "Cover", "door_sensor": "Door sensor",
            "elevation_max": "Sun elevation: maximum", "elevation_min": "Sun elevation: minimum",
            "min_move_interval": "Minimum interval between shading moves", "name": "Name",
            "room_temperature_sensor": "Room temperature sensor", "schedule_profile": "Schedule profile",
            "shading_rule": "Shading rule", "tolerance_left": "Sun tolerance: left", "tolerance_right": "Sun tolerance: right",
            "wind_action": "Wind protection action", "wind_enabled": "Wind protection", "wind_hold": "Wind hold time",
            "wind_lower": "Wind release threshold", "wind_upper": "Wind activation threshold"
          },
          "data_description": {
            "azimuth": "Compass direction the window faces, 0–359°.",
            "confirm_window": "How long a commanded move may take before it counts as unconfirmed. Must exceed the cover's travel time.",
            "door_sensor": "While this door is open the cover never closes.",
            "shading_rule": "How forecast and room temperature combine to decide shading."
          },
          "title": "Cover"
        }
      }
    },
    "profile": {
      "abort": {"reconfigure_successful": "Schedule profile updated."},
      "entry_type": "Schedule profile",
      "error": {"invalid_rules": "The rules are not valid: {problems}"},
      "initiate_flow": {"user": "Add schedule profile"},
      "step": {
        "reconfigure": {
          "data": {"name": "Name", "quiet_end": "Quiet hours end", "quiet_start": "Quiet hours start"},
          "sections": {
            "rule_1": {"name": "Rule 1", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_2": {"name": "Rule 2", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_3": {"name": "Rule 3", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_4": {"name": "Rule 4", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}}
          },
          "title": "Schedule profile"
        },
        "user": {
          "data": {"name": "Name", "quiet_end": "Quiet hours end", "quiet_start": "Quiet hours start"},
          "data_description": {"quiet_start": "Between start and end no shading or schedule move happens; wind protection still acts."},
          "sections": {
            "rule_1": {"name": "Rule 1", "description": "A close rule holds until the next rule or a manual move; an open rule opens once.", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_2": {"name": "Rule 2", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_3": {"name": "Rule 3", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}},
            "rule_4": {"name": "Rule 4", "data": {"action": "Action", "earliest": "Not before", "enabled": "Enabled", "latest": "Not after", "offset_minutes": "Offset (minutes)", "time": "Time", "time_mode": "Time mode"}}
          },
          "title": "Schedule profile"
        }
      }
    }
  },
```
and extend the `selector` object with:
```json
    "rule_action": {"options": {"closed": "Close", "open": "Open"}},
    "shading_rule": {"options": {"either": "Forecast or room temperature", "forecast_with_room": "Forecast, refined by room temperature", "room_only": "Room temperature only"}},
    "time_mode": {"options": {"fixed": "Fixed time", "sunrise": "Relative to sunrise", "sunset": "Relative to sunset"}},
    "wind_action": {"options": {"hold": "Hold position", "open": "Force open"}}
```
Keep the JSON valid (commas) and keys sorted within each object; ruff does not check JSON, but hassfest does.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/ha/test_subentry_flows.py tests/ha/test_config_flow.py -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 15 passed. If `hass.config_entries.async_add_subentry` refuses a `ConfigSubentry` built from `ConfigSubentryData` in the tests, build it with `ConfigSubentry(data=MappingProxyType(d["data"]), subentry_type=d["subentry_type"], title=d["title"], unique_id=None)` in the conftest helper instead and update both helpers accordingly (report the change).

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: cover and schedule-profile subentry flows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Integration setup, devices, Store wiring, reload

**Files:**
- Modify: `custom_components/cover_automation/__init__.py`
- Test: `tests/ha/test_init.py`

**Interfaces:**
- Produces: `@dataclass CoverAutomationData` (`hub: HubConfig`, `covers: dict[str, tuple[CoverConfig, CoverBindings]]`, `profiles: dict[str, Profile]`, `store: CoverAutomationStore`, `hub_device_id: str`); `type CoverAutomationConfigEntry = ConfigEntry[CoverAutomationData]`; `async_setup_entry`, `async_unload_entry`, `async_remove_entry`, `async_migrate_entry`, `async_remove_config_entry_device`; `ensure_devices(hass, entry) -> str` (hub device id).
- Consumes: Tasks 2–5.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_init.py`:
```python
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from custom_components.cover_automation import const
from custom_components.cover_automation.engine.model import Owner
from tests.ha.conftest import cover_subentry_data, profile_subentry_data, set_cover, set_sensor, set_sun, set_weather


async def setup_hub(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_sun(hass)
    set_sensor(hass, "sensor.wind", 5, unit="km/h", device_class="wind_speed")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_hub_and_cover_devices(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**profile_subentry_data("Night")))
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    assert hub_entry.state is ConfigEntryState.LOADED

    registry = dr.async_get(hass)
    hub_device = registry.async_get_device_by_identifier((const.DOMAIN, hub_entry.entry_id), hub_entry.entry_id)
    assert hub_device is not None and hub_device.config_subentry_id is None
    cover_sub = next(s for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER)
    cover_device = registry.async_get_device_by_identifier((const.DOMAIN, cover_sub.subentry_id), hub_entry.entry_id)
    assert cover_device is not None
    assert cover_device.config_subentry_id == cover_sub.subentry_id
    assert cover_device.via_device_id == hub_device.id and cover_device.name == "Bedroom"

    data = hub_entry.runtime_data
    assert data.hub.weather_entity == "weather.home"
    assert set(data.covers) == {cover_sub.subentry_id}
    assert len(data.profiles) == 1
    assert data.hub_device_id == hub_device.id


async def test_setup_not_ready_without_weather_or_sun(hass: HomeAssistant, hub_entry) -> None:
    set_sun(hass)
    assert not await hass.config_entries.async_setup(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY
    set_weather(hass)
    hass.states.async_remove("sun.sun")
    await hass.config_entries.async_reload(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY


async def test_missing_optional_sensor_raises_repair_but_loads(hass: HomeAssistant, hub_entry) -> None:
    set_weather(hass)
    set_sun(hass)  # no sensor.wind state
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(const.DOMAIN, f"missing_entity_{hub_entry.entry_id}_sensor.wind")
    assert issue is not None and issue.translation_key == "missing_entity"


async def test_store_is_loaded_and_saved_on_unload(hass: HomeAssistant, hub_entry, hass_storage) -> None:
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    data = hub_entry.runtime_data
    sub_id = next(iter(data.covers))
    data.store.data.covers[sub_id].owner = Owner.USER
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.NOT_LOADED
    assert hass_storage[const.storage_key(hub_entry.entry_id)]["data"]["covers"][sub_id]["owner"] == "user"


async def test_subentry_change_reloads_entry(hass: HomeAssistant, hub_entry) -> None:
    await setup_hub(hass, hub_entry)
    assert hub_entry.runtime_data.covers == {}
    set_cover(hass, "cover.bedroom")
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    assert len(hub_entry.runtime_data.covers) == 1


async def test_options_change_reloads_entry(hass: HomeAssistant, hub_entry) -> None:
    await setup_hub(hass, hub_entry)
    hass.config_entries.async_update_entry(hub_entry, options={**hub_entry.options, const.CONF_HOT_HIGH: 30.0})
    await hass.async_block_till_done()
    assert hub_entry.runtime_data.hub.hot_high == 30.0


async def test_remove_entry_deletes_store(hass: HomeAssistant, hub_entry, hass_storage) -> None:
    await setup_hub(hass, hub_entry)
    key = const.storage_key(hub_entry.entry_id)
    await hub_entry.runtime_data.store.async_save()
    assert key in hass_storage
    await hass.config_entries.async_remove(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_stale_cover_device_can_be_removed(hass: HomeAssistant, hub_entry) -> None:
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    set_cover(hass, "cover.bedroom")
    await setup_hub(hass, hub_entry)
    from custom_components.cover_automation import async_remove_config_entry_device

    registry = dr.async_get(hass)
    hub_device = registry.async_get_device_by_identifier((const.DOMAIN, hub_entry.entry_id), hub_entry.entry_id)
    cover_sub = next(iter(hub_entry.subentries.values()))
    cover_device = registry.async_get_device_by_identifier((const.DOMAIN, cover_sub.subentry_id), hub_entry.entry_id)
    assert hub_device is not None and cover_device is not None
    assert not await async_remove_config_entry_device(hass, hub_entry, hub_device)
    assert not await async_remove_config_entry_device(hass, hub_entry, cover_device)
    stale = registry.async_get_or_create(config_entry_id=hub_entry.entry_id, identifiers={(const.DOMAIN, "gone")})
    assert await async_remove_config_entry_device(hass, hub_entry, stale)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_init.py -v`
Expected: FAIL (the empty `__init__.py` has no `async_setup_entry`, so setup fails / attributes missing).

- [ ] **Step 3: Write the implementation**

`custom_components/cover_automation/__init__.py`:
```python
"""Cover Automation: drives roller covers open or closed from sun, weather, room temperature,
wind, frost, door sensors and schedules. Setup order per spec §5."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.weather import WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from . import const
from .config_map import CoverBindings, HubConfig, cover_config, hub_config, profile
from .engine.model import CoverConfig, CoverPersisted
from .engine.schedule import Profile
from .store import CoverAutomationStore

_LOGGER = logging.getLogger(__name__)
SUN_ENTITY = "sun.sun"


@dataclass(slots=True)
class CoverAutomationData:
    hub: HubConfig
    covers: dict[str, tuple[CoverConfig, CoverBindings]]
    profiles: dict[str, Profile]
    store: CoverAutomationStore
    hub_device_id: str
    missing_entities: list[str] = field(default_factory=list)


type CoverAutomationConfigEntry = ConfigEntry[CoverAutomationData]


def _missing_entity_issue_id(entry: ConfigEntry, entity_id: str) -> str:
    return f"missing_entity_{entry.entry_id}_{entity_id}"


def _validate_required_entities(hass: HomeAssistant, hub: HubConfig) -> None:
    """Weather (with daily forecast) and sun.sun must exist, otherwise retry (spec §5)."""
    weather = hass.states.get(hub.weather_entity)
    if weather is None or weather.state in ("unavailable", "unknown"):
        raise ConfigEntryNotReady(f"weather entity {hub.weather_entity} is not available yet")
    features = int(weather.attributes.get("supported_features", 0) or 0)
    if not features & WeatherEntityFeature.FORECAST_DAILY:
        raise ConfigEntryNotReady(f"weather entity {hub.weather_entity} reports no daily forecast yet")
    if hass.states.get(SUN_ENTITY) is None:
        raise ConfigEntryNotReady("sun.sun is not available yet")


def _check_optional_entities(
    hass: HomeAssistant, entry: ConfigEntry, hub: HubConfig, covers: dict[str, tuple[CoverConfig, CoverBindings]]
) -> list[str]:
    """Missing optional sensors do not block setup; they raise repair issues (spec §5)."""
    wanted: list[str] = [e for e in (hub.wind_sensor, hub.outdoor_temperature_sensor, hub.sunny_override_entity, hub.hot_override_entity) if e]
    for _cfg, bind in covers.values():
        wanted.extend(e for e in (bind.cover_entity, bind.door_sensor, bind.room_sensor) if e)
    missing = [e for e in wanted if hass.states.get(e) is None]
    for entity_id in wanted:
        issue_id = _missing_entity_issue_id(entry, entity_id)
        if entity_id in missing:
            ir.async_create_issue(
                hass,
                const.DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, issue_id)
    return missing


@callback
def ensure_devices(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Create the hub device and one device per cover subentry (2026.8 rules). Returns the hub device id."""
    registry = dr.async_get(hass)
    hub_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
        identifiers={(const.DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Cover Automation",
        model="Hub",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(const.DOMAIN, subentry.subentry_id)},
            name=str(subentry.data.get(const.CONF_NAME) or subentry.title),
            manufacturer="Cover Automation",
            model="Cover",
            via_device_id=hub_device.id,
        )
    return hub_device.id


async def async_setup_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    hub = hub_config(entry)
    _validate_required_entities(hass, hub)

    profiles = {s.subentry_id: profile(s) for s in entry.get_subentries_of_type(const.SUBENTRY_PROFILE)}
    covers: dict[str, tuple[CoverConfig, CoverBindings]] = {}
    for subentry in entry.get_subentries_of_type(const.SUBENTRY_COVER):
        cfg, bind = cover_config(subentry, hub)
        if cfg.profile_id is not None and cfg.profile_id not in profiles:
            _LOGGER.warning("Cover %s references missing profile %s; treating as none", cfg.name, cfg.profile_id)
            ir.async_create_issue(
                hass, const.DOMAIN, f"missing_profile_{subentry.subentry_id}", is_fixable=False,
                severity=ir.IssueSeverity.WARNING, translation_key="missing_profile",
                translation_placeholders={"cover": cfg.name},
            )
        else:
            ir.async_delete_issue(hass, const.DOMAIN, f"missing_profile_{subentry.subentry_id}")
        covers[subentry.subentry_id] = (cfg, bind)

    hub_device_id = ensure_devices(hass, entry)
    store = CoverAutomationStore(hass, entry.entry_id)
    await store.async_load()
    for subentry_id in covers:
        store.data.covers.setdefault(subentry_id, CoverPersisted())

    entry.runtime_data = CoverAutomationData(
        hub=hub, covers=covers, profiles=profiles, store=store, hub_device_id=hub_device_id,
        missing_entities=_check_optional_entities(hass, entry, hub, covers),
    )

    await hass.config_entries.async_forward_entry_setups(entry, const.PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hub options and subentry changes all arrive here; a reload rebuilds runtime_data (spec §3)."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CoverAutomationConfigEntry) -> bool:
    await entry.runtime_data.store.async_save()
    return await hass.config_entries.async_unload_platforms(entry, const.PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await CoverAutomationStore(hass, entry.entry_id).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 1:
        return False  # downgrade from a newer major version is not supported
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting devices whose subentry no longer exists (spec §5)."""
    if device.config_subentry_id is None:
        return (const.DOMAIN, entry.entry_id) not in device.identifiers
    return device.config_subentry_id not in entry.subentries
```
Add to `translations/en.json` a top-level `issues` object:
```json
  "issues": {
    "missing_entity": {
      "description": "The entity {entity_id} referenced by Cover Automation does not exist. Fix the configuration or restore the entity.",
      "title": "Configured entity is missing"
    },
    "missing_profile": {
      "description": "Cover {cover} references a schedule profile that was deleted. It behaves as if it had no profile until you pick another one.",
      "title": "Schedule profile is missing"
    }
  }
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/ha -v && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: all HA tests pass (8 in test_init.py). Note: `test_setup_not_ready_without_weather_or_sun` first calls `async_setup` without a weather state; `ConfigEntryNotReady` puts the entry into `SETUP_RETRY`. In `test_stale_cover_device_can_be_removed`, the hub device check returns False because its identifier matches the entry.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: integration setup with devices, Store wiring and reload listener

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Translation completeness check and README

**Files:**
- Create: `tests/ha/test_translations.py`, `README.md`
- Modify: `custom_components/cover_automation/translations/en.json` only if the test finds gaps

**Interfaces:**
- Produces: a test guaranteeing every flow field, error, abort reason, selector option and issue key used in code has an English translation; a README with install and configuration steps.

- [ ] **Step 1: Write the failing test**

`tests/ha/test_translations.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from custom_components.cover_automation import const
from custom_components.cover_automation.config_flow import (
    _HUB_OPTION_KEYS,
    cover_schema,
    hub_data_schema,
    profile_schema,
    thresholds_schema,
)

TRANSLATIONS = json.loads(Path("custom_components/cover_automation/translations/en.json").read_text())


def keys_of(schema) -> set[str]:
    return {str(k) for k in schema.schema}


def test_hub_flow_fields_are_translated(hass) -> None:
    for step, schema in (("user", hub_data_schema()), ("reconfigure", hub_data_schema()),
                         ("thresholds", thresholds_schema({}, "°C"))):
        translated = set(TRANSLATIONS["config"]["step"][step]["data"])
        assert keys_of(schema) <= translated, f"{step}: {keys_of(schema) - translated}"
    assert set(_HUB_OPTION_KEYS) <= set(TRANSLATIONS["options"]["step"]["init"]["data"])


def test_flow_errors_and_aborts_are_translated() -> None:
    assert {"entity_not_found", "weather_no_daily"} <= set(TRANSLATIONS["config"]["error"])
    assert {"single_instance_allowed", "reconfigure_successful"} <= set(TRANSLATIONS["config"]["abort"])
    cover = TRANSLATIONS["config_subentries"]["cover"]
    assert {"already_configured", "cover_unsupported", "floor_not_below_ceiling", "wind_lower_not_below_upper",
            "room_sensor_required", "confirm_window_too_short"} <= set(cover["error"])
    assert "invalid_rules" in TRANSLATIONS["config_subentries"]["profile"]["error"]
    for kind in ("cover", "profile"):
        block = TRANSLATIONS["config_subentries"][kind]
        assert block["entry_type"] and block["initiate_flow"]["user"]
        assert set(block["step"]) >= {"user", "reconfigure"}


def test_cover_subentry_fields_are_translated(hass, hub_entry) -> None:
    schema = cover_schema(hass, hub_entry, {})
    for step in ("user", "reconfigure"):
        translated = set(TRANSLATIONS["config_subentries"]["cover"]["step"][step]["data"])
        assert keys_of(schema) <= translated, f"{step}: {keys_of(schema) - translated}"


def test_profile_subentry_fields_and_sections_are_translated() -> None:
    schema = profile_schema({})
    for step in ("user", "reconfigure"):
        block = TRANSLATIONS["config_subentries"]["profile"]["step"][step]
        top = {k for k in keys_of(schema) if not k.startswith("rule_")}
        assert top <= set(block["data"])
        for i in range(1, const.MAX_RULES + 1):
            sec = block["sections"][f"rule_{i}"]
            assert set(sec["data"]) == {const.CONF_RULE_ENABLED, const.CONF_RULE_ACTION, const.CONF_RULE_TIME_MODE,
                                        const.CONF_RULE_TIME, const.CONF_RULE_OFFSET, const.CONF_RULE_EARLIEST, const.CONF_RULE_LATEST}


def test_selector_options_and_issues_are_translated() -> None:
    sel = TRANSLATIONS["selector"]
    assert set(sel["weather_condition"]["options"]) == set(const.WEATHER_CONDITIONS)
    assert set(sel["shading_rule"]["options"]) == {"forecast_with_room", "room_only", "either"}
    assert set(sel["time_mode"]["options"]) == {"fixed", "sunrise", "sunset"}
    assert set(sel["wind_action"]["options"]) == {"open", "hold"}
    assert set(sel["rule_action"]["options"]) == {"closed", "open"}
    assert {"missing_entity", "missing_profile"} <= set(TRANSLATIONS["issues"])


def test_translation_keys_are_sorted_recursively() -> None:
    def check(obj, path="root"):
        if isinstance(obj, dict):
            assert list(obj) == sorted(obj), f"unsorted keys at {path}"
            for k, v in obj.items():
                check(v, f"{path}.{k}")

    check(TRANSLATIONS)
```

- [ ] **Step 2: Run to verify it fails or passes**

Run: `.venv/bin/pytest tests/ha/test_translations.py -v`
Expected: any failure points at a missing or unsorted key; fix `en.json` until green (sorting: use `python - <<'PY'` with `json.dump(..., sort_keys=True, indent=2, ensure_ascii=False)` to rewrite the file, then re-run).

- [ ] **Step 3: Write the README**

`README.md`:
```markdown
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
```

- [ ] **Step 4: Run everything**

Run: `.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pyright`
Expected: engine + HA tests all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add README.md custom_components tests
git commit -m "test: translation completeness; docs: README for install and configuration

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage (plan 2a scope):** §3 hub entry fields and validation (Tasks 4, 6), cover subentry fields/validation/units (Task 5), profile subentry with four rule sections and quiet hours validated by the engine (Task 5), reload mechanism (Task 6), devices with `via_device_id` (Task 6), entity-reference identity = subentry (Task 5/6), `version`/`minor_version` + migrate hook (Task 6); §5 Store contents for persisted scalars + latch + hub runtime selects and save policy (Task 3), setup validation with `ConfigEntryNotReady`, optional-sensor repairs, unload/remove/remove-device hooks (Task 6); §6 manifest, hacs.json, CI, translations without `strings.json` (Tasks 0, 1, 7). Deferred to plan 2b: everything behavioural (controller, entities, services, logbook, diagnostics, runtime repairs, entity-rename tracking, `async_at_started` first evaluation).
- **Type consistency:** `HubConfig`/`CoverBindings` fields used identically in Tasks 2 and 6; `StoreData` API used in Tasks 3 and 6; `hub_data_schema`, `thresholds_schema`, `cover_schema`, `profile_schema`, `_HUB_OPTION_KEYS` referenced by Task 7 exist with those names in Tasks 4–5; `CoverPersisted`, `DailyLatch`, `ShadingMode`, `ReopeningMode`, `Profile`, `Rule`, `QuietHours`, `TimeMode`, `Target`, `ShadingRule`, `WindAction`, `CoverConfig` match the engine API.
- **Known simplifications:** wind thresholds use the wind sensor's current unit at flow time and store it; conversion at read time is plan 2b. Codeowner/URL values in the manifest are placeholders for the owner's GitHub handle.
