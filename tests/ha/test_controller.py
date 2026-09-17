from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.cover_automation import const
from custom_components.cover_automation.controller import EVENT_ACTION, CoverAutomationController
from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    Mode,
    Owner,
    ReopeningMode,
    ShadingMode,
    Status,
    Target,
)
from custom_components.cover_automation.forecast import TodayForecast
from homeassistant.components import persistent_notification
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from tests.ha.conftest import (
    WEATHER,
    cover_subentry_data,
    profile_subentry_data,
    set_cover,
    set_sensor,
    set_sun,
    set_weather,
)

HOT = TodayForecast(30.0, 18.0)


def _subentry_id(hub_entry, subentry_type: str) -> str:
    return next(
        s.subentry_id for s in hub_entry.subentries.values() if s.subentry_type == subentry_type
    )


async def build_controller(
    hass: HomeAssistant,
    hub_entry,
    *,
    cover_overrides=None,
    persisted=None,
    store_overrides=None,
    profile=None,
    elevation=40.0,
    cover_state=("open", 100, 3),
):
    """Set up the hub (2a) with one cover subentry and build an unstarted controller on top."""
    overrides = dict(cover_overrides or {})
    if profile is not None:
        hass.config_entries.async_add_subentry(hub_entry, ConfigSubentry(**profile))
        overrides[const.CONF_SCHEDULE_PROFILE] = _subentry_id(hub_entry, const.SUBENTRY_PROFILE)
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom", **overrides))
    )
    sub_id = _subentry_id(hub_entry, const.SUBENTRY_COVER)
    set_weather(hass, condition="sunny", temperature=20.0)
    set_sun(hass, elevation=elevation, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h", device_class="wind_speed")
    state, position, features = cover_state
    set_cover(hass, "cover.bedroom", state=state, position=position, features=features)
    # These tests drive a controller of their own, so keep the entry's controller parked:
    # `async_at_started` only runs its job once Home Assistant has finished starting.
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(hub_entry.entry_id)
    await hass.async_block_till_done()
    data = hub_entry.runtime_data
    if persisted is not None:
        data.store.data.covers[sub_id] = persisted
    for field, value in (store_overrides or {}).items():
        setattr(data.store.data, field, value)
    controller = CoverAutomationController(
        hass,
        hub_entry,
        hub=data.hub,
        covers=data.covers,
        profiles=data.profiles,
        store=data.store,
        hub_device_id=data.hub_device_id,
    )
    return controller, sub_id


async def start_controller(hass: HomeAssistant, hub_entry, *, forecast=HOT, **kwargs):
    """`build_controller` plus a first evaluation with the forecast patched."""
    controller, sub_id = await build_controller(hass, hub_entry, **kwargs)
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
    hass.set_state(CoreState.starting)  # park the entry's own controller (see start_controller)
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
    hass.set_state(CoreState.starting)  # park the entry's own controller (see start_controller)
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


async def test_schedule_rule_fires_and_updates_views(hass, hub_entry, cover_services, freezer):
    """A fixed close rule fires, commands the cover and lands in the hub and cover views."""
    closes = cover_services["close"]
    # The 07:00 open rule keeps yesterday's 21:30 close rule from holding the cover shut at
    # 21:00 (a close rule is a standing hold until the next rule fires).
    rules = [
        {
            const.CONF_RULE_ACTION: "closed",
            const.CONF_RULE_TIME_MODE: "fixed",
            const.CONF_RULE_TIME: "21:30:00",
        },
        {
            const.CONF_RULE_ACTION: "open",
            const.CONF_RULE_TIME_MODE: "fixed",
            const.CONF_RULE_TIME: "07:00:00",
        },
    ]
    freezer.move_to(dt_util.now().replace(hour=21, minute=0, second=0, microsecond=0))
    controller, sub_id = await start_controller(
        hass,
        hub_entry,
        forecast=TodayForecast(10.0, 5.0),
        elevation=-10.0,
        profile=profile_subentry_data("Night", rules=rules, quiet=None),
    )
    try:
        assert not closes  # night, cold: nothing to do before the rule fires
        assert controller.hub_view.next_event_action == "closed"
        assert controller.hub_view.next_event_profile == "Night"
        assert "schedule" in (controller.cover_views[sub_id].next_planned_action or "")
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(closes) == 1
        assert "rule 1" in (controller.cover_views[sub_id].active_rule or "")
    finally:
        await controller.async_stop()


RELEASE_RULES = [
    {
        const.CONF_RULE_ACTION: "closed",
        const.CONF_RULE_TIME_MODE: "fixed",
        const.CONF_RULE_TIME: "21:30:00",
    },
    {
        const.CONF_RULE_ACTION: "release",
        const.CONF_RULE_TIME_MODE: "fixed",
        const.CONF_RULE_TIME: "08:00:00",
    },
]


@pytest.fixture
async def bedroom_overnight(hass, hub_entry, cover_services, freezer):
    """21:30: the close rule shuts the bedroom and the hold runs into the next morning.

    Owns the controller for the whole test -- an assertion failure while setting the
    scenario up must not leave a running controller (and its timers) behind -- and keeps
    the forecast patched across the local-midnight refetch.
    """
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ):
        freezer.move_to(dt_util.now().replace(hour=21, minute=0, second=0, microsecond=0))
        controller, sub_id = await start_controller(
            hass,
            hub_entry,
            elevation=-10.0,  # night: only the schedule has an opinion
            profile=profile_subentry_data("Bedroom", rules=RELEASE_RULES, quiet=None),
        )
        try:
            assert not cover_services["close"]
            freezer.tick(timedelta(minutes=31))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            assert len(cover_services["close"]) == 1
            set_cover(hass, "cover.bedroom", state="closed", position=0)
            await hass.async_block_till_done()
            assert controller.engine(sub_id).p.owner is Owner.ENGINE
            yield controller, sub_id
        finally:
            await controller.async_stop()


