from __future__ import annotations

from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverPersisted,
    CoverState,
    Layer,
    Owner,
    ReopeningMode,
    Target,
    WindAction,
)
from custom_components.cover_automation.engine.schedule import Profile, QuietHours, Rule, TimeMode

from tests.engine.conftest import at, t
from tests.engine.replay import Sim

ENGINE_CLOSED = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)

CFG = CoverConfig(
    "c", "Bedroom", 180.0, has_door_sensor=True, wind_enabled=True, wind_upper=60.0, wind_lower=50.0
)
CLOSE_2130 = Rule(Target.CLOSED, TimeMode.FIXED, time=t("21:30"))
OPEN_0700 = Rule(Target.OPEN, TimeMode.FIXED, time=t("07:00"))


def shaded_sim(reopening=ReopeningMode.PASSIVE, profile=None) -> Sim:
    """13:00: engine has closed the cover for shading."""
    sim = Sim(CFG, at("2026-07-01", "12:00"), profile=profile, reopening=reopening)
    sim.day(hits=True)
    sim.advance(60)
    assert sim.actual is CoverState.CLOSED and sim.engine.p.owner is Owner.ENGINE
    return sim


def test_a_open_against_shading_cloud_and_next_day():
    for mode in (ReopeningMode.PASSIVE, ReopeningMode.ACTIVE):
        sim = shaded_sim(mode)
        sim.until("2026-07-01", "14:00")
        sim.manual(CoverState.OPEN)
        sim.until("2026-07-01", "14:30")
        sim.sunny = False  # cloud (already debounced in the harness)
        sim.until("2026-07-01", "14:50")
        sim.sunny = True
        sim.until("2026-07-01", "16:59")
        assert sim.actual is CoverState.OPEN  # cloud never ends the override
        sim.day(hits=False)  # 17:00 sun leaves -> override ends (sun_hits false)
        sim.until("2026-07-01", "17:30")
        assert sim.engine.p.dam is None and sim.actual is CoverState.OPEN
        sim.night()
        sim.until("2026-07-02", "09:59")
        sim.day(hits=True)
        sim.until("2026-07-02", "10:30")
        assert sim.actual is CoverState.CLOSED, mode  # shaded again next day
        assert sim.engine.p.owner is Owner.ENGINE


def test_b_user_close_then_schedule_and_morning_open():
    sim = Sim(CFG, at("2026-07-01", "13:00"), profile=Profile("p", "p", (CLOSE_2130,)))
    sim.day(hits=False)
    sim.advance(30)
    sim.until("2026-07-01", "14:00")
    sim.manual(CoverState.CLOSED)  # engine wanted open
    sim.day(hits=True)
    sim.until("2026-07-01", "18:00")
    sim.day(hits=False)
    sim.until("2026-07-01", "20:00")
    assert sim.actual is CoverState.CLOSED  # passive: never reopens a user-closed cover
    sim.night()
    sim.until("2026-07-02", "07:00")
    sim.day(hits=False)  # July: sun is up, not yet on this window
    sim.until("2026-07-02", "07:30")
    assert sim.actual is CoverState.CLOSED
    sim.manual(CoverState.OPEN)  # releases the 21:30 hold for good; dam = closed (the hold)
    sim.until("2026-07-02", "10:00")  # desired open != dam for 30 min -> override ends by dwell
    sim.day(hits=True)
    sim.until("2026-07-02", "11:30")
    assert sim.actual is CoverState.CLOSED  # shading works again (N1 fixed)
    sim.day(hits=False)
    sim.until("2026-07-02", "18:30")
    assert sim.actual is CoverState.OPEN  # and it reopens: the hold did not re-arm


def test_c_wind_under_hold_with_quiet_hours_recloses_at_release():
    profile = Profile("p", "p", (CLOSE_2130,), QuietHours(t("22:00"), t("07:00")))
    sim = Sim(CFG, at("2026-07-01", "21:00"), profile=profile)
    sim.night()
    sim.until("2026-07-01", "21:31")
    assert sim.actual is CoverState.CLOSED
    sim.wind_active = True
    sim.until("2026-07-01", "22:05")
    assert sim.actual is CoverState.OPEN and sim.commands[-1][2] is Layer.WIND
    sim.wind_active = False
    sim.until("2026-07-01", "23:05")
    assert sim.actual is CoverState.CLOSED and sim.commands[-1][2] is Layer.SCHEDULE  # restoring


