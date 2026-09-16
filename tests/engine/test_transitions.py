from __future__ import annotations

from datetime import timedelta

from custom_components.cover_automation.engine import commands
from custom_components.cover_automation.engine.classify import (
    check_pending,
    on_transition,
    pending_deadline,
)
from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverPersisted,
    CoverRuntime,
    CoverState,
    Decision,
    Desired,
    Layer,
    Owner,
    Target,
    TransitionKind,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "13:00")
CFG = CoverConfig("c", "c", 180.0, min_move_interval_s=600, confirm_window_s=120)
SHADE = Decision(Desired.CLOSED, Layer.SHADING, "shade")


def s(seconds: int):
    return T0 + timedelta(seconds=seconds)


def test_send_claims_ownership_and_clears_dam_only_for_schedule():
    p = CoverPersisted(owner=Owner.USER, dam=Target.OPEN)
    rt = CoverRuntime()
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    assert p.owner is Owner.ENGINE and p.engine_target is Target.CLOSED
    assert p.dam is Target.OPEN  # shading send keeps the override
    assert rt.pending is not None and rt.pending.target is Target.CLOSED and rt.last_send_at == T0
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SCHEDULE, s(1))
    assert p.dam is None


def test_match_confirms_and_clears_failures():
    p, rt = CoverPersisted(), CoverRuntime(consecutive_failures=2, backoff_s=1200, unconfirmed=True)
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    assert (
        on_transition(p, rt, CFG, CoverState.MOVING, s(1)).kind is TransitionKind.PENDING_PROGRESS
    )
    assert on_transition(p, rt, CFG, CoverState.CLOSED, s(20)).kind is TransitionKind.MATCH
    assert (
        rt.pending is None
        and rt.consecutive_failures == 0
        and rt.backoff_s == 0
        and not rt.unconfirmed
    )
    assert p.owner is Owner.ENGINE


def test_position_only_cover_partial_for_45s_is_not_manual():
    p, rt = CoverPersisted(), CoverRuntime()
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    assert (
        on_transition(p, rt, CFG, CoverState.PARTIAL, s(3)).kind is TransitionKind.PENDING_PROGRESS
    )
    assert check_pending(p, rt, CFG, CoverState.PARTIAL, s(45)) is None
    assert pending_deadline(rt, CFG) == s(3 + 120)  # measured from last progress
    assert on_transition(p, rt, CFG, CoverState.CLOSED, s(46)).kind is TransitionKind.MATCH
    assert p.owner is Owner.ENGINE and p.dam is None


def test_contrary_state_for_10s_is_a_manual_move():
    p, rt = CoverPersisted(), CoverRuntime(last_evaluation=SHADE)
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    on_transition(p, rt, CFG, CoverState.MOVING, s(1))
    assert (
        on_transition(p, rt, CFG, CoverState.OPEN, s(5)).kind is TransitionKind.PENDING_PROGRESS
    )  # user reversed
    assert pending_deadline(rt, CFG) == s(15)
    assert check_pending(p, rt, CFG, CoverState.OPEN, s(14)) is None
    assert check_pending(p, rt, CFG, CoverState.OPEN, s(15)) == "manual"
    assert rt.pending is None and p.owner is Owner.USER and p.dam is Target.CLOSED


def test_contrary_then_target_is_still_a_match():
    p, rt = CoverPersisted(), CoverRuntime()
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    on_transition(p, rt, CFG, CoverState.OPEN, s(5))
    assert on_transition(p, rt, CFG, CoverState.CLOSED, s(9)).kind is TransitionKind.MATCH


def test_confirm_window_expiry_marks_unconfirmed_with_backoff():
    p, rt = CoverPersisted(), CoverRuntime()
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    assert check_pending(p, rt, CFG, CoverState.OPEN, s(119)) is None
    assert check_pending(p, rt, CFG, CoverState.OPEN, s(120)) == "unconfirmed"
    assert rt.unconfirmed and rt.pending is None and rt.consecutive_failures == 1
    assert rt.backoff_until == s(120 + 600)
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, s(800))
    check_pending(p, rt, CFG, CoverState.OPEN, s(920))
    assert rt.backoff_until == s(920 + 1200)  # doubled