async def test_release_rule_reopens_the_bedroom_when_nothing_wants_shade(
    hass, cover_services, freezer, bedroom_overnight
):
    """Decision 33: at 08:00 the hold ends and the layers below open the cover again."""
    controller, sub_id = bedroom_overnight
    freezer.tick(timedelta(hours=10, minutes=24))  # 07:55 the next morning
    set_sun(hass, elevation=40.0, azimuth=0.0)  # sun up but off this window
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert controller.cover_views[sub_id].status is Status.SCHEDULE_HOLD
    assert not cover_services["open"]  # the hold outranks the shading layer
    freezer.tick(timedelta(minutes=10))  # 08:05: the release rule has fired
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cover_services["open"]) == 1
    view = controller.cover_views[sub_id]
    assert view.status is Status.OPEN_NO_SHADE and view.winning_layer == "shading"
    assert "release rule 2" in (view.active_rule or "")


async def test_release_rule_leaves_the_bedroom_shut_while_shading_wants_it(
    hass, cover_services, freezer, bedroom_overnight
):
    """The same release rule sends nothing when the morning sun is on the window."""
    controller, sub_id = bedroom_overnight
    freezer.tick(timedelta(hours=10, minutes=34))  # 08:05, hot and sunny
    set_sun(hass, elevation=40.0, azimuth=180.0)  # sun straight on the window
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not cover_services["open"] and len(cover_services["close"]) == 1
    view = controller.cover_views[sub_id]
    assert view.status is Status.CLOSED_SHADING and view.winning_layer == "shading"


async def test_rule_skipped_repair(hass, hub_entry, cover_services, freezer):
    """A sunset rule that quiet hours swallow raises one repair per profile."""
    rules = [
        {
            const.CONF_RULE_ACTION: "closed",
            const.CONF_RULE_TIME_MODE: "sunset",
            const.CONF_RULE_OFFSET: 0,
        }
    ]
    controller, _sub_id = await start_controller(
        hass,
        hub_entry,
        profile=profile_subentry_data("Night", rules=rules, quiet=("00:00:00", "23:59:00")),
    )
    try:
        profile_id = _subentry_id(hub_entry, const.SUBENTRY_PROFILE)
        issue = ir.async_get(hass).async_get_issue(const.DOMAIN, f"rule_skipped_{profile_id}")
        assert issue is not None
        assert issue.translation_placeholders == {"profile": "Night", "rule": "1"}
    finally:
        await controller.async_stop()


