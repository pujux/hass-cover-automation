"""Actual-state classification (spec §1.0). Transition rules are added in Task 9."""

from __future__ import annotations

from .model import CoverState, Target

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
    return (
        (state is CoverState.OPEN
        and target is Target.CLOSED)
        or (state is CoverState.CLOSED and target is Target.OPEN)
    )
