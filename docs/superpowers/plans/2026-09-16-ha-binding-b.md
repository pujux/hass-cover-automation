# HA Binding Part B (behaviour) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cover_automation` actually drive covers: read Home Assistant states into engine signals, run the engine on every trigger, send open/close commands, track pending moves, persist state, expose hub and per-cover entities, services, logbook entries, diagnostics and runtime repair issues, with end-to-end scenario tests.

**Architecture:** The engine (`engine/`, pure Python, unchanged API) stays the single decision maker. New Home Assistant modules around it: `units.py` (unit-safe readings), `forecast.py` (daily forecast fetch), `repairs.py` (issue helpers), `views.py` (read-only snapshots entities render), `entity.py` (entity bases + dispatcher), `signals_adapter.py` (HA states → `HubSignals`/`CoverInputs` via the engine's signal primitives), `scheduler.py` (schedule rule timers, sun times, next event), `controller.py` (subscriptions, timers, evaluate → act → persist loop, commands, pending tracking, repairs, notifications, logbook events, write API for entities/services), five thin entity platforms, `services.py`, `logbook.py`, `diagnostics.py`, and `__init__.py` wiring. Everything works in °C internally; sensors declare `°C` as native unit so HA displays the household unit. Wind readings are converted to each cover's stored `wind_unit`.

**Tech Stack:** Python 3.14, Home Assistant 2026.8.3 via `pytest-homeassistant-custom-component==0.13.357` (asyncio auto mode, `freezer`, `async_fire_time_changed`, `async_mock_service`, `async_capture_events`), voluptuous, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-09-15-cover-automation-design.md` (revision 3.3): §1.3–§1.6 (gate, commands, override lifecycle), §2 (inputs, timers, triggers), §4 (entities, services, logbook, repairs, diagnostics), §5 (persistence, startup order, command failures, unload). Decisions `docs/design-decisions.md` #1–#30. Backlog inputs: `docs/reviews/2026-09-16-ha-binding-a-final-review.md` ("Deferred to plan 2b") and the parked lines of `docs/reviews/2026-09-15-engine-sdd-ledger.md`.

## Global Constraints

- Home Assistant ≥ 2026.8 (tests pin 2026.8.3); Python ≥ 3.14.2; `engine/` keeps zero HA imports and pyright strict; the whole component stays pyright-clean at "standard" and ruff-clean (`ANN`, `RUF`, `B`, `SIM`, `UP`, `I`, line length 100; tests ignore ANN/E501).
- Startup order (spec §5): validate → devices → Store → forward platforms → update listener → controller started with `async_at_started`; first evaluation per cover: `engine.decide()` → `engine.reconcile(actual, decision, now)` → `engine.evaluate()` and act. Covers that are `cover_unavailable` are skipped until they report. The controller must call `engine.on_transition()` before any `evaluate()` that sees a new actual state (engine ledger C2 contract).
- Store saves: `store.async_save()` immediately after `on_command_sent`, `reset`, `reconcile` that changed anything, and a transition classified `MANUAL`; `store.schedule_save()` after every evaluation; immediate on unload.
- Commands (spec §1.3 gate 11): `cover.open_cover`/`cover.close_cover` when the cover advertises OPEN/CLOSE, else `cover.set_cover_position` with 100/0; neither → repair `cover_unsupported` and suppress. Simulated sends: log + logbook event with `simulated: true`, no service call. Service call raising → `engine.on_command_failed(now)` (returns the retry time), status `command_failed`, one retry timer.
- Timers (spec §2): `async_track_time_change(hour=0, minute=0, second=10)` for local midnight; `async_call_later` for every relative timer (per-cover next check, debounce, retry); `async_track_point_in_time` only for the next schedule rule; `async_track_time_interval` for the hourly forecast refresh and the 5-minute fallback tick. Every listener/timer is cancelled on unload; tests must leave no lingering timers.
- Triggers (spec §2): state changes of covers, door sensors, room sensors, wind sensor, outdoor temperature sensor, weather entity, `sun.sun`, override entities; runtime entity writes; the timers above.
- Units: thresholds are stored with the unit they were entered in (`HubConfig.temperature_unit`, `CoverBindings.temperature_unit`, `CoverBindings.wind_unit`). Convert every temperature threshold and reading to °C before the engine sees it (`HubConfig.temperature_unit` and `CoverBindings.temperature_unit` may differ on one install). Convert wind readings to the cover's stored `wind_unit`; a wind sensor whose current unit differs from the stored unit raises `wind_unit_changed` (conversion still applies).
- Entities (spec §4): `has_entity_name = True`; unique ids `f"{entry_id}_{key}"` (hub) and `f"{subentry_id}_{key}"` (cover); per-cover entities added with `config_subentry_id=subentry_id`; entities are views over engine/controller state and write through the controller (decision 17); the cover `status` sensor and `manual_override` binary sensor have **no** entity category; every other entity is CONFIG or DIAGNOSTIC as listed in §4; the status sensor declares a literal `_unrecorded_attributes` frozenset naming every attribute except `desired_state`, `actual_state`, `reason`.
- Services (spec §4): `cover_automation.reset_override`, `cover_automation.evaluate_now`, registered once in `async_setup`, targets resolved with `async_extract_referenced_entity_ids(hass, TargetSelection(call.data))`; no target → all covers; devices map to subentries via `device.config_subentry_id`; entities map via the uncategorised `status`/`manual_override` entities. `services.yaml` must exist with `target:` for hassfest.
- Logbook (spec §4): event `cover_automation_action` with `{entity_id, action, reason, layer, simulated, cover_name}` on every real or simulated send; `logbook.py` describes it attributed to the cover entity.
- Repairs (spec §4): non-fixable, auto-clearing, per-cover issues use `translation_placeholders={"cover": name}`; ids listed in Task 2. Frost conflict additionally raises a persistent notification once per episode.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; one commit per task at least.

## File structure

```
custom_components/cover_automation/units.py             read_float, temperature/speed conversion helpers
custom_components/cover_automation/forecast.py          async_fetch_today via weather.get_forecasts
custom_components/cover_automation/repairs.py           issue ids, set_issue(), runtime prefixes for the stale sweep
custom_components/cover_automation/views.py             HubView, CoverView snapshots; dispatcher signal name
custom_components/cover_automation/entity.py            ControllerProtocol, HubEntity, CoverEntityBase
custom_components/cover_automation/signals_adapter.py   HubSignalSource, CoverSignalSet, classify_state, sun_position
custom_components/cover_automation/scheduler.py         HassSunTimes, ScheduleTracker (rule timers, views, next event)
custom_components/cover_automation/controller.py        CoverAutomationController
custom_components/cover_automation/switch.py            hub simulation_mode/verbose_logging; cover enabled
custom_components/cover_automation/select.py            hub shading_mode/reopening_mode; cover mode
custom_components/cover_automation/button.py            hub evaluate_now; cover reset_override
custom_components/cover_automation/sensor.py            hub forecast_max_today/forecast_min_today/next_scheduled_event; cover status
custom_components/cover_automation/binary_sensor.py     hub hot_day/sunny/frost_active/any_wind_protection_active/problem; cover manual_override/sun_hits/wind_protection_active
custom_components/cover_automation/services.py          register + handlers
custom_components/cover_automation/services.yaml
custom_components/cover_automation/logbook.py
custom_components/cover_automation/diagnostics.py
custom_components/cover_automation/__init__.py          PLATFORMS, controller lifecycle, async_setup (services), stale-sweep prefixes
custom_components/cover_automation/translations/en.json entity names/states, services, new issues
tests/ha/test_units.py, test_forecast.py, test_repairs.py, test_signals_adapter.py, test_scheduler.py,
tests/ha/test_controller.py (unit-level with fakes), test_entities.py, test_services.py, test_logbook.py,
tests/ha/test_diagnostics.py, test_scenarios_ha.py (end-to-end), tests/ha/fakes.py (FakeController)
```

Task order and parallelism (for the executor): wave 1 = Task 0 ‖ Task 1; wave 2 = Task 2 ‖ Task 3 ‖ Task 4; wave 3 = Task 5 ‖ Task 6 ‖ Task 7; wave 4 = Task 8; wave 5 = Task 9. Crossovers: `en.json` (Tasks 0, 2, 6, 7, 8, 9 add disjoint keys — reconcile by JSON union, recursive sort) and `__init__.py` (Task 0 minor edits, Task 2 sweep prefixes, Task 9 wiring — merge in that order).

---

### Task 0: Plan 2a backlog sweep

**Files:**
- Modify: `custom_components/cover_automation/const.py`, `config_flow.py`, `config_map.py`, `store.py`, `__init__.py`, `translations/en.json`; tests `tests/ha/test_config_flow.py`, `test_subentry_flows.py`, `test_init.py`, `test_store.py`, `test_const_manifest.py`

**Interfaces:**
- Produces: `const.DEFAULT_SUNNY_CONDITIONS: tuple[str, ...]`, `const.DEFAULT_AZIMUTH = 180.0`, `const.DEFAULT_WIND_UPPER_KMH = 60.0`, `const.DEFAULT_WIND_LOWER_KMH = 50.0`; `CoverAutomationStore.prune(keep: Iterable[str])`; `store.py` `_async_migrate_func`; `__init__.async_migrate_entry` that advances `minor_version`.

- [ ] **Step 1: Write the failing tests** (add to the existing files)

```python
# tests/ha/test_const_manifest.py — replace the sunny assertion
    assert const.DEFAULT_SUNNY_CONDITIONS == ("sunny", "partlycloudy")
    assert isinstance(const.DEFAULT_SUNNY_CONDITIONS, tuple)

# tests/ha/test_store.py
async def test_prune_drops_records_of_removed_covers(hass, hass_storage):
    store = CoverAutomationStore(hass, "e")
    await store.async_load()
    store.data.covers["keep"] = CoverPersisted()
    store.data.covers["gone"] = CoverPersisted()
    store.prune(keep={"keep"})
    assert set(store.data.covers) == {"keep"}

async def test_store_minor_version_migration_passthrough(hass, hass_storage):
    hass_storage[const.storage_key("e")] = {"version": 1, "minor_version": 0, "key": const.storage_key("e"),
                                            "data": {"covers": {}, "simulation": True}}
    data = await CoverAutomationStore(hass, "e").async_load()
    assert data.simulation is True

# tests/ha/test_init.py
async def test_setup_prunes_store_records_of_removed_covers(hass, hub_entry, hass_storage):
    hass_storage[const.storage_key(hub_entry.entry_id)] = {"version": 1, "minor_version": 1,
        "key": const.storage_key(hub_entry.entry_id), "data": {"covers": {"ghost": CoverPersisted().to_dict()}}}
    await setup_hub(hass, hub_entry)
    assert "ghost" not in hub_entry.runtime_data.store.data.covers

async def test_setup_not_ready_when_weather_unavailable_or_without_daily(hass, hub_entry):
    set_sun(hass)
    hass.states.async_set(WEATHER, "unavailable")
    assert not await hass.config_entries.async_setup(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY
    set_weather(hass, daily=False)
    await hass.config_entries.async_reload(hub_entry.entry_id)
    assert hub_entry.state is ConfigEntryState.SETUP_RETRY

async def test_migrate_entry_advances_minor_version(hass):
    entry = MockConfigEntry(domain=const.DOMAIN, data={const.CONF_WEATHER_ENTITY: WEATHER}, options=hub_options(),
                            version=1, minor_version=0)
    entry.add_to_hass(hass)
    from custom_components.cover_automation import async_migrate_entry
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (1, 1)

# tests/ha/test_config_flow.py
async def test_options_flow_clears_override_entities(hass, hub_entry):
    hass.config_entries.async_update_entry(hub_entry, options={**hub_entry.options, const.CONF_SUNNY_OVERRIDE_ENTITY: "binary_sensor.x"})
    result = await hass.config_entries.options.async_init(hub_entry.entry_id)
    posted = {k: v for k, v in hub_options().items() if k != const.CONF_TEMPERATURE_UNIT}
    result = await hass.config_entries.options.async_configure(result["flow_id"], posted)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert const.CONF_SUNNY_OVERRIDE_ENTITY not in hub_entry.options

# tests/ha/test_subentry_flows.py
async def test_profile_reconfigure_prefills_rules_and_quiet_hours(hass, hub_entry):
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**profile_subentry_data("Night")))
    sub = next(iter(hub_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (hub_entry.entry_id, const.SUBENTRY_PROFILE),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id})
    schema = result["data_schema"].schema
    suggested = {str(k): k.description.get("suggested_value") for k in schema if getattr(k, "description", None)}
    assert suggested[const.CONF_QUIET_START] == "22:00:00" and suggested[const.CONF_QUIET_END] == "07:00:00"
    rule_1 = next(v for k, v in schema.items() if str(k) == "rule_1")
    inner = {str(k): k for k in rule_1.schema.schema}
    assert inner[const.CONF_RULE_ENABLED].default() is True
    assert inner[const.CONF_RULE_TIME].description["suggested_value"] == "21:30:00"
    rule_2 = next(v for k, v in schema.items() if str(k) == "rule_2")
    assert {str(k): k for k in rule_2.schema.schema}[const.CONF_RULE_ENABLED].default() is False

async def test_cover_elevation_min_must_be_below_max(hass, hub_entry):
    set_weather(hass); set_cover(hass, "cover.bedroom")
    result = await start(hass, hub_entry, const.SUBENTRY_COVER)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_INPUT, const.CONF_ELEVATION_MIN: 50, const.CONF_ELEVATION_MAX: 40})
    assert result["errors"] == {const.CONF_ELEVATION_MIN: "elevation_min_not_below_max"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha -q 2>&1 | tail -15`
Expected: the new tests fail (attribute errors, missing validation, unpruned store).

- [ ] **Step 3: Implement**

`const.py`: `DEFAULT_SUNNY_CONDITIONS: Final[tuple[str, ...]] = ("sunny", "partlycloudy")`; add `DEFAULT_AZIMUTH: Final = 180.0`, `DEFAULT_WIND_UPPER_KMH: Final = 60.0`, `DEFAULT_WIND_LOWER_KMH: Final = 50.0`.

`config_flow.py`: schema default `list(const.DEFAULT_SUNNY_CONDITIONS)`; `_complete_options` default `list(...)`; replace the azimuth `180` and wind `60`/`50` literals with the constants; replace `("unavailable", "unknown")` with `(STATE_UNAVAILABLE, STATE_UNKNOWN)` from `homeassistant.const`; in `validate_cover_input` add
```python
    if float(data[const.CONF_ELEVATION_MIN]) >= float(data[const.CONF_ELEVATION_MAX]):
        errors[const.CONF_ELEVATION_MIN] = "elevation_min_not_below_max"
```
`config_map.py`: `_opt_str` only treats `const.PROFILE_NONE` as unset for `const.CONF_SCHEDULE_PROFILE` (add a `none_sentinel: bool = False` parameter used by the profile call); `hub_config` temperature unit fallback gets the comment "hand-written entries only; flows always stamp the unit".

`store.py`:
```python
    def prune(self, keep: Iterable[str]) -> None:
        keep_set = set(keep)
        for cover_id in [c for c in self.data.covers if c not in keep_set]:
            del self.data.covers[cover_id]
```
and construct `Store(..., minor_version=..., )` via a subclass with `async def _async_migrate_func(self, old_major_version, old_minor_version, old_data): return old_data` (minor bumps pass through; a major bump raises `NotImplementedError` explicitly with a message).

`__init__.py`: after seeding, `store.prune(covers)`; replace `("unavailable", "unknown")` with the constants; `async_migrate_entry`:
```python
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 1:
        return False
    if entry.minor_version < 1:
        hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    return True
```
`en.json`: `config_subentries.cover.error.elevation_min_not_below_max`; `data_description` blocks for `options.step.init` (copy the six from `config.step.thresholds`), `config.step.reconfigure` (copy from `user`), `config_subentries.cover.step.reconfigure` and `config_subentries.profile.step.reconfigure` (copy from their `user` twins, including `sections.rule_1.description`); `initiate_flow.reconfigure`: "Reconfigure cover" / "Reconfigure schedule profile". Re-dump sorted.

- [ ] **Step 4: Run everything**

Run: `.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: all green (the translation test still passes; `test_defaults_match_spec` updated).

- [ ] **Step 5: Commit**

```bash
git add -A custom_components tests
git commit -m "chore: plan 2a backlog — tuple defaults, prune Store, migration hooks, validation and translation polish

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Units and forecast

**Files:**
- Create: `custom_components/cover_automation/units.py`, `custom_components/cover_automation/forecast.py`
- Test: `tests/ha/test_units.py`, `tests/ha/test_forecast.py`

**Interfaces:**
- Produces:
  - `read_float(state: State | None) -> float | None` (None for missing/`unavailable`/`unknown`/non-numeric)
  - `to_celsius(value: float, unit: str | None) -> float` (None or unknown unit → assume °C)
  - `from_celsius(value: float, unit: str) -> float`
  - `speed_convert(value: float, from_unit: str | None, to_unit: str | None) -> float` (either unit None or not convertible → value unchanged)
  - `temperature_unit_of(state: State | None) -> str | None` (`unit_of_measurement` attribute)
  - `@dataclass(frozen=True) TodayForecast(max_c: float | None, min_c: float | None)`
  - `async def async_fetch_today(hass, weather_entity: str, day: date, tz: tzinfo) -> TodayForecast | None` — None when the call raises, the response is empty, or no entry falls on `day`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_units.py`:
```python
from __future__ import annotations

import pytest
from homeassistant.core import State

from custom_components.cover_automation.units import (
    from_celsius,
    read_float,
    speed_convert,
    temperature_unit_of,
    to_celsius,
)


@pytest.mark.parametrize(
    ("state", "expected"),
    [(None, None), (State("sensor.x", "unavailable"), None), (State("sensor.x", "unknown"), None),
     (State("sensor.x", "abc"), None), (State("sensor.x", "21.5"), 21.5), (State("sensor.x", "-3"), -3.0)],
)
def test_read_float(state, expected):
    assert read_float(state) == expected


def test_temperature_conversions():
    assert to_celsius(68.0, "°F") == pytest.approx(20.0)
    assert to_celsius(20.0, "°C") == 20.0
    assert to_celsius(20.0, None) == 20.0
    assert to_celsius(293.15, "K") == pytest.approx(20.0)
    assert from_celsius(20.0, "°F") == pytest.approx(68.0)
    assert temperature_unit_of(State("sensor.t", "1", {"unit_of_measurement": "°F"})) == "°F"
    assert temperature_unit_of(None) is None


def test_speed_conversions():
    assert speed_convert(36.0, "km/h", "m/s") == pytest.approx(10.0)
    assert speed_convert(10.0, "m/s", "km/h") == pytest.approx(36.0)
    assert speed_convert(12.0, None, "km/h") == 12.0
    assert speed_convert(12.0, "km/h", None) == 12.0
    assert speed_convert(12.0, "bogus", "km/h") == 12.0
```

`tests/ha/test_forecast.py`:
```python
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from custom_components.cover_automation.forecast import TodayForecast, async_fetch_today
from tests.ha.conftest import WEATHER, set_weather

TZ = ZoneInfo("Europe/Vienna")


def register_forecast(hass: HomeAssistant, entries, *, raise_error: bool = False) -> None:
    async def handler(call: ServiceCall):
        if raise_error:
            raise RuntimeError("boom")
        return {call.data["entity_id"]: {"forecast": entries}}

    hass.services.async_register("weather", "get_forecasts", handler, supports_response=SupportsResponse.ONLY)


async def test_fetch_today_picks_local_day_and_converts_to_celsius(hass: HomeAssistant) -> None:
    set_weather(hass, unit="°F")
    register_forecast(hass, [
        {"datetime": "2026-07-09T22:00:00+00:00", "temperature": 60.0, "templow": 50.0},  # 10 July 00:00 Vienna
        {"datetime": "2026-07-10T22:00:00+00:00", "temperature": 86.0, "templow": 59.0},  # 11 July
    ])
    today = await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ)
    assert today is not None and today.max_c == pytest.approx(15.5556, abs=1e-3) and today.min_c == pytest.approx(10.0)


async def test_fetch_today_handles_missing_low_and_no_match(hass: HomeAssistant) -> None:
    set_weather(hass)
    register_forecast(hass, [{"datetime": "2026-07-10T10:00:00+02:00", "temperature": 30.0}])
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) == TodayForecast(30.0, None)
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 12), TZ) is None


async def test_fetch_today_returns_none_on_error_or_empty(hass: HomeAssistant) -> None:
    set_weather(hass)
    register_forecast(hass, [], raise_error=True)
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) is None
    register_forecast(hass, [])
    assert await async_fetch_today(hass, WEATHER, date(2026, 7, 10), TZ) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/ha/test_units.py tests/ha/test_forecast.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`units.py`:
```python
"""Unit-safe reading of Home Assistant states (spec §2 "Units")."""

from __future__ import annotations

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import State
from homeassistant.util.unit_conversion import SpeedConverter, TemperatureConverter


def read_float(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, "", None):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def temperature_unit_of(state: State | None) -> str | None:
    if state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    return str(unit) if unit else None


def to_celsius(value: float, unit: str | None) -> float:
    if unit in (None, UnitOfTemperature.CELSIUS) or unit not in TemperatureConverter.VALID_UNITS:
        return value
    return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)


