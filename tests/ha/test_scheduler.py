from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from custom_components.cover_automation.engine.model import CoverState, Desired
from custom_components.cover_automation.engine.schedule import (
    Profile,
    QuietHours,
    Rule,
    RuleAction,
    TimeMode,
)
from custom_components.cover_automation.scheduler import HassSunTimes, ScheduleTracker
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


class FixedSun:
    def sunrise(self, day: date) -> datetime:
        return datetime.combine(day, time(6, 0), tzinfo=dt_util.get_default_time_zone())

    def sunset(self, day: date) -> datetime:
        return datetime.combine(day, time(20, 0), tzinfo=dt_util.get_default_time_zone())


NIGHT = Profile(
    "p1",
    "Night",
    (
        Rule(RuleAction.CLOSED, TimeMode.FIXED, time(21, 30)),
        Rule(RuleAction.OPEN, TimeMode.SUNRISE, None, 30, earliest=time(7, 0)),
    ),
    QuietHours(time(22, 0), time(7, 0)),
)


BEDROOM = Profile(
    "p3",
    "Bedroom",
    (
        Rule(RuleAction.CLOSED, TimeMode.FIXED, time(21, 30)),
        Rule(RuleAction.RELEASE, TimeMode.FIXED, time(8, 0)),
    ),
)


def local(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=dt_util.get_default_time_zone())


async def test_next_event_and_view(hass: HomeAssistant) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    tracker = ScheduleTracker(
        hass, {"p1": NIGHT}, {"c1": ["p1"], "c2": []}, on_fire, sun=FixedSun()
    )
    now = local(2026, 7, 10, 20, 0)
    ev = tracker.next_event(now)
    assert ev is not None and ev.at == local(2026, 7, 10, 21, 30) and ev.action is RuleAction.CLOSED
    assert (
        ev.covers == ("c1",)
        and ev.profile_name == "Night"
        and tracker.next_event_for("c2", now) is None
    )
    assert tracker.view("c2", now, CoverState.OPEN, None, {}).desired is Desired.LEAVE_ALONE
    after = local(2026, 7, 10, 21, 31)
    view = tracker.view("c1", after, CoverState.OPEN, None, {})
    assert view.desired is Desired.CLOSED and view.rule_index == 0 and view.quiet_active is False
    assert view.profile_id == "p1"
    assert (
        tracker.active_rule_label(view.profile_id, view.rule_index, view.rule_fired_at)
        == "close rule 1 of Night (21:30)"
    )
    assert (
        tracker.view("c1", local(2026, 7, 10, 22, 30), CoverState.CLOSED, None, {}).quiet_active
        is True
    )
    morning = tracker.next_event(after)
    assert (
        morning is not None
        and morning.at == local(2026, 7, 11, 7, 0)
        and morning.action is RuleAction.OPEN
    )  # 06:30 clamped by earliest 07:00


async def test_release_rule_next_event_view_and_label(hass: HomeAssistant) -> None:
    async def on_fire(covers, at):
        return None

    tracker = ScheduleTracker(hass, {"p3": BEDROOM}, {"c1": ["p3"]}, on_fire, sun=FixedSun())
    ev = tracker.next_event(local(2026, 7, 10, 22, 0))
    assert ev is not None and ev.at == local(2026, 7, 11, 8, 0)
    assert ev.action is RuleAction.RELEASE and ev.action.value == "release"
    before = local(2026, 7, 11, 7, 59)
    assert tracker.view("c1", before, CoverState.CLOSED, None, {}).desired is Desired.CLOSED
    after = local(2026, 7, 11, 8, 1)
    view = tracker.view("c1", after, CoverState.CLOSED, None, {})
    assert view.desired is Desired.LEAVE_ALONE and view.rule_index == 1
    assert (
        tracker.active_rule_label(view.profile_id, view.rule_index, view.rule_fired_at)
        == "release rule 2 of Bedroom (08:00)"
    )


