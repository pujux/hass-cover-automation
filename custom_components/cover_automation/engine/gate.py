"""The act gate (spec §1.3): may the engine make actual match desired right now?"""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import (
    Action,
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverRuntime,
    CoverState,
    Decision,
    Defer,
    HubSignals,
    Layer,
    ReopeningMode,
    Send,
    Suppress,
    Target,
)


def _send(target: Target, layer: Layer, s: HubSignals, rt: CoverRuntime) -> Action:
    if s.simulation:
        if rt.last_simulated == (layer, target):
            return Suppress("simulated_duplicate")
        return Send(target, layer, simulated=True)
    return Send(target, layer)


def _backoff(rt: CoverRuntime, now: datetime) -> Defer | None:
    if rt.backoff_until is not None and now < rt.backoff_until:
        return Defer(rt.backoff_until, "backoff")
    return None


def decide(
    decision: Decision,
    cfg: CoverConfig,
    p: CoverPersisted,
    inputs: CoverInputs,
    s: HubSignals,
    rt: CoverRuntime,
    now: datetime,
) -> Action:
    target = decision.desired.as_target()
    if target is None or target.matches(inputs.actual):
        return None
    # 1. disabled
    if not p.enabled:
        return Suppress("disabled")
    # 2. frost is handled by the layer stack (frost yields leave_alone -> target None)
    # 3. wind
    if decision.layer is Layer.WIND:
        return _send(target, Layer.WIND, s, rt)
    # 4. door
    if decision.layer is Layer.DOOR:
        if (
            p.dam is not None
            and p.manual_move_at is not None
            and inputs.door_last_changed is not None
            and p.manual_move_at > inputs.door_last_changed
        ):
            return Suppress("override_after_door_opened")
        if (deferred := _backoff(rt, now)) is not None:
            return deferred
        return _send(target, Layer.DOOR, s, rt)
    # 5. override
    if p.dam is not None and target is p.dam:
        return Suppress("manual_override")
    # 6. reopening mode for shading opens
    if decision.layer is Layer.SHADING and target is Target.OPEN:
        if s.reopening_mode is ReopeningMode.OFF:
            return Suppress("reopening_off")
        if s.reopening_mode is ReopeningMode.PASSIVE and not p.owns(inputs.actual):
            return Suppress("reopening_passive")
    # 7. moving: §1.3 calls this "defer until settled". There is no time to defer to, so the
    # engine suppresses and the controller surfaces `Suppress("moving")` as the cover's
    # pending move (§4 `next_planned_action`); the settle transition re-evaluates.
    if inputs.actual is CoverState.MOVING:
        return Suppress("moving")
    # 8. minimum interval (shading only; clock = any send)
    if decision.layer is Layer.SHADING and rt.last_send_at is not None:
        earliest = rt.last_send_at + timedelta(seconds=cfg.min_move_interval_s)
        if now < earliest:
            return Defer(earliest, "min_interval")
    # 9. backoff
    if (deferred := _backoff(rt, now)) is not None:
        return deferred
    # 10./11. simulation or send
    return _send(target, decision.layer, s, rt)