def from_celsius(value: float, unit: str) -> float:
    if unit == UnitOfTemperature.CELSIUS or unit not in TemperatureConverter.VALID_UNITS:
        return value
    return TemperatureConverter.convert(value, UnitOfTemperature.CELSIUS, unit)


def speed_convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    if from_unit is None or to_unit is None or from_unit == to_unit:
        return value
    if from_unit not in SpeedConverter.VALID_UNITS or to_unit not in SpeedConverter.VALID_UNITS:
        return value
    return SpeedConverter.convert(value, from_unit, to_unit)
```

`forecast.py`:
```python
"""Daily forecast fetch: weather.get_forecasts → today's max/min in °C (spec §2 "Hot day")."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Any

from homeassistant.components.weather import (
    ATTR_FORECAST_TEMP,
    ATTR_FORECAST_TEMP_LOW,
    ATTR_FORECAST_TIME,
    DOMAIN as WEATHER_DOMAIN,
    SERVICE_GET_FORECASTS,
)
from homeassistant.components.weather.const import ATTR_WEATHER_TEMPERATURE_UNIT
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .units import to_celsius

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TodayForecast:
    max_c: float | None
    min_c: float | None


def _entry_day(raw: Any, tz: tzinfo) -> date | None:
    parsed = dt_util.parse_datetime(str(raw)) if raw is not None else None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).date()


async def async_fetch_today(
    hass: HomeAssistant, weather_entity: str, day: date, tz: tzinfo
) -> TodayForecast | None:
    try:
        response = await hass.services.async_call(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            {ATTR_ENTITY_ID: weather_entity, "type": "daily"},
            blocking=True,
            return_response=True,
        )
    except Exception as err:  # noqa: BLE001 - any failure is "no forecast today"
        _LOGGER.warning("Daily forecast fetch from %s failed: %s", weather_entity, err)
        return None
    entries = ((response or {}).get(weather_entity) or {}).get("forecast") or []
    state = hass.states.get(weather_entity)
    unit = str(state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)) if state and state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT) else None
    for entry in entries:
        if _entry_day(entry.get(ATTR_FORECAST_TIME), tz) != day:
            continue
        high = entry.get(ATTR_FORECAST_TEMP)
        low = entry.get(ATTR_FORECAST_TEMP_LOW)
        return TodayForecast(
            max_c=to_celsius(float(high), unit) if high is not None else None,
            min_c=to_celsius(float(low), unit) if low is not None else None,
        )
    return None
```
`ATTR_FORECAST_*` and `SERVICE_GET_FORECASTS` live in `homeassistant.components.weather` (`__init__.py`); if pyright flags a private import, import from `homeassistant.components.weather.const` where the name exists and keep the rest.

- [ ] **Step 4: Run, lint, type-check**

Run: `.venv/bin/pytest tests/ha/test_units.py tests/ha/test_forecast.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright`
Expected: 8 passed, clean. (`test_fetch_today_picks_local_day...`: 60 °F → 15.56 °C, 50 °F → 10 °C; the first entry is 10 July 00:00 Vienna.)

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: unit-safe state reading and daily forecast fetch

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Repairs helper, views and entity bases

**Files:**
- Create: `custom_components/cover_automation/repairs.py`, `views.py`, `entity.py`, `tests/ha/fakes.py`
- Modify: `custom_components/cover_automation/__init__.py` (stale sweep uses `repairs.ENTRY_ISSUE_PREFIXES`), `translations/en.json` (new issue texts)
- Test: `tests/ha/test_repairs.py`

**Interfaces:**
- Produces (`repairs.py`): issue kind constants `ISSUE_FROST_CONFLICT="frost_conflict"`, `ISSUE_WIND_UNAVAILABLE="wind_sensor_unavailable"`, `ISSUE_WIND_UNIT_CHANGED="wind_unit_changed"`, `ISSUE_WEATHER_UNAVAILABLE="weather_unavailable"`, `ISSUE_FORECAST_FAILED="forecast_fetch_failed"`, `ISSUE_SUN_MISSING="sun_missing"`, `ISSUE_FROST_SOURCE_UNAVAILABLE="frost_source_unavailable"`, `ISSUE_DOOR_UNAVAILABLE="door_sensor_unavailable"`, `ISSUE_ROOM_UNUSABLE="room_sensor_unusable"`, `ISSUE_COVER_UNSUPPORTED="cover_unsupported"`, `ISSUE_RULE_SKIPPED="rule_skipped"`, `ISSUE_COMMAND_FAILURES="command_failures"`; `hub_issue_id(entry_id, kind) -> str` = `f"{kind}_{entry_id}"`; `cover_issue_id(kind, subentry_id) -> str` = `f"{kind}_{subentry_id}"`; `set_issue(hass, issue_id, active: bool, *, translation_key: str, placeholders: dict[str, str] | None = None, severity=ir.IssueSeverity.WARNING) -> bool` (returns whether the issue is now active; creates or deletes; idempotent); `ENTRY_ISSUE_PREFIXES: tuple[str, ...]` (every kind + `"_"`, plus the four 2a prefixes) for the stale sweep; `entry_owned_issue_ids(entry, wanted_entities, cover_ids, profile_ids) -> set[str]`.
- Produces (`views.py`): `signal_update(entry_id) -> str`; frozen dataclasses `HubView` and `CoverView` exactly as listed below; `EMPTY_HUB_VIEW`.
- Produces (`entity.py`): `class ControllerProtocol(Protocol)` with `hub_view: HubView`, `cover_views: Mapping[str, CoverView]`, `cover_names: Mapping[str, str]`, `async def async_set_enabled(cover_id, enabled)`, `async_set_mode(cover_id, mode: Mode)`, `async_set_shading_mode(mode: ShadingMode)`, `async_set_reopening_mode(mode: ReopeningMode)`, `async_set_simulation(on: bool)`, `async_set_verbose(on: bool)`, `async_reset_override(cover_id)`, `async_evaluate_now(cover_ids: Iterable[str] | None = None)`; `class HubEntity(Entity)` (`__init__(entry, controller, key)`, `_attr_has_entity_name = True`, `_attr_should_poll = False`, `_attr_translation_key = key`, unique id `f"{entry.entry_id}_{key}"`, `DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})`, subscribes to `signal_update` in `async_added_to_hass` and calls `async_write_ha_state`); `class CoverEntityBase(Entity)` (`__init__(entry, controller, subentry_id, key)`, unique id `f"{subentry_id}_{key}"`, `DeviceInfo(identifiers={(DOMAIN, subentry_id)})`, property `view -> CoverView`).
- Produces (`tests/ha/fakes.py`): `FakeController` implementing `ControllerProtocol` with mutable `hub_view`/`cover_views`, recording every write call in `.calls: list[tuple]`, and `notify(hass, entry_id)` that dispatches the update signal.

```python
# views.py dataclasses (verbatim)
@dataclass(frozen=True, slots=True)
class HubView:
    sunny: bool | None = None
    hot_day: bool | None = None
    frost: bool | None = None
    any_wind_active: bool = False
    forecast_max_c: float | None = None
    forecast_min_c: float | None = None
    next_event_at: datetime | None = None
    next_event_profile: str | None = None
    next_event_action: str | None = None
    next_event_covers: tuple[str, ...] = ()
    shading_mode: ShadingMode = ShadingMode.AUTO
    reopening_mode: ReopeningMode = ReopeningMode.PASSIVE
    simulation: bool = False
    verbose: bool = False
    problem: bool = False

@dataclass(frozen=True, slots=True)
class CoverView:
    name: str
    cover_entity: str
    status: Status = Status.IDLE
    desired_state: str = "leave_alone"
    actual_state: str = "cover_unavailable"
    winning_layer: str = "none"
    reason: str = ""
    sun_hits: bool = False
    sunny: bool | None = None
    hot_day: bool | None = None
    room_state: str = "none"        # none | cold | comfortable | hot | degraded
    wind_state: str = "disabled"    # disabled | inactive | active | unavailable
    active_rule: str | None = None
    next_planned_action: str | None = None
    next_planned_at: datetime | None = None
    last_engine_move: datetime | None = None
    owner: str | None = None
    degraded: bool = False
    enabled: bool = True
    mode: Mode = Mode.AUTO
    override_active: bool = False
    override_since: datetime | None = None
    overridden_desired: str | None = None
    wind_active: bool = False
```

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_repairs.py`:
```python
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.cover_automation import const, repairs


async def test_set_issue_creates_and_clears(hass: HomeAssistant) -> None:
    issue_id = repairs.cover_issue_id(repairs.ISSUE_FROST_CONFLICT, "sub1")
    assert issue_id == "frost_conflict_sub1"
    assert repairs.set_issue(hass, issue_id, True, translation_key="frost_conflict", placeholders={"cover": "Bedroom"})
    issue = ir.async_get(hass).async_get_issue(const.DOMAIN, issue_id)
    assert issue is not None and issue.translation_placeholders == {"cover": "Bedroom"} and not issue.is_fixable
    assert repairs.set_issue(hass, issue_id, True, translation_key="frost_conflict", placeholders={"cover": "Bedroom"})  # idempotent
    assert not repairs.set_issue(hass, issue_id, False, translation_key="frost_conflict")
    assert ir.async_get(hass).async_get_issue(const.DOMAIN, issue_id) is None


def test_prefixes_cover_every_kind() -> None:
    kinds = [v for k, v in vars(repairs).items() if k.startswith("ISSUE_")]
    assert kinds and all(any(f"{kind}_" == p for p in repairs.ENTRY_ISSUE_PREFIXES) for kind in kinds)
    assert {"missing_entity_", "missing_profile_", "broken_cover_config_", "broken_profile_config_"} <= set(repairs.ENTRY_ISSUE_PREFIXES)


async def test_stale_sweep_removes_runtime_issues_of_deleted_covers(hass: HomeAssistant, hub_entry) -> None:
    from custom_components.cover_automation import _delete_stale_issues

    repairs.set_issue(hass, repairs.cover_issue_id(repairs.ISSUE_COMMAND_FAILURES, "gone"), True, translation_key="command_failures", placeholders={"cover": "x"})
    keep = repairs.hub_issue_id(hub_entry.entry_id, repairs.ISSUE_WEATHER_UNAVAILABLE)
    repairs.set_issue(hass, keep, True, translation_key="weather_unavailable")
    _delete_stale_issues(hass, hub_entry, owned_issue_ids={keep})
    reg = ir.async_get(hass)
    assert reg.async_get_issue(const.DOMAIN, "command_failures_gone") is None
    assert reg.async_get_issue(const.DOMAIN, keep) is not None
```
Plus `tests/ha/test_entities.py` gets its first test here (the rest come in Tasks 6/7):
```python
async def test_hub_entity_base_identity(hass: HomeAssistant, hub_entry) -> None:
    from custom_components.cover_automation.entity import HubEntity, CoverEntityBase
    from tests.ha.fakes import FakeController

    ctrl = FakeController()
    e = HubEntity(hub_entry, ctrl, "sunny")
    assert e.unique_id == f"{hub_entry.entry_id}_sunny" and e.translation_key == "sunny" and e.has_entity_name
    assert (const.DOMAIN, hub_entry.entry_id) in e.device_info["identifiers"]
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom")
    c = CoverEntityBase(hub_entry, ctrl, "sub1", "status")
    assert c.unique_id == "sub1_status" and (const.DOMAIN, "sub1") in c.device_info["identifiers"]
    assert c.view.name == "Bedroom"
```

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/pytest tests/ha/test_repairs.py tests/ha/test_entities.py -q` → import errors.

- [ ] **Step 3: Implement**

`repairs.py`:
```python
"""Repair-issue helpers (spec §4 Repairs). All issues are non-fixable and auto-clearing."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import const

ISSUE_FROST_CONFLICT = "frost_conflict"
ISSUE_WIND_UNAVAILABLE = "wind_sensor_unavailable"
ISSUE_WIND_UNIT_CHANGED = "wind_unit_changed"
ISSUE_WEATHER_UNAVAILABLE = "weather_unavailable"
ISSUE_FORECAST_FAILED = "forecast_fetch_failed"
ISSUE_SUN_MISSING = "sun_missing"
ISSUE_FROST_SOURCE_UNAVAILABLE = "frost_source_unavailable"
ISSUE_DOOR_UNAVAILABLE = "door_sensor_unavailable"
ISSUE_ROOM_UNUSABLE = "room_sensor_unusable"
ISSUE_COVER_UNSUPPORTED = "cover_unsupported"
ISSUE_RULE_SKIPPED = "rule_skipped"
ISSUE_COMMAND_FAILURES = "command_failures"

_RUNTIME_KINDS = (
    ISSUE_FROST_CONFLICT, ISSUE_WIND_UNAVAILABLE, ISSUE_WIND_UNIT_CHANGED, ISSUE_WEATHER_UNAVAILABLE,
    ISSUE_FORECAST_FAILED, ISSUE_SUN_MISSING, ISSUE_FROST_SOURCE_UNAVAILABLE, ISSUE_DOOR_UNAVAILABLE,
    ISSUE_ROOM_UNUSABLE, ISSUE_COVER_UNSUPPORTED, ISSUE_RULE_SKIPPED, ISSUE_COMMAND_FAILURES,
)
SETUP_PREFIXES = ("missing_entity_", "missing_profile_", "broken_cover_config_", "broken_profile_config_")
ENTRY_ISSUE_PREFIXES: tuple[str, ...] = tuple(f"{k}_" for k in _RUNTIME_KINDS) + SETUP_PREFIXES


def hub_issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def cover_issue_id(kind: str, subentry_id: str) -> str:
    return f"{kind}_{subentry_id}"


def set_issue(
    hass: HomeAssistant,
    issue_id: str,
    active: bool,
    *,
    translation_key: str,
    placeholders: dict[str, str] | None = None,
    severity: ir.IssueSeverity = ir.IssueSeverity.WARNING,
) -> bool:
    """Create the issue when active, delete it otherwise. Returns `active`."""
    if active:
        ir.async_create_issue(
            hass, const.DOMAIN, issue_id, is_fixable=False, severity=severity,
            translation_key=translation_key, translation_placeholders=placeholders,
        )
    else:
        ir.async_delete_issue(hass, const.DOMAIN, issue_id)
    return active


def entry_owned_issue_ids(
    entry: ConfigEntry, wanted_entities: Iterable[str], cover_ids: Iterable[str], profile_ids: Iterable[str]
) -> set[str]:
    """Every issue id this entry may legitimately hold right now (for the stale sweep)."""
    covers, profiles = list(cover_ids), list(profile_ids)
    ids = {f"missing_entity_{entry.entry_id}_{e}" for e in wanted_entities}
    ids.update(hub_issue_id(entry.entry_id, k) for k in (
        ISSUE_WIND_UNAVAILABLE, ISSUE_WEATHER_UNAVAILABLE, ISSUE_FORECAST_FAILED, ISSUE_SUN_MISSING,
        ISSUE_FROST_SOURCE_UNAVAILABLE,
    ))
    for cover_id in covers:
        ids.update(cover_issue_id(k, cover_id) for k in (
            "missing_profile", "broken_cover_config", ISSUE_FROST_CONFLICT, ISSUE_WIND_UNIT_CHANGED,
            ISSUE_DOOR_UNAVAILABLE, ISSUE_ROOM_UNUSABLE, ISSUE_COVER_UNSUPPORTED, ISSUE_COMMAND_FAILURES,
        ))
    for profile_id in profiles:
        ids.update(cover_issue_id(k, profile_id) for k in ("broken_profile_config", ISSUE_RULE_SKIPPED))
    return ids
```
`__init__.py`: `_entry_issue_ids` delegates to `repairs.entry_owned_issue_ids(entry, wanted, [s.subentry_id for s in cover subentries], [s.subentry_id for s in profile subentries])`; `_delete_stale_issues` matches `issue_id.startswith(entity_prefix) or issue_id.startswith(repairs.ENTRY_ISSUE_PREFIXES)` (a tuple works with `startswith`) and drops the four private prefix constants (keep the issue-id helper functions used elsewhere).

`views.py`: the two dataclasses above plus
```python
def signal_update(entry_id: str) -> str:
    return f"{const.DOMAIN}_update_{entry_id}"