def test_d_door_under_hold_then_manual_close_then_reset_at_night():
    sim = Sim(CFG, at("2026-07-01", "21:00"), profile=Profile("p", "p", (CLOSE_2130,)))
    sim.night()
    sim.until("2026-07-01", "22:00")
    assert sim.actual is CoverState.CLOSED
    sim.open_door()
    sim.advance(1)
    assert sim.actual is CoverState.OPEN
    sim.until("2026-07-01", "22:05")
    sim.manual(CoverState.CLOSED)  # user closes while the door is open: respected
    sim.advance(5)
    assert sim.actual is CoverState.CLOSED
    sim.close_door()
    sim.until("2026-07-01", "23:00")
    sim.engine.reset(sim.actual, sim.now)
    sim.advance(10)
    assert sim.actual is CoverState.CLOSED  # no night-time opening after reset


def test_e_frost_then_wind_then_release():
    sim = Sim(
        CFG,
        at("2026-01-10", "08:00"),
        persisted=CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED),
    )
    sim.actual = CoverState.CLOSED
    sim.frost = True
    sim.wind_active = True
    sim.advance(30)
    assert sim.actual is CoverState.CLOSED and sim.notifications == 1
    sim.frost = False
    sim.advance(1)
    assert sim.actual is CoverState.OPEN and sim.commands[-1][2] is Layer.WIND
    sim.wind_active = False
    sim.day(hits=False)
    sim.advance(30)
    assert sim.actual is CoverState.OPEN


def test_f_restart_with_command_in_flight_keeps_engine_ownership():
    sim = Sim(CFG, at("2026-07-01", "12:00"), travel_s=60)
    sim.day(hits=True)
    sim.advance(1)  # close sent, cover PARTIAL
    assert sim.actual is CoverState.PARTIAL
    sim.actual = CoverState.CLOSED  # finishes during downtime
    sim.restart()
    assert sim.engine.p.owner is Owner.ENGINE and sim.engine.p.owns(CoverState.CLOSED)
    sim.day(hits=False)
    sim.advance(15)
    assert sim.actual is CoverState.OPEN  # passive reopening still works


def test_f2_restart_during_frost_keeps_live_override():
    sim = shaded_sim()
    sim.until("2026-07-01", "14:00")
    sim.manual(CoverState.OPEN)
    sim.frost = True
    sim.advance(5)
    sim.restart()
    assert sim.engine.p.dam is Target.CLOSED
    sim.frost = False
    sim.advance(30)
    assert sim.actual is CoverState.OPEN  # not re-closed


def test_g_single_open_rule_does_not_suppress_shading():
    profile = Profile("p", "p", (Rule(Target.OPEN, TimeMode.SUNRISE, offset_minutes=30),))
    sim = Sim(
        CFG,
        at("2026-07-01", "05:00"),
        profile=profile,
        persisted=CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED),
    )
    sim.actual = CoverState.CLOSED
    sim.day(hits=False)
    sim.until("2026-07-01", "05:31")
    assert sim.actual is CoverState.OPEN
    sim.day(hits=True)
    sim.until("2026-07-01", "10:00")
    assert sim.actual is CoverState.CLOSED  # shading runs after the one-shot open


def test_h_two_close_rules_reclose():
    profile = Profile(
        "p",
        "p",
        (
            Rule(Target.CLOSED, TimeMode.FIXED, time=t("20:00")),
            Rule(Target.CLOSED, TimeMode.FIXED, time=t("22:00")),
        ),
    )
    sim = Sim(CFG, at("2026-07-01", "19:00"), profile=profile)
    sim.night()
    sim.until("2026-07-01", "20:01")
    assert sim.actual is CoverState.CLOSED
    sim.until("2026-07-01", "20:30")
    sim.manual(CoverState.OPEN)
    sim.until("2026-07-01", "21:59")
    assert sim.actual is CoverState.OPEN
    sim.until("2026-07-01", "22:01")
    assert sim.actual is CoverState.CLOSED