async def test_problem_flag_is_seeded_at_start(hass, hub_entry, cover_services, freezer):
    """An issue that already exists before the controller starts shows up in the hub view."""
    # An id outside `repairs.ENTRY_ISSUE_PREFIXES`: the setup-time stale sweep leaves it alone
    # and the controller never clears it, so only the seeding in `async_start` can surface it.
    ir.async_create_issue(
        hass,
        const.DOMAIN,
        "pre_existing_problem",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="sun_missing",
    )
    controller, _sub_id = await start_controller(hass, hub_entry)
    try:
        assert controller.hub_view.problem is True
    finally:
        await controller.async_stop()


async def test_midnight_task_after_stop_arms_nothing(hass, hub_entry, cover_services, freezer):
    """A trigger task created just before `async_stop` must not re-arm the schedule (F1)."""
    rules = [
        {
            const.CONF_RULE_ACTION: "closed",
            const.CONF_RULE_TIME_MODE: "fixed",
            const.CONF_RULE_TIME: "21:30:00",
        }
    ]
    controller, _sub_id = await start_controller(
        hass, hub_entry, profile=profile_subentry_data("Night", rules=rules, quiet=None)
    )
    assert controller._schedule._unsub is not None
    commands = len(cover_services["close"])
    await controller.async_stop()
    assert controller._schedule._unsub is None
    controller._on_midnight(dt_util.utcnow())
    controller._on_forecast_tick(dt_util.utcnow())
    controller._on_fallback_tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert controller._schedule._unsub is None
    assert len(cover_services["close"]) == commands  # nothing evaluated after the stop


async def test_command_in_flight_is_not_resent(hass, hub_entry, cover_services, freezer):
    """A pending command is never re-sent while the cover has not reported back yet."""
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
        assert controller.engine(sub_id).rt.pending is not None
        # The wind layer skips the minimum-interval gate, so only the in-flight guard can
        # keep these evaluations from re-sending the open the cover has not confirmed yet.
        await controller.async_evaluate_now()
        await controller.async_evaluate_now({sub_id})
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1
    finally:
        await controller.async_stop()


async def test_evaluation_waiting_on_a_lock_does_nothing_after_stop(
    hass, hub_entry, cover_services, freezer
):
    """A task queued on a contended per-cover lock must not act or arm a timer after stop."""
    controller, sub_id = await start_controller(hass, hub_entry)
    commands = len(cover_services["close"])
    lock = controller._locks[sub_id]
    await lock.acquire()
    evaluation = hass.async_create_task(controller.async_evaluate())
    await asyncio.sleep(0)
    stop = hass.async_create_task(controller.async_stop())
    await asyncio.sleep(0)
    assert not controller.started  # `async_stop` is now draining the locks
    lock.release()
    await stop
    await evaluation
    await hass.async_block_till_done()
    assert len(cover_services["close"]) == commands
    assert controller._cover_timers == {}


async def test_stop_during_start_leaves_nothing_running(hass, hub_entry, cover_services):
    """A reload that unloads while the start job is still fetching the forecast must win.

    `async_at_started` hands back a no-op unsubscribe once HA is running, so the start job
    cannot be cancelled: `async_start` has to notice the stop itself and subscribe to nothing.
    """
    hass.config_entries.async_add_subentry(
        hub_entry, ConfigSubentry(**cover_subentry_data("cover.bedroom"))
    )
    set_weather(hass, condition="sunny", temperature=20.0)
    set_sun(hass, elevation=40.0, azimuth=180.0)
    set_sensor(hass, "sensor.wind", 5.0, unit="km/h")
    set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
    hass.set_state(CoreState.starting)  # park the entry's own controller (see start_controller)
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
    blocked = asyncio.Event()

    async def slow_fetch(*args, **kwargs):
        await blocked.wait()
        return HOT

    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(side_effect=slow_fetch),
    ):
        start = hass.async_create_task(controller.async_start())
        for _ in range(10):
            await asyncio.sleep(0)
        assert not start.done()
        await controller.async_stop()
        blocked.set()
        await start

    assert controller.started is False
    assert controller._unsubs == []
    assert controller._schedule._unsub is None
    assert not cover_services["close"]


