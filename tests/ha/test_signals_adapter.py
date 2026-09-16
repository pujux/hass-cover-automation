from __future__ import annotations

from datetime import date, timedelta

import pytest
from custom_components.cover_automation import const
from custom_components.cover_automation.config_map import cover_config, hub_config
from custom_components.cover_automation.engine.model import CoverState, DoorState, ScheduleView
from custom_components.cover_automation.engine.signals import DailyLatch
from custom_components.cover_automation.forecast import TodayForecast
from custom_components.cover_automation.signals_adapter import (
    CoverSignalSet,
    HubSignalSource,
    classify_state,
    cover_supports,
    sun_position,
)
from custom_components.cover_automation.store import StoreData
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from tests.ha.conftest import WEATHER, cover_subentry_data, set_sensor, set_sun, set_weather


@pytest.mark.parametrize(
    ("state", "position", "expected"),
    [
        ("open", 100, CoverState.OPEN),
        ("open", 50, CoverState.PARTIAL),
        ("open", None, CoverState.OPEN),
        ("closed", 0, CoverState.CLOSED),
        ("opening", 40, CoverState.MOVING),
        ("closing", None, CoverState.MOVING),
        ("unavailable", None, CoverState.UNAVAILABLE),
    ],
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
    # Debounce.next_change_at() anchors the off-delay to the update() call at which the raw
    # change was first *observed* (now+5min here), not to real-world "now" -- the adapter has
    # no way to know the condition flipped before it polled. So the 20-minute off delay
    # completes at now+5min+20min=now+25min, not now+20min.
    src.update(now + timedelta(minutes=5))
    assert src.sunny_state is True  # off delay 20 min from the observation at +5min
    assert src.next_check_at() == now + timedelta(minutes=25)
    src.update(now + timedelta(minutes=26))
    assert src.sunny_state is False
    hass.states.async_set(WEATHER, "unavailable")
    src.update(now + timedelta(minutes=27))
    assert src.sunny_state is False  # held within grace
    src.update(now + timedelta(minutes=27) + timedelta(seconds=1800))
    assert src.sunny_state is None and src.weather_unavailable_beyond_grace(
        now + timedelta(minutes=57)
    )
    hass.config_entries.async_update_entry(
        hub_entry,
        options={
            **hub_entry.options,
            const.CONF_SUNNY_OVERRIDE_ENTITY: "binary_sensor.force_sunny",
        },
    )
    hass.states.async_set("binary_sensor.force_sunny", "on")
    src2 = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src2.seed(now)
    assert src2.sunny_state is True


async def test_weather_availability_tracked_even_with_sunny_override(
    hass: HomeAssistant, hub_entry
):
    """The weather entity is still the frost fallback/forecast source when overridden."""
    now = dt_util.utcnow()
    hass.config_entries.async_update_entry(
        hub_entry,
        options={
            **hub_entry.options,
            const.CONF_SUNNY_OVERRIDE_ENTITY: "binary_sensor.force_sunny",
        },
    )
    hass.states.async_set("binary_sensor.force_sunny", "on")
    hass.states.async_set(WEATHER, "unavailable")
    src = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src.seed(now)
    assert src.sunny_state is True  # the override still wins
    assert src.weather_unavailable_beyond_grace(now + timedelta(minutes=30))


async def test_next_check_at_includes_weather_and_forecast_grace_expiry(
    hass: HomeAssistant, hub_entry
):
    now = dt_util.utcnow()
    set_weather(hass, condition="sunny")
    src = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src.seed(now)
    hass.states.async_set(WEATHER, "unavailable")
    src.update(now)
    assert src.next_check_at() == now + timedelta(seconds=1800)  # weather_grace_s


async def test_frost_from_fahrenheit_sensor(hass: HomeAssistant, hub_entry):
    hass.config_entries.async_update_entry(
        hub_entry, data={**hub_entry.data, const.CONF_OUTDOOR_TEMPERATURE_SENSOR: "sensor.outdoor"}
    )
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


async def test_frost_source_unavailable_when_weather_lacks_temperature(
    hass: HomeAssistant, hub_entry
):
    """No outdoor sensor configured, and the mandatory weather entity has no reading."""
    now = dt_util.utcnow()
    hass.states.async_set(WEATHER, "sunny", {"supported_features": 1})  # no "temperature" attr
    src = HubSignalSource(hass, hub_config(hub_entry), DailyLatch())
    src.seed(now)
    assert src.frost_source_unavailable is True


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
    assert (
        hub_signals.sun_elevation == 35.0
        and hub_signals.hot_day is None
        and hub_signals.sunny is True
    )


def cover_set(hass, hub_entry, **overrides):
    hub = hub_config(hub_entry)
    sub = ConfigSubentry(**cover_subentry_data("cover.bedroom", **overrides))
    cfg, bind = cover_config(sub, hub)
    return (
        CoverSignalSet(
            hass, cfg, bind, hub, wind_active=False, sun_release_margin=hub.sun_release_margin
        ),
        cfg,
        bind,
    )


async def test_room_in_fahrenheit_and_labels(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_sensor(hass, "sensor.room", 66.0, unit="°F")  # 18.9 °C < 21
    sig, _, _ = cover_set(hass, hub_entry, **{const.CONF_ROOM_SENSOR: "sensor.room"})
    sig.seed(now, (180.0, 40.0))
    inputs = sig.inputs(CoverState.OPEN, ScheduleView())
    assert inputs.room_cold and not inputs.room_hot and sig.room_state == "cold"
    set_sensor(hass, "sensor.room", 80.0, unit="°F")  # 26.7 °C >= 25
    sig.update(now, (180.0, 40.0))  # raw change observed; the 10-minute dwell starts here
    sig.update(now + timedelta(minutes=11), (180.0, 40.0))
    assert sig.inputs(CoverState.OPEN, ScheduleView()).room_hot and sig.room_state == "hot"
    hass.states.async_set("sensor.room", "unavailable")
    sig.update(now + timedelta(minutes=12), (180.0, 40.0))
    assert (
        sig.inputs(CoverState.OPEN, ScheduleView()).room_degraded and sig.room_state == "degraded"
    )


async def test_wind_conversion_and_unit_mismatch(hass: HomeAssistant, hub_entry):
    now = dt_util.utcnow()
    set_sensor(hass, "sensor.wind", 20.0, unit="m/s")  # 72 km/h
    sig, _, _ = cover_set(
        hass,
        hub_entry,
        **{
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_UNIT: "km/h",
        },
    )
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
    sig, _, _ = cover_set(
        hass,
        hub_entry,
        **{const.CONF_SHADING_RULE: "room_only", const.CONF_ROOM_SENSOR: "sensor.room"},
    )
    sig.seed(dt_util.utcnow(), None)
    assert sig.room_unusable is True
