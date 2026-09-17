"""Layered schedule evaluation: several profiles per cover in priority order (spec §1.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from custom_components.cover_automation.engine.model import CoverState, Desired
from custom_components.cover_automation.engine.schedule import (
    Profile,
    QuietHours,
    Rule,
    RuleAction,
    TimeMode,
    view_layered,
)

from tests.engine.conftest import TZ, at, t


class FakeSun:
    def sunrise(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 5, 0, tzinfo=TZ)

    def sunset(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 21, 0, tzinfo=TZ)


SUN = FakeSun()


def close_at(clock: str) -> Rule:
    return Rule(RuleAction.CLOSED, TimeMode.FIXED, time=t(clock))


def open_at(clock: str) -> Rule:
    return Rule(RuleAction.OPEN, TimeMode.FIXED, time=t(clock))


def release_at(clock: str) -> Rule:
    return Rule(RuleAction.RELEASE, TimeMode.FIXED, time=t(clock))


def test_empty_list_is_a_bare_view():
    v = view_layered((), at("2026-07-01", "12:00"), SUN, CoverState.OPEN, None, {})
    assert v.desired is Desired.LEAVE_ALONE and not v.quiet_active
    assert v.profile_id is None and v.rule_fired_at is None


def test_highest_priority_wins():
    a = Profile("a", "A", (close_at("20:00"),))
    b = Profile("b", "B", (open_at("20:30"),))
    now = at("2026-07-01", "20:35")
    v = view_layered((a, b), now, SUN, CoverState.CLOSED, None, {})
    assert v.desired is Desired.CLOSED and v.profile_id == "a" and v.rule_index == 0
    # swap the order and the same two profiles produce the other opinion
    v2 = view_layered((b, a), now, SUN, CoverState.CLOSED, None, {})
    assert v2.desired is Desired.OPEN and v2.profile_id == "b"


def test_a_released_high_priority_profile_hands_down():
    a = Profile("a", "A", (close_at("20:00"),))
    b = Profile("b", "B", (close_at("20:30"),))
    now = at("2026-07-01", "21:00")
    manual = at("2026-07-01", "20:45")  # after both fire times: both released
    assert (
        view_layered((a, b), now, SUN, CoverState.OPEN, manual, {}).desired is Desired.LEAVE_ALONE
    )
    # a manual move that only releases A hands the decision to B
    manual_a_only = at("2026-07-01", "20:15")
    v = view_layered((a, b), now, SUN, CoverState.OPEN, manual_a_only, {})
    assert v.desired is Desired.CLOSED and v.profile_id == "b"


def test_a_silent_high_priority_profile_hands_down():
    """A release rule ends A's hold; B's own hold still stands and decides."""
    a = Profile("a", "A", (close_at("20:00"), release_at("22:00")))
    b = Profile("b", "B", (close_at("21:00"),))
    v = view_layered((a, b), at("2026-07-01", "22:30"), SUN, CoverState.CLOSED, None, {})
    assert v.desired is Desired.CLOSED and v.profile_id == "b" and v.rule_index == 0


def test_overlapping_holds_keep_the_cover_closed_until_the_later_release():
    """A holds 20:00→06:00, B holds 22:00→09:00: B keeps it closed past A's release."""
    a = Profile("a", "A", (close_at("20:00"), release_at("06:00")))
    b = Profile("b", "B", (close_at("22:00"), release_at("09:00")))
    profiles = (a, b)
    closed = view_layered(profiles, at("2026-07-01", "23:00"), SUN, CoverState.CLOSED, None, {})
    assert closed.desired is Desired.CLOSED and closed.profile_id == "a"
    # 07:00: A has released, B still holds
    still = view_layered(profiles, at("2026-07-02", "07:00"), SUN, CoverState.CLOSED, None, {})
    assert still.desired is Desired.CLOSED and still.profile_id == "b"
    # 09:30: both released, the layers below decide again; the display falls back to A
    free = view_layered(profiles, at("2026-07-02", "09:30"), SUN, CoverState.CLOSED, None, {})
    assert free.desired is Desired.LEAVE_ALONE and free.profile_id == "a"
    assert free.rule_fired_at == at("2026-07-02", "06:00") and free.rule_index == 1


def test_quiet_hours_are_the_union_regardless_of_priority():
    loud = Profile("a", "A", (close_at("20:00"),))
    quiet = Profile("b", "B", (), QuietHours(t("22:00"), t("07:00")))
    now = at("2026-07-01", "23:00")
    v = view_layered((loud, quiet), now, SUN, CoverState.CLOSED, None, {})
    assert v.quiet_active and v.desired is Desired.CLOSED and v.profile_id == "a"
    # and the other way round: the low-priority winner still carries the union
    v2 = view_layered((quiet, loud), now, SUN, CoverState.CLOSED, None, {})
    assert v2.quiet_active and v2.desired is Desired.CLOSED and v2.profile_id == "a"
    # no rule has fired at all: a bare view that still reports quiet
    v3 = view_layered((quiet,), now, SUN, CoverState.OPEN, None, {})
    assert v3.quiet_active and v3.rule_fired_at is None and v3.profile_id is None


def test_per_profile_satisfied_markers_keep_two_open_rules_independent():
    a = Profile("a", "A", (open_at("07:00"),))
    b = Profile("b", "B", (open_at("07:05"),))
    now = at("2026-07-01", "07:10")
    fresh = view_layered((a, b), now, SUN, CoverState.CLOSED, None, {})
    assert fresh.desired is Desired.OPEN and fresh.profile_id == "a"
    assert fresh.open_rule_fired_at == at("2026-07-01", "07:00")
    # A's open rule already satisfied: B's is untouched and asks on its own account
    satisfied = {"a": at("2026-07-01", "07:00")}
    v = view_layered((a, b), now, SUN, CoverState.CLOSED, None, satisfied)
    assert v.desired is Desired.OPEN and v.profile_id == "b"
    assert v.open_rule_fired_at == at("2026-07-01", "07:05")
    # both satisfied: no opinion, and the display falls back to the highest priority
    both = {"a": at("2026-07-01", "07:00"), "b": at("2026-07-01", "07:05")}
    done = view_layered((a, b), now, SUN, CoverState.CLOSED, None, both)
    assert done.desired is Desired.LEAVE_ALONE and done.profile_id == "a"


def test_open_rule_fired_at_comes_from_the_winning_profile_only():
    """A's open rule is spent; B closes. The marker must not be re-armed from A."""
    a = Profile("a", "A", (open_at("07:00"),))
    b = Profile("b", "B", (close_at("07:02"),))
    satisfied = {"a": at("2026-07-01", "07:00")}
    v = view_layered((a, b), at("2026-07-01", "07:05"), SUN, CoverState.CLOSED, None, satisfied)
    assert v.desired is Desired.CLOSED and v.profile_id == "b" and v.open_rule_fired_at is None


def test_layered_view_honours_an_explicit_timezone():
    """I2: like `view`, the local day and clock come from `tz` when the caller passes one."""
    p = Profile("p", "P", (close_at("21:00"),), QuietHours(t("22:00"), t("07:00")))
    utc = at("2026-07-01", "23:00").astimezone(UTC)
    v = view_layered((p,), utc, SUN, CoverState.OPEN, None, {}, tz=TZ)
    assert v.quiet_active and v.rule_fired_at == at("2026-07-01", "21:00")
    assert v.desired is Desired.CLOSED and v.profile_id == "p"
