from __future__ import annotations

from datetime import timedelta

from custom_components.cover_automation.engine.cover import CoverEngine
from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverState,
    DamLayer,
    Defer,
    Desired,
    DoorState,
    HubSignals,
    Layer,
    Owner,
    ScheduleView,
    Send,
    Status,
    Suppress,
    Target,
    TransitionKind,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "13:00")
CFG = CoverConfig(
    "c", "c", 180.0, wind_enabled=True, wind_upper=60, wind_lower=50, has_door_sensor=True
)


def sig(now=T0, **kw) -> HubSignals:
    base = {
        "now": now,
        "sun_elevation": 30.0,
        "frost": False,
        "frost_near_freezing": False,
        "sunny": True,
        "hot_day": True,
    }
    base.update(kw)
    return HubSignals(**base)


def inp(**kw) -> CoverInputs:
    base = {"actual": CoverState.OPEN, "sun_hits": True}
    base.update(kw)
    return CoverInputs(**base)


def test_shade_close_round_trip_gives_engine_ownership():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.OPEN))
    r = e.evaluate(inp(), sig())
    assert r.action == Send(Target.CLOSED, Layer.SHADING) and r.status is Status.CLOSED_SHADING
    e.on_command_sent(r.action, T0)
    assert r.next_check_at is None  # nothing pending before the send was registered
    r2 = e.evaluate(inp(), sig())
    assert r2.next_check_at == T0 + timedelta(seconds=120)  # confirm window
    assert (
        e.on_transition(CoverState.CLOSED, T0 + timedelta(seconds=30)).kind is TransitionKind.MATCH
    )
    assert e.p.owns(CoverState.CLOSED)
    # sun leaves -> reopen (passive: engine owns)
    r3 = e.evaluate(inp(actual=CoverState.CLOSED, sun_hits=False), sig(T0 + timedelta(hours=4)))
    assert r3.action == Send(Target.OPEN, Layer.SHADING) and r3.status is Status.OPEN_NO_SHADE


def test_frost_conflict_notifies_once_per_episode():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    r = e.evaluate(inp(actual=CoverState.CLOSED, wind_active=True), sig(frost=True))
    assert r.notify_frost_conflict and r.status is Status.HELD_FROST and r.action is None
    r2 = e.evaluate(inp(actual=CoverState.CLOSED, wind_active=True), sig(frost=True))
    assert not r2.notify_frost_conflict
    e.evaluate(inp(actual=CoverState.CLOSED, wind_active=False), sig(frost=False))
    r3 = e.evaluate(inp(actual=CoverState.CLOSED, wind_active=True), sig(frost=True))
    assert r3.notify_frost_conflict


def test_wind_release_restoring_move_bypasses_quiet_hours_once():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    quiet_hold = ScheduleView(
        quiet_active=True, desired=Desired.CLOSED, rule_fired_at=T0, rule_index=0
    )
    r = e.evaluate(inp(actual=CoverState.CLOSED, wind_active=True, schedule=quiet_hold), sig())
    assert r.action == Send(Target.OPEN, Layer.WIND)
    e.on_command_sent(r.action, T0)
    e.on_transition(CoverState.OPEN, T0 + timedelta(seconds=20))
    r2 = e.evaluate(
        inp(actual=CoverState.OPEN, wind_active=False, schedule=quiet_hold),
        sig(T0 + timedelta(minutes=30)),
    )
    assert r2.action == Send(Target.CLOSED, Layer.SCHEDULE)  # restoring move despite quiet hours
    e.on_command_sent(r2.action, T0 + timedelta(minutes=30))
    e.on_transition(CoverState.CLOSED, T0 + timedelta(minutes=31))
    r3 = e.evaluate(
        inp(actual=CoverState.CLOSED, wind_active=False, schedule=quiet_hold),
        sig(T0 + timedelta(minutes=40)),
    )
    assert r3.action is None and e.rt.restoring_until is None


def test_restoring_exemption_survives_a_frost_hold():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    quiet_hold = ScheduleView(
        quiet_active=True, desired=Desired.CLOSED, rule_fired_at=T0, rule_index=0
    )
    r = e.evaluate(inp(actual=CoverState.CLOSED, wind_active=True, schedule=quiet_hold), sig())
    e.on_command_sent(r.action, T0)
    e.on_transition(CoverState.OPEN, T0 + timedelta(seconds=20))
    # wind releases while frost is active: the frost layer wins, but the exemption must survive
    r2 = e.evaluate(
        inp(actual=CoverState.OPEN, wind_active=False, schedule=quiet_hold),
        sig(T0 + timedelta(minutes=1), frost=True),
    )
    assert r2.action is None and e.rt.restoring_until is not None
    # frost releases two minutes later, still inside quiet hours: the restoring move goes through
    r3 = e.evaluate(
        inp(actual=CoverState.OPEN, wind_active=False, schedule=quiet_hold),
        sig(T0 + timedelta(minutes=3)),
    )
    assert r3.action == Send(Target.CLOSED, Layer.SCHEDULE)