EMPTY_HUB_VIEW = HubView()
```

`entity.py`:
```python
"""Entity bases: views over controller state, written through the controller (decision 17)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import const
from .engine.model import Mode, ReopeningMode, ShadingMode
from .views import CoverView, HubView, signal_update


class ControllerProtocol(Protocol):
    hub_view: HubView
    cover_views: Mapping[str, CoverView]
    cover_names: Mapping[str, str]

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None: ...
    async def async_set_mode(self, cover_id: str, mode: Mode) -> None: ...
    async def async_set_shading_mode(self, mode: ShadingMode) -> None: ...
    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None: ...
    async def async_set_simulation(self, on: bool) -> None: ...
    async def async_set_verbose(self, on: bool) -> None: ...
    async def async_reset_override(self, cover_id: str) -> None: ...
    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None: ...


class _BaseEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, key: str) -> None:
        self._entry = entry
        self._controller = controller
        self._key = key
        self._attr_translation_key = key

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_update(self._entry.entry_id), self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def hub_view(self) -> HubView:
        return self._controller.hub_view


class HubEntity(_BaseEntity):
    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, key: str) -> None:
        super().__init__(entry, controller, key)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(const.DOMAIN, entry.entry_id)})


class CoverEntityBase(_BaseEntity):
    def __init__(
        self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str, key: str
    ) -> None:
        super().__init__(entry, controller, key)
        self.subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(const.DOMAIN, subentry_id)})

    @property
    def view(self) -> CoverView:
        return self._controller.cover_views[self.subentry_id]
```

`tests/ha/fakes.py`:
```python
"""Test doubles shared by the entity, service and platform tests."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.cover_automation.engine.model import Mode, ReopeningMode, ShadingMode
from custom_components.cover_automation.views import CoverView, HubView, signal_update


class FakeController:
    def __init__(self) -> None:
        self.hub_view = HubView()
        self.cover_views: dict[str, CoverView] = {}
        self.cover_names: dict[str, str] = {}
        self.calls: list[tuple] = []

    def notify(self, hass: HomeAssistant, entry_id: str) -> None:
        async_dispatcher_send(hass, signal_update(entry_id))

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None:
        self.calls.append(("set_enabled", cover_id, enabled))

    async def async_set_mode(self, cover_id: str, mode: Mode) -> None:
        self.calls.append(("set_mode", cover_id, mode))

    async def async_set_shading_mode(self, mode: ShadingMode) -> None:
        self.calls.append(("set_shading_mode", mode))

    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None:
        self.calls.append(("set_reopening_mode", mode))

    async def async_set_simulation(self, on: bool) -> None:
        self.calls.append(("set_simulation", on))

    async def async_set_verbose(self, on: bool) -> None:
        self.calls.append(("set_verbose", on))

    async def async_reset_override(self, cover_id: str) -> None:
        self.calls.append(("reset_override", cover_id))

    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None:
        self.calls.append(("evaluate_now", tuple(cover_ids) if cover_ids is not None else None))
```

`en.json` → `issues` gets these keys (title + description each; per-cover ones use `{cover}`, `rule_skipped` uses `{profile}` and `{rule}`, `wind_unit_changed` uses `{cover}`, `{stored}`, `{current}`): `frost_conflict` ("Wind or door protection blocked by frost" / "{cover} should open for wind or door protection, but the outdoor temperature is at or below the frost threshold so it was left in place."), `wind_sensor_unavailable`, `wind_unit_changed`, `weather_unavailable`, `forecast_fetch_failed`, `sun_missing`, `frost_source_unavailable`, `door_sensor_unavailable`, `room_sensor_unusable`, `cover_unsupported`, `rule_skipped`, `command_failures` ("Cover commands keep failing" / "Three consecutive commands to {cover} failed or were never confirmed. Check the cover and its integration."). Re-dump sorted.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: repair-issue helpers, view snapshots and entity bases

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Signals adapter (HA states → engine inputs)

**Files:**
- Create: `custom_components/cover_automation/signals_adapter.py`
- Test: `tests/ha/test_signals_adapter.py`

**Interfaces:**
- Produces:
  - `SUN_ENTITY = "sun.sun"`
  - `classify_state(state: State | None, tolerance: float) -> CoverState` (uses `engine.classify.classify(state.state, current_position, tolerance)`)
  - `sun_position(hass) -> tuple[float, float] | None` → `(azimuth, elevation)`
  - `cover_supports(state: State | None) -> tuple[bool, bool]` → `(open_and_close, set_position)`
  - `class HubSignalSource(hass, hub: HubConfig, latch: DailyLatch)` with `seed(now)`, `update(now)`, `apply_forecast(today: TodayForecast | None, day: date, now)`, `rollover(day)`, `signals(now, store: StoreData, sun_elevation: float) -> HubSignals`, `next_check_at() -> datetime | None`; properties `sunny_state: bool | None`, `hot_day: bool | None`, `frost: FrostSignal`, `latch: DailyLatch`, `weather_unavailable_beyond_grace(now) -> bool`, `forecast_failed_beyond_grace(now) -> bool`, `frost_source_unavailable: bool` (outdoor source configured and unreadable right now), `wind_sensor_unavailable: bool`.
  - `class CoverSignalSet(hass, cfg: CoverConfig, bind: CoverBindings, hub: HubConfig, *, wind_active: bool, sun_release_margin: float)` with `seed(now, sun)`, `update(now, sun)`, `inputs(actual: CoverState, schedule: ScheduleView) -> CoverInputs`, `next_check_at() -> datetime | None`, properties `room_state: str` (`none|cold|comfortable|hot|degraded`), `wind_state: str` (`disabled|inactive|active|unavailable`), `wind_active: bool`, `wind_unit_mismatch: bool`, `wind_unit_current: str | None`, `door_unavailable: bool`, `room_unusable: bool` (shading rule `room_only` and the room reading is unusable), `sun_hits_state: bool`.
- Consumes: Task 1 `units`, `forecast.TodayForecast`; engine `classify`, `signals`, `sun`, `model`.

Semantics (spec §2): sunny = weather `state` ∈ `hub.sunny_conditions`, held through `Graceful[str](hub.weather_grace_s)` (None beyond grace) then `Debounce(on, off)`; `sunny_override_entity` on/off replaces the computed value (unavailable → None). Hot day = `latch.hot_day` unless `hot_override_entity` is set (on/off/None). Frost = `FrostSignal(threshold=to_celsius(hub.frost_threshold, hub.temperature_unit), grace_s=hub.weather_grace_s)` fed with the outdoor temperature in °C from `hub.outdoor_temperature_sensor` (unit attribute) or, when not configured, the weather entity's `temperature` attribute in its `temperature_unit`. Forecast: `latch.update(day, today.max_c, today.min_c, hot_high_c, hot_low_c)` with thresholds converted to °C; `today is None` marks `forecast_failed_since` (first failure time) and leaves the latch untouched; a success clears it. Room: `RoomTemperature(floor_c, ceiling_c)` fed °C readings; `seed()` at start. Wind: `WindProtection(cfg.wind_upper, cfg.wind_lower, cfg.wind_hold_s, active=wind_active)` fed `speed_convert(value, sensor_unit, bind.wind_unit)`; only when `cfg.wind_enabled and hub.wind_sensor`. Door: `DoorState.NONE` without a sensor; `UNAVAILABLE` when the state is missing/unavailable/unknown; `OPEN` for `on`, else `CLOSED`; `door_last_changed = state.last_changed`. Sun hits: `SunHits(cfg, margin)` seeded with the strict test, `update(az, el)` afterwards; when `sun` is None the last value stays.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_signals_adapter.py`:
```python
from __future__ import annotations

from datetime import date, timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.cover_automation import const
from custom_components.cover_automation.config_map import cover_config, hub_config
from custom_components.cover_automation.engine.model import CoverState, DoorState, ScheduleView
from custom_components.cover_automation.engine.signals import DailyLatch
from custom_components.cover_automation.forecast import TodayForecast
from custom_components.cover_automation.store import StoreData
from custom_components.cover_automation.signals_adapter import (
    CoverSignalSet,
    HubSignalSource,
    classify_state,
    cover_supports,
    sun_position,
)
from tests.ha.conftest import WEATHER, cover_subentry_data, set_sensor, set_sun, set_weather
from homeassistant.config_entries import ConfigSubentry


@pytest.mark.parametrize(
    ("state", "position", "expected"),
    [("open", 100, CoverState.OPEN), ("open", 50, CoverState.PARTIAL), ("open", None, CoverState.OPEN),
     ("closed", 0, CoverState.CLOSED), ("opening", 40, CoverState.MOVING), ("closing", None, CoverState.MOVING),
     ("unavailable", None, CoverState.UNAVAILABLE)],
)
def test_classify_state(state, position, expected):
    attrs = {"current_position": position} if position is not None else {}
    assert classify_state(State("cover.x", state, attrs), 5.0) is expected
    assert classify_state(None, 5.0) is CoverState.UNAVAILABLE


def test_sun_position_and_cover_supports(hass: HomeAssistant):
    assert sun_position(hass) is None
    set_sun(hass, elevation=30.0, azimuth=170.0)
    assert sun_position(hass) == (170.0, 30.0)
    assert cover_supports(State("cover.x", "open", {"supported_features": 3})) == (True, False)
    assert cover_supports(State("cover.x", "open", {"supported_features": 4})) == (False, True)
    assert cover_supports(None) == (False, False)


def make_hub(hub_entry, **data_overrides):
    return hub_config(hub_entry)


async def test_sunny_debounce_grace_and_override(hass: HomeAssistant, hub_entry, freezer):
    now = dt_util.utcnow()
    set_weather(hass, condition="sunny")
    src = HubSignalSource(hass, make_hub(hub_entry), DailyLatch())
    src.seed(now)
    assert src.sunny_state is True
    set_weather(hass, condition="cloudy")
    src.update(now + timedelta(minutes=5))
    assert src.sunny_state is True  # off delay 20 min
    assert src.next_check_at() == now + timedelta(minutes=20)
    src.update(now + timedelta(minutes=21))
    assert src.sunny_state is False
    hass.states.async_set(WEATHER, "unavailable")
    src.update(now + timedelta(minutes=22))
    assert src.sunny_state is False  # held within grace
    src.update(now + timedelta(minutes=22) + timedelta(seconds=1800))
    assert src.sunny_state is None and src.weather_unavailable_beyond_grace(now + timedelta(minutes=52))
    hass.config_entries.async_update_entry(hub_entry, options={**hub_entry.options, const.CONF_SUNNY_OVERRIDE_ENTITY: "binary_sensor.force_sunny"})
    hass.states.async_set("binary_sensor.force_sunny", "on")
    src2 = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src2.seed(now)
    assert src2.sunny_state is True


async def test_frost_from_fahrenheit_sensor(hass: HomeAssistant, hub_entry):
    hass.config_entries.async_update_entry(hub_entry, data={**hub_entry.data, const.CONF_OUTDOOR_TEMPERATURE_SENSOR: "sensor.outdoor"})
    hub = hub_config(hub_entry)
    now = dt_util.utcnow()
    set_weather(hass)
    set_sensor(hass, "sensor.outdoor", 30, unit="°F")  # -1.1 °C
    src = HubSignalSource(hass, hub, DailyLatch())
    src.seed(now)
    assert src.frost.active is True
    set_sensor(hass, "sensor.outdoor", 33.5, unit="°F")  # 0.8 °C: still within release band
    src.update(now)
    assert src.frost.active is True
    set_sensor(hass, "sensor.outdoor", 36, unit="°F")  # 2.2 °C
    src.update(now)
    assert src.frost.active is False
    hass.states.async_set("sensor.outdoor", "unavailable")
    src.update(now)
    assert src.frost_source_unavailable is True


async def test_frost_falls_back_to_weather_temperature(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_weather(hass, temperature=-2.0)
    src = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src.seed(now)
    assert src.frost.active is True


async def test_hot_day_latches_and_forecast_failure_is_tracked(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_weather(hass)
    src = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src.seed(now)
    day = date(2026, 7, 10)
    src.apply_forecast(TodayForecast(30.0, 18.0), day, now)
    assert src.hot_day is True and src.latch.max == 30.0
    src.apply_forecast(TodayForecast(20.0, 10.0), day, now)
    assert src.hot_day is True and src.latch.max == 30.0  # latched, max only rises
    src.apply_forecast(None, day, now)
    assert not src.forecast_failed_beyond_grace(now)
    assert src.forecast_failed_beyond_grace(now + timedelta(seconds=1801))
    src.rollover(date(2026, 7, 11))
    assert src.hot_day is None
    hub_signals = src.signals(now, StoreData(), 35.0)
    assert hub_signals.sun_elevation == 35.0 and hub_signals.hot_day is None and hub_signals.sunny is True


def cover_set(hass, hub_entry, **overrides):
    hub = hub_config(hub_entry)
    sub = ConfigSubentry(**cover_subentry_data("cover.bedroom", **overrides))
    cfg, bind = cover_config(sub, hub)
    return CoverSignalSet(hass, cfg, bind, hub, wind_active=False, sun_release_margin=hub.sun_release_margin), cfg, bind


async def test_room_in_fahrenheit_and_labels(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_sensor(hass, "sensor.room", 66.0, unit="°F")  # 18.9 °C < 21
    sig, _, _ = cover_set(hass, hub_entry, **{const.CONF_ROOM_SENSOR: "sensor.room"})
    sig.seed(now, (180.0, 40.0))
    inputs = sig.inputs(CoverState.OPEN, ScheduleView())
    assert inputs.room_cold and not inputs.room_hot and sig.room_state == "cold"
    set_sensor(hass, "sensor.room", 80.0, unit="°F")  # 26.7 °C ≥ 25
    sig.update(now, (180.0, 40.0))  # raw change observed; the 10-minute dwell starts here
    sig.update(now + timedelta(minutes=11), (180.0, 40.0))
    assert sig.inputs(CoverState.OPEN, ScheduleView()).room_hot and sig.room_state == "hot"
    hass.states.async_set("sensor.room", "unavailable")
    sig.update(now + timedelta(minutes=12), (180.0, 40.0))
    assert sig.inputs(CoverState.OPEN, ScheduleView()).room_degraded and sig.room_state == "degraded"


async def test_wind_conversion_and_unit_mismatch(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_sensor(hass, "sensor.wind", 20.0, unit="m/s")  # 72 km/h
    sig, _, _ = cover_set(hass, hub_entry, **{const.CONF_WIND_ENABLED: True, const.CONF_WIND_UPPER: 60,
                                              const.CONF_WIND_LOWER: 50, const.CONF_WIND_UNIT: "km/h"})
    sig.seed(now, (180.0, 40.0))
    assert sig.wind_active is True and sig.wind_state == "active" and sig.wind_unit_mismatch is True
    set_sensor(hass, "sensor.wind", 10.0, unit="km/h")
    sig.update(now, (180.0, 40.0))
    assert sig.wind_active is True and sig.wind_unit_mismatch is False  # hold 15 min
    sig.update(now + timedelta(minutes=16), (180.0, 40.0))
    assert sig.wind_active is False and sig.wind_state == "inactive"
    hass.states.async_set("sensor.wind", "unavailable")
    sig.update(now + timedelta(minutes=17), (180.0, 40.0))
    assert sig.wind_state == "unavailable"


async def test_door_states_and_sun_hits(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    sig, _, _ = cover_set(hass, hub_entry, **{const.CONF_DOOR_SENSOR: "binary_sensor.door"})
    sig.seed(now, (180.0, 40.0))
    inputs = sig.inputs(CoverState.OPEN, ScheduleView())
    assert inputs.door is DoorState.UNAVAILABLE and sig.door_unavailable
    hass.states.async_set("binary_sensor.door", "on")
    inputs = sig.inputs(CoverState.OPEN, ScheduleView())
    assert inputs.door is DoorState.OPEN and inputs.door_last_changed is not None
    hass.states.async_set("binary_sensor.door", "off")
    assert sig.inputs(CoverState.OPEN, ScheduleView()).door is DoorState.CLOSED
    assert inputs.sun_hits is True
    sig.update(now, (300.0, 40.0))
    assert sig.inputs(CoverState.OPEN, ScheduleView()).sun_hits is False
    sig.update(now, None)  # sun missing keeps the last value
    assert sig.sun_hits_state is False


async def test_room_only_without_sensor_is_unusable(hass: HomeAssistant, hub_entry):
    sig, _, _ = cover_set(hass, hub_entry, **{const.CONF_SHADING_RULE: "room_only", const.CONF_ROOM_SENSOR: "sensor.room"})
    sig.seed(dt_util.utcnow(), None)
    assert sig.room_unusable is True
```

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/pytest tests/ha/test_signals_adapter.py -q` → import error.

- [ ] **Step 3: Implement**

`signals_adapter.py`:
```python
"""Home Assistant states → engine signals (spec §2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.cover import ATTR_CURRENT_POSITION, CoverEntityFeature
from homeassistant.components.sun.const import STATE_ATTR_AZIMUTH, STATE_ATTR_ELEVATION
from homeassistant.components.weather.const import ATTR_WEATHER_TEMPERATURE, ATTR_WEATHER_TEMPERATURE_UNIT
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .config_map import CoverBindings, HubConfig
from .engine import classify
from .engine.model import CoverConfig, CoverInputs, CoverState, DoorState, HubSignals, ScheduleView, ShadingRule
from .engine.signals import DailyLatch, Debounce, FrostSignal, Graceful, RoomTemperature, WindProtection
from .engine.sun import SunHits
from .forecast import TodayForecast
from .store import StoreData
from .units import read_float, speed_convert, temperature_unit_of, to_celsius

SUN_ENTITY = "sun.sun"
_UNUSABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def classify_state(state: State | None, tolerance: float) -> CoverState:
    if state is None:
        return CoverState.UNAVAILABLE
    raw = state.attributes.get(ATTR_CURRENT_POSITION)
    position = float(raw) if isinstance(raw, (int, float)) else None
    return classify.classify(state.state, position, tolerance)


def sun_position(hass: HomeAssistant) -> tuple[float, float] | None:
    state = hass.states.get(SUN_ENTITY)
    if state is None:
        return None
    az, el = state.attributes.get(STATE_ATTR_AZIMUTH), state.attributes.get(STATE_ATTR_ELEVATION)
    if not isinstance(az, (int, float)) or not isinstance(el, (int, float)):
        return None
    return float(az), float(el)


def cover_supports(state: State | None) -> tuple[bool, bool]:
    features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) if state else 0
    open_close = bool(features & CoverEntityFeature.OPEN) and bool(features & CoverEntityFeature.CLOSE)
    return open_close, bool(features & CoverEntityFeature.SET_POSITION)


def _binary(state: State | None) -> bool | None:
    if state is None or state.state in _UNUSABLE:
        return None
    return state.state == STATE_ON


class HubSignalSource:
    """Sunny, hot day and frost for the whole house."""

    def __init__(self, hass: HomeAssistant, hub: HubConfig, latch: DailyLatch) -> None:
        self.hass, self.hub, self.latch = hass, hub, latch
        self._condition: Graceful[str] = Graceful(hub.weather_grace_s)
        self._sunny = Debounce(hub.sunny_on_delay_s, hub.sunny_off_delay_s)
        self.frost = FrostSignal(
            threshold=to_celsius(hub.frost_threshold, hub.temperature_unit), grace_s=hub.weather_grace_s
        )
        self.hot_high_c = to_celsius(hub.hot_high, hub.temperature_unit)
        self.hot_low_c = to_celsius(hub.hot_low, hub.temperature_unit) if hub.hot_low is not None else None
        self._weather_unavailable_since: datetime | None = None
        self._forecast_failed_since: datetime | None = None

    # -- raw readings ----------------------------------------------------------------
    def _weather(self) -> State | None:
        state = self.hass.states.get(self.hub.weather_entity)
        return None if state is None or state.state in _UNUSABLE else state

    def _raw_sunny(self, now: datetime) -> bool | None:
        if self.hub.sunny_override_entity:
            return _binary(self.hass.states.get(self.hub.sunny_override_entity))
        weather = self._weather()
        condition = self._condition.update(weather.state if weather else None, now)
        if weather is None:
            self._weather_unavailable_since = self._weather_unavailable_since or now
        else:
            self._weather_unavailable_since = None
        return None if condition is None else condition in self.hub.sunny_conditions

    def _outdoor_c(self) -> float | None:
        if self.hub.outdoor_temperature_sensor:
            state = self.hass.states.get(self.hub.outdoor_temperature_sensor)
            value = read_float(state)
            return None if value is None else to_celsius(value, temperature_unit_of(state))
        weather = self._weather()
        if weather is None:
            return None
        raw = weather.attributes.get(ATTR_WEATHER_TEMPERATURE)
        if not isinstance(raw, (int, float)):
            return None
        unit = weather.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
        return to_celsius(float(raw), str(unit) if unit else None)

    # -- lifecycle -------------------------------------------------------------------
    def seed(self, now: datetime) -> None:
        self._sunny.seed(self._raw_sunny(now), now)
        self.frost.update(self._outdoor_c(), now)

    def update(self, now: datetime) -> None:
        self._sunny.update(self._raw_sunny(now), now)
        self.frost.update(self._outdoor_c(), now)

    def apply_forecast(self, today: TodayForecast | None, day: date, now: datetime) -> None:
        if today is None:
            self._forecast_failed_since = self._forecast_failed_since or now
            return
        self._forecast_failed_since = None
        self.latch.update(day, today.max_c, today.min_c, self.hot_high_c, self.hot_low_c)

    def rollover(self, day: date) -> None:
        self.latch.rollover(day)

    def signals(self, now: datetime, store: StoreData, sun_elevation: float) -> HubSignals:
        return HubSignals(
            now=now, sun_elevation=sun_elevation, frost=self.frost.active,
            frost_near_freezing=self.frost.near_freezing_last_known, sunny=self.sunny_state,
            hot_day=self.hot_day, shading_mode=store.shading_mode, reopening_mode=store.reopening_mode,
            simulation=store.simulation,
        )

    def next_check_at(self) -> datetime | None:
        return self._sunny.next_change_at()

    # -- views -----------------------------------------------------------------------
    @property
    def sunny_state(self) -> bool | None:
        return self._sunny.state

    @property
    def hot_day(self) -> bool | None:
        if self.hub.hot_override_entity:
            return _binary(self.hass.states.get(self.hub.hot_override_entity))
        return self.latch.hot_day

    def weather_unavailable_beyond_grace(self, now: datetime) -> bool:
        since = self._weather_unavailable_since
        return since is not None and (now - since).total_seconds() >= self.hub.weather_grace_s

    def forecast_failed_beyond_grace(self, now: datetime) -> bool:
        since = self._forecast_failed_since
        return since is not None and (now - since).total_seconds() >= self.hub.weather_grace_s

    @property
    def frost_source_unavailable(self) -> bool:
        return self._outdoor_c() is None

    @property
    def wind_sensor_unavailable(self) -> bool:
        return bool(self.hub.wind_sensor) and read_float(self.hass.states.get(self.hub.wind_sensor)) is None


class CoverSignalSet:
    """Sun hits, room temperature, wind and door for one cover."""

    def __init__(
        self, hass: HomeAssistant, cfg: CoverConfig, bind: CoverBindings, hub: HubConfig,
        *, wind_active: bool, sun_release_margin: float,
    ) -> None:
        self.hass, self.cfg, self.bind, self.hub = hass, cfg, bind, hub
        self.sun_hits = SunHits(cfg, sun_release_margin)
        self.room: RoomTemperature | None = None
        if bind.room_sensor:
            self.room = RoomTemperature(
                to_celsius(cfg.comfort_floor, bind.temperature_unit),
                to_celsius(cfg.comfort_ceiling, bind.temperature_unit),
            )
        self.wind: WindProtection | None = None
        if cfg.wind_enabled and hub.wind_sensor:
            self.wind = WindProtection(cfg.wind_upper, cfg.wind_lower, cfg.wind_hold_s, active=wind_active)
        self._room_flags: tuple[bool, bool, bool] = (False, False, False)
        self._room_reading: float | None = None
        self._wind_reading: float | None = None
        self.wind_unit_current: str | None = None

    def _room_temp_c(self) -> float | None:
        if not self.bind.room_sensor:
            return None
        state = self.hass.states.get(self.bind.room_sensor)
        value = read_float(state)
        return None if value is None else to_celsius(value, temperature_unit_of(state))

    def _wind_value(self) -> float | None:
        if not self.hub.wind_sensor:
            return None
        state = self.hass.states.get(self.hub.wind_sensor)
        value = read_float(state)
        self.wind_unit_current = temperature_unit_of(state)  # generic unit_of_measurement reader
        return None if value is None else speed_convert(value, self.wind_unit_current, self.bind.wind_unit)

    def _apply(self, now: datetime, sun: tuple[float, float] | None, *, seed: bool) -> None:
        if sun is not None:
            (self.sun_hits.seed if seed else self.sun_hits.update)(sun[0], sun[1])
        if self.room is not None:
            self._room_reading = self._room_temp_c()
            self._room_flags = (self.room.seed if seed else self.room.update)(self._room_reading, now)
        if self.wind is not None:
            self._wind_reading = self._wind_value()
            self.wind.update(self._wind_reading, now)

    def seed(self, now: datetime, sun: tuple[float, float] | None) -> None:
        self._apply(now, sun, seed=True)

    def update(self, now: datetime, sun: tuple[float, float] | None) -> None:
        self._apply(now, sun, seed=False)

    def _door(self) -> tuple[DoorState, datetime | None]:
        if not self.bind.door_sensor:
            return DoorState.NONE, None
        state = self.hass.states.get(self.bind.door_sensor)
        if state is None or state.state in _UNUSABLE:
            return DoorState.UNAVAILABLE, None
        return (DoorState.OPEN if state.state == STATE_ON else DoorState.CLOSED), state.last_changed

    def inputs(self, actual: CoverState, schedule: ScheduleView) -> CoverInputs:
        door, changed = self._door()
        cold, hot, degraded = self._room_flags
        return CoverInputs(
            actual=actual, door=door, door_last_changed=changed, sun_hits=bool(self.sun_hits.state),
            room_cold=cold, room_hot=hot, room_degraded=degraded, wind_active=self.wind_active,
            schedule=schedule,
        )

    def next_check_at(self) -> datetime | None:
        candidates = [c for c in (
            self.room.next_check_at() if self.room else None,
            self.wind.next_check_at() if self.wind else None,
        ) if c is not None]
        return min(candidates, default=None)

    @property
    def sun_hits_state(self) -> bool:
        return bool(self.sun_hits.state)

    @property
    def wind_active(self) -> bool:
        return self.wind.active if self.wind is not None else False

    @property
    def wind_state(self) -> str:
        if self.wind is None:
            return "disabled"
        if self.wind.unavailable:
            return "unavailable"
        return "active" if self.wind.active else "inactive"

    @property
    def wind_unit_mismatch(self) -> bool:
        return (
            self.wind is not None and self.wind_unit_current is not None
            and self.bind.wind_unit is not None and self.wind_unit_current != self.bind.wind_unit
        )

    @property
    def room_state(self) -> str:
        if self.room is None:
            return "none"
        cold, hot, degraded = self._room_flags
        if degraded:
            return "degraded"
        return "cold" if cold else "hot" if hot else "comfortable"

    @property
    def room_unusable(self) -> bool:
        return self.cfg.shading_rule is ShadingRule.ROOM_ONLY and (self.room is None or self._room_flags[2])

    @property
    def door_unavailable(self) -> bool:
        return self._door()[0] is DoorState.UNAVAILABLE
```
(`temperature_unit_of` reads `unit_of_measurement`; it is unit-agnostic despite the name — if that reads badly, add `unit_of = temperature_unit_of` alias in `units.py` and use it for the wind sensor.)

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_signals_adapter.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green. 

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: signals adapter mapping Home Assistant states onto engine inputs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Scheduler (rule timers, sun times, next event)

**Files:**
- Create: `custom_components/cover_automation/scheduler.py`
- Test: `tests/ha/test_scheduler.py`

**Interfaces:**
- Produces:
  - `class HassSunTimes(hass)` implementing the engine's `SunTimes` protocol via `get_astral_event_date(hass, SUN_EVENT_SUNRISE|SUN_EVENT_SUNSET, day)`; a `None` result (polar day/night) falls back to 06:00 / 18:00 local of that day with a debug log.
  - `@dataclass(frozen=True) NextEvent(at: datetime, profile_id: str, profile_name: str, action: Target, rule_index: int, covers: tuple[str, ...])`
  - `class ScheduleTracker(hass, profiles: Mapping[str, Profile], cover_profile: Mapping[str, str | None], on_fire: Callable[[frozenset[str], datetime], Awaitable[None]], *, sun: SunTimes | None = None)` with `tz` property (`dt_util.get_default_time_zone()`), `view(cover_id, now, actual, manual_move_at, satisfied_fire_at) -> ScheduleView`, `next_event(now) -> NextEvent | None`, `next_event_for(cover_id, now) -> NextEvent | None`, `active_rule_label(cover_id, now) -> str | None` (e.g. `"close rule 1 of Bedroom (21:30)"`), `skipped_rules_today(now) -> list[tuple[str, int]]` (profile ids + rule indexes whose `fire_time` is None for a sun-relative rule), `async_arm(now)`, `async_cancel()`.
- Consumes: engine `schedule.view/next_fire/last_fired/fire_time`, `Profile`, `Target`.

Behaviour: `async_arm` cancels any pending timer, computes the earliest `next_fire` over all profiles that have covers, and schedules `async_track_point_in_time` for it. When it fires, every profile whose `next_fire` (computed at `at - 30 s`) lies within 30 s of `at` contributes its covers; `on_fire(covers, at)` is awaited, then the tracker re-arms from `at` (`next_fire` is strictly later than `now`). Profiles without covers never arm timers but still appear in `next_event` only when they have covers.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_scheduler.py`:
```python
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cover_automation.engine.model import CoverState, Desired, Target
from custom_components.cover_automation.engine.schedule import Profile, QuietHours, Rule, TimeMode
from custom_components.cover_automation.scheduler import HassSunTimes, ScheduleTracker


class FixedSun:
    def sunrise(self, day: date) -> datetime:
        return datetime.combine(day, time(6, 0), tzinfo=dt_util.get_default_time_zone())

    def sunset(self, day: date) -> datetime:
        return datetime.combine(day, time(20, 0), tzinfo=dt_util.get_default_time_zone())


NIGHT = Profile("p1", "Night", (Rule(Target.CLOSED, TimeMode.FIXED, time(21, 30)),
                                Rule(Target.OPEN, TimeMode.SUNRISE, None, 30, earliest=time(7, 0))),
                QuietHours(time(22, 0), time(7, 0)))


def local(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=dt_util.get_default_time_zone())


async def test_next_event_and_view(hass: HomeAssistant) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": "p1", "c2": None}, on_fire, sun=FixedSun())
    now = local(2026, 7, 10, 20, 0)
    ev = tracker.next_event(now)
    assert ev is not None and ev.at == local(2026, 7, 10, 21, 30) and ev.action is Target.CLOSED
    assert ev.covers == ("c1",) and ev.profile_name == "Night" and tracker.next_event_for("c2", now) is None
    assert tracker.view("c2", now, CoverState.OPEN, None, None).desired is Desired.LEAVE_ALONE
    after = local(2026, 7, 10, 21, 31)
    view = tracker.view("c1", after, CoverState.OPEN, None, None)
    assert view.desired is Desired.CLOSED and view.rule_index == 0 and view.quiet_active is False
    assert tracker.active_rule_label("c1", after) == "close rule 1 of Night (21:30)"
    assert tracker.view("c1", local(2026, 7, 10, 22, 30), CoverState.CLOSED, None, None).quiet_active is True
    morning = tracker.next_event(after)
    assert morning is not None and morning.at == local(2026, 7, 11, 7, 0) and morning.action is Target.OPEN  # 06:30 clamped by earliest 07:00


async def test_arm_fires_and_rearms(hass: HomeAssistant, freezer) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": "p1"}, on_fire, sun=FixedSun())
    tracker.async_arm(dt_util.utcnow())
    freezer.move_to(local(2026, 7, 10, 21, 30, ) + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(fired) == 1 and fired[0][0] == frozenset({"c1"})
    assert tracker.next_event(dt_util.utcnow()).at == local(2026, 7, 11, 7, 0)
    tracker.async_cancel()


async def test_hass_sun_times_and_skipped_rules(hass: HomeAssistant) -> None:
    sun = HassSunTimes(hass)
    day = date(2026, 7, 10)
    assert sun.sunrise(day) < sun.sunset(day)
    # a sunset close rule whose only clamp would land on the previous day is skipped
    all_day_quiet = Profile("p2", "Quiet", (Rule(Target.CLOSED, TimeMode.SUNSET, None, 0),), QuietHours(time(0, 0), time(23, 59)))
    tracker = ScheduleTracker(hass, {"p2": all_day_quiet}, {"c1": "p2"}, lambda c, a: None, sun=FixedSun())  # type: ignore[arg-type]
    assert tracker.skipped_rules_today(local(2026, 7, 10, 12, 0)) == [("p2", 0)]
```

- [ ] **Step 2: Run to verify they fail** — import error.

- [ ] **Step 3: Implement**

`scheduler.py`:
```python
"""Schedule rule timers and views (spec §1.2 layer 5, §2 timers, decision 28)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .engine import schedule
from .engine.model import CoverState, ScheduleView, Target
from .engine.schedule import Profile, SunTimes, TimeMode

