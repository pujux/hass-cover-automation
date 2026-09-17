"""Schedule profiles: rule fire times, quiet hours, holds and releases (spec §1.2 layer 5, §3).

Timezone contract: every rule and quiet-hours boundary is a wall-clock time in the home's
local zone. The query functions (`quiet_window`, `quiet_active`, `last_fired`, `next_fire`,
`fired_between`, `view`) take an optional `tz` and convert their datetime arguments to it
first; without it they read the local day and clock off the argument's own `tzinfo`, so a
caller passing `dt_util.utcnow()` and no `tz` silently evaluates the schedule in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Protocol

from .const import OPEN_RULE_WINDOW_S
from .model import CoverState, Desired, ScheduleView, Target


class TimeMode(StrEnum):
    FIXED = "fixed"
    SUNRISE = "sunrise"
    SUNSET = "sunset"


class RuleAction(StrEnum):
    """What a schedule rule does when it fires (spec §1.2 layer 5, decisions 13 and 33).

    The `closed`/`open` values are the ones stored profiles already carry, so adding
    `release` does not touch existing configuration.
    """

    CLOSED = "closed"
    OPEN = "open"
    RELEASE = "release"

    @property
    def target(self) -> Target | None:
        """The state this action drives the cover to; None for `release`, which has none."""
        return None if self is RuleAction.RELEASE else Target(self.value)


@dataclass(frozen=True, slots=True)
class Rule:
    action: RuleAction
    time_mode: TimeMode
    time: time | None = None
    offset_minutes: int = 0
    earliest: time | None = None
    latest: time | None = None


@dataclass(frozen=True, slots=True)
class QuietHours:
    start: time
    end: time


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    name: str
    rules: tuple[Rule, ...] = ()
    quiet_hours: QuietHours | None = None


class SunTimes(Protocol):
    def sunrise(self, day: date) -> datetime: ...

    def sunset(self, day: date) -> datetime: ...


def _combine(day: date, clock: time, tz: tzinfo) -> datetime:
    return datetime.combine(day, clock).replace(tzinfo=tz)


def quiet_window(
    quiet: QuietHours | None, at: datetime, *, tz: tzinfo | None = None
) -> tuple[datetime, datetime] | None:
    """The quiet-hours window [start, end) that contains `at`, or None."""
    if quiet is None:
        return None
    if tz is not None:
        at = at.astimezone(tz)
    else:
        tz = at.tzinfo
    assert tz is not None
    day = at.date()
    if quiet.start < quiet.end:
        start, end = _combine(day, quiet.start, tz), _combine(day, quiet.end, tz)
        return (start, end) if start <= at < end else None
    if quiet.start == quiet.end:
        return None
    if at.time() >= quiet.start:
        return _combine(day, quiet.start, tz), _combine(day + timedelta(days=1), quiet.end, tz)
    if at.time() < quiet.end:
        return _combine(day - timedelta(days=1), quiet.start, tz), _combine(day, quiet.end, tz)
    return None


def quiet_active(quiet: QuietHours | None, now: datetime, *, tz: tzinfo | None = None) -> bool:
    return quiet_window(quiet, now, tz=tz) is not None


def fire_time(
    rule: Rule, day: date, sun: SunTimes, quiet: QuietHours | None, tz: tzinfo
) -> datetime | None:
    if rule.time_mode is TimeMode.FIXED:
        if rule.time is None:
            return None
        fire = _combine(day, rule.time, tz)
        return None if quiet_active(quiet, fire) else fire
    base = sun.sunrise(day) if rule.time_mode is TimeMode.SUNRISE else sun.sunset(day)
    fire = (base + timedelta(minutes=rule.offset_minutes)).astimezone(tz)
    if rule.earliest is not None:
        fire = max(fire, _combine(day, rule.earliest, tz))
    if rule.latest is not None:
        fire = min(fire, _combine(day, rule.latest, tz))
    window = quiet_window(quiet, fire)
    if window is None:
        return fire
    start, end = window
    if rule.action.target is Target.CLOSED:
        clamped = start - timedelta(minutes=1)
        return clamped if clamped.date() == day else None
    # open and release rules are clamped forward to the end of the window
    return end


def fire_times(
    profile: Profile, day: date, sun: SunTimes, tz: tzinfo
) -> list[tuple[datetime, int]]:
    out: list[tuple[datetime, int]] = []
    for index, rule in enumerate(profile.rules):
        fire = fire_time(rule, day, sun, profile.quiet_hours, tz)
        if fire is not None:
            out.append((fire, index))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _candidates(
    profile: Profile, now: datetime, sun: SunTimes, days: range
) -> list[tuple[datetime, int]]:
    tz = now.tzinfo
    assert tz is not None
    out: list[tuple[datetime, int]] = []
    for delta in days:
        out.extend(fire_times(profile, now.date() + timedelta(days=delta), sun, tz))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def last_fired(
    profile: Profile, now: datetime, sun: SunTimes, *, tz: tzinfo | None = None
) -> tuple[datetime, int] | None:
    if tz is not None:
        now = now.astimezone(tz)
    past = [c for c in _candidates(profile, now, sun, range(-2, 1)) if c[0] <= now]
    return past[-1] if past else None


def next_fire(
    profile: Profile, now: datetime, sun: SunTimes, *, tz: tzinfo | None = None
) -> tuple[datetime, int] | None:
    if tz is not None:
        now = now.astimezone(tz)
    future = [c for c in _candidates(profile, now, sun, range(0, 2)) if c[0] > now]
    return future[0] if future else None


def fired_between(
    profile: Profile, start: datetime, end: datetime, sun: SunTimes, *, tz: tzinfo | None = None
) -> list[tuple[datetime, int]]:
    if tz is not None:
        start, end = start.astimezone(tz), end.astimezone(tz)
    span_days = (end.date() - start.date()).days
    return [
        c for c in _candidates(profile, end, sun, range(-span_days - 1, 1)) if start < c[0] <= end
    ]


def view(
    profile: Profile,
    now: datetime,
    sun: SunTimes,
    actual: CoverState,
    manual_move_at: datetime | None,
    satisfied_fire_at: datetime | None = None,
    *,
    tz: tzinfo | None = None,
) -> ScheduleView:
    """Spec §1.2 layer 5. `satisfied_fire_at` is the fire time of the open rule the engine
    has already seen satisfied (CoverRuntime.open_rule_satisfied_at)."""
    if tz is not None:
        now = now.astimezone(tz)
    quiet = quiet_active(profile.quiet_hours, now)
    last = last_fired(profile, now, sun)
    if last is None:
        return ScheduleView(quiet_active=quiet)
    fired_at, index = last
    rule = profile.rules[index]
    open_fired_at = fired_at if rule.action is RuleAction.OPEN else None
    released = manual_move_at is not None and manual_move_at > fired_at
    if released:
        return ScheduleView(quiet, Desired.LEAVE_ALONE, fired_at, index, True, open_fired_at)
    if rule.action is RuleAction.RELEASE:
        # The hold is over and nothing is forced: the layers below decide (decision 33).
        return ScheduleView(quiet, Desired.LEAVE_ALONE, fired_at, index, False, None)
    if rule.action is RuleAction.CLOSED:
        return ScheduleView(quiet, Desired.CLOSED, fired_at, index, False, None)
    # open rule: one-shot. Opinion only while unsatisfied, within the window, and cover not open.
    in_window = (now - fired_at).total_seconds() <= OPEN_RULE_WINDOW_S
    unsatisfied = satisfied_fire_at != fired_at and actual is not CoverState.OPEN
    desired = Desired.OPEN if (in_window and unsatisfied) else Desired.LEAVE_ALONE
    return ScheduleView(quiet, desired, fired_at, index, False, open_fired_at)


def _time_in_quiet(quiet: QuietHours | None, clock: time) -> bool:
    if quiet is None or quiet.start == quiet.end:
        return False
    if quiet.start < quiet.end:
        return quiet.start <= clock < quiet.end
    return clock >= quiet.start or clock < quiet.end


def validate(profile: Profile) -> list[str]:
    """Config-time validation messages (empty list = valid)."""
    problems: list[str] = []
    has_close = any(rule.action is RuleAction.CLOSED for rule in profile.rules)
    for n, rule in enumerate(profile.rules, start=1):
        if rule.action is RuleAction.RELEASE and not has_close:
            problems.append(f"rule {n} (release) has no earlier close rule to release")
        if rule.time_mode is TimeMode.FIXED:
            if rule.time is None:
                problems.append(f"rule {n} needs a time")
            elif _time_in_quiet(profile.quiet_hours, rule.time):
                problems.append(f"rule {n} fires inside quiet hours")
        elif rule.earliest is not None and rule.latest is not None and rule.earliest > rule.latest:
            problems.append(f"rule {n} earliest is after latest")
    return problems