async def test_expired_weather_grace_stops_arming_the_cover_timer(
    hass, hub_entry, cover_services, freezer
):
    """F1: a grace expiry that has passed is no longer a timer candidate (no 1 Hz loop)."""
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        assert controller.engine(sub_id).rt.pending is None
        hass.states.async_set(WEATHER, "unavailable")
        await hass.async_block_till_done()
        assert sub_id in controller._cover_timers  # armed for the grace expiry itself
        with (
            patch(
                "custom_components.cover_automation.controller.async_fetch_today",
                AsyncMock(return_value=HOT),  # keep the outage to the weather entity alone
            ),
            patch(
                "custom_components.cover_automation.controller.async_call_later",
                wraps=async_call_later,
            ) as arm,
        ):
            for _ in range(3):
                freezer.tick(timedelta(minutes=31))
                async_fire_time_changed(hass)
                await hass.async_block_till_done()
                # The expiry is in the past for the whole outage: nothing left to wait for.
                assert controller._cover_timers == {}
        assert arm.call_count == 0  # not one re-arm per evaluation, let alone per second
    finally:
        await controller.async_stop()


async def test_retry_deadline_is_dropped_once_the_disagreement_resolves(hass, hub_entry, freezer):
    """F1: a failed command whose target is reached by hand must not keep a timer alive."""

    async def failing(call: ServiceCall) -> None:
        raise HomeAssistantError("device offline")

    hass.services.async_register("cover", "close_cover", failing)
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        assert sub_id in controller._retry_at
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        assert controller._retry_at == {}
        assert controller._cover_timers == {}
    finally:
        await controller.async_stop()


async def test_write_api_works_before_the_controller_starts(
    hass, hub_entry, cover_services, hass_storage
):
    """F2: the platforms are up before `async_at_started`, so the setters must not need engines."""
    controller, sub_id = await build_controller(hass, hub_entry)
    await controller.async_set_enabled(sub_id, False)
    await controller.async_set_mode(sub_id, Mode.DARK_ONLY)
    await controller.async_reset_override(sub_id)  # needs the live actual: a no-op before start
    stored = hass_storage[const.storage_key(hub_entry.entry_id)]["data"]["covers"][sub_id]
    assert stored["enabled"] is False and stored["mode"] == "dark_only"
    assert controller.cover_views[sub_id].enabled is False  # the seeded view follows the write
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        p = controller.engine(sub_id).p
        assert p.enabled is False and p.mode is Mode.DARK_ONLY
        assert not cover_services["close"]
    finally:
        await controller.async_stop()


async def test_views_are_seeded_from_the_store_before_start(hass, hub_entry, cover_services):
    """F3: entities must publish persisted state, not dataclass defaults, until the first run."""
    controller, sub_id = await build_controller(
        hass,
        hub_entry,
        persisted=CoverPersisted(enabled=False, mode=Mode.PROTECTION_ONLY),
        store_overrides={
            "shading_mode": ShadingMode.OFF,
            "reopening_mode": ReopeningMode.ACTIVE,
            "simulation": True,
            "verbose": True,
        },
        cover_state=("closed", 0, 3),
    )
    view = controller.cover_views[sub_id]
    assert view.enabled is False and view.mode is Mode.PROTECTION_ONLY
    assert view.status is Status.DISABLED and view.actual_state == "closed"
    hub = controller.hub_view
    assert hub.shading_mode is ShadingMode.OFF and hub.reopening_mode is ReopeningMode.ACTIVE
    assert hub.simulation is True and hub.verbose is True


async def test_core_config_update_rearms_the_schedule_and_evaluates(
    hass, hub_entry, cover_services, freezer
):
    """F6: a time-zone/location change moves every schedule time (spec §2/§5)."""
    controller, sub_id = await start_controller(
        hass, hub_entry, profile=profile_subentry_data("Night", quiet=None)
    )
    try:
        unsub_before = controller._schedule._unsub
        view_before = controller.cover_views[sub_id]
        assert unsub_before is not None
        hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE)
        await hass.async_block_till_done()
        assert controller._schedule._unsub is not unsub_before  # re-armed
        assert controller.cover_views[sub_id] is not view_before  # and re-evaluated
    finally:
        await controller.async_stop()


async def test_position_only_cover_uses_set_cover_position(hass, hub_entry, cover_services):
    """F5: a cover that only advertises SET_POSITION is driven by position."""
    controller, _sub_id = await start_controller(
        hass, hub_entry, cover_state=("open", 100, CoverEntityFeature.SET_POSITION)
    )
    try:
        assert not cover_services["close"]
        assert len(cover_services["position"]) == 1
        call = cover_services["position"][0]
        assert call.data["entity_id"] == "cover.bedroom" and call.data["position"] == 0
    finally:
        await controller.async_stop()