_LOGGER = logging.getLogger(__name__)
_MATCH_TOLERANCE = timedelta(seconds=30)  # rules have minute granularity; HA fires at or after the point in time


class HassSunTimes:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _event(self, event: str, day: date, fallback: time) -> datetime:
        result = get_astral_event_date(self.hass, event, day)
        if result is None:
            _LOGGER.debug("No %s on %s at this location; using %s local", event, day, fallback)
            return datetime.combine(day, fallback, tzinfo=dt_util.get_default_time_zone())
        return result

    def sunrise(self, day: date) -> datetime:
        return self._event(SUN_EVENT_SUNRISE, day, time(6, 0))

    def sunset(self, day: date) -> datetime:
        return self._event(SUN_EVENT_SUNSET, day, time(18, 0))


@dataclass(frozen=True, slots=True)
class NextEvent:
    at: datetime
    profile_id: str
    profile_name: str
    action: Target
    rule_index: int
    covers: tuple[str, ...]


class ScheduleTracker:
    def __init__(
        self,
        hass: HomeAssistant,
        profiles: Mapping[str, Profile],
        cover_profile: Mapping[str, str | None],
        on_fire: Callable[[frozenset[str], datetime], Awaitable[None]],
        *,
        sun: SunTimes | None = None,
    ) -> None:
        self.hass = hass
        self.profiles = dict(profiles)
        self.cover_profile = dict(cover_profile)
        self._on_fire = on_fire
        self.sun: SunTimes = sun or HassSunTimes(hass)
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def tz(self) -> tzinfo:
        return dt_util.get_default_time_zone()

    def _covers_of(self, profile_id: str) -> tuple[str, ...]:
        return tuple(sorted(c for c, p in self.cover_profile.items() if p == profile_id))

    def _profile_for(self, cover_id: str) -> Profile | None:
        profile_id = self.cover_profile.get(cover_id)
        return self.profiles.get(profile_id) if profile_id else None

    def view(
        self, cover_id: str, now: datetime, actual: CoverState,
        manual_move_at: datetime | None, satisfied_fire_at: datetime | None,
    ) -> ScheduleView:
        profile = self._profile_for(cover_id)
        if profile is None:
            return ScheduleView()
        return schedule.view(profile, now, self.sun, actual, manual_move_at, satisfied_fire_at, tz=self.tz)

    def _events(self, now: datetime) -> list[NextEvent]:
        events: list[NextEvent] = []
        for profile_id, profile in self.profiles.items():
            covers = self._covers_of(profile_id)
            if not covers:
                continue
            nxt = schedule.next_fire(profile, now, self.sun, tz=self.tz)
            if nxt is None:
                continue
            at, index = nxt
            events.append(NextEvent(at, profile_id, profile.name, profile.rules[index].action, index, covers))
        events.sort(key=lambda e: (e.at, e.profile_id))
        return events

    def next_event(self, now: datetime) -> NextEvent | None:
        events = self._events(now)
        return events[0] if events else None

    def next_event_for(self, cover_id: str, now: datetime) -> NextEvent | None:
        profile_id = self.cover_profile.get(cover_id)
        return next((e for e in self._events(now) if e.profile_id == profile_id), None)

    def active_rule_label(self, cover_id: str, now: datetime) -> str | None:
        profile = self._profile_for(cover_id)
        if profile is None:
            return None
        last = schedule.last_fired(profile, now, self.sun, tz=self.tz)
        if last is None:
            return None
        fired_at, index = last
        action = "close" if profile.rules[index].action is Target.CLOSED else "open"
        return f"{action} rule {index + 1} of {profile.name} ({fired_at.astimezone(self.tz):%H:%M})"

    def skipped_rules_today(self, now: datetime) -> list[tuple[str, int]]:
        day = now.astimezone(self.tz).date()
        skipped: list[tuple[str, int]] = []
        for profile_id, profile in self.profiles.items():
            for index, rule in enumerate(profile.rules):
                if rule.time_mode is TimeMode.FIXED:
                    continue
                if schedule.fire_time(rule, day, self.sun, profile.quiet_hours, self.tz) is None:
                    skipped.append((profile_id, index))
        return skipped

    @callback
    def async_arm(self, now: datetime) -> None:
        self.async_cancel()
        event = self.next_event(now)
        if event is None:
            return
        self._unsub = async_track_point_in_time(self.hass, self._fired, event.at)

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _fired(self, at: datetime) -> None:
        self._unsub = None
        probe = at - _MATCH_TOLERANCE
        covers: set[str] = set()
        for event in self._events(probe):
            if abs(event.at - at) <= _MATCH_TOLERANCE:
                covers.update(event.covers)
        if covers:
            await self._on_fire(frozenset(covers), at)
        self.async_arm(at)
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_scheduler.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green. If `async_track_point_in_time` rejects the coroutine function directly, wrap it: `HassJob(self._fired)`.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: schedule tracker with rule timers, sun times and next-event views

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Controller

