"""Reconcile persisted state with the live cover after downtime (spec §5)."""

from __future__ import annotations

from datetime import datetime

from . import override
from .model import CoverPersisted, CoverState, Decision, Owner, Target


def reconcile(
    p: CoverPersisted, actual: CoverState, first_decision: Decision | None, now: datetime
) -> str:
    if p.owner is None:
        p.owner = Owner.ENGINE
        p.engine_target = Target.from_state(actual)
        override.clear(p)
        return "first_setup"
    if actual in (CoverState.MOVING, CoverState.UNAVAILABLE):
        return "unchanged"
    if p.engine_target is None or p.engine_target.matches(actual):
        return "unchanged"
    if p.owner is Owner.ENGINE:
        override.on_manual_move(p, first_decision, actual, now)
        return "manual_during_downtime"
    return "override_kept"