async def test_arm_fires_and_rearms(hass: HomeAssistant, freezer) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": ["p1"]}, on_fire, sun=FixedSun())
    tracker.async_arm(dt_util.utcnow())
    freezer.move_to(
        local(
            2026,
            7,
            10,
            21,
            30,
        )
        + timedelta(seconds=1)
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(fired) == 1 and fired[0][0] == frozenset({"c1"})
    assert tracker.next_event(dt_util.utcnow()).at == local(2026, 7, 11, 7, 0)
    tracker.async_cancel()


async def test_arm_runs_the_handler_on_the_injected_task_factory(
    hass: HomeAssistant, freezer
) -> None:
    """F9: with a `create_task` the fire handler runs on a task the entry owns."""
    fired: list[tuple[frozenset[str], datetime]] = []
    tasks: list[str] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    def create_task(coro, name):
        tasks.append(name)
        hass.async_create_task(coro)

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(
        hass,
        {"p1": NIGHT},
        {"c1": ["p1"]},
        on_fire,
        sun=FixedSun(),
        create_task=create_task,
    )
    tracker.async_arm(dt_util.utcnow())
    freezer.move_to(local(2026, 7, 10, 21, 30) + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert tasks == ["schedule rule"]
    assert len(fired) == 1 and fired[0][0] == frozenset({"c1"})
    assert tracker.next_event(dt_util.utcnow()).at == local(2026, 7, 11, 7, 0)  # re-armed
    tracker.async_cancel()


async def test_fired_handler_exception_still_rearms(hass: HomeAssistant, freezer, caplog) -> None:
    calls: list[tuple[frozenset[str], datetime]] = []

    async def flaky_on_fire(covers, at):
        calls.append((covers, at))
        if len(calls) == 1:
            raise RuntimeError("boom")

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": ["p1"]}, flaky_on_fire, sun=FixedSun())
    tracker.async_arm(dt_util.utcnow())
    freezer.move_to(local(2026, 7, 10, 21, 30) + timedelta(seconds=1))
    with caplog.at_level(logging.ERROR):
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(calls) == 1
    assert "Schedule rule handler failed" in caplog.text
    assert tracker._unsub is not None  # re-armed despite the handler exception

    # the re-armed timer still fires the next (morning) event normally
    freezer.move_to(local(2026, 7, 11, 7, 0) + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 2 and calls[1][0] == frozenset({"c1"})
    tracker.async_cancel()


async def test_cancel_while_handler_suspended_prevents_rearm(hass: HomeAssistant, freezer) -> None:
    """async_cancel during an in-flight on_fire must not let the stale _fired re-arm (fix round 2)."""
    resume = asyncio.Event()
    fired: list[frozenset[str]] = []

    async def on_fire(covers, at):
        await resume.wait()
        fired.append(covers)

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": ["p1"]}, on_fire, sun=FixedSun())
    tracker.async_arm(dt_util.utcnow())
    freezer.move_to(local(2026, 7, 10, 21, 30) + timedelta(seconds=1))
    # HA runs coroutine jobs as eager tasks: this synchronously drives _fired up to the
    # `await resume.wait()` suspension point before returning.
    async_fire_time_changed(hass)

    tracker.async_cancel()  # e.g. integration unload while the handler is still awaiting

    resume.set()
    await hass.async_block_till_done()

    assert fired == [frozenset({"c1"})]
    assert tracker._unsub is None  # not silently resurrected by the stale _fired
    assert tracker._armed_at is None


async def test_fired_only_covers_the_exact_instant(hass: HomeAssistant, freezer) -> None:
    """Two profiles firing 20s apart must not be batched together (plan decision, fix round 1)."""
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    profile_a = Profile("pa", "A", (Rule(RuleAction.CLOSED, TimeMode.FIXED, time(21, 30, 0)),))
    profile_b = Profile("pb", "B", (Rule(RuleAction.CLOSED, TimeMode.FIXED, time(21, 30, 20)),))

    freezer.move_to(local(2026, 7, 10, 21, 29, 50))
    tracker = ScheduleTracker(
        hass,
        {"pa": profile_a, "pb": profile_b},
        {"ca": ["pa"], "cb": ["pb"]},
        on_fire,
        sun=FixedSun(),
    )
    tracker.async_arm(dt_util.utcnow())

    freezer.move_to(local(2026, 7, 10, 21, 30, 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert fired == [(frozenset({"ca"}), local(2026, 7, 10, 21, 30, 0))]

    freezer.move_to(local(2026, 7, 10, 21, 30, 21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert fired[1] == (frozenset({"cb"}), local(2026, 7, 10, 21, 30, 20))
    assert len(fired) == 2
    tracker.async_cancel()


async def test_hass_sun_times_and_skipped_rules(hass: HomeAssistant) -> None:
    sun = HassSunTimes(hass)
    day = date(2026, 7, 10)
    assert sun.sunrise(day) < sun.sunset(day)
    # a sunset close rule whose only clamp would land on the previous day is skipped
    all_day_quiet = Profile(
        "p2",
        "Quiet",
        (Rule(RuleAction.CLOSED, TimeMode.SUNSET, None, 0),),
        QuietHours(time(0, 0), time(23, 59)),
    )
    tracker = ScheduleTracker(
        hass, {"p2": all_day_quiet}, {"c1": ["p2"]}, lambda c, a: None, sun=FixedSun()
    )  # type: ignore[arg-type]
    assert tracker.skipped_rules_today(local(2026, 7, 10, 12, 0)) == [("p2", 0)]


EVENING = Profile("p4", "Evening", (Rule(RuleAction.CLOSED, TimeMode.FIXED, time(20, 0)),))


async def test_layered_cover_merges_and_plans_across_its_profiles(hass: HomeAssistant) -> None:
    """A cover with two profiles: the next event is the earliest of the two, and the merged
    view names the profile that is actually holding."""

    async def on_fire(covers, at):
        return None

    tracker = ScheduleTracker(
        hass,
        {"p4": EVENING, "p3": BEDROOM},
        {"c1": ["p4", "p3"]},
        on_fire,
        sun=FixedSun(),
    )
    now = local(2026, 7, 10, 19, 0)
    ev = tracker.next_event_for("c1", now)
    assert ev is not None and ev.at == local(2026, 7, 10, 20, 0) and ev.profile_id == "p4"
    # 20:30: only Evening has fired, so it decides
    v = tracker.view("c1", local(2026, 7, 10, 20, 30), CoverState.OPEN, None, {})
    assert v.desired is Desired.CLOSED and v.profile_id == "p4"
    # 22:00: Bedroom's own close rule has fired too, but the higher priority still wins
    v2 = tracker.view("c1", local(2026, 7, 10, 22, 0), CoverState.CLOSED, None, {})
    assert v2.desired is Desired.CLOSED and v2.profile_id == "p4"
    assert tracker._covers_of("p3") == ("c1",) and tracker._covers_of("p4") == ("c1",)


async def test_disabled_profiles_are_silent_and_arm_nothing(hass: HomeAssistant) -> None:
    async def on_fire(covers, at):
        return None

    off: set[str] = set()
    tracker = ScheduleTracker(
        hass,
        {"p4": EVENING, "p3": BEDROOM},
        {"c1": ["p4", "p3"]},
        on_fire,
        sun=FixedSun(),
        enabled=lambda profile_id: profile_id not in off,
    )
    at = local(2026, 7, 10, 22, 0)
    assert tracker.view("c1", at, CoverState.CLOSED, None, {}).profile_id == "p4"
    off.add("p4")
    # Evening is switched off: Bedroom, one rank down, decides instead
    handed_down = tracker.view("c1", at, CoverState.CLOSED, None, {})
    assert handed_down.desired is Desired.CLOSED and handed_down.profile_id == "p3"
    assert tracker.next_event_for("c1", at).profile_id == "p3"
    off.add("p3")
    assert tracker.view("c1", at, CoverState.CLOSED, None, {}).desired is Desired.LEAVE_ALONE
    assert tracker.next_event(at) is None and tracker.next_event_for("c1", at) is None
    tracker.async_arm(at)
    assert tracker._unsub is None  # nothing to arm while every profile is off
    assert tracker.skipped_rules_today(at) == []
