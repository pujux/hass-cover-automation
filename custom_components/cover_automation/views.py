"""Read-only snapshots of hub/cover state, dispatched to entities via a per-entry signal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import const
from .engine.model import Mode, ReopeningMode, ShadingMode, Status


def signal_update(entry_id: str) -> str:
    return f"{const.DOMAIN}_update_{entry_id}"


@dataclass(frozen=True, slots=True)
class HubView:
    sunny: bool | None = None
    hot_day: bool | None = None
    frost: bool | None = None
    any_wind_active: bool = False
    forecast_max_c: float | None = None
    forecast_min_c: float | None = None
    next_event_at: datetime | None = None
    next_event_profile: str | None = None
    next_event_action: str | None = None
    next_event_covers: tuple[str, ...] = ()
    shading_mode: ShadingMode = ShadingMode.AUTO
    reopening_mode: ReopeningMode = ReopeningMode.PASSIVE
    simulation: bool = False
    verbose: bool = False
    problem: bool = False


@dataclass(frozen=True, slots=True)
class CoverView:
    name: str
    cover_entity: str
    status: Status = Status.IDLE
    desired_state: str = "leave_alone"
    actual_state: str = "cover_unavailable"
    winning_layer: str = "none"
    reason: str = ""
    sun_hits: bool = False
    sunny: bool | None = None
    hot_day: bool | None = None
    room_state: str = "none"  # none | cold | comfortable | hot | degraded
    wind_state: str = "disabled"  # disabled | inactive | active | unavailable
    active_rule: str | None = None
    next_planned_action: str | None = None
    next_planned_at: datetime | None = None
    last_engine_move: datetime | None = None
    owner: str | None = None
    degraded: bool = False
    enabled: bool = True
    mode: Mode = Mode.AUTO
    override_active: bool = False
    override_since: datetime | None = None
    overridden_desired: str | None = None
    wind_active: bool = False


EMPTY_HUB_VIEW = HubView()