def test_i_sun_relative_rule_clamped_before_quiet_hours():
    profile = Profile(
        "p",
        "p",
        (Rule(Target.CLOSED, TimeMode.SUNSET, offset_minutes=90),),
        QuietHours(t("22:00"), t("07:00")),
    )  # 22:30 -> clamped to 21:59
    sim = Sim(CFG, at("2026-07-01", "21:00"), profile=profile)
    sim.night()
    sim.until("2026-07-01", "21:58")
    assert sim.actual is CoverState.OPEN
    sim.until("2026-07-01", "22:00")
    assert sim.actual is CoverState.CLOSED


def test_k_user_stop_at_partial_is_respected_until_override_ends():
    sim = Sim(CFG, at("2026-07-01", "12:00"), travel_s=120)
    sim.day(hits=True)
    sim.advance(1)  # close sent, PARTIAL
    sim._arrival = None  # the user presses stop: the cover stays PARTIAL, never reaches CLOSED
    sim.advance(3)
    assert sim.engine.rt.unconfirmed
    # the cover is PARTIAL and the engine wants closed; after backoff it retries
    sim.advance(11)
    assert sim.commands[-1][1] is Target.CLOSED and len(sim.commands) == 2


def test_l_room_hot_flap_is_damped_by_min_interval():
    sim = Sim(CFG, at("2026-07-01", "12:00"))
    sim.day(hits=True)
    sim.hot_day = False
    for minute in range(60):
        sim.room_hot = (minute // 5) % 2 == 0  # flips every 5 minutes
        sim.advance(1)
    assert len(sim.commands) <= 7  # at most one move per min_move_interval (10 min)


def test_n1_close_rule_release_is_sticky():
    sim = Sim(CFG, at("2026-07-01", "21:00"), profile=Profile("p", "p", (CLOSE_2130,)))
    sim.night()
    sim.until("2026-07-02", "07:00")
    sim.day(hits=False)
    sim.until("2026-07-02", "07:30")
    sim.manual(CoverState.OPEN)
    sim.until("2026-07-02", "10:00")
    sim.day(hits=True)
    sim.until("2026-07-02", "11:30")
    assert sim.actual is CoverState.CLOSED and sim.engine.p.owner is Owner.ENGINE
    sim.day(hits=False)
    sim.until("2026-07-02", "18:30")
    assert sim.actual is CoverState.OPEN  # hold did not re-arm on the engine's close


def test_n4_shading_send_does_not_clear_unrelated_override():
    sim = Sim(CFG, at("2026-07-01", "22:00"))
    sim.night()
    sim.advance(1)
    sim.manual(CoverState.CLOSED)  # dam = open (inverse of new actual)
    assert sim.engine.p.dam is Target.OPEN
    sim.until("2026-07-02", "09:00")
    sim.day(hits=False)
    sim.advance(60)
    assert sim.actual is CoverState.CLOSED  # override toward open blocks reopening (passive too)


def test_n5_open_rule_then_shading_is_interval_damped():
    profile = Profile("p", "p", (OPEN_0700,))
    sim = Sim(
        CFG,
        at("2026-07-01", "06:50"),
        profile=profile,
        persisted=CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED),
    )
    sim.actual = CoverState.CLOSED
    sim.day(hits=True)
    sim.until("2026-07-01", "07:05")
    assert sim.actual is CoverState.OPEN
    sim.until("2026-07-01", "07:09")
    assert sim.actual is CoverState.OPEN  # no immediate re-close
    sim.until("2026-07-01", "07:12")
    assert sim.actual is CoverState.CLOSED  # after min interval


def test_wind_hold_action_holds_position():
    cfg = CoverConfig(
        "c",
        "c",
        180.0,
        wind_enabled=True,
        wind_upper=60,
        wind_lower=50,
        wind_action=WindAction.HOLD,
    )
    sim = Sim(cfg, at("2026-07-01", "12:00"))
    sim.day(hits=True)
    sim.advance(1)
    assert sim.actual is CoverState.CLOSED
    sim.wind_active = True
    sim.day(hits=False)
    sim.advance(30)
    assert sim.actual is CoverState.CLOSED  # hold: no reopening during wind
