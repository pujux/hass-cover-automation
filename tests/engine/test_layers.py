from __future__ import annotations

from custom_components.cover_automation.engine.layers import evaluate, want_shade
from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverState,
    Desired,
    DoorState,
    HubSignals,
    Layer,
    Mode,
    ScheduleView,
    ShadingMode,
    ShadingRule,
    WindAction,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "12:00")


def cfg(**kw) -> CoverConfig:
    base = {
        "cover_id": "c",
        "name": "c",
        "azimuth": 180.0,
        "wind_enabled": True,
        "wind_upper": 60.0,
        "wind_lower": 50.0,
    }
    base.update(kw)
    return CoverConfig(**base)


def sig(**kw) -> HubSignals:
    base = {
        "now": T0,
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


def sched(desired=Desired.LEAVE_ALONE, quiet=False) -> ScheduleView:
    return ScheduleView(quiet_active=quiet, desired=desired, rule_fired_at=T0, rule_index=0)


def test_frost_active_wins_over_wind_and_reports_wind_opinion():
    d = evaluate(cfg(), CoverPersisted(), inp(wind_active=True), sig(frost=True))
    assert d.desired is Desired.LEAVE_ALONE and d.layer is Layer.FROST
    assert d.wind_opinion is Desired.OPEN


def test_frost_unknown_lets_wind_through_unless_near_freezing():
    d = evaluate(cfg(), CoverPersisted(), inp(wind_active=True), sig(frost=None))
    assert d.layer is Layer.WIND and d.desired is Desired.OPEN
    d2 = evaluate(
        cfg(), CoverPersisted(), inp(wind_active=True), sig(frost=None, frost_near_freezing=True)
    )
    assert d2.layer is Layer.FROST and d2.desired is Desired.LEAVE_ALONE
    d3 = evaluate(cfg(), CoverPersisted(), inp(door=DoorState.OPEN), sig(frost=None))
    assert d3.layer is Layer.FROST and d3.door_opinion is Desired.OPEN


def test_wind_hold_action_and_wind_over_everything():
    d = evaluate(cfg(wind_action=WindAction.HOLD), CoverPersisted(), inp(wind_active=True), sig())
    assert (
        d.layer is Layer.WIND
        and d.desired is Desired.LEAVE_ALONE
        and d.wind_opinion is Desired.LEAVE_ALONE
    )
    d2 = evaluate(
        cfg(),
        CoverPersisted(),
        inp(wind_active=True, door=DoorState.OPEN, schedule=sched(Desired.CLOSED, quiet=True)),
        sig(),
    )
    assert d2.layer is Layer.WIND and d2.desired is Desired.OPEN


def test_wind_disabled_cover_ignores_wind():
    d = evaluate(
        cfg(wind_enabled=False),
        CoverPersisted(),
        inp(wind_active=True, actual=CoverState.CLOSED),
        sig(),
    )
    assert d.layer is not Layer.WIND


def test_door_open_beats_schedule_and_unavailable_blocks():
    d = evaluate(
        cfg(), CoverPersisted(), inp(door=DoorState.OPEN, schedule=sched(Desired.CLOSED)), sig()
    )
    assert d.layer is Layer.DOOR and d.desired is Desired.OPEN
    d2 = evaluate(
        cfg(),
        CoverPersisted(),
        inp(door=DoorState.UNAVAILABLE, schedule=sched(Desired.CLOSED)),
        sig(),
    )
    assert d2.layer is Layer.DOOR and d2.desired is Desired.LEAVE_ALONE


def test_protection_only_skips_quiet_schedule_shading_but_keeps_door():
    p = CoverPersisted(mode=Mode.PROTECTION_ONLY)
    d = evaluate(cfg(), p, inp(schedule=sched(Desired.CLOSED, quiet=True)), sig())
    assert d.layer is Layer.NONE and d.desired is Desired.LEAVE_ALONE
    d2 = evaluate(cfg(), p, inp(door=DoorState.OPEN), sig())
    assert d2.layer is Layer.DOOR


def test_quiet_hours_block_unless_restoring():
    d = evaluate(cfg(), CoverPersisted(), inp(schedule=sched(Desired.CLOSED, quiet=True)), sig())
    assert d.layer is Layer.QUIET_HOURS
    d2 = evaluate(
        cfg(),
        CoverPersisted(),
        inp(schedule=sched(Desired.CLOSED, quiet=True)),
        sig(),
        restoring=True,
    )
    assert d2.layer is Layer.SCHEDULE and d2.desired is Desired.CLOSED


def test_schedule_layer_and_reasons():
    d = evaluate(cfg(), CoverPersisted(), inp(schedule=sched(Desired.OPEN)), sig())
    assert d.layer is Layer.SCHEDULE and d.reason == "schedule_open"


def test_shading_only_in_daylight_and_when_enabled():
    d = evaluate(cfg(), CoverPersisted(), inp(), sig(sun_elevation=0.0))
    assert d.layer is Layer.NONE and d.reason == "night"
    d2 = evaluate(cfg(), CoverPersisted(), inp(), sig(shading_mode=ShadingMode.OFF))
    assert d2.layer is Layer.NONE and d2.reason == "shading_off"


def test_shading_close_open_dark_only_and_unknown():
    d = evaluate(cfg(), CoverPersisted(), inp(), sig())
    assert d.desired is Desired.CLOSED and d.layer is Layer.SHADING and d.want_shade is True
    d2 = evaluate(cfg(), CoverPersisted(), inp(sun_hits=False), sig())
    assert d2.desired is Desired.OPEN and d2.want_shade is False
    d3 = evaluate(cfg(), CoverPersisted(mode=Mode.DARK_ONLY), inp(sun_hits=False), sig())
    assert d3.desired is Desired.LEAVE_ALONE and d3.layer is Layer.SHADING
    d4 = evaluate(cfg(), CoverPersisted(), inp(), sig(sunny=None))
    assert d4.desired is Desired.LEAVE_ALONE and d4.reason == "shading_unknown"


def test_want_shade_rules():
    c = cfg()
    assert (
        want_shade(c, inp(sun_hits=False), sig(sunny=None)) is False
    )  # no sun: no unknowns needed
    assert want_shade(c, inp(), sig(sunny=False)) is False
    assert want_shade(c, inp(room_cold=True), sig()) is False  # comfort floor
    assert (
        want_shade(c, inp(room_hot=True), sig(hot_day=False)) is True
    )  # ceiling overrides forecast
    assert want_shade(c, inp(), sig(hot_day=None)) is None
    assert want_shade(c, inp(room_hot=True), sig(hot_day=None)) is True
    room_only = cfg(shading_rule=ShadingRule.ROOM_ONLY, has_room_sensor=True)
    assert want_shade(room_only, inp(), sig(hot_day=True)) is False
    assert want_shade(room_only, inp(room_hot=True), sig(hot_day=None)) is True
    either = cfg(shading_rule=ShadingRule.EITHER)
    assert want_shade(either, inp(room_cold=True), sig(hot_day=True)) is True  # no comfort floor


def test_room_only_with_a_degraded_sensor_is_unknown():
    """I3: `room_only` has no second input; a dead room sensor must not read as 'not hot'."""
    room_only = cfg(shading_rule=ShadingRule.ROOM_ONLY, has_room_sensor=True)
    assert want_shade(room_only, inp(room_degraded=True), sig(hot_day=True)) is None
    d = evaluate(room_only, CoverPersisted(), inp(room_degraded=True), sig())
    assert d.desired is Desired.LEAVE_ALONE
    assert d.layer is Layer.SHADING and d.reason == "shading_unknown"
    # the same cover with a healthy sensor still opens when the room is not hot
    d2 = evaluate(room_only, CoverPersisted(), inp(), sig())
    assert d2.desired is Desired.OPEN and d2.reason == "no_shade"


def test_forced_modes_bypass_weather_and_room():
    c = cfg(elevation_min=10.0, elevation_max=40.0)
    assert (
        want_shade(
            c,
            inp(room_cold=True),
            sig(sunny=False, hot_day=False, shading_mode=ShadingMode.FORCED_SUNLIT),
        )
        is True
    )
    assert want_shade(c, inp(sun_hits=False), sig(shading_mode=ShadingMode.FORCED_SUNLIT)) is False
    assert (
        want_shade(
            c, inp(sun_hits=False), sig(sun_elevation=20.0, shading_mode=ShadingMode.FORCED_ALL)
        )
        is True
    )
    assert (
        want_shade(
            c, inp(sun_hits=True), sig(sun_elevation=45.0, shading_mode=ShadingMode.FORCED_ALL)
        )
        is False
    )
