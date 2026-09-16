"""Actual-state classification and transition rules (spec §1.0, §1.4)."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import commands, override
from .const import CONTRARY_PERSIST_S
from .model import (
    CoverConfig,
    CoverPersisted,
    CoverRuntime,
    CoverState,
    DamLayer,
    Owner,
    Target,
    TransitionKind,
    TransitionResult,
)

_UNAVAILABLE = {None, "unavailable", "unknown"}
_MOVING = {"opening", "closing"}


def classify(ha_state: str | None, position: float | None, tolerance: float) -> CoverState:
    if ha_state in _UNAVAILABLE:
        return CoverState.UNAVAILABLE
    if ha_state in _MOVING:
        return CoverState.MOVING
    if ha_state == "closed" or (position is not None and position <= tolerance):
        return CoverState.CLOSED
    if ha_state == "open":
        if position is None or position >= 100.0 - tolerance:
            return CoverState.OPEN
        return CoverState.PARTIAL
    return CoverState.UNAVAILABLE


def is_settled(state: CoverState) -> bool:
    return state in (CoverState.OPEN, CoverState.CLOSED, CoverState.PARTIAL)


def is_contrary(state: CoverState, target: Target) -> bool:
    """Only the opposite end state is contrary (spec §1.0)."""
    return (state is CoverState.OPEN and target is Target.CLOSED) or (
        state is CoverState.CLOSED and target is Target.OPEN
    )


def _kind(
    p: CoverPersisted, rt: CoverRuntime, new_state: CoverState, now: datetime
) -> TransitionKind:
    pending = rt.pending
    if pending is not None:
        if pending.target.matches(new_state):
            commands.confirm(rt)
            return TransitionKind.MATCH
        if is_contrary(new_state, pending.target):
            if rt.contrary_since is None:
                rt.contrary_since = now
            return TransitionKind.PENDING_PROGRESS
        pending.last_progress_at = now
        rt.contrary_since = None
        return TransitionKind.PENDING_PROGRESS
    if new_state in (CoverState.MOVING, CoverState.UNAVAILABLE):
        return TransitionKind.IGNORED
    if new_state is rt.last_settled:
        # C2: an unavailable/moving round trip back to the same settled state is not a move.
        return TransitionKind.IGNORED
    if (
        p.owner is Owner.ENGINE
        and p.engine_target is not None
        and p.engine_target.matches(new_state)
    ):
        commands.confirm(rt)
        return TransitionKind.LATE_MATCH
    override.on_manual_move(p, rt.last_evaluation, new_state, now)
    return TransitionKind.MANUAL


def on_transition(
    p: CoverPersisted, rt: CoverRuntime, cfg: CoverConfig, new_state: CoverState, now: datetime
) -> TransitionResult:
    del cfg  # reserved for per-cover rules; the confirm window is checked in check_pending
    kind = _kind(p, rt, new_state, now)
    if is_settled(new_state):
        rt.last_settled = new_state
    return TransitionResult(kind, new_state)


def pending_deadline(rt: CoverRuntime, cfg: CoverConfig) -> datetime | None:
    if rt.pending is None:
        return None
    deadline = rt.pending.last_progress_at + timedelta(seconds=cfg.confirm_window_s)
    if rt.contrary_since is not None:
        deadline = min(deadline, rt.contrary_since + timedelta(seconds=CONTRARY_PERSIST_S))
    return deadline


def check_pending(
    p: CoverPersisted, rt: CoverRuntime, cfg: CoverConfig, current_state: CoverState, now: datetime
) -> str | None:
    if rt.pending is None:
        return None
    if (
        rt.contrary_since is not None
        and is_contrary(current_state, rt.pending.target)
        and (now - rt.contrary_since).total_seconds() >= CONTRARY_PERSIST_S
    ):
        rt.pending = None
        rt.contrary_since = None
        override.on_manual_move(p, rt.last_evaluation, current_state, now)
        return "manual"
    if (now - rt.pending.last_progress_at).total_seconds() >= cfg.confirm_window_s:
        if current_state is CoverState.PARTIAL:
            # Decision 27: the cover settled part-way through an engine move and stayed
            # there for the whole confirm window -- the user pressed stop. `partial` is
            # never contrary (§1.0), so this is the only place that can see it. The override
            # dams the target the engine was moving toward; backoff and `unconfirmed` stay
            # untouched because nothing failed.
            p.owner = Owner.USER
            p.manual_move_at = now
            p.dam = rt.pending.target
            p.dam_layer = DamLayer.OTHER
            rt.pending = None
            rt.contrary_since = None
            return "manual"
        commands.on_unconfirmed(p, rt, cfg, now)
        return "unconfirmed"
    return None