async def test_cover_without_usable_features_raises_a_repair_until_it_reports_them(
    hass, hub_entry, cover_services
):
    """F5: features=0 means no command at all, with a repair that clears when they show up."""
    controller, sub_id = await start_controller(hass, hub_entry, cover_state=("open", 100, 0))
    try:
        reg = ir.async_get(hass)
        issue_id = f"cover_unsupported_{sub_id}"
        assert not cover_services["close"] and not cover_services["position"]
        assert reg.async_get_issue(const.DOMAIN, issue_id) is not None
        set_cover(hass, "cover.bedroom", state="open", position=100, features=3)
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, issue_id) is None
        assert len(cover_services["close"]) == 1
    finally:
        await controller.async_stop()


async def test_door_layer_opens_holds_and_reports_a_missing_sensor(
    hass, hub_entry, cover_services, freezer
):
    """F7a: an open door wins over shading, and a missing door sensor raises a repair."""
    hass.states.async_set("binary_sensor.terrace", "off")
    controller, sub_id = await start_controller(
        hass, hub_entry, cover_overrides={const.CONF_DOOR_SENSOR: "binary_sensor.terrace"}
    )
    try:
        assert len(cover_services["close"]) == 1  # hot and sunny: shading closed it
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.terrace", "on")
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1  # the door layer skips the minimum interval
        assert controller.cover_views[sub_id].status is Status.DOOR_OPEN
        set_cover(hass, "cover.bedroom", state="open", position=100)
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.terrace", "off")
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 1  # shading is held by the minimum interval
        freezer.tick(timedelta(minutes=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 2  # ... and closes again once it has passed
        hass.states.async_remove("binary_sensor.terrace")
        await hass.async_block_till_done()
        issue = ir.async_get(hass).async_get_issue(
            const.DOMAIN, f"door_sensor_unavailable_{sub_id}"
        )
        assert issue is not None
    finally:
        await controller.async_stop()


async def test_frost_conflict_notifies_once_and_is_dismissed_when_it_clears(
    hass, hub_entry, cover_services, freezer
):
    """F7b/F12: frost beats wind, notifies once, and cleans up when the frost goes away."""
    hass.config_entries.async_update_entry(
        hub_entry,
        data={**hub_entry.data, const.CONF_OUTDOOR_TEMPERATURE_SENSOR: "sensor.outdoor"},
    )
    set_sensor(hass, "sensor.outdoor", -5.0, unit="°C", device_class="temperature")
    controller, sub_id = await build_controller(
        hass,
        hub_entry,
        cover_overrides={
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_UNIT: "km/h",
        },
        cover_state=("closed", 0, 3),
    )
    set_sensor(hass, "sensor.wind", 75.0, unit="km/h", device_class="wind_speed")
    reg = ir.async_get(hass)
    issue_id = f"frost_conflict_{sub_id}"
    with (
        patch(
            "custom_components.cover_automation.controller.async_fetch_today",
            AsyncMock(return_value=HOT),
        ),
        patch.object(persistent_notification, "async_create") as create,
        patch.object(persistent_notification, "async_dismiss") as dismiss,
    ):
        await controller.async_start()
        await hass.async_block_till_done()
        try:
            assert not cover_services["open"]  # frost holds the cover shut
            assert controller.cover_views[sub_id].status is Status.HELD_FROST
            assert reg.async_get_issue(const.DOMAIN, issue_id) is not None
            assert create.call_count == 1
            assert create.call_args.kwargs["notification_id"] == f"cover_automation_frost_{sub_id}"
            await controller.async_evaluate_now()
            assert create.call_count == 1  # notified once per conflict, not per evaluation
            set_sensor(hass, "sensor.outdoor", 10.0, unit="°C", device_class="temperature")
            await hass.async_block_till_done()
            assert reg.async_get_issue(const.DOMAIN, issue_id) is None
            assert len(cover_services["open"]) == 1  # wind may protect the cover again
            dismiss.assert_called_once_with(hass, f"cover_automation_frost_{sub_id}")
        finally:
            await controller.async_stop()


async def test_quiet_hours_block_a_shading_close(hass, hub_entry, cover_services, freezer):
    """F7c: inside quiet hours nothing moves, and the status says why."""
    rules = [
        {
            const.CONF_RULE_ACTION: "open",
            const.CONF_RULE_TIME_MODE: "fixed",
            const.CONF_RULE_TIME: "07:00:00",
        }
    ]
    freezer.move_to(dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0))
    controller, sub_id = await start_controller(
        hass,
        hub_entry,
        profile=profile_subentry_data("Night", rules=rules, quiet=("22:00:00", "07:00:00")),
    )
    try:
        view = controller.cover_views[sub_id]
        assert not cover_services["close"] and not cover_services["open"]
        assert view.status is Status.QUIET_HOURS and view.winning_layer == "quiet_hours"
    finally:
        await controller.async_stop()


async def test_confirm_window_expiry_then_a_user_stop_part_way(
    hass, hub_entry, cover_services, freezer
):
    """F7d: a cover that never moves goes `unconfirmed`; one that stops part-way is a user stop."""
    controller, sub_id = await start_controller(hass, hub_entry)
    try:
        engine = controller.engine(sub_id)
        assert len(cover_services["close"]) == 1 and engine.rt.pending is not None
        freezer.tick(timedelta(seconds=121))  # confirm window, cover still reporting `open`
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert controller.cover_views[sub_id].status is Status.UNCONFIRMED
        assert engine.rt.pending is None and engine.rt.backoff_until is not None
        freezer.tick(timedelta(minutes=11))  # backoff and minimum interval both elapse
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 2 and engine.rt.pending is not None
        set_cover(hass, "cover.bedroom", state="open", position=50)  # the user hit stop
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=121))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        view = controller.cover_views[sub_id]
        assert engine.p.owner is Owner.USER and engine.p.dam is Target.CLOSED
        assert view.status is Status.PARTIAL and view.override_active
    finally:
        await controller.async_stop()


