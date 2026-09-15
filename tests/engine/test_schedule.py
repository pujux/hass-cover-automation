from __future__ import annotations

from datetime import date, datetime

from custom_components.cover_automation.engine.model import CoverState, Desired, Target
from custom_components.cover_automation.engine.schedule import (
    Profile,
    QuietHours,
    Rule,
    TimeMode,
    fire_time,
    fired_between,
    last_fired,
    next_fire,
    quiet_active,
    validate,
    view,
)

from tests.engine.conftest import TZ, at, t


class FakeSun:
    def sunrise(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 5, 0, tzinfo=TZ)

    def sunset(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 21, 0, tzinfo=TZ)


SUN = FakeSun()
DAY = date(2026, 7, 1)


def close_at(clock: str) -> Rule:
    return Rule(Target.CLOSED, TimeMode.FIXED, time=t(clock))


def open_at(clock: str) -> Rule:
    return Rule(Target.OPEN, TimeMode.FIXED, time=t(clock))


def test_fixed_fire_time():
    assert fire_time(close_at("21:30"), DAY, SUN, None, TZ) == at("2026-07-01", "21:30")


def test_sun_relative_with_clamps():
    r = Rule(Target.OPEN, TimeMode.SUNRISE, offset_minutes=30, earliest=t("07:00"))
    assert fire_time(r, DAY, SUN, None, TZ) == at("2026-07-01", "07:00")  # 05:30 clamped up
    r2 = Rule(Target.CLOSED, TimeMode.SUNSET, offset_minutes=-30, latest=t("20:00"))
    assert fire_time(r2, DAY, SUN, None, TZ) == at("2026-07-01", "20:00")  # 20:30 clamped down
    r3 = Rule(Target.CLOSED, TimeMode.SUNSET, offset_minutes=15)
    assert fire_time(r3, DAY, SUN, None, TZ) == at("2026-07-01", "21:15")


def test_quiet_hours_spanning_midnight():
    q = QuietHours(t("22:00"), t("07:00"))
    assert quiet_active(q, at("2026-07-01", "23:00"))
    assert quiet_active(q, at("2026-07-02", "06:59"))
    assert not quiet_active(q, at("2026-07-02", "07:00"))
    assert not quiet_active(q, at("2026-07-01", "12:00"))


def test_sun_relative_rule_inside_quiet_hours_is_clamped():
    q = QuietHours(t("22:00"), t("07:00"))
    close_rule = Rule(Target.CLOSED, TimeMode.SUNSET, offset_minutes=90)  # 22:30
    assert fire_time(close_rule, DAY, SUN, q, TZ) == at("2026-07-01", "21:59")
    open_rule = Rule(Target.OPEN, TimeMode.SUNRISE, offset_minutes=30)  # 05:30
    assert fire_time(open_rule, DAY, SUN, q, TZ) == at("2026-07-01", "07:00")


def test_fixed_rule_inside_quiet_hours_is_invalid():
    q = QuietHours(t("22:00"), t("07:00"))
    assert fire_time(close_at("23:00"), DAY, SUN, q, TZ) is None
    p = Profile("p", "p", (close_at("23:00"),), q)
    assert validate(p) == ["rule 1 fires inside quiet hours"]


def test_last_and_next_fire_and_between():
    p = Profile("p", "p", (open_at("07:00"), close_at("21:30")), None)
    now = at("2026-07-01", "12:00")
    assert last_fired(p, now, SUN) == (at("2026-07-01", "07:00"), 0)
    assert next_fire(p, now, SUN) == (at("2026-07-01", "21:30"), 1)
    early = at("2026-07-01", "03:00")
    assert last_fired(p, early, SUN) == (at("2026-06-30", "21:30"), 1)
    assert fired_between(p, at("2026-07-01", "06:00"), at("2026-07-01", "22:00"), SUN) == [
        (at("2026-07-01", "07:00"), 0),
        (at("2026-07-01", "21:30"), 1),
    ]


def test_close_hold_until_next_rule_or_manual_release():
    p = Profile("p", "p", (close_at("21:30"),), None)
    fired = at("2026-07-01", "21:30")
    v = view(p, at("2026-07-02", "03:00"), SUN, CoverState.CLOSED, manual_move_at=None)
    assert v.desired is Desired.CLOSED and v.rule_fired_at == fired and not v.released
    v2 = view(
        p, at("2026-07-02", "08:00"), SUN, CoverState.OPEN, manual_move_at=at("2026-07-02", "07:30")
    )
    assert v2.desired is Desired.LEAVE_ALONE and v2.released
    # a manual move BEFORE the fire time does not release
    v3 = view(
        p, at("2026-07-01", "22:00"), SUN, CoverState.OPEN, manual_move_at=at("2026-07-01", "21:00")
    )
    assert v3.desired is Desired.CLOSED and not v3.released


def test_open_rule_is_one_shot():
    p = Profile("p", "p", (open_at("07:00"), close_at("21:30")), None)
    fired = at("2026-07-01", "07:00")
    assert view(p, fired, SUN, CoverState.CLOSED, None).desired is Desired.OPEN
    assert view(p, fired, SUN, CoverState.OPEN, None).desired is Desired.LEAVE_ALONE  # already open
    v = view(p, at("2026-07-01", "07:10"), SUN, CoverState.CLOSED, None)
    assert (
        v.desired is Desired.OPEN and v.open_rule_fired_at == fired
    )  # blocked so far: keep asking
    # satisfied earlier, then closed again by shading: no re-open
    assert (
        view(
            p, at("2026-07-01", "07:10"), SUN, CoverState.CLOSED, None, satisfied_fire_at=fired
        ).desired
        is Desired.LEAVE_ALONE
    )
    # window expired: no opinion even if never satisfied
    assert (
        view(p, at("2026-07-01", "07:16"), SUN, CoverState.CLOSED, None).desired
        is Desired.LEAVE_ALONE
    )
    assert view(p, at("2026-07-01", "23:00"), SUN, CoverState.OPEN, None).desired is Desired.CLOSED


def test_two_close_rules_reclose_after_release():
    p = Profile("p", "p", (close_at("20:00"), close_at("22:00")), None)
    v = view(
        p, at("2026-07-01", "21:00"), SUN, CoverState.OPEN, manual_move_at=at("2026-07-01", "20:30")
    )
    assert v.released and v.desired is Desired.LEAVE_ALONE
    v2 = view(
        p, at("2026-07-01", "22:00"), SUN, CoverState.OPEN, manual_move_at=at("2026-07-01", "20:30")
    )
    assert not v2.released and v2.desired is Desired.CLOSED and v2.rule_index == 1


def test_quiet_active_in_view_and_no_rules():
    p = Profile("p", "p", (), QuietHours(t("22:00"), t("07:00")))
    v = view(p, at("2026-07-01", "23:00"), SUN, CoverState.OPEN, None)
    assert v.quiet_active and v.desired is Desired.LEAVE_ALONE and v.rule_fired_at is None


def test_validate_rejects_bad_rules():
    bad = Profile(
        "p",
        "p",
        (
            Rule(Target.CLOSED, TimeMode.FIXED, time=None),
            Rule(
                Target.OPEN,
                TimeMode.SUNRISE,
                offset_minutes=0,
                earliest=t("09:00"),
                latest=t("08:00"),
            ),
        ),
        None,
    )
    assert validate(bad) == ["rule 1 needs a time", "rule 2 earliest is after latest"]
    assert validate(Profile("p", "p", (close_at("21:00"),), None)) == []
    assert next_fire(Profile("p", "p", (), None), at("2026-07-01", "12:00"), SUN) is None