def test_status_precedence_partial_over_override_and_disabled_first():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.USER, dam=Target.CLOSED, enabled=False))
    r = e.evaluate(inp(actual=CoverState.PARTIAL), sig())
    assert r.status is Status.DISABLED
    e.p.enabled = True
    r2 = e.evaluate(inp(actual=CoverState.PARTIAL), sig())
    assert r2.status is Status.PARTIAL
    r3 = e.evaluate(inp(actual=CoverState.OPEN), sig())
    assert r3.status is Status.MANUAL_OVERRIDE and r3.action == Suppress("manual_override")


def test_defer_is_reported_as_next_check():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.OPEN))
    e.rt.last_send_at = T0 - timedelta(seconds=60)
    r = e.evaluate(inp(), sig())
    assert isinstance(r.action, Defer) and r.next_check_at == r.action.until


def test_reset_and_repair_needed():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.USER, dam=Target.OPEN))
    e.reset(CoverState.CLOSED, T0)
    assert e.p.owner is Owner.ENGINE and e.p.dam is None and e.p.engine_target is Target.CLOSED
    assert not e.repair_needed
    e.rt.consecutive_failures = 3
    assert e.repair_needed


def test_door_layer_status_and_command():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    r = e.evaluate(inp(actual=CoverState.CLOSED, door=DoorState.OPEN, door_last_changed=T0), sig())
    assert r.status is Status.DOOR_OPEN and r.action == Send(Target.OPEN, Layer.DOOR)


def test_new_override_does_not_inherit_dwell_from_previous_episode():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    # episode 1: user opens against shading; desired stays CLOSED != dam? no: dam = CLOSED, so
    # feed evaluations where desired is OPEN (sun not hitting) to accumulate 20 min of dwell
    e.on_transition(
        CoverState.OPEN, T0
    )  # manual open; last_evaluation is None -> dam = inverse(OPEN) = CLOSED
    assert e.p.dam is Target.CLOSED
    for minutes in (1, 10, 20):
        e.evaluate(
            inp(actual=CoverState.OPEN, sun_hits=False), sig(T0 + timedelta(minutes=minutes))
        )
    assert e.p.dam is Target.CLOSED  # 20 min < 30 min dwell
    # a schedule rule fires: override ends via rule; dwell must be reset by the same evaluate
    e.evaluate(
        inp(actual=CoverState.OPEN, sun_hits=False),
        sig(T0 + timedelta(minutes=21)),
        rule_fired=True,
    )
    assert e.p.dam is None
    # episode 2: a schedule hold (layer SCHEDULE) is the last evaluation; the user opens against it
    hold = ScheduleView(desired=Desired.CLOSED, rule_fired_at=T0, rule_index=0)
    e.on_command_sent(Send(Target.CLOSED, Layer.SCHEDULE), T0 + timedelta(minutes=40))
    e.on_transition(CoverState.CLOSED, T0 + timedelta(minutes=41))
    e.evaluate(
        inp(actual=CoverState.CLOSED, sun_hits=False, schedule=hold),
        sig(T0 + timedelta(minutes=42)),
    )
    e.on_transition(CoverState.OPEN, T0 + timedelta(minutes=43))  # manual open against the hold
    assert e.p.dam is Target.CLOSED and e.p.dam_layer is DamLayer.OTHER
    # desired now OPEN (released hold -> shading, no sun): 20 minutes must NOT end the override
    released = ScheduleView(
        desired=Desired.LEAVE_ALONE, rule_fired_at=T0, rule_index=0, released=True
    )
    e.evaluate(
        inp(actual=CoverState.OPEN, sun_hits=False, schedule=released),
        sig(T0 + timedelta(minutes=44)),
    )
    e.evaluate(
        inp(actual=CoverState.OPEN, sun_hits=False, schedule=released),
        sig(T0 + timedelta(minutes=64)),
    )
    assert e.p.dam is Target.CLOSED  # only 20 of 30 minutes; nothing inherited from episode 1
    e.evaluate(
        inp(actual=CoverState.OPEN, sun_hits=False, schedule=released),
        sig(T0 + timedelta(minutes=75)),
    )
    assert e.p.dam is None  # 31 minutes: ends on its own schedule


def test_frost_conflict_not_notified_when_unknown_and_not_near_freezing():
    e = CoverEngine(CFG, CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED))
    door = inp(actual=CoverState.CLOSED, door=DoorState.OPEN, door_last_changed=T0)
    r = e.evaluate(door, sig(frost=None, frost_near_freezing=False))
    assert r.status is Status.HELD_FROST and r.action is None and not r.notify_frost_conflict
    r2 = e.evaluate(door, sig(frost=None, frost_near_freezing=True))
    assert r2.notify_frost_conflict
