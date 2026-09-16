"""End-to-end: the integration set up through Home Assistant, driving mocked cover services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.cover_automation import const
from custom_components.cover_automation.forecast import TodayForecast
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from tests.ha.conftest import (
    cover_subentry_data,
    profile_subentry_data,
    set_cover,
    set_sensor,
    set_sun,
    set_weather,
)

HOT = TodayForecast(30.0, 18.0)


@pytest.fixture
def forecast():
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ) as m:
        yield m


@pytest.fixture
def services(hass):
    return {
        "close": async_mock_service(hass, "cover", "close_cover"),
        "open": async_mock_service(hass, "cover", "open_cover"),
    }


async def setup_full(hass, hub_entry, *, profile=None, cover_overrides=None):
    if profile is not None:
        hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**profile))
        prof_id = next(
            s.subentry_id
            for s in hub_entry.subentries.values()
            if s.subentry_type == const.SUBENTRY_PROFILE
        )
        cover_overrides = {**(cover_overrides or {}), const.CONF_SCHEDULE_PROFILE: prof_id}
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom", **(cover_overrides or {})))
    )
    sub_id = next(
        s.subentry_id
        for s in hub_entry.subentries.values()
        if s.subentry_type == const.SUBENTRY_COVER
    )
    set_weather(hass, condition="sunny", temperature=20.0)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h", device_class="wind_speed")
    set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    return sub_id


def status_entity(hass, sub_id):
    return er.async_get(hass).async_get_entity_id("sensor", const.DOMAIN, f"{sub_id}_status")


async def test_full_setup_creates_entities_and_closes_on_hot_sunny_day(
    hass, hub_entry, forecast, services
) -> None:
    sub_id = await setup_full(hass, hub_entry)
    assert hub_entry.state is ConfigEntryState.LOADED
    assert len(services["close"]) == 1
    registry = er.async_get(hass)
    status_id = status_entity(hass, sub_id)
    assert status_id is not None and registry.async_get(status_id).entity_category is None
    assert registry.async_get(status_id).config_subentry_id == sub_id
    mo_id = registry.async_get_entity_id("binary_sensor", const.DOMAIN, f"{sub_id}_manual_override")
    assert registry.async_get(mo_id).entity_category is None
    for key, platform in (
        ("sunny", "binary_sensor"),
        ("forecast_max_today", "sensor"),
        ("problem", "binary_sensor"),
    ):
        eid = registry.async_get_entity_id(platform, const.DOMAIN, f"{hub_entry.entry_id}_{key}")
        assert registry.async_get(eid).entity_category is EntityCategory.DIAGNOSTIC
    hot_id = registry.async_get_entity_id(
        "binary_sensor", const.DOMAIN, f"{hub_entry.entry_id}_hot_day"
    )
    assert hass.states.get(hot_id).state == "on"
    assert hass.states.get(status_id).state in ("open_no_shade", "closed_shading")
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    await hass.async_block_till_done()
    assert hass.states.get(status_id).state == "closed_shading"
    assert hass.states.get(status_id).attributes["desired_state"] == "closed"
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()


async def test_enabled_switch_and_reset_service(hass, hub_entry, forecast, services) -> None:
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
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="open", position=100)
    await hass.async_block_till_done()
    mo_id = registry.async_get_entity_id("binary_sensor", const.DOMAIN, f"{sub_id}_manual_override")
    assert hass.states.get(mo_id).state == "on"
    device_id = registry.async_get(mo_id).device_id
    await hass.services.async_call(
        const.DOMAIN, "reset_override", {"device_id": device_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(mo_id).state == "off"
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()


async def test_schedule_rule_closes_at_time(hass, hub_entry, services, freezer) -> None:
    freezer.move_to(
        dt_util.as_utc(dt_util.now().replace(hour=21, minute=0, second=0, microsecond=0))
    )
    set_sun(hass, elevation=-5.0, azimuth=300.0)
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=TodayForecast(15.0, 8.0)),
    ):
        # Both rules: with only a close rule the schedule layer would still be holding
        # CLOSED from yesterday's 21:30 fire, so the cover would close at setup already.
        # The morning open rule is the last one fired before 21:00 and is long out of its
        # one-shot window, so the schedule has no opinion until 21:30.
        profile = profile_subentry_data(
            "Night",
            rules=[
                {
                    const.CONF_RULE_ACTION: "open",
                    const.CONF_RULE_TIME_MODE: "fixed",
                    const.CONF_RULE_TIME: "07:00:00",
                },
                {
                    const.CONF_RULE_ACTION: "closed",
                    const.CONF_RULE_TIME_MODE: "fixed",
                    const.CONF_RULE_TIME: "21:30:00",
                },
            ],
            quiet=None,
        )
        sub_id = await setup_full(hass, hub_entry, profile=profile)
        assert not services["close"]
        next_id = er.async_get(hass).async_get_entity_id(
            "sensor", const.DOMAIN, f"{hub_entry.entry_id}_next_scheduled_event"
        )
        assert hass.states.get(next_id).attributes["action"] == "closed"
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(services["close"]) == 1
        assert hass.states.get(status_entity(hass, sub_id)).state in (
            "schedule_hold",
            "closed_shading",
            "idle",
        )
        assert await hass.config_entries.async_unload(hub_entry.entry_id)
        await hass.async_block_till_done()


async def test_override_survives_reload(hass, hub_entry, forecast, services) -> None:
    sub_id = await setup_full(hass, hub_entry)
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="open", position=100)
    await hass.async_block_till_done()
    assert hub_entry.runtime_data.store.data.covers[sub_id].dam is not None
    await hass.config_entries.async_reload(hub_entry.entry_id)
    await hass.async_block_till_done()
    assert hub_entry.state is ConfigEntryState.LOADED
    mo_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", const.DOMAIN, f"{sub_id}_manual_override"
    )
    assert hass.states.get(mo_id).state == "on"
    assert len(services["close"]) == 1  # no re-close after the reload
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()


async def test_unload_stops_controller(hass, hub_entry, forecast, services, freezer) -> None:
    await setup_full(hass, hub_entry)
    assert await hass.config_entries.async_unload(hub_entry.entry_id)
    await hass.async_block_till_done()
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(services["close"]) == 1