async def test_midnight_rollover_clears_hot_day_and_refetches_the_forecast(
    hass, hub_entry, cover_services, freezer
):
    """F7e: the local-midnight job rolls the latch over and fetches today's forecast again."""
    freezer.move_to(dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0))
    controller, _sub_id = await start_controller(hass, hub_entry)
    try:
        assert controller.hub_view.hot_day is True
        with patch(
            "custom_components.cover_automation.controller.async_fetch_today",
            AsyncMock(return_value=None),
        ) as fetch:
            freezer.tick(timedelta(hours=1, minutes=1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            assert fetch.called
        assert controller.hub_view.hot_day is None
    finally:
        await controller.async_stop()


async def test_missing_entity_repairs_follow_live_state(hass, hub_entry, cover_services):
    """R1: an optional entity that disappears (or returns) is repaired without a reload."""
    set_sensor(hass, "binary_sensor.door", "off", device_class="door")
    controller, _sub_id = await start_controller(
        hass, hub_entry, cover_overrides={const.CONF_DOOR_SENSOR: "binary_sensor.door"}
    )
    reg = ir.async_get(hass)
    issue_id = f"missing_entity_{hub_entry.entry_id}_binary_sensor.door"
    try:
        assert reg.async_get_issue(const.DOMAIN, issue_id) is None
        assert hub_entry.runtime_data.missing_entities == []

        hass.states.async_remove("binary_sensor.door")
        await hass.async_block_till_done()
        issue = reg.async_get_issue(const.DOMAIN, issue_id)
        assert issue is not None and issue.translation_key == "missing_entity"
        assert issue.translation_placeholders == {"entity_id": "binary_sensor.door"}
        assert hub_entry.runtime_data.missing_entities == ["binary_sensor.door"]

        set_sensor(hass, "binary_sensor.door", "off", device_class="door")
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, issue_id) is None
        assert hub_entry.runtime_data.missing_entities == []
    finally:
        await controller.async_stop()


async def test_protection_keeps_running_through_a_sun_outage(hass, hub_entry, cover_services):
    """R3: a `sun.sun` blip must not pause wind, frost and door protection."""
    controller, _sub_id = await start_controller(
        hass,
        hub_entry,
        cover_overrides={
            const.CONF_WIND_ENABLED: True,
            const.CONF_WIND_UPPER: 60,
            const.CONF_WIND_LOWER: 50,
            const.CONF_WIND_UNIT: "km/h",
        },
    )
    reg = ir.async_get(hass)
    issue_id = f"sun_missing_{hub_entry.entry_id}"
    try:
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        hass.states.async_remove("sun.sun")
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, issue_id) is not None

        set_sensor(hass, "sensor.wind", 75.0, unit="km/h", device_class="wind_speed")
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1  # evaluated on the cached sun position
        assert reg.async_get_issue(const.DOMAIN, issue_id) is not None

        set_sun(hass, elevation=40.0, azimuth=180.0)
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, issue_id) is None
    finally:
        await controller.async_stop()


