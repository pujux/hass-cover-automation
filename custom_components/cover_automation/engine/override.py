"""Manual override lifecycle (spec §1.4 manual move, §1.5)."""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import (
    CoverPersisted,
    CoverRuntime,
    CoverState,
    DamLayer,
    Decision,
    Desired,
    Layer,
    Owner,
    Target,
)


def clear(p: CoverPersisted) -> None:
    p.dam = None
    p.dam_layer = None


def on_manual_move(
    p: CoverPersisted, last_eval: Decision | None, new_actual: CoverState, now: datetime
) -> None:
    p.owner = Owner.USER
    p.manual_move_at = now
    if not p.enabled:
        clear(p)
        return
    if last_eval is not None and last_eval.desired is not Desired.LEAVE_ALONE:
        p.dam = last_eval.desired.as_target()
        p.dam_layer = DamLayer.SHADING if last_eval.layer is Layer.SHADING else DamLayer.OTHER
        return
    inverse = Target.from_state(new_actual)
    if inverse is not None:
        p.dam = inverse.inverse
        p.dam_layer = DamLayer.OTHER
        return
    clear(p)


def on_rule_fired(p: CoverPersisted) -> None:
    clear(p)


def reset(p: CoverPersisted, actual: CoverState, now: datetime) -> None:
    del now  # reset never touches manual_move_at (spec §1.5 b)
    p.owner = Owner.ENGINE
    p.engine_target = Target.from_state(actual)
    clear(p)


def update_dwell(
    p: CoverPersisted,
    rt: CoverRuntime,
    decision: Decision,
    sun_hits: bool,
    now: datetime,
) -> datetime | None:
    """Apply §1.5 (d) and (e). Returns when the running dwell would complete."""
    if p.dam is None:
        rt.override_dwell.reset()
        return None
    if p.dam_layer is DamLayer.SHADING and not sun_hits:
        clear(p)
        rt.override_dwell.reset()
        return None
    if decision.desired is Desired.LEAVE_ALONE:
        cond: bool | None = None
    else:
        cond = decision.desired.as_target() is not p.dam
    if rt.override_dwell.update(cond, now):
        clear(p)
        rt.override_dwell.reset()
        return None
    remaining = rt.override_dwell.remaining_s()
    return now + timedelta(seconds=remaining) if remaining is not None else None
