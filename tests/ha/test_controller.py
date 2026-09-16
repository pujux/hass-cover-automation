from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.cover_automation import const
from custom_components.cover_automation.controller import EVENT_ACTION, CoverAutomationController
from custom_components.cover_automation.engine.model import CoverPersisted, Owner, Status, Target
from custom_components.cover_automation.forecast import TodayForecast
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from tests.ha.conftest import cover_subentry_data, set_cover, set_sensor, set_sun, set_weather

HOT = TodayForecast(30.0, 18.0)


async def start_controller(
    hass: HomeAssistant, hub_entry, *, forecast=HOT, cover_overrides=None, persisted=None
):
    """Set up the hub (2a) with one cover subentry and start a controller on top of it."""
    overrides = cover_overrides or {}
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom", **overrides))
    )
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
        hass,
        hub_entry,
        hub=data.hub,
        covers=data.covers,
        profiles=data.profiles,
        store=data.store,
        hub_device_id=data.hub_device_id,
    )
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=forecast),
    ):
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


async def test_hot_sunny_day_closes_cover_and_persists_ownership(
    hass, hub_entry, cover_services, hass_storage, freezer
):
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        assert len(cover_services["close"]) == 1
        assert cover_services["close"][0].data["entity_id"] == "cover.bedroom"
        p = controller.engine(sub_id).p
        assert p.owner is Owner.ENGINE and p.engine_target is Target.CLOSED
        stored = hass_storage[const.storage_key(hub_entry.entry_id)]["data"]["covers"][sub_id]
        assert stored["owner"] == "engine" and stored["engine_target"] == "closed"
        view = controller.cover_views[sub_id]
        assert (
            view.desired_state == "closed"
            and view.winning_layer == "shading"
            and view.next_planned_action
        )
        set_cover(hass, "cover.bedroom", state="closing", position=60)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        assert controller.cover_views[sub_id].status is Status.CLOSED_SHADING
        assert controller.engine(sub_id).rt.pending is None
        assert len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_manual_open_becomes_override_and_is_respected(
    hass, hub_entry, cover_services, freezer
):
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="opening", position=20)
        await hass.async_block_till_done()
        set_cover(hass, "cover.bedroom", state="open", position=100)
        await hass.async_block_till_done()
        view = controller.cover_views[sub_id]
        assert (
            view.status is Status.MANUAL_OVERRIDE
            and view.override_active
            and view.overridden_desired == "closed"
        )
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
        hass,
        hub_entry,
        cover_overrides={
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_UNIT: "km/h",
        },
    )
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        set_sensor(hass, "sensor.wind", 75.0, unit="km/h", device_class="wind_speed")
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1
        view = controller.cover_views[sub_id]
        assert (
            view.status is Status.PROTECTED_WIND
            and view.wind_active
            and controller.hub_view.any_wind_active
        )
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
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_weather(hass)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h")
    set_cover(hass, "cover.bedroom")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    data = hub_entry.runtime_data
    data.store.data.simulation = True
    controller = CoverAutomationController(
        hass,
        hub_entry,
        hub=data.hub,
        covers=data.covers,
        profiles=data.profiles,
        store=data.store,
        hub_device_id=data.hub_device_id,
    )
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        assert not cover_services["close"]
        assert (
            len(events) == 1
            and events[0].data["simulated"] is True
            and events[0].data["action"] == "close"
        )
        assert (
            events[0].data["entity_id"] == "cover.bedroom" and events[0].data["layer"] == "shading"
        )
    finally:
        await controller.async_stop()


async def test_disabled_cover_gets_no_commands(hass, hub_entry, cover_services):
    controller, sub_id = await start_controller(
        hass, hub_entry, persisted=CoverPersisted(enabled=False)
    )
    try:
        assert (
            not cover_services["close"] and controller.cover_views[sub_id].status is Status.DISABLED
        )
        await controller.async_set_enabled(sub_id, True)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_unavailable_cover_is_skipped_until_it_reports(hass, hub_entry, cover_services):
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    sub_id = next(iter(hub_entry.subentries))
    set_weather(hass)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h")
    hass.states.async_set("cover.bedroom", "unavailable")
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    data = hub_entry.runtime_data
    controller = CoverAutomationController(
        hass,
        hub_entry,
        hub=data.hub,
        covers=data.covers,
        profiles=data.profiles,
        store=data.store,
        hub_device_id=data.hub_device_id,
    )
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        assert (
            controller.cover_views[sub_id].status is Status.COVER_UNAVAILABLE
            and not cover_services["close"]
        )
        assert controller.engine(sub_id).p.owner is None  # not reconciled yet
        set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
        await hass.async_block_till_done()
        assert (
            controller.engine(sub_id).p.owner is Owner.ENGINE and len(cover_services["close"]) == 1
        )
    finally:
        await controller.async_stop()


async def test_hub_repairs_and_problem_flag(hass, hub_entry, cover_services, freezer):
    controller, _sub_id = await start_controller(hass, hub_entry, forecast=None)
    try:
        assert not controller.hub_view.problem
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        reg = ir.async_get(hass)
        assert (
            reg.async_get_issue(const.DOMAIN, f"forecast_fetch_failed_{hub_entry.entry_id}")
            is not None
        )
        assert controller.hub_view.problem is True
        hass.states.async_remove("sun.sun")
        await controller.async_evaluate_now()
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, f"sun_missing_{hub_entry.entry_id}") is not None
    finally:
        await controller.async_stop()


async def test_stop_cancels_everything(hass, hub_entry, cover_services, freezer):
    controller, _sub_id = await start_controller(hass, hub_entry)
    await controller.async_stop()
    assert not controller.started
    set_cover(hass, "cover.bedroom", state="closed", position=0)
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert (
        len(cover_services["close"]) == 1
    )  # nothing ran after stop (and no lingering timers at teardown)