**Files:**
- Create: `custom_components/cover_automation/controller.py`
- Test: `tests/ha/test_controller.py`

**Interfaces:**
- Produces: `EVENT_ACTION = "cover_automation_action"`; `class CoverAutomationController(hass, entry, *, hub: HubConfig, covers: Mapping[str, tuple[CoverConfig, CoverBindings]], profiles: Mapping[str, Profile], store: CoverAutomationStore, hub_device_id: str)` implementing `entity.ControllerProtocol` plus: `async def async_start() -> None` (coroutine job for `async_at_started`), `async def async_stop() -> None`, `async def async_evaluate(now: datetime | None = None, *, cover_ids: Iterable[str] | None = None, rule_fired: frozenset[str] = frozenset()) -> None`, `def engine(cover_id) -> CoverEngine`, `def snapshot() -> dict[str, Any]` (diagnostics: per cover `persisted`, `runtime` summary, `view`), attributes `hub_view`, `cover_views`, `cover_names`, `started: bool`.
- Consumes: Tasks 1–4, engine facade, Store, repairs, views.

Behaviour contract (the tests below pin it):
1. `async_start`: builds engines (`CoverEngine(cfg, store.data.covers[id], override_dwell_s=hub.override_dwell_s)`), signal sets (`wind_active=persisted.wind_active`), the `ScheduleTracker`; seeds hub signals; fetches today's forecast (`async_fetch_today`, imported into the controller module so tests can patch `custom_components.cover_automation.controller.async_fetch_today`); subscribes `async_track_state_change_event` for every referenced entity; arms midnight (`async_track_time_change(hour=0, minute=0, second=10)`), hourly forecast refresh and the 5-minute fallback tick (`async_track_time_interval`); subscribes `EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED` for the `problem` flag; applies `store.data.verbose` to the integration logger; arms the schedule; runs `async_evaluate()`.
2. Per cover, the first evaluation is `decide()` → `reconcile()` → `evaluate()`; a cover whose actual is `cover_unavailable` is skipped (view status `cover_unavailable`) and reconciled the first time it reports a settled state.
3. `_sync_actual` classifies the current cover state on every evaluation and, when it changed since the last sync for a reconciled cover, calls `engine.on_transition(actual, now)` first; a `MANUAL` kind saves the Store immediately.
4. Acting: `Send` → (simulated: log, `engine.on_command_sent`, event) else feature check → `engine.on_command_sent(action, now)` → service call with `blocking=True` → on `HomeAssistantError`: warning, `retry_at = engine.on_command_failed(now)`, per-cover timer at `retry_at`; on success: `store.async_save()`, `EVENT_ACTION` fired with `{entity_id, cover_name, action ("open"|"close"), reason, layer, simulated}`, `last_engine_move` recorded. `notify_frost_conflict` → `persistent_notification.async_create(..., notification_id=f"cover_automation_frost_{cover_id}")` + repair `frost_conflict`; the repair clears when the winning layer is no longer frost.
5. Per-cover timer = min(`result.next_check_at`, signal set `next_check_at()`, hub `next_check_at()`, retry time); fired → `engine.check_pending(actual, now)` when a pending record exists (save when the persisted dict changed) → evaluate that cover.
6. Repairs updated every evaluation: hub `weather_unavailable` (beyond grace), `forecast_fetch_failed` (beyond grace), `sun_missing`, `frost_source_unavailable` (only when the outdoor sensor is configured or the weather entity is up but has no temperature), `wind_sensor_unavailable` (sensor configured and at least one cover has wind enabled); per cover `wind_unit_changed`, `door_sensor_unavailable`, `room_sensor_unusable`, `command_failures` (`engine.repair_needed`), `rule_skipped` per profile from `skipped_rules_today`.
7. Views rebuilt after every evaluation and published with `async_dispatcher_send(hass, signal_update(entry_id))`; `store.schedule_save()` after every evaluation.
8. Write API: `async_set_enabled`/`async_set_mode` mutate the persisted record, save immediately, evaluate the cover; `async_set_shading_mode`/`async_set_reopening_mode`/`async_set_simulation`/`async_set_verbose` mutate `store.data`, save, (verbose sets `logging.getLogger("custom_components.cover_automation")` to DEBUG or NOTSET), evaluate all; `async_reset_override` → `engine.reset(actual, now)`, save, evaluate; `async_evaluate_now(cover_ids)` → evaluate.
9. `async_stop` cancels every subscription and timer (state listeners, hub timers, per-cover timers, schedule), saves the Store, sets `started = False`. Nothing may fire afterwards.
10. Per-cover exceptions inside the evaluation loop are logged with `_LOGGER.exception` and never abort the other covers (spec §5).

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_controller.py`:
```python
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.cover_automation import const
from custom_components.cover_automation.controller import EVENT_ACTION, CoverAutomationController
from custom_components.cover_automation.engine.model import CoverPersisted, Owner, Status, Target
from custom_components.cover_automation.forecast import TodayForecast
from tests.ha.conftest import cover_subentry_data, set_cover, set_sensor, set_sun, set_weather

HOT = TodayForecast(30.0, 18.0)


async def start_controller(hass: HomeAssistant, hub_entry, *, forecast=HOT, cover_overrides=None, persisted=None):
    """Set up the hub (2a) with one cover subentry and start a controller on top of it."""
    overrides = cover_overrides or {}
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom", **overrides)))
    sub_id = next(iter(hub_entry.subentries))
    set_weather(hass, condition="sunny", temperature=20.0)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h", device_class="wind_speed")
    set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    data = hub_entry.runtime_data
    if persisted is not None:
        data.store.data.covers[sub_id] = persisted
    controller = CoverAutomationController(
        hass, hub_entry, hub=data.hub, covers=data.covers, profiles=data.profiles, store=data.store,
        hub_device_id=data.hub_device_id,
    )
    with patch("custom_components.cover_automation.controller.async_fetch_today", AsyncMock(return_value=forecast)):
        await controller.async_start()
        await hass.async_block_till_done()
    return controller, sub_id


@pytest.fixture
def cover_services(hass: HomeAssistant):
    return {
        "close": async_mock_service(hass, "cover", "close_cover"),
        "open": async_mock_service(hass, "cover", "open_cover"),
        "position": async_mock_service(hass, "cover", "set_cover_position"),
    }


async def test_hot_sunny_day_closes_cover_and_persists_ownership(hass, hub_entry, cover_services, hass_storage, freezer):
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        assert len(cover_services["close"]) == 1
        assert cover_services["close"][0].data["entity_id"] == "cover.bedroom"
        p = controller.engine(sub_id).p
        assert p.owner is Owner.ENGINE and p.engine_target is Target.CLOSED
        stored = hass_storage[const.storage_key(hub_entry.entry_id)]["data"]["covers"][sub_id]
        assert stored["owner"] == "engine" and stored["engine_target"] == "closed"
        view = controller.cover_views[sub_id]
        assert view.desired_state == "closed" and view.winning_layer == "shading" and view.next_planned_action
        set_cover(hass, "cover.bedroom", state="closing", position=60)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        assert controller.cover_views[sub_id].status is Status.CLOSED_SHADING
        assert controller.engine(sub_id).rt.pending is None
        assert len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_manual_open_becomes_override_and_is_respected(hass, hub_entry, cover_services, freezer):
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="opening", position=20)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="open", position=100)
        await hass.async_block_till_done()
        view = controller.cover_views[sub_id]
        assert view.status is Status.MANUAL_OVERRIDE and view.override_active and view.overridden_desired == "closed"
        assert controller.engine(sub_id).p.owner is Owner.USER
        freezer.tick(timedelta(minutes=12))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 1  # no re-close during the dwell
        await controller.async_reset_override(sub_id)
        await hass.async_block_till_done()
        assert not controller.cover_views[sub_id].override_active
        assert len(cover_services["close"]) == 2  # shading closes again after the reset
    finally:
        await controller.async_stop()


async def test_wind_opens_even_during_override(hass, hub_entry, cover_services, freezer):
    controller, sub_id = await start_controller(
        hass, hub_entry, cover_overrides={const.CONF_WIND_ENABLED: True, const.CONF_WIND_UPPER: 60,
                                          const.CONF_WIND_LOWER: 50, const.CONF_WIND_UNIT: "km/h"})
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        set_sensor(hass, "sensor.wind", 75.0, unit="km/h", device_class="wind_speed")
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1
        view = controller.cover_views[sub_id]
        assert view.status is Status.PROTECTED_WIND and view.wind_active and controller.hub_view.any_wind_active
    finally:
        await controller.async_stop()


async def test_command_failure_sets_status_and_retries(hass, hub_entry, freezer):
    attempts: list[ServiceCall] = []

    async def failing(call: ServiceCall) -> None:
        attempts.append(call)
        raise HomeAssistantError("device offline")

    hass.services.async_register("cover", "close_cover", failing)
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        assert len(attempts) == 1 and controller.cover_views[sub_id].status is Status.COMMAND_FAILED
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(attempts) == 2
    finally:
        await controller.async_stop()


async def test_simulation_logs_event_without_service_call(hass, hub_entry, cover_services):
    events = async_capture_events(hass, EVENT_ACTION)
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    set_weather(hass); set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h"); set_cover(hass, "cover.bedroom")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    data = hub_entry.runtime_data
    data.store.data.simulation = True
    controller = CoverAutomationController(hass, hub_entry, hub=data.hub, covers=data.covers, profiles=data.profiles,
                                           store=data.store, hub_device_id=data.hub_device_id)
    with patch("custom_components.cover_automation.controller.async_fetch_today", AsyncMock(return_value=HOT)):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        assert not cover_services["close"]
        assert len(events) == 1 and events[0].data["simulated"] is True and events[0].data["action"] == "close"
        assert events[0].data["entity_id"] == "cover.bedroom" and events[0].data["layer"] == "shading"
    finally:
        await controller.async_stop()


async def test_disabled_cover_gets_no_commands(hass, hub_entry, cover_services):
    controller, sub_id = await start_controller(hass, hub_entry, persisted=CoverPersisted(enabled=False))
    try:
        assert not cover_services["close"] and controller.cover_views[sub_id].status is Status.DISABLED
        await controller.async_set_enabled(sub_id, True)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_unavailable_cover_is_skipped_until_it_reports(hass, hub_entry, cover_services):
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    sub_id = next(iter(hub_entry.subentries))
    set_weather(hass); set_sun(hass, elevation=40.0, azimuth=180.0); set_sensor(hass, "sensor.wind", 5.0, unit="km/h")
    hass.states.async_set("cover.bedroom", "unavailable")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    data = hub_entry.runtime_data
    controller = CoverAutomationController(hass, hub_entry, hub=data.hub, covers=data.covers, profiles=data.profiles,
                                           store=data.store, hub_device_id=data.hub_device_id)
    with patch("custom_components.cover_automation.controller.async_fetch_today", AsyncMock(return_value=HOT)):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        assert controller.cover_views[sub_id].status is Status.COVER_UNAVAILABLE and not cover_services["close"]
        assert controller.engine(sub_id).p.owner is None  # not reconciled yet
        set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
        await hass.async_block_till_done()
        assert controller.engine(sub_id).p.owner is Owner.ENGINE and len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_hub_repairs_and_problem_flag(hass, hub_entry, cover_services, freezer):
    controller, sub_id = await start_controller(hass, hub_entry, forecast=None)
    try:
        assert not controller.hub_view.problem
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        reg = ir.async_get(hass)
        assert reg.async_get_issue(const.DOMAIN, f"forecast_fetch_failed_{hub_entry.entry_id}") is not None
        assert controller.hub_view.problem is True
        hass.states.async_remove("sun.sun")
        await controller.async_evaluate_now()
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, f"sun_missing_{hub_entry.entry_id}") is not None
    finally:
        await controller.async_stop()


async def test_stop_cancels_everything(hass, hub_entry, cover_services, freezer):
    controller, sub_id = await start_controller(hass, hub_entry)
    await controller.async_stop()
    assert not controller.started
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cover_services["close"]) == 1  # nothing ran after stop (and no lingering timers at teardown)
```

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/pytest tests/ha/test_controller.py -q` → import error.

- [ ] **Step 3: Implement**

