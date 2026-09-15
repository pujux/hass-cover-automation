"""The layer stack (spec §1.2): first layer with an opinion sets the desired state."""

from __future__ import annotations

from .model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    Decision,
    Desired,
    DoorState,
    HubSignals,
    Layer,
    Mode,
    ShadingMode,
    ShadingRule,
    WindAction,
)


def wind_opinion(cfg: CoverConfig, inputs: CoverInputs) -> Desired:
    if cfg.wind_enabled and inputs.wind_active and cfg.wind_action is WindAction.OPEN:
        return Desired.OPEN
    return Desired.LEAVE_ALONE


def want_shade(cfg: CoverConfig, inputs: CoverInputs, s: HubSignals) -> bool | None:
    if s.shading_mode is ShadingMode.FORCED_SUNLIT:
        return inputs.sun_hits
    if s.shading_mode is ShadingMode.FORCED_ALL:
        return cfg.elevation_min <= s.sun_elevation <= cfg.elevation_max
    if not inputs.sun_hits:
        return False
    if s.sunny is None:
        return None
    if not s.sunny:
        return False
    if cfg.shading_rule is ShadingRule.ROOM_ONLY:
        return inputs.room_hot
    if cfg.shading_rule is ShadingRule.FORECAST_WITH_ROOM and inputs.room_cold:
        return False
    if inputs.room_hot:
        return True
    return s.hot_day


def evaluate(
    cfg: CoverConfig,
    p: CoverPersisted,
    inputs: CoverInputs,
    s: HubSignals,
    *,
    restoring: bool = False,
) -> Decision:
    wind_op = wind_opinion(cfg, inputs)
    door_op = Desired.OPEN if inputs.door is DoorState.OPEN else Desired.LEAVE_ALONE
    wind_wants = cfg.wind_enabled and inputs.wind_active

    def dec(desired: Desired, layer: Layer, reason: str, want: bool | None = None) -> Decision:
        return Decision(desired, layer, reason, wind_op, door_op, want)

    # 1. Frost
    if s.frost is True:
        return dec(Desired.LEAVE_ALONE, Layer.FROST, "frost_active")
    if s.frost is None and not (wind_wants and not s.frost_near_freezing):
        return dec(Desired.LEAVE_ALONE, Layer.FROST, "frost_unknown")
    # 2. Wind
    if wind_wants:
        reason = "wind_open" if wind_op is Desired.OPEN else "wind_hold"
        return dec(wind_op, Layer.WIND, reason)
    # 3. Door
    if inputs.door is DoorState.OPEN:
        return dec(Desired.OPEN, Layer.DOOR, "door_open")
    if inputs.door is DoorState.UNAVAILABLE:
        return dec(Desired.LEAVE_ALONE, Layer.DOOR, "door_unavailable")
    if p.mode is Mode.PROTECTION_ONLY:
        return dec(Desired.LEAVE_ALONE, Layer.NONE, "protection_only")
    # 4. Quiet hours
    if inputs.schedule.quiet_active and not restoring:
        return dec(Desired.LEAVE_ALONE, Layer.QUIET_HOURS, "quiet_hours")
    # 5. Schedule
    sched = inputs.schedule.desired
    if sched is not Desired.LEAVE_ALONE:
        reason = "schedule_close" if sched is Desired.CLOSED else "schedule_open"
        return dec(sched, Layer.SCHEDULE, reason)
    # 6. Shading
    if s.sun_elevation <= 0.0:
        return dec(Desired.LEAVE_ALONE, Layer.NONE, "night")
    if s.shading_mode is ShadingMode.OFF:
        return dec(Desired.LEAVE_ALONE, Layer.NONE, "shading_off")
    want = want_shade(cfg, inputs, s)
    if want is None:
        return dec(Desired.LEAVE_ALONE, Layer.SHADING, "shading_unknown", None)
    if want:
        return dec(Desired.CLOSED, Layer.SHADING, "shade", True)
    if p.mode is Mode.DARK_ONLY:
        return dec(Desired.LEAVE_ALONE, Layer.SHADING, "dark_only_no_shade", False)
    # 7. Default for auto: open when no shade is needed
    return dec(Desired.OPEN, Layer.SHADING, "no_shade", False)
