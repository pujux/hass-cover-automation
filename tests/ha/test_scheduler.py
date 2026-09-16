from __future__ import annotations

from datetime import date, datetime, time, timedelta

from custom_components.cover_automation.engine.model import CoverState, Desired, Target
from custom_components.cover_automation.engine.schedule import Profile, QuietHours, Rule, TimeMode
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
        Rule(Target.CLOSED, TimeMode.FIXED, time(21, 30)),
        Rule(Target.OPEN, TimeMode.SUNRISE, None, 30, earliest=time(7, 0)),
    ),
    QuietHours(time(22, 0), time(7, 0)),
)


def local(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=dt_util.get_default_time_zone())


async def test_next_event_and_view(hass: HomeAssistant) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    tracker = ScheduleTracker(
        hass, {"p1": NIGHT}, {"c1": "p1", "c2": None}, on_fire, sun=FixedSun()
    )
    now = local(2026, 7, 10, 20, 0)
    ev = tracker.next_event(now)
    assert ev is not None and ev.at == local(2026, 7, 10, 21, 30) and ev.action is Target.CLOSED
    assert (
        ev.covers == ("c1",)
        and ev.profile_name == "Night"
        and tracker.next_event_for("c2", now) is None
    )
    assert tracker.view("c2", now, CoverState.OPEN, None, None).desired is Desired.LEAVE_ALONE
    after = local(2026, 7, 10, 21, 31)
    view = tracker.view("c1", after, CoverState.OPEN, None, None)
    assert view.desired is Desired.CLOSED and view.rule_index == 0 and view.quiet_active is False
    assert tracker.active_rule_label("c1", after) == "close rule 1 of Night (21:30)"
    assert (
        tracker.view("c1", local(2026, 7, 10, 22, 30), CoverState.CLOSED, None, None).quiet_active
        is True
    )
    morning = tracker.next_event(after)
    assert (
        morning is not None
        and morning.at == local(2026, 7, 11, 7, 0)
        and morning.action is Target.OPEN
    )  # 06:30 clamped by earliest 07:00


async def test_arm_fires_and_rearms(hass: HomeAssistant, freezer) -> None:
    fired: list[tuple[frozenset[str], datetime]] = []

    async def on_fire(covers, at):
        fired.append((covers, at))

    freezer.move_to(local(2026, 7, 10, 21, 29))
    tracker = ScheduleTracker(hass, {"p1": NIGHT}, {"c1": "p1"}, on_fire, sun=FixedSun())
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


async def test_hass_sun_times_and_skipped_rules(hass: HomeAssistant) -> None:
    sun = HassSunTimes(hass)
    day = date(2026, 7, 10)
    assert sun.sunrise(day) < sun.sunset(day)
    # a sunset close rule whose only clamp would land on the previous day is skipped
    all_day_quiet = Profile(
        "p2",
        "Quiet",
        (Rule(Target.CLOSED, TimeMode.SUNSET, None, 0),),
        QuietHours(time(0, 0), time(23, 59)),
    )
    tracker = ScheduleTracker(
        hass, {"p2": all_day_quiet}, {"c1": "p2"}, lambda c, a: None, sun=FixedSun()
    )  # type: ignore[arg-type]
    assert tracker.skipped_rules_today(local(2026, 7, 10, 12, 0)) == [("p2", 0)]