`controller.py`:
```python
"""Controller: subscriptions, timers, evaluate → act → persist (spec §1.3–§1.5, §2, §5)."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import const, repairs
from .config_map import CoverBindings, HubConfig
from .engine.cover import CoverEngine
from .engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverState,
    Defer,
    Layer,
    Mode,
    ReopeningMode,
    Send,
    ShadingMode,
    Status,
    StepResult,
    Target,
    TransitionKind,
)
from .engine.schedule import Profile
from .forecast import async_fetch_today
from .scheduler import ScheduleTracker
from .signals_adapter import CoverSignalSet, HubSignalSource, classify_state, cover_supports, sun_position
from .store import CoverAutomationStore
from .views import CoverView, HubView, signal_update

_LOGGER = logging.getLogger(__name__)
_INTEGRATION_LOGGER = logging.getLogger("custom_components.cover_automation")


def _jsonable(value: Any) -> Any:
    """datetime → ISO string, StrEnum → value, recursively (diagnostics)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)

EVENT_ACTION = f"{const.DOMAIN}_action"
FALLBACK_TICK = timedelta(minutes=5)
FORECAST_REFRESH = timedelta(hours=1)
FORECAST_THROTTLE = timedelta(minutes=10)


class CoverAutomationController:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        hub: HubConfig,
        covers: Mapping[str, tuple[CoverConfig, CoverBindings]],
        profiles: Mapping[str, Profile],
        store: CoverAutomationStore,
        hub_device_id: str,
    ) -> None:
        self.hass, self.entry, self.hub = hass, entry, hub
        self._covers = dict(covers)
        self._profiles = dict(profiles)
        self._store = store
        self.hub_device_id = hub_device_id
        self.started = False
        self.cover_names: dict[str, str] = {cid: cfg.name for cid, (cfg, _b) in covers.items()}
        self._engines: dict[str, CoverEngine] = {}
        self._signals: dict[str, CoverSignalSet] = {}
        self._hub_signals = HubSignalSource(hass, hub, store.data.latch)
        self._schedule = ScheduleTracker(
            hass, self._profiles, {cid: cfg.profile_id for cid, (cfg, _b) in covers.items()}, self._on_rule_fired
        )
        self._actual: dict[str, CoverState] = {}
        self._reconciled: set[str] = set()
        self._retry_at: dict[str, datetime] = {}
        self._last_engine_move: dict[str, datetime] = {}
        self._last_result: dict[str, tuple[StepResult, CoverInputs]] = {}
        self._cover_timers: dict[str, CALLBACK_TYPE] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_forecast_fetch: datetime | None = None
        self._watched: dict[str, set[str] | None] = {}
        self._problem = False
        self.hub_view = HubView()
        self.cover_views: dict[str, CoverView] = {
            cid: CoverView(name=cfg.name, cover_entity=bind.cover_entity) for cid, (cfg, bind) in covers.items()
        }

    # -- lifecycle ---------------------------------------------------------------------

    def engine(self, cover_id: str) -> CoverEngine:
        return self._engines[cover_id]

    async def async_start(self) -> None:
        now = dt_util.utcnow()
        for cover_id, (cfg, bind) in self._covers.items():
            persisted = self._store.data.covers.setdefault(cover_id, CoverPersisted())
            self._engines[cover_id] = CoverEngine(cfg, persisted, override_dwell_s=self.hub.override_dwell_s)
            self._signals[cover_id] = CoverSignalSet(
                self.hass, cfg, bind, self.hub, wind_active=persisted.wind_active,
                sun_release_margin=self.hub.sun_release_margin,
            )
        self._apply_verbose(self._store.data.verbose)
        self._hub_signals.seed(now)
        await self._async_refresh_forecast(now, force=True)
        self._subscribe()
        self.started = True
        self._schedule.async_arm(now)
        await self.async_evaluate(now)

    async def async_stop(self) -> None:
        self.started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in self._cover_timers.values():
            cancel()
        self._cover_timers.clear()
        self._schedule.async_cancel()
        await self._store.async_save()

    def _subscribe(self) -> None:
        hub, hass = self.hub, self.hass
        watched: dict[str, set[str] | None] = {}  # entity → affected cover ids (None = all)
        for e in (hub.weather_entity, hub.wind_sensor, hub.outdoor_temperature_sensor,
                  hub.sunny_override_entity, hub.hot_override_entity, "sun.sun"):
            if e:
                watched[e] = None
        for cover_id, (_cfg, bind) in self._covers.items():
            for e in (bind.cover_entity, bind.door_sensor, bind.room_sensor):
                if e:
                    watched.setdefault(e, set())
                    if watched[e] is not None:
                        watched[e].add(cover_id)  # type: ignore[union-attr]
        self._watched = watched
        self._unsubs.append(async_track_state_change_event(hass, list(watched), self._on_state_change))
        self._unsubs.append(async_track_time_change(hass, self._on_midnight, hour=0, minute=0, second=10))
        self._unsubs.append(async_track_time_interval(hass, self._on_forecast_tick, FORECAST_REFRESH))
        self._unsubs.append(async_track_time_interval(hass, self._on_fallback_tick, FALLBACK_TICK))
        self._unsubs.append(hass.bus.async_listen(ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, self._on_issue_event))

    # -- triggers ------------------------------------------------------------------------

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        affected = self._watched.get(entity_id)
        if entity_id == self.hub.weather_entity:
            self.hass.async_create_task(self._async_weather_changed())
            return
        self.hass.async_create_task(self.async_evaluate(cover_ids=affected))

    async def _async_weather_changed(self) -> None:
        now = dt_util.utcnow()
        await self._async_refresh_forecast(now, force=False)
        await self.async_evaluate(now)

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_midnight())

    async def _async_midnight(self) -> None:
        now = dt_util.utcnow()
        self._hub_signals.rollover(dt_util.as_local(now).date())
        await self._async_refresh_forecast(now, force=True)
        self._schedule.async_arm(now)
        await self.async_evaluate(now)

    @callback
    def _on_forecast_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_forecast_tick())

    async def _async_forecast_tick(self) -> None:
        now = dt_util.utcnow()
        await self._async_refresh_forecast(now, force=True)
        await self.async_evaluate(now)

    @callback
    def _on_fallback_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate())

    @callback
    def _on_issue_event(self, _event: Event[Any]) -> None:
        self._refresh_problem()
        self._publish()

    def _refresh_problem(self) -> None:
        registry = ir.async_get(self.hass)
        self._problem = any(
            domain == const.DOMAIN and issue.dismissed_version is None
            for (domain, _iid), issue in registry.issues.items()
        )

    async def _on_rule_fired(self, covers: frozenset[str], at: datetime) -> None:
        await self.async_evaluate(at, cover_ids=covers, rule_fired=covers)

    async def _async_refresh_forecast(self, now: datetime, *, force: bool) -> None:
        if not force and self._last_forecast_fetch and now - self._last_forecast_fetch < FORECAST_THROTTLE:
            return
        self._last_forecast_fetch = now
        local_day = dt_util.as_local(now).date()
        today = await async_fetch_today(self.hass, self.hub.weather_entity, local_day, dt_util.get_default_time_zone())
        self._hub_signals.apply_forecast(today, local_day, now)

    # -- evaluation ----------------------------------------------------------------------

    async def async_evaluate(
        self, now: datetime | None = None, *, cover_ids: Iterable[str] | None = None,
        rule_fired: frozenset[str] = frozenset(),
    ) -> None:
        if not self.started:
            return
        now = now or dt_util.utcnow()
        ids = list(cover_ids) if cover_ids is not None else list(self._engines)
        sun = sun_position(self.hass)
        self._hub_signals.update(now)
        self._update_hub_repairs(now, sun)
        if sun is not None:
            hub_sig = self._hub_signals.signals(now, self._store.data, sun[1])
            for cover_id in ids:
                try:
                    await self._evaluate_cover(cover_id, now, sun, hub_sig, cover_id in rule_fired)
                except Exception:  # per-cover isolation (spec §5)
                    _LOGGER.exception("Evaluation of cover %s failed", self.cover_names[cover_id])
        self._update_profile_repairs(now)
        self._publish()
        self._store.schedule_save()

    async def _sync_actual(self, cover_id: str, now: datetime) -> CoverState:
        _cfg, bind = self._covers[cover_id]
        actual = classify_state(self.hass.states.get(bind.cover_entity), self.hub.tolerance)
        previous = self._actual.get(cover_id)
        self._actual[cover_id] = actual
        if previous is not None and actual is not previous and cover_id in self._reconciled:
            result = self._engines[cover_id].on_transition(actual, now)
            _LOGGER.debug("%s: %s → %s (%s)", self.cover_names[cover_id], previous, actual, result.kind)
            if result.kind is TransitionKind.MANUAL:
                await self._store.async_save()
        return actual

    async def _evaluate_cover(
        self, cover_id: str, now: datetime, sun: tuple[float, float], hub_sig: Any, rule_fired: bool
    ) -> None:
        engine, sig = self._engines[cover_id], self._signals[cover_id]
        actual = await self._sync_actual(cover_id, now)
        if cover_id not in self._reconciled:
            if actual is CoverState.UNAVAILABLE:
                self.cover_views[cover_id] = CoverView(
                    name=self.cover_names[cover_id], cover_entity=self._covers[cover_id][1].cover_entity,
                    status=Status.COVER_UNAVAILABLE, enabled=engine.p.enabled, mode=engine.p.mode,
                )
                return
            sig.seed(now, sun)
            first = self._schedule.view(cover_id, now, actual, engine.p.manual_move_at, None)
            decision = engine.decide(sig.inputs(actual, first), hub_sig)
            before = engine.p.to_dict()
            message = engine.reconcile(actual, decision, now)
            _LOGGER.debug("%s: reconcile → %s", self.cover_names[cover_id], message)
            self._reconciled.add(cover_id)
            if engine.p.to_dict() != before:
                await self._store.async_save()
        else:
            sig.update(now, sun)
        schedule = self._schedule.view(
            cover_id, now, actual, engine.p.manual_move_at, engine.rt.open_rule_satisfied_at
        )
        inputs = sig.inputs(actual, schedule)
        result = engine.evaluate(inputs, hub_sig, rule_fired=rule_fired)
        self._last_result[cover_id] = (result, inputs)
        await self._act(cover_id, result, now)
        self._update_cover_repairs(cover_id, sig)
        self.cover_views[cover_id] = self._build_cover_view(cover_id, result, inputs, now)
        self._arm_cover_timer(cover_id, result, sig, now)

    # -- acting --------------------------------------------------------------------------

    async def _act(self, cover_id: str, result: StepResult, now: datetime) -> None:
        engine = self._engines[cover_id]
        cfg, bind = self._covers[cover_id]
        decision, action = result.decision, result.action
        frost_issue = repairs.cover_issue_id(repairs.ISSUE_FROST_CONFLICT, cover_id)
        if result.notify_frost_conflict:
            persistent_notification.async_create(
                self.hass,
                f"{cfg.name} should open for wind or door protection, but frost protection keeps it in place.",
                title="Cover Automation",
                notification_id=f"{const.DOMAIN}_frost_{cover_id}",
            )
            repairs.set_issue(self.hass, frost_issue, True, translation_key="frost_conflict", placeholders={"cover": cfg.name})
        elif decision.layer is not Layer.FROST:
            repairs.set_issue(self.hass, frost_issue, False, translation_key="frost_conflict")
        if not isinstance(action, Send):
            return
        verb = "open" if action.target is Target.OPEN else "close"
        if action.simulated:
            _LOGGER.info("Simulation: would %s %s (%s: %s)", verb, cfg.name, decision.layer.value, decision.reason)
            engine.on_command_sent(action, now)
            self._fire_action_event(cover_id, verb, decision, simulated=True)
            return
        open_close, set_position = cover_supports(self.hass.states.get(bind.cover_entity))
        unsupported = not open_close and not set_position
        repairs.set_issue(
            self.hass, repairs.cover_issue_id(repairs.ISSUE_COVER_UNSUPPORTED, cover_id), unsupported,
            translation_key="cover_unsupported", placeholders={"cover": cfg.name},
        )
        if unsupported:
            return
        if open_close:
            service, data = (SERVICE_OPEN_COVER if verb == "open" else SERVICE_CLOSE_COVER), {ATTR_ENTITY_ID: bind.cover_entity}
        else:
            service = SERVICE_SET_COVER_POSITION
            data = {ATTR_ENTITY_ID: bind.cover_entity, ATTR_POSITION: 100 if verb == "open" else 0}
        engine.on_command_sent(action, now)
        try:
            await self.hass.services.async_call(COVER_DOMAIN, service, data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: %s failed: %s", cfg.name, service, err)
            self._retry_at[cover_id] = engine.on_command_failed(now)
            return
        self._retry_at.pop(cover_id, None)
        self._last_engine_move[cover_id] = now
        await self._store.async_save()
        self._fire_action_event(cover_id, verb, decision, simulated=False)

    def _fire_action_event(self, cover_id: str, verb: str, decision: Any, *, simulated: bool) -> None:
        _cfg, bind = self._covers[cover_id]
        self.hass.bus.async_fire(EVENT_ACTION, {
            ATTR_ENTITY_ID: bind.cover_entity, "cover_name": self.cover_names[cover_id], "action": verb,
            "reason": decision.reason, "layer": decision.layer.value, "simulated": simulated,
        })

    # -- timers --------------------------------------------------------------------------

    def _arm_cover_timer(self, cover_id: str, result: StepResult, sig: CoverSignalSet, now: datetime) -> None:
        if cancel := self._cover_timers.pop(cover_id, None):
            cancel()
        candidates = [c for c in (result.next_check_at, sig.next_check_at(), self._hub_signals.next_check_at(),
                                  self._retry_at.get(cover_id)) if c is not None]
        if not candidates:
            return
        delay = max(1.0, (min(candidates) - now).total_seconds())
        self._cover_timers[cover_id] = async_call_later(self.hass, delay, partial(self._on_cover_timer, cover_id))

    @callback
    def _on_cover_timer(self, cover_id: str, _now: datetime) -> None:
        self._cover_timers.pop(cover_id, None)
        self.hass.async_create_task(self._async_cover_timer(cover_id))

    async def _async_cover_timer(self, cover_id: str) -> None:
        if not self.started:
            return
        now = dt_util.utcnow()
        engine = self._engines[cover_id]
        if engine.rt.pending is not None:
            actual = await self._sync_actual(cover_id, now)
            before = engine.p.to_dict()
            if (message := engine.check_pending(actual, now)) is not None:
                _LOGGER.debug("%s: %s", self.cover_names[cover_id], message)
            if engine.p.to_dict() != before:
                await self._store.async_save()
        await self.async_evaluate(now, cover_ids={cover_id})

    # -- repairs -------------------------------------------------------------------------

    def _update_hub_repairs(self, now: datetime, sun: tuple[float, float] | None) -> None:
        hs, hub, eid = self._hub_signals, self.hub, self.entry.entry_id
        any_wind = any(cfg.wind_enabled for cfg, _b in self._covers.values())
        repairs.set_issue(self.hass, repairs.hub_issue_id(eid, repairs.ISSUE_WEATHER_UNAVAILABLE),
                          hs.weather_unavailable_beyond_grace(now), translation_key="weather_unavailable")
        repairs.set_issue(self.hass, repairs.hub_issue_id(eid, repairs.ISSUE_FORECAST_FAILED),
                          hs.forecast_failed_beyond_grace(now), translation_key="forecast_fetch_failed")
        repairs.set_issue(self.hass, repairs.hub_issue_id(eid, repairs.ISSUE_SUN_MISSING), sun is None,
                          translation_key="sun_missing")
        if hub.outdoor_temperature_sensor:
            frost_source_missing = hs.frost_source_unavailable
        else:  # weather-entity temperature: only flag when the entity is up but has no temperature
            frost_source_missing = hs.frost.active is None and hs.frost_source_unavailable and not hs.weather_unavailable_beyond_grace(now)
        repairs.set_issue(self.hass, repairs.hub_issue_id(eid, repairs.ISSUE_FROST_SOURCE_UNAVAILABLE),
                          frost_source_missing, translation_key="frost_source_unavailable")
        repairs.set_issue(self.hass, repairs.hub_issue_id(eid, repairs.ISSUE_WIND_UNAVAILABLE),
                          any_wind and hs.wind_sensor_unavailable, translation_key="wind_sensor_unavailable")

    def _update_cover_repairs(self, cover_id: str, sig: CoverSignalSet) -> None:
        name, engine = self.cover_names[cover_id], self._engines[cover_id]
        placeholders = {"cover": name}
        repairs.set_issue(self.hass, repairs.cover_issue_id(repairs.ISSUE_WIND_UNIT_CHANGED, cover_id), sig.wind_unit_mismatch,
                          translation_key="wind_unit_changed",
                          placeholders={**placeholders, "stored": str(self._covers[cover_id][1].wind_unit), "current": str(sig.wind_unit_current)})
        repairs.set_issue(self.hass, repairs.cover_issue_id(repairs.ISSUE_DOOR_UNAVAILABLE, cover_id), sig.door_unavailable,
                          translation_key="door_sensor_unavailable", placeholders=placeholders)
        repairs.set_issue(self.hass, repairs.cover_issue_id(repairs.ISSUE_ROOM_UNUSABLE, cover_id), sig.room_unusable,
                          translation_key="room_sensor_unusable", placeholders=placeholders)
        repairs.set_issue(self.hass, repairs.cover_issue_id(repairs.ISSUE_COMMAND_FAILURES, cover_id), engine.repair_needed,
                          translation_key="command_failures", placeholders=placeholders)

    def _update_profile_repairs(self, now: datetime) -> None:
        skipped = set(self._schedule.skipped_rules_today(now))
        for profile_id, profile in self._profiles.items():
            for index in range(len(profile.rules)):
                repairs.set_issue(
                    self.hass, repairs.cover_issue_id(repairs.ISSUE_RULE_SKIPPED, f"{profile_id}_{index}"),
                    (profile_id, index) in skipped, translation_key="rule_skipped",
                    placeholders={"profile": profile.name, "rule": str(index + 1)},
                )

    # -- views ---------------------------------------------------------------------------

    def _planned(self, cover_id: str, result: StepResult, now: datetime) -> tuple[str | None, datetime | None]:
        engine = self._engines[cover_id]
        if isinstance(result.action, Defer):
            target = result.decision.desired.value
            return f"{target} ({result.action.reason})", result.action.until
        if engine.rt.pending is not None:
            return f"{engine.rt.pending.target.value} (in progress)", None
        event = self._schedule.next_event_for(cover_id, now)
        if event is not None:
            return f"{event.action.value} by schedule", event.at
        return None, None

    def _build_cover_view(self, cover_id: str, result: StepResult, inputs: CoverInputs, now: datetime) -> CoverView:
        engine, sig = self._engines[cover_id], self._signals[cover_id]
        cfg, bind = self._covers[cover_id]
        p, d = engine.p, result.decision
        planned, planned_at = self._planned(cover_id, result, now)
        return CoverView(
            name=cfg.name, cover_entity=bind.cover_entity, status=result.status,
            desired_state=d.desired.value, actual_state=inputs.actual.value, winning_layer=d.layer.value,
            reason=d.reason, sun_hits=inputs.sun_hits, sunny=self._hub_signals.sunny_state,
            hot_day=self._hub_signals.hot_day, room_state=sig.room_state, wind_state=sig.wind_state,
            active_rule=self._schedule.active_rule_label(cover_id, now), next_planned_action=planned,
            next_planned_at=planned_at, last_engine_move=self._last_engine_move.get(cover_id),
            owner=p.owner.value if p.owner else None, degraded=inputs.room_degraded, enabled=p.enabled,
            mode=p.mode, override_active=p.dam is not None,
            override_since=p.manual_move_at if p.dam is not None else None,
            overridden_desired=p.dam.value if p.dam else None, wind_active=inputs.wind_active,
        )

    def _build_hub_view(self, now: datetime) -> HubView:
        hs, data = self._hub_signals, self._store.data
        event = self._schedule.next_event(now)
        return HubView(
            sunny=hs.sunny_state, hot_day=hs.hot_day, frost=hs.frost.active,
            any_wind_active=any(v.wind_active for v in self.cover_views.values()),
            forecast_max_c=hs.latch.max, forecast_min_c=hs.latch.min,
            next_event_at=event.at if event else None, next_event_profile=event.profile_name if event else None,
            next_event_action=event.action.value if event else None,
            next_event_covers=tuple(self.cover_names[c] for c in event.covers) if event else (),
            shading_mode=data.shading_mode, reopening_mode=data.reopening_mode, simulation=data.simulation,
            verbose=data.verbose, problem=self._problem,
        )

    def _publish(self) -> None:
        self.hub_view = self._build_hub_view(dt_util.utcnow())
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for cover_id, engine in self._engines.items():
            rt = engine.rt
            out[cover_id] = {
                "persisted": engine.p.to_dict(),
                "runtime": {
                    "pending": None if rt.pending is None else {"target": rt.pending.target.value, "layer": rt.pending.layer.value,
                                                                "sent_at": rt.pending.sent_at.isoformat()},
                    "last_send_at": rt.last_send_at.isoformat() if rt.last_send_at else None,
                    "backoff_until": rt.backoff_until.isoformat() if rt.backoff_until else None,
                    "consecutive_failures": rt.consecutive_failures, "command_failed": rt.command_failed,
                    "unconfirmed": rt.unconfirmed, "restoring_until": rt.restoring_until.isoformat() if rt.restoring_until else None,
                },
                "view": _jsonable(asdict(self.cover_views[cover_id])),
            }
        return out

    # -- write API (decision 17) ---------------------------------------------------------

    def _apply_verbose(self, on: bool) -> None:
        _INTEGRATION_LOGGER.setLevel(logging.DEBUG if on else logging.NOTSET)

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None:
        self._engines[cover_id].p.enabled = enabled
        await self._store.async_save()
        await self.async_evaluate(cover_ids={cover_id})

    async def async_set_mode(self, cover_id: str, mode: Mode) -> None:
        self._engines[cover_id].p.mode = mode
        await self._store.async_save()
        await self.async_evaluate(cover_ids={cover_id})

    async def async_set_shading_mode(self, mode: ShadingMode) -> None:
        self._store.data.shading_mode = mode
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None:
        self._store.data.reopening_mode = mode
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_simulation(self, on: bool) -> None:
        self._store.data.simulation = on
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_verbose(self, on: bool) -> None:
        self._store.data.verbose = on
        self._apply_verbose(on)
        await self._store.async_save()
        self._publish()

    async def async_reset_override(self, cover_id: str) -> None:
        now = dt_util.utcnow()
        actual = await self._sync_actual(cover_id, now)
        self._engines[cover_id].reset(actual, now)
        await self._store.async_save()
        await self.async_evaluate(now, cover_ids={cover_id})

    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None:
        await self.async_evaluate(cover_ids=cover_ids)
```
Notes for the implementer: tasks created from callbacks may use `self.entry.async_create_background_task(self.hass, coro, name=...)` so they are cancelled with the entry; if that helper misbehaves for an entry that is not loaded in a unit test, keep `hass.async_create_task`. Add `async def async_start_job(self, hass: HomeAssistant) -> None: await self.async_start()` (the coroutine `async_at_started` calls in Task 9).

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_controller.py -q && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green; teardown must report no lingering timers.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: controller — evaluate, act, persist, timers, repairs and write API

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Switch, select and button platforms

**Files:**
- Create: `custom_components/cover_automation/switch.py`, `select.py`, `button.py`
- Test: `tests/ha/test_entities.py` (extend)