async def test_sun_cache_expires_and_stops_evaluating(hass, hub_entry, cover_services, freezer):
    """R3: a stale position would keep a daylight elevation, so the cache is bounded."""
    controller, _sub_id = await start_controller(
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
        hass.states.async_remove("sun.sun")
        await hass.async_block_till_done()

        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        set_sensor(hass, "sensor.wind", 75.0, unit="km/h", device_class="wind_speed")
        await hass.async_block_till_done()
        assert not cover_services["open"]  # beyond the TTL no cover is evaluated at all
        assert (
            ir.async_get(hass).async_get_issue(const.DOMAIN, f"sun_missing_{hub_entry.entry_id}")
            is not None
        )
    finally:
        await controller.async_stop()


async def test_start_without_a_sun_position_skips_every_cover(hass, hub_entry, cover_services):
    """The never-known branch: nothing to fall back on, so no cover is evaluated."""
    controller, sub_id = await build_controller(hass, hub_entry)
    hass.states.async_remove("sun.sun")
    with patch(
        "custom_components.cover_automation.controller.async_fetch_today",
        AsyncMock(return_value=HOT),
    ):
        await controller.async_start()
        await hass.async_block_till_done()
    try:
        assert not cover_services["close"] and not cover_services["open"]
        assert controller.cover_views[sub_id].winning_layer == "none"
        assert (
            ir.async_get(hass).async_get_issue(const.DOMAIN, f"sun_missing_{hub_entry.entry_id}")
            is not None
        )
    finally:
        await controller.async_stop()


WIND_OVERRIDE = "input_boolean.windschutz"
WIND_COVER = {
    const.CONF_WIND_ENABLED: True,
    const.CONF_WIND_UPPER: 60,
    const.CONF_WIND_LOWER: 50,
    const.CONF_WIND_UNIT: "km/h",
}


def _configure_wind_override(hass, hub_entry, entity_id: str = WIND_OVERRIDE) -> None:
    hass.config_entries.async_update_entry(
        hub_entry, options={**hub_entry.options, const.CONF_WIND_OVERRIDE_ENTITY: entity_id}
    )


async def test_wind_override_forces_protection_and_defers_the_re_close(
    hass, hub_entry, cover_services, freezer
):
    """The hub's storm switch opens a wind-enabled cover while the wind sensor stays calm."""
    _configure_wind_override(hass, hub_entry)
    hass.states.async_set(WIND_OVERRIDE, "off")
    controller, sub_id = await start_controller(hass, hub_entry, cover_overrides=WIND_COVER)
    try:
        assert len(cover_services["close"]) == 1  # hot sunny day: shading closed it
        set_cover(hass, "cover.bedroom", state="closed", position=0)
        await hass.async_block_till_done()
        assert controller.cover_views[sub_id].wind_state == "inactive"

        hass.states.async_set(WIND_OVERRIDE, "on")
        await hass.async_block_till_done()
        assert len(cover_services["open"]) == 1
        view = controller.cover_views[sub_id]
        assert view.status is Status.PROTECTED_WIND and view.wind_active
        assert view.wind_state == "forced" and controller.hub_view.any_wind_active

        set_cover(hass, "cover.bedroom", state="open", position=100)
        await hass.async_block_till_done()
        hass.states.async_set(WIND_OVERRIDE, "off")
        await hass.async_block_till_done()
        view = controller.cover_views[sub_id]
        assert view.wind_state == "inactive" and not view.wind_active
        assert not controller.hub_view.any_wind_active
        # The release opens the engine's restoring window (a quiet-hours exemption only,
        # see the test below); what actually holds the shading close back here is gate 8,
        # the minimum interval since the wind open.
        assert controller.engine(sub_id).rt.restoring_until is not None
        assert len(cover_services["close"]) == 1
        assert view.next_planned_action == "closed (min_interval)"
        assert view.next_planned_at == controller.engine(sub_id).p.last_send_at + timedelta(
            minutes=10
        )
        freezer.tick(timedelta(minutes=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 2  # min interval elapsed: shading closes again
    finally:
        await controller.async_stop()


async def test_wind_override_release_may_close_during_quiet_hours(
    hass, hub_entry, cover_services, freezer
):
    """The restoring window is a quiet-hours exemption: the post-release close goes out.

    `test_quiet_hours_block_a_shading_close` is the counterpart without a wind episode.
    """
    _configure_wind_override(hass, hub_entry)
    hass.states.async_set(WIND_OVERRIDE, "on")
    freezer.move_to(dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0))
    controller, sub_id = await start_controller(
        hass,
        hub_entry,
        cover_overrides=WIND_COVER,
        profile=profile_subentry_data(
            "Night",
            rules=[
                {
                    const.CONF_RULE_ACTION: "open",
                    const.CONF_RULE_TIME_MODE: "fixed",
                    const.CONF_RULE_TIME: "07:00:00",
                }
            ],
            quiet=("22:00:00", "07:00:00"),
        ),
    )
    try:
        # Forced wind wants the cover open and it already is, so nothing has been sent yet.
        assert not cover_services["close"] and not cover_services["open"]
        assert controller.cover_views[sub_id].wind_state == "forced"

        hass.states.async_set(WIND_OVERRIDE, "off")
        await hass.async_block_till_done()
        assert len(cover_services["close"]) == 1  # quiet hours exempted while restoring
        view = controller.cover_views[sub_id]
        assert view.winning_layer == "shading" and view.status is Status.CLOSED_SHADING
        # The send itself consumes the window (`commands.on_sent` clears it), so the
        # command going out at all is the evidence that quiet hours were exempted.
        assert controller.engine(sub_id).rt.restoring_until is None
    finally:
        await controller.async_stop()


async def test_wind_override_leaves_a_cover_without_wind_protection_alone(
    hass, hub_entry, cover_services
):
    _configure_wind_override(hass, hub_entry)
    hass.states.async_set(WIND_OVERRIDE, "on")
    controller, sub_id = await start_controller(hass, hub_entry)  # wind_enabled defaults to False
    try:
        assert len(cover_services["open"]) == 0
        view = controller.cover_views[sub_id]
        assert view.wind_state == "disabled" and not view.wind_active
        assert not controller.hub_view.any_wind_active
    finally:
        await controller.async_stop()


@pytest.mark.parametrize(("wind_action", "opens"), [("open", 1), ("hold", 0)])
async def test_wind_override_is_applied_on_the_first_reconcile_for_either_wind_action(
    hass, hub_entry, cover_services, wind_action, opens
):
    """The helper is already on at startup, so forcing applies from the first evaluation."""
    _configure_wind_override(hass, hub_entry)
    hass.states.async_set(WIND_OVERRIDE, "on")
    controller, sub_id = await start_controller(
        hass,
        hub_entry,
        cover_overrides={**WIND_COVER, const.CONF_WIND_ACTION: wind_action},
        cover_state=("closed", 0, 3),
    )
    try:
        assert len(cover_services["open"]) == opens
        assert not cover_services["close"]  # wind wins over the hot sunny day
        view = controller.cover_views[sub_id]
        assert view.status is Status.PROTECTED_WIND and view.wind_state == "forced"
        assert view.wind_active and controller.hub_view.any_wind_active
    finally:
        await controller.async_stop()


async def test_missing_wind_override_entity_raises_a_repair(hass, hub_entry, cover_services):
    _configure_wind_override(hass, hub_entry)
    controller, _sub_id = await start_controller(hass, hub_entry, cover_overrides=WIND_COVER)
    reg = ir.async_get(hass)
    issue_id = f"missing_entity_{hub_entry.entry_id}_{WIND_OVERRIDE}"
    try:
        issue = reg.async_get_issue(const.DOMAIN, issue_id)
        assert issue is not None and issue.translation_key == "missing_entity"
        assert issue.translation_placeholders == {"entity_id": WIND_OVERRIDE}
        hass.states.async_set(WIND_OVERRIDE, "off")
        await hass.async_block_till_done()
        assert reg.async_get_issue(const.DOMAIN, issue_id) is None
    finally:
        await controller.async_stop()