def test_late_match_without_pending_is_not_manual():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    rt = CoverRuntime(unconfirmed=True)
    assert on_transition(p, rt, CFG, CoverState.CLOSED, T0).kind is TransitionKind.LATE_MATCH
    assert not rt.unconfirmed and p.owner is Owner.ENGINE


def test_manual_move_without_pending_uses_last_evaluation():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    rt = CoverRuntime(last_evaluation=SHADE)
    assert on_transition(p, rt, CFG, CoverState.MOVING, T0).kind is TransitionKind.IGNORED
    assert on_transition(p, rt, CFG, CoverState.OPEN, s(5)).kind is TransitionKind.MANUAL
    assert p.owner is Owner.USER and p.dam is Target.CLOSED and p.manual_move_at == s(5)
    assert p.engine_target is Target.CLOSED


def test_unavailable_round_trip_to_the_same_state_is_not_a_manual_move():
    """C2: a connectivity blip must not release schedule holds or create an override."""
    p = CoverPersisted(owner=Owner.USER, engine_target=Target.OPEN, manual_move_at=s(-3600))
    rt = CoverRuntime(last_evaluation=SHADE, last_settled=CoverState.CLOSED)
    assert on_transition(p, rt, CFG, CoverState.UNAVAILABLE, s(1)).kind is TransitionKind.IGNORED
    assert on_transition(p, rt, CFG, CoverState.CLOSED, s(60)).kind is TransitionKind.IGNORED
    assert p.manual_move_at == s(-3600) and p.dam is None and p.owner is Owner.USER


def test_moving_round_trip_to_the_same_state_is_not_a_manual_move():
    p = CoverPersisted(owner=Owner.USER, engine_target=Target.OPEN)
    rt = CoverRuntime(last_evaluation=SHADE, last_settled=CoverState.CLOSED)
    assert on_transition(p, rt, CFG, CoverState.MOVING, s(1)).kind is TransitionKind.IGNORED
    assert on_transition(p, rt, CFG, CoverState.CLOSED, s(60)).kind is TransitionKind.IGNORED
    assert p.manual_move_at is None and p.dam is None


def test_unavailable_round_trip_to_a_different_state_is_a_manual_move():
    p = CoverPersisted(owner=Owner.USER, engine_target=Target.OPEN)
    rt = CoverRuntime(last_evaluation=SHADE, last_settled=CoverState.CLOSED)
    assert on_transition(p, rt, CFG, CoverState.UNAVAILABLE, s(1)).kind is TransitionKind.IGNORED
    assert on_transition(p, rt, CFG, CoverState.OPEN, s(60)).kind is TransitionKind.MANUAL
    assert p.manual_move_at == s(60) and p.dam is Target.CLOSED
    assert rt.last_settled is CoverState.OPEN


def test_command_failed_retries_after_30s_then_backs_off():
    p, rt = CoverPersisted(), CoverRuntime()
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, T0)
    assert commands.on_command_failed(p, rt, CFG, s(1)) == s(31)
    assert rt.command_failed and rt.pending is None and rt.consecutive_failures == 1
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, s(31))
    assert commands.on_command_failed(p, rt, CFG, s(32)) == s(32 + 600)
    commands.on_command_sent(p, rt, Target.CLOSED, Layer.SHADING, s(700))
    commands.on_command_failed(p, rt, CFG, s(701))
    assert rt.consecutive_failures == 3 and rt.backoff_until == s(701 + 1200)


def test_simulated_send_touches_only_last_simulated():
    p, rt = CoverPersisted(), CoverRuntime()
    commands.on_simulated(rt, Target.CLOSED, Layer.WIND)
    assert rt.last_simulated == (Layer.WIND, Target.CLOSED)
    assert p.owner is None and rt.pending is None and rt.last_send_at is None