**Interfaces:**
- Produces: each module has `async def async_setup_entry(hass, entry, async_add_entities)` reading `entry.runtime_data.controller` (a `ControllerProtocol`) and `entry.runtime_data.covers`; hub entities added once, cover entities added per subentry with `config_subentry_id=subentry_id`.
  - `switch.py`: `SimulationModeSwitch` (key `simulation_mode`, CONFIG, `is_on = hub_view.simulation`, `async_turn_on/off → controller.async_set_simulation`), `VerboseLoggingSwitch` (key `verbose_logging`, CONFIG → `async_set_verbose`), `CoverEnabledSwitch` (key `enabled`, CONFIG, `is_on = view.enabled` → `async_set_enabled(subentry_id, …)`).
  - `select.py`: `ShadingModeSelect` (key `shading_mode`, options `[m.value for m in ShadingMode]`, current `hub_view.shading_mode.value`, `async_select_option → async_set_shading_mode(ShadingMode(option))`), `ReopeningModeSelect` (key `reopening_mode`), `CoverModeSelect` (key `mode`, options from `Mode`, `view.mode.value` → `async_set_mode`); all CONFIG.
  - `button.py`: `EvaluateNowButton` (key `evaluate_now`, CONFIG → `async_evaluate_now()`), `ResetOverrideButton` (key `reset_override`, CONFIG → `async_reset_override(subentry_id)`).
- Consumes: Task 2 `HubEntity`, `CoverEntityBase`, `ControllerProtocol`; `tests/ha/fakes.FakeController`.

- [ ] **Step 1: Write the failing tests** (append to `tests/ha/test_entities.py`)

```python
from types import SimpleNamespace

from homeassistant.helpers.entity import EntityCategory

from custom_components.cover_automation import button, select, switch
from custom_components.cover_automation.engine.model import Mode, ReopeningMode, ShadingMode
from custom_components.cover_automation.views import CoverView, HubView


async def collect(hass, hub_entry, module, ctrl):
    """Run a platform's async_setup_entry against a fake controller; return (entities, per-subentry ids)."""
    hub_entry.runtime_data = SimpleNamespace(controller=ctrl, covers={"sub1": (None, None)})
    added: list = []
    subentry_ids: list = []

    def add(entities, update_before_add=False, *, config_subentry_id=None):
        added.extend(entities)
        subentry_ids.append(config_subentry_id)

    await module.async_setup_entry(hass, hub_entry, add)
    return added, subentry_ids


def fake_with_cover():
    ctrl = FakeController()
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom", enabled=True, mode=Mode.AUTO)
    ctrl.cover_names["sub1"] = "Bedroom"
    return ctrl


async def test_switch_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    ctrl.hub_view = HubView(simulation=True, verbose=False)
    entities, sub_ids = await collect(hass, hub_entry, switch, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {"simulation_mode", "verbose_logging", "enabled"} and "sub1" in sub_ids
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)
    assert by_key["simulation_mode"].is_on is True and by_key["verbose_logging"].is_on is False and by_key["enabled"].is_on is True
    await by_key["simulation_mode"].async_turn_off()
    await by_key["verbose_logging"].async_turn_on()
    await by_key["enabled"].async_turn_off()
    assert ctrl.calls == [("set_simulation", False), ("set_verbose", True), ("set_enabled", "sub1", False)]
    assert by_key["enabled"].unique_id == "sub1_enabled" and by_key["simulation_mode"].unique_id == f"{hub_entry.entry_id}_simulation_mode"


async def test_select_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    ctrl.hub_view = HubView(shading_mode=ShadingMode.FORCED_ALL, reopening_mode=ReopeningMode.ACTIVE)
    entities, _ = await collect(hass, hub_entry, select, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert by_key["shading_mode"].current_option == "forced_all" and by_key["shading_mode"].options == ["off", "auto", "forced_sunlit", "forced_all"]
    assert by_key["reopening_mode"].current_option == "active" and by_key["mode"].current_option == "auto"
    await by_key["shading_mode"].async_select_option("auto")
    await by_key["reopening_mode"].async_select_option("off")
    await by_key["mode"].async_select_option("dark_only")
    assert ctrl.calls == [("set_shading_mode", ShadingMode.AUTO), ("set_reopening_mode", ReopeningMode.OFF), ("set_mode", "sub1", Mode.DARK_ONLY)]
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)


async def test_button_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    entities, _ = await collect(hass, hub_entry, button, ctrl)
    by_key = {e.translation_key: e for e in entities}
    await by_key["evaluate_now"].async_press()
    await by_key["reset_override"].async_press()
    assert ctrl.calls == [("evaluate_now", None), ("reset_override", "sub1")]
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)
```

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/pytest tests/ha/test_entities.py -q` → import errors.

- [ ] **Step 3: Implement**

`switch.py`:
```python
"""Switch platform: hub simulation/verbose, per-cover enabled (spec §4)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities([SimulationModeSwitch(entry, controller), VerboseLoggingSwitch(entry, controller)])
    for subentry_id in entry.runtime_data.covers:
        async_add_entities([CoverEnabledSwitch(entry, controller, subentry_id)], config_subentry_id=subentry_id)


class SimulationModeSwitch(HubEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "simulation_mode")

    @property
    def is_on(self) -> bool:
        return self.hub_view.simulation

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_simulation(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_simulation(False)


class VerboseLoggingSwitch(HubEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "verbose_logging")

    @property
    def is_on(self) -> bool:
        return self.hub_view.verbose

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_verbose(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_verbose(False)


class CoverEnabledSwitch(CoverEntityBase, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str) -> None:
        super().__init__(entry, controller, subentry_id, "enabled")

    @property
    def is_on(self) -> bool:
        return self.view.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(self.subentry_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(self.subentry_id, False)
```
`select.py` follows the same shape with `SelectEntity` (`_attr_options`, `current_option`, `async_select_option(option)` converting through the enum) for `ShadingModeSelect("shading_mode", ShadingMode, hub_view.shading_mode, async_set_shading_mode)`, `ReopeningModeSelect("reopening_mode", …)`, `CoverModeSelect("mode", Mode, view.mode, async_set_mode(subentry_id, …))`; write the three classes out in full (no shared factory needed). `button.py` with `ButtonEntity` and `async_press` for `EvaluateNowButton("evaluate_now")` → `await self._controller.async_evaluate_now()` and `ResetOverrideButton("reset_override")` → `await self._controller.async_reset_override(self.subentry_id)`. `AddConfigEntryEntitiesCallback` lives in `homeassistant.helpers.entity_platform`; the test's `add` shim accepts the `config_subentry_id` keyword.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_entities.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: switch, select and button platforms writing through the controller

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Sensor and binary sensor platforms

**Files:**
- Create: `custom_components/cover_automation/sensor.py`, `binary_sensor.py`
- Test: `tests/ha/test_entities.py` (extend)

**Interfaces:**
- Produces:
  - `sensor.py`: `ForecastMaxSensor` (key `forecast_max_today`, DIAGNOSTIC, `SensorDeviceClass.TEMPERATURE`, `native_unit_of_measurement = UnitOfTemperature.CELSIUS`, `native_value = hub_view.forecast_max_c`), `ForecastMinSensor` (`forecast_min_today`), `NextScheduledEventSensor` (key `next_scheduled_event`, DIAGNOSTIC, `SensorDeviceClass.TIMESTAMP`, `native_value = hub_view.next_event_at`, attributes `profile`, `action`, `covers`), `CoverStatusSensor` (key `status`, **no category**, `SensorDeviceClass.ENUM`, `options = [s.value for s in Status]`, `native_value = view.status.value`, `extra_state_attributes` = `desired_state, actual_state, winning_layer, reason, sun_hits, sunny, hot_day, room_state, wind_state, active_rule, next_planned_action, next_planned_at (ISO or None), last_engine_move (ISO or None), owner, degraded`; class attribute `_unrecorded_attributes = frozenset({"winning_layer", "sun_hits", "sunny", "hot_day", "room_state", "wind_state", "active_rule", "next_planned_action", "next_planned_at", "last_engine_move", "owner", "degraded"})`).
  - `binary_sensor.py`: hub DIAGNOSTIC `HotDaySensor` (`hot_day`, `is_on = hub_view.hot_day`, may be None), `SunnySensor` (`sunny`), `FrostActiveSensor` (`frost_active`, device class COLD), `AnyWindProtectionSensor` (`any_wind_protection_active`), `ProblemSensor` (`problem`, device class PROBLEM, `is_on = hub_view.problem`); cover `ManualOverrideSensor` (key `manual_override`, **no category**, `is_on = view.override_active`, attributes `since` (ISO), `overridden_desired`), `SunHitsSensor` (`sun_hits`, DIAGNOSTIC), `WindProtectionActiveSensor` (`wind_protection_active`, DIAGNOSTIC).

- [ ] **Step 1: Write the failing tests** (append)

```python
from datetime import UTC, datetime

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.cover_automation import binary_sensor, sensor
from custom_components.cover_automation.engine.model import Status


async def test_sensor_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    at = datetime(2026, 7, 10, 19, 30, tzinfo=UTC)
    ctrl.hub_view = HubView(forecast_max_c=30.5, forecast_min_c=18.0, next_event_at=at, next_event_profile="Night",
                            next_event_action="closed", next_event_covers=("Bedroom",))
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom", status=Status.CLOSED_SHADING,
                                         desired_state="closed", actual_state="closed", winning_layer="shading",
                                         reason="sun hits, hot day", sun_hits=True, sunny=True, hot_day=True,
                                         room_state="none", wind_state="inactive", owner="engine", last_engine_move=at)
    entities, sub_ids = await collect(hass, hub_entry, sensor, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {"forecast_max_today", "forecast_min_today", "next_scheduled_event", "status"}
    assert by_key["forecast_max_today"].native_value == 30.5 and by_key["forecast_max_today"].native_unit_of_measurement == "°C"
    assert by_key["forecast_max_today"].device_class is SensorDeviceClass.TEMPERATURE
    assert by_key["next_scheduled_event"].native_value == at and by_key["next_scheduled_event"].extra_state_attributes == {"profile": "Night", "action": "closed", "covers": ["Bedroom"]}
    status = by_key["status"]
    assert status.entity_category is None and status.device_class is SensorDeviceClass.ENUM
    assert status.native_value == "closed_shading" and status.options == [s.value for s in Status]
    attrs = status.extra_state_attributes
    assert attrs["desired_state"] == "closed" and attrs["reason"] == "sun hits, hot day" and attrs["last_engine_move"] == at.isoformat()
    assert {"desired_state", "actual_state", "reason"}.isdisjoint(type(status)._unrecorded_attributes)
    assert set(attrs) - {"desired_state", "actual_state", "reason"} <= set(type(status)._unrecorded_attributes)
    assert all(e.entity_category is EntityCategory.DIAGNOSTIC for e in entities if e.translation_key != "status")


async def test_binary_sensor_platform(hass, hub_entry):
    ctrl = fake_with_cover()
    since = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    ctrl.hub_view = HubView(sunny=True, hot_day=None, frost=False, any_wind_active=True, problem=True)
    ctrl.cover_views["sub1"] = CoverView(name="Bedroom", cover_entity="cover.bedroom", override_active=True,
                                         override_since=since, overridden_desired="closed", sun_hits=True, wind_active=True)
    entities, _ = await collect(hass, hub_entry, binary_sensor, ctrl)
    by_key = {e.translation_key: e for e in entities}
    assert set(by_key) == {"hot_day", "sunny", "frost_active", "any_wind_protection_active", "problem",
                           "manual_override", "sun_hits", "wind_protection_active"}
    assert by_key["sunny"].is_on is True and by_key["hot_day"].is_on is None and by_key["frost_active"].is_on is False
    assert by_key["problem"].is_on is True and by_key["problem"].device_class is BinarySensorDeviceClass.PROBLEM
    mo = by_key["manual_override"]
    assert mo.entity_category is None and mo.is_on is True
    assert mo.extra_state_attributes == {"since": since.isoformat(), "overridden_desired": "closed"}
    assert by_key["sun_hits"].is_on is True and by_key["wind_protection_active"].is_on is True
    assert all(e.entity_category is EntityCategory.DIAGNOSTIC for e in entities if e.translation_key != "manual_override")
```

- [ ] **Step 2: Run to verify they fail** — import errors.

- [ ] **Step 3: Implement**

`sensor.py`:
```python
"""Sensor platform: forecast values, next scheduled event, per-cover status (spec §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine.model import Status
from .entity import ControllerProtocol, CoverEntityBase, HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller: ControllerProtocol = entry.runtime_data.controller
    async_add_entities([
        ForecastMaxSensor(entry, controller), ForecastMinSensor(entry, controller),
        NextScheduledEventSensor(entry, controller),
    ])
    for subentry_id in entry.runtime_data.covers:
        async_add_entities([CoverStatusSensor(entry, controller, subentry_id)], config_subentry_id=subentry_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class ForecastMaxSensor(HubEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "forecast_max_today")

    @property
    def native_value(self) -> float | None:
        return self.hub_view.forecast_max_c


class ForecastMinSensor(HubEntity, SensorEntity):
    (same attributes; key "forecast_min_today"; native_value = hub_view.forecast_min_c)


class NextScheduledEventSensor(HubEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol) -> None:
        super().__init__(entry, controller, "next_scheduled_event")

    @property
    def native_value(self) -> datetime | None:
        return self.hub_view.next_event_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self.hub_view
        return {"profile": view.next_event_profile, "action": view.next_event_action,
                "covers": list(view.next_event_covers)}


class CoverStatusSensor(CoverEntityBase, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in Status]
    _unrecorded_attributes = frozenset({
        "winning_layer", "sun_hits", "sunny", "hot_day", "room_state", "wind_state", "active_rule",
        "next_planned_action", "next_planned_at", "last_engine_move", "owner", "degraded",
    })

    def __init__(self, entry: ConfigEntry, controller: ControllerProtocol, subentry_id: str) -> None:
        super().__init__(entry, controller, subentry_id, "status")

    @property
    def native_value(self) -> str:
        return self.view.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self.view
        return {
            "desired_state": v.desired_state, "actual_state": v.actual_state, "winning_layer": v.winning_layer,
            "reason": v.reason, "sun_hits": v.sun_hits, "sunny": v.sunny, "hot_day": v.hot_day,
            "room_state": v.room_state, "wind_state": v.wind_state, "active_rule": v.active_rule,
            "next_planned_action": v.next_planned_action, "next_planned_at": _iso(v.next_planned_at),
            "last_engine_move": _iso(v.last_engine_move), "owner": v.owner, "degraded": v.degraded,
        }
```
Write `ForecastMinSensor` out in full (no "same as" in code). `binary_sensor.py`: same pattern with `BinarySensorEntity`; hub sensors DIAGNOSTIC with `is_on` from the hub view (`hot_day`/`sunny` may return `None`), `FrostActiveSensor` device class `BinarySensorDeviceClass.COLD`, `ProblemSensor` device class `PROBLEM`; `ManualOverrideSensor` no category with attributes `{"since": _iso(view.override_since), "overridden_desired": view.overridden_desired}`; `SunHitsSensor`, `WindProtectionActiveSensor` DIAGNOSTIC.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_entities.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: sensor and binary sensor platforms as views over controller state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Services, logbook and diagnostics

**Files:**
- Create: `custom_components/cover_automation/services.py`, `services.yaml`, `logbook.py`, `diagnostics.py`
- Test: `tests/ha/test_services.py`, `tests/ha/test_logbook.py`, `tests/ha/test_diagnostics.py`

**Interfaces:**
- Produces:
  - `services.py`: `SERVICE_RESET_OVERRIDE = "reset_override"`, `SERVICE_EVALUATE_NOW = "evaluate_now"`, `@callback def async_setup_services(hass) -> None` (idempotent: registers both services with `schema=vol.Schema(cv.TARGET_SERVICE_FIELDS)` when not yet registered), `def resolve_cover_ids(hass, call: ServiceCall) -> set[str] | None` (None = all covers): `TargetSelection(call.data)`; `has_any_target` False → None; otherwise `selected = async_extract_referenced_entity_ids(hass, selection)`; entity ids (`selected.referenced | selected.indirectly_referenced`) → entity registry `config_subentry_id` of entries whose platform is our domain; `selected.referenced_devices` → device registry `config_subentry_id`; `def controllers(hass) -> list[ControllerProtocol]` from `hass.config_entries.async_loaded_entries(DOMAIN)` with `runtime_data.controller`.
  - `services.yaml`: both services with `target: {entity: {integration: cover_automation, domain: [sensor, binary_sensor]}, device: {integration: cover_automation}}`.
  - `logbook.py`: `async_describe_events(hass, async_describe_event)` describing `EVENT_ACTION` → `{LOGBOOK_ENTRY_NAME: cover_name, LOGBOOK_ENTRY_MESSAGE: "closed for shading: sun hits, hot day" (+ " (simulated)"), LOGBOOK_ENTRY_ENTITY_ID: entity_id}`.
  - `diagnostics.py`: `async_get_config_entry_diagnostics(hass, entry) -> dict[str, Any]` = `{"hub": asdict(hub) with sunny_conditions sorted list, "subentries": [{"id", "type", "title", "data"}], "covers": controller.snapshot() or {}, "store": store.data.to_dict(), "views": {cover_id: asdict-ish}}` (no redaction).
- Consumes: controller `EVENT_ACTION`, `ControllerProtocol`, `CoverAutomationData` fields.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_services.py`:
```python
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.cover_automation import const, services
from tests.ha.fakes import FakeController


def fake_entry(hass, hub_entry, ctrl):
    hub_entry.runtime_data = SimpleNamespace(controller=ctrl, covers={"sub1": (None, None), "sub2": (None, None)})
    hub_entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_setup_services_is_idempotent(hass: HomeAssistant):
    services.async_setup_services(hass)
    services.async_setup_services(hass)
    assert hass.services.has_service(const.DOMAIN, "reset_override") and hass.services.has_service(const.DOMAIN, "evaluate_now")


async def test_resolve_targets_by_device_and_entity(hass: HomeAssistant, hub_entry):
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=hub_entry.entry_id, config_subentry_id="sub2",
                                                 identifiers={(const.DOMAIN, "sub2")})
    ent = er.async_get(hass).async_get_or_create("sensor", const.DOMAIN, "sub1_status", config_entry=hub_entry,
                                                 config_subentry_id="sub1")
    call = ServiceCall(hass, const.DOMAIN, "evaluate_now", {"device_id": [dev.id], "entity_id": [ent.entity_id]})
    assert services.resolve_cover_ids(hass, call) == {"sub1", "sub2"}
    assert services.resolve_cover_ids(hass, ServiceCall(hass, const.DOMAIN, "evaluate_now", {})) is None


async def test_services_dispatch_to_controller(hass: HomeAssistant, hub_entry):
    from custom_components.cover_automation.views import CoverView

    ctrl = FakeController()
    ctrl.cover_views = {"sub1": CoverView(name="A", cover_entity="cover.a"), "sub2": CoverView(name="B", cover_entity="cover.b")}
    fake_entry(hass, hub_entry, ctrl)
    services.async_setup_services(hass)
    await hass.services.async_call(const.DOMAIN, "evaluate_now", {}, blocking=True)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=hub_entry.entry_id, config_subentry_id="sub1",
                                                 identifiers={(const.DOMAIN, "sub1")})
    await hass.services.async_call(const.DOMAIN, "reset_override", {"device_id": dev.id}, blocking=True)
    assert ctrl.calls == [("evaluate_now", None), ("reset_override", "sub1")]
```
(If `MockConfigEntry.mock_state` is unavailable, add the entry to hass and patch `hass.config_entries.async_loaded_entries` to return `[hub_entry]`.)

`tests/ha/test_logbook.py`:
```python
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.cover_automation import const
from custom_components.cover_automation.controller import EVENT_ACTION
from custom_components.cover_automation.logbook import async_describe_events


async def test_describe_action_event(hass: HomeAssistant):
    described: dict = {}

    def register(domain, event_name, describe):
        described[(domain, event_name)] = describe

    async_describe_events(hass, register)
    describe = described[(const.DOMAIN, EVENT_ACTION)]

    class FakeEvent:
        data = {"entity_id": "cover.bedroom", "cover_name": "Bedroom", "action": "close", "reason": "sun hits, hot day",
                "layer": "shading", "simulated": True}
        context_id = "ctx"

    entry = describe(FakeEvent())
    assert entry["name"] == "Bedroom" and entry["entity_id"] == "cover.bedroom"
    assert entry["message"] == "closed for shading: sun hits, hot day (simulated)"
```

`tests/ha/test_diagnostics.py`:
```python
from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant

from custom_components.cover_automation.diagnostics import async_get_config_entry_diagnostics
from tests.ha.conftest import cover_subentry_data, set_cover, set_sensor, set_sun, set_weather


async def test_diagnostics_without_controller(hass: HomeAssistant, hub_entry):
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom")))
    set_weather(hass); set_sun(hass); set_sensor(hass, "sensor.wind", 3, unit="km/h"); set_cover(hass, "cover.bedroom")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, hub_entry)
    assert diag["hub"]["weather_entity"] == "weather.home" and diag["hub"]["sunny_conditions"] == ["partlycloudy", "sunny"]
    assert diag["subentries"][0]["type"] == "cover" and diag["subentries"][0]["data"]["cover_entity"] == "cover.bedroom"
    assert set(diag["store"]) >= {"covers", "latch", "shading_mode"}
    assert "covers" in diag
```

- [ ] **Step 2: Run to verify they fail** — import errors.

- [ ] **Step 3: Implement**

`services.py`:
```python
"""Services: reset_override and evaluate_now with HA target selection (spec §4)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from . import const
from .entity import ControllerProtocol

SERVICE_RESET_OVERRIDE = "reset_override"
SERVICE_EVALUATE_NOW = "evaluate_now"


def controllers(hass: HomeAssistant) -> list[ControllerProtocol]:
    out: list[ControllerProtocol] = []
    for entry in hass.config_entries.async_loaded_entries(const.DOMAIN):
        data = getattr(entry, "runtime_data", None)
        controller = getattr(data, "controller", None)
        if controller is not None:
            out.append(controller)
    return out


def resolve_cover_ids(hass: HomeAssistant, call: ServiceCall) -> set[str] | None:
    selection = TargetSelection(call.data)
    if not selection.has_any_target:
        return None
    selected = async_extract_referenced_entity_ids(hass, selection)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    cover_ids: set[str] = set()
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entry = ent_reg.async_get(entity_id)
        if entry is not None and entry.platform == const.DOMAIN and entry.config_subentry_id:
            cover_ids.add(entry.config_subentry_id)
    for device_id in selected.referenced_devices:
        device = dev_reg.async_get(device_id)
        if device is not None and device.config_subentry_id:
            cover_ids.add(device.config_subentry_id)
    return cover_ids


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(const.DOMAIN, SERVICE_RESET_OVERRIDE):
        return

    async def reset_override(call: ServiceCall) -> None:
        ids = resolve_cover_ids(hass, call)
        for controller in controllers(hass):
            for cover_id in controller.cover_views if ids is None else ids:
                if cover_id in controller.cover_views:
                    await controller.async_reset_override(cover_id)

    async def evaluate_now(call: ServiceCall) -> None:
        ids = resolve_cover_ids(hass, call)
        for controller in controllers(hass):
            await controller.async_evaluate_now(None if ids is None else [c for c in ids if c in controller.cover_views])

    schema = vol.Schema(cv.TARGET_SERVICE_FIELDS)
    hass.services.async_register(const.DOMAIN, SERVICE_RESET_OVERRIDE, reset_override, schema=schema)
    hass.services.async_register(const.DOMAIN, SERVICE_EVALUATE_NOW, evaluate_now, schema=schema)
```
(`cv.TARGET_SERVICE_FIELDS` exists in 2026.8's `config_validation`; if the name differs, use `cv.ENTITY_SERVICE_FIELDS`.)

`services.yaml`:
```yaml
reset_override:
  target:
    entity:
      integration: cover_automation
      domain:
        - sensor
        - binary_sensor
    device:
      integration: cover_automation
evaluate_now:
  target:
    entity:
      integration: cover_automation
      domain:
        - sensor
        - binary_sensor
    device:
      integration: cover_automation
```

`logbook.py`:
```python
"""Describe cover_automation_action events in the logbook (spec §4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_CONTEXT_ID,
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    LazyEventPartialState,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback

from . import const
from .controller import EVENT_ACTION


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None],
) -> None:
    @callback
    def describe(event: LazyEventPartialState) -> dict[str, Any]:
        data = event.data
        verb = "closed" if data.get("action") == "close" else "opened"
        message = f"{verb} for {data.get('layer')}: {data.get('reason')}"
        if data.get("simulated"):
            message += " (simulated)"
        return {
            LOGBOOK_ENTRY_NAME: data.get("cover_name"),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: data.get(ATTR_ENTITY_ID),
            LOGBOOK_ENTRY_CONTEXT_ID: event.context_id,
        }

    async_describe_event(const.DOMAIN, EVENT_ACTION, describe)
```

`diagnostics.py`:
```python
"""Diagnostics download (spec §4): hub config, subentries, engine snapshot, Store."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    hub = asdict(data.hub)
    hub["sunny_conditions"] = sorted(hub["sunny_conditions"])
    controller = getattr(data, "controller", None)
    return {
        "hub": hub,
        "subentries": [
            {"id": s.subentry_id, "type": s.subentry_type, "title": s.title, "data": dict(s.data)}
            for s in entry.subentries.values()
        ],
        "covers": controller.snapshot() if controller is not None and getattr(controller, "started", False) else {},
        "store": data.store.data.to_dict(),
    }
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/ha/test_services.py tests/ha/test_logbook.py tests/ha/test_diagnostics.py -q && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pyright` → green.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "feat: services with target resolution, logbook describer and diagnostics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Wiring, translations, README and end-to-end scenarios

**Files:**
- Modify: `custom_components/cover_automation/const.py` (PLATFORMS), `__init__.py` (controller lifecycle, `async_setup`), `translations/en.json` (entities, services), `README.md`
- Test: `tests/ha/test_scenarios_ha.py`, `tests/ha/test_translations.py` (extend), `tests/ha/test_init.py` (extend)

**Interfaces:**
- Produces: `const.PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]`; `CoverAutomationData.controller: CoverAutomationController`; `async_setup(hass, config) -> bool` calling `services.async_setup_services(hass)`; `async_setup_entry` creates the controller after the Store is loaded and before platforms are forwarded, starts it with `entry.async_on_unload(async_at_started(hass, controller.async_start_job))` where `async_start_job(hass)` is a coroutine function taking `hass` (what `async_at_started` passes); `async_unload_entry` awaits `controller.async_stop()` before saving/unloading.
- Translations: `entity.switch.{simulation_mode,verbose_logging,enabled}.name`, `entity.select.{shading_mode,reopening_mode,mode}.{name,state.*}`, `entity.button.{evaluate_now,reset_override}.name`, `entity.sensor.{forecast_max_today,forecast_min_today,next_scheduled_event}.name`, `entity.sensor.status.{name,state.<every Status value>}`, `entity.binary_sensor.{hot_day,sunny,frost_active,any_wind_protection_active,problem,manual_override,sun_hits,wind_protection_active}.name`, `services.{reset_override,evaluate_now}.{name,description}`.

- [ ] **Step 1: Write the failing tests**

`tests/ha/test_scenarios_ha.py`:
```python
"""End-to-end: the integration set up through Home Assistant, driving mocked cover services."""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.cover_automation import const
from custom_components.cover_automation.forecast import TodayForecast
from tests.ha.conftest import cover_subentry_data, profile_subentry_data, set_cover, set_sensor, set_sun, set_weather

HOT = TodayForecast(30.0, 18.0)


@pytest.fixture
def forecast():
    with patch("custom_components.cover_automation.controller.async_fetch_today", AsyncMock(return_value=HOT)) as m:
        yield m


@pytest.fixture
def services(hass):
    return {"close": async_mock_service(hass, "cover", "close_cover"), "open": async_mock_service(hass, "cover", "open_cover")}


async def setup_full(hass, hub_entry, *, profile=None, cover_overrides=None):
    if profile is not None:
        hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**profile))
        prof_id = next(s.subentry_id for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_PROFILE)
        cover_overrides = {**(cover_overrides or {}), const.CONF_SCHEDULE_PROFILE: prof_id}
    hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom", **(cover_overrides or {}))))
    sub_id = next(s.subentry_id for s in hub_entry.subentries.values() if s.subentry_type == const.SUBENTRY_COVER)
    set_weather(hass, condition="sunny", temperature=20.0)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h", device_class="wind_speed")
    set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    return sub_id


def status_entity(hass, sub_id):
    return er.async_get(hass).async_get_entity_id("sensor", const.DOMAIN, f"{sub_id}_status")


async def test_full_setup_creates_entities_and_closes_on_hot_sunny_day(hass, hub_entry, forecast, services):
    sub_id = await setup_full(hass, hub_entry)
    assert hub_entry.state is ConfigEntryState.LOADED
    assert len(services["close"]) == 1
    registry = er.async_get(hass)
    status_id = status_entity(hass, sub_id)
    assert status_id is not None and registry.async_get(status_id).entity_category is None
    assert registry.async_get(status_id).config_subentry_id == sub_id
    mo_id = registry.async_get_entity_id("binary_sensor", const.DOMAIN, f"{sub_id}_manual_override")
    assert registry.async_get(mo_id).entity_category is None
    for key, platform in (("sunny", "binary_sensor"), ("forecast_max_today", "sensor"), ("problem", "binary_sensor")):
        eid = registry.async_get_entity_id(platform, const.DOMAIN, f"{hub_entry.entry_id}_{key}")
        assert registry.async_get(eid).entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(registry.async_get_entity_id("binary_sensor", const.DOMAIN, f"{hub_entry.entry_id}_hot_day")).state == "on"
    assert hass.states.get(status_id).state == "open_no_shade" or hass.states.get(status_id).state == "closed_shading"
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    await hass.async_block_till_done()
    assert hass.states.get(status_id).state == "closed_shading"
    assert hass.states.get(status_id).attributes["desired_state"] == "closed"


async def test_enabled_switch_and_reset_service(hass, hub_entry, forecast, services):
    sub_id = await setup_full(hass, hub_entry)
    registry = er.async_get(hass)
    switch_id = registry.async_get_entity_id("switch", const.DOMAIN, f"{sub_id}_enabled")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(status_entity(hass, sub_id)).state == "disabled"
    assert hub_entry.runtime_data.store.data.covers[sub_id].enabled is False
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()
    # manual override then service reset by device target
    set_cover(hass, "cover.bedroom", state="closed", position=0); await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="open", position=100); await hass.async_block_till_done()
    mo_id = registry.async_get_entity_id("binary_sensor", const.DOMAIN, f"{sub_id}_manual_override")
    assert hass.states.get(mo_id).state == "on"
    device_id = registry.async_get(mo_id).device_id
    await hass.services.async_call(const.DOMAIN, "reset_override", {"device_id": device_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(mo_id).state == "off"


async def test_schedule_rule_closes_at_time(hass, hub_entry, services, freezer):
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=21, minute=0, second=0, microsecond=0)))
    set_sun(hass, elevation=-5.0, azimuth=300.0)
    with patch("custom_components.cover_automation.controller.async_fetch_today", AsyncMock(return_value=TodayForecast(15.0, 8.0))):
        sub_id = await setup_full(hass, hub_entry, profile=profile_subentry_data("Night", quiet=None))
        assert not services["close"]
        next_id = er.async_get(hass).async_get_entity_id("sensor", const.DOMAIN, f"{hub_entry.entry_id}_next_scheduled_event")
        assert hass.states.get(next_id).attributes["action"] == "closed"
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(services["close"]) == 1
        assert hass.states.get(status_entity(hass, sub_id)).state in ("schedule_hold", "closed_shading", "idle")


async def test_override_survives_reload(hass, hub_entry, forecast, services):
    sub_id = await setup_full(hass, hub_entry)
    set_cover(hass, "cover.bedroom", state="closed", position=0); await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="open", position=100); await hass.async_block_till_done()
    assert hub_entry.runtime_data.store.data.covers[sub_id].dam is not None
    await hass.config_entries.async_reload(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    mo_id = er.async_get(hass).async_get_entity_id("binary_sensor", const.DOMAIN, f"{sub_id}_manual_override")
    assert hass.states.get(mo_id).state == "on"
    assert len(services["close"]) == 1  # no re-close after the reload


async def test_unload_stops_controller(hass, hub_entry, forecast, services, freezer):
    sub_id = await setup_full(hass, hub_entry)
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    freezer.tick(timedelta(hours=1)); async_fire_time_changed(hass); await hass.async_block_till_done()
    assert len(services["close"]) == 1
```
Also add to `tests/ha/test_translations.py`: every `translation_key` used by the platform classes has `entity.<platform>.<key>.name`; the `status` sensor has a `state` entry for every `Status` value; the two selects have a `state` entry for every option; both services have `name` and `description`.

- [ ] **Step 2: Run to verify they fail** — entities missing (platforms not forwarded), service not registered.

- [ ] **Step 3: Implement**

`const.py`: `PLATFORMS: Final[list[Platform]] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]`.

`__init__.py` additions:
```python
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv
from .controller import CoverAutomationController
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(const.DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True
```
`CoverAutomationData` gains `controller: CoverAutomationController` (set right after the Store is loaded and pruned):
```python
    controller = CoverAutomationController(
        hass, entry, hub=hub, covers=covers, profiles=profiles, store=store, hub_device_id=hub_device_id
    )
    entry.runtime_data = CoverAutomationData(hub=hub, covers=covers, profiles=profiles, store=store,
                                             hub_device_id=hub_device_id, controller=controller)
    await hass.config_entries.async_forward_entry_setups(entry, const.PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(async_at_started(hass, partial(_async_check_optional_entities, entry=entry, hub=hub, covers=covers)))
    entry.async_on_unload(async_at_started(hass, controller.async_start_job))
```
with `async def async_start_job(self, hass: HomeAssistant) -> None: await self.async_start()` added to the controller. `async_unload_entry`: `await entry.runtime_data.controller.async_stop()` first, then the existing save + platform unload.

`en.json`: add the `entity` and `services` objects described in Interfaces (`entity.sensor.status.state` must list all 14 `Status` values with readable labels, e.g. `"closed_shading": "Closed for shading"`, `"held_frost": "Held by frost protection"`; select states: shading_mode `off/auto/forced_sunlit/forced_all`, reopening_mode `active/passive/off`, mode `auto/dark_only/protection_only`). Re-dump sorted.

`README.md`: replace the "installs and configures only" disclaimer with a short "How it decides" section (layer order frost > wind > door > quiet hours > schedule > shading, 0/100 only, manual override behaviour), list the entities and the two services, and note simulation mode and the logbook entries.

- [ ] **Step 4: Run everything**

Run: `.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pyright`
Expected: all green, no lingering timers. If `test_schedule_rule_closes_at_time` is flaky around DST or the test time zone (US/Pacific by default), set `await hass.config.async_set_time_zone("Europe/Vienna")` at the start of the test and keep the 21:00 local base.

- [ ] **Step 5: Commit**

```bash
git add -A custom_components tests README.md
git commit -m "feat: wire controller and platforms into setup; services registration; entity translations; end-to-end scenarios

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** §1.3 gate 10/11 (simulation, feature check, unsupported repair) → Task 5 `_act`; §1.4/§1.5 handled by the engine, exercised through Task 5/9 tests (manual override, reset); §2 signals → Task 3, timers → Task 4/5, triggers → Task 5 `_subscribe`; §4 entities → Tasks 6/7, services → Task 8, logbook → Task 8, repairs → Tasks 2/5, diagnostics → Task 8; §5 startup order → Task 5/9, Store saves → Task 5, command failures/backoff → Task 5 (engine), unload → Task 5/9. Backlog from the 2a final review → Task 0 (and Task 5 re-evaluates `missing_entity`-style problems through the `problem` flag; the deferred optional-entity check itself stays in `__init__`).
- **Engine parked items:** the `if p.enabled` guard in `check_pending` and `prev_wind_active` while disabled are engine changes; they are NOT in this plan (engine untouched) — record as a follow-up in the ledger if the controller can disable a cover with a pending record (it can: `async_set_enabled(False)` while pending). Executor: add a small engine task if the final review confirms the reachable path.
- **Type consistency:** `HubView`/`CoverView` fields used in Tasks 5–8 match Task 2; `ControllerProtocol` methods match the controller's write API and `FakeController`; `signal_update` used by `entity.py`, `controller.py`, `fakes.py`; `EVENT_ACTION` defined in `controller.py`, consumed by `logbook.py`; `repairs.*` ids used consistently (`cover_issue_id(kind, id)`, `hub_issue_id(entry_id, kind)`); `CoverSignalSet.inputs(actual, schedule)` and `HubSignalSource.signals(now, store, elevation)` match the controller calls; `ScheduleTracker(hass, profiles, cover_profile, on_fire, *, sun=None)` matches Tasks 4/5.
- **Known simplifications:** `frost_source_unavailable` semantics simplified per the Task 5 notes; `snapshot()` view serialisation via `asdict` helper; the `problem` flag counts every non-dismissed issue of this domain.
