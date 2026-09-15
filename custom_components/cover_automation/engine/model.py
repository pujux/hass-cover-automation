"""Enums and dataclasses shared by the engine (spec §1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .const import (
    COMFORT_CEILING_C,
    COMFORT_FLOOR_C,
    CONFIRM_WINDOW_S,
    MIN_MOVE_INTERVAL_S,
    WIND_HOLD_S,
)
from .signals import ContinuousCondition


class CoverState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"
    MOVING = "moving"
    UNAVAILABLE = "cover_unavailable"


class Target(StrEnum):
    OPEN = "open"
    CLOSED = "closed"

    @property
    def inverse(self) -> Target:
        return Target.CLOSED if self is Target.OPEN else Target.OPEN

    def matches(self, state: CoverState) -> bool:
        return state.value == self.value

    @staticmethod
    def from_state(state: CoverState) -> Target | None:
        if state is CoverState.OPEN:
            return Target.OPEN
        if state is CoverState.CLOSED:
            return Target.CLOSED
        return None


class Desired(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    LEAVE_ALONE = "leave_alone"

    def as_target(self) -> Target | None:
        if self is Desired.LEAVE_ALONE:
            return None
        return Target(self.value)


class Layer(StrEnum):
    FROST = "frost"
    WIND = "wind"
    DOOR = "door"
    QUIET_HOURS = "quiet_hours"
    SCHEDULE = "schedule"
    SHADING = "shading"
    NONE = "none"


class Mode(StrEnum):
    AUTO = "auto"
    DARK_ONLY = "dark_only"
    PROTECTION_ONLY = "protection_only"


class ShadingMode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    FORCED_SUNLIT = "forced_sunlit"
    FORCED_ALL = "forced_all"


class ReopeningMode(StrEnum):
    ACTIVE = "active"
    PASSIVE = "passive"
    OFF = "off"


class ShadingRule(StrEnum):
    FORECAST_WITH_ROOM = "forecast_with_room"
    ROOM_ONLY = "room_only"
    EITHER = "either"


class WindAction(StrEnum):
    OPEN = "open"
    HOLD = "hold"


class Owner(StrEnum):
    ENGINE = "engine"
    USER = "user"


class DamLayer(StrEnum):
    SHADING = "shading"
    OTHER = "other"


class DoorState(StrEnum):
    NONE = "none"
    OPEN = "open"
    CLOSED = "closed"
    UNAVAILABLE = "unavailable"


class Status(StrEnum):
    """Per-cover status values in precedence order (spec §4)."""

    COVER_UNAVAILABLE = "cover_unavailable"
    DISABLED = "disabled"
    COMMAND_FAILED = "command_failed"
    UNCONFIRMED = "unconfirmed"
    HELD_FROST = "held_frost"
    PROTECTED_WIND = "protected_wind"
    DOOR_OPEN = "door_open"
    QUIET_HOURS = "quiet_hours"
    PARTIAL = "partial"
    MANUAL_OVERRIDE = "manual_override"
    SCHEDULE_HOLD = "schedule_hold"
    CLOSED_SHADING = "closed_shading"
    OPEN_NO_SHADE = "open_no_shade"
    IDLE = "idle"


class TransitionKind(StrEnum):
    MATCH = "match"
    LATE_MATCH = "late_match"
    MANUAL = "manual"
    PENDING_PROGRESS = "pending_progress"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class CoverConfig:
    cover_id: str
    name: str
    azimuth: float
    tolerance_left: float = 60.0
    tolerance_right: float = 60.0
    elevation_min: float = 0.0
    elevation_max: float = 90.0
    shading_rule: ShadingRule = ShadingRule.FORECAST_WITH_ROOM
    has_room_sensor: bool = False
    comfort_floor: float = COMFORT_FLOOR_C
    comfort_ceiling: float = COMFORT_CEILING_C
    has_door_sensor: bool = False
    wind_enabled: bool = False
    wind_upper: float = 0.0
    wind_lower: float = 0.0
    wind_hold_s: int = WIND_HOLD_S
    wind_action: WindAction = WindAction.OPEN
    profile_id: str | None = None
    min_move_interval_s: int = MIN_MOVE_INTERVAL_S
    confirm_window_s: int = CONFIRM_WINDOW_S


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_to_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        msg = f"naive datetime not allowed: {value!r}"
        raise ValueError(msg)
    return parsed


@dataclass(slots=True)
class CoverPersisted:
    """The eight per-cover scalars kept in the Store (spec §5)."""

    owner: Owner | None = None
    engine_target: Target | None = None
    manual_move_at: datetime | None = None
    dam: Target | None = None
    dam_layer: DamLayer | None = None
    wind_active: bool = False
    enabled: bool = True
    mode: Mode = Mode.AUTO

    @property
    def override_active(self) -> bool:
        return self.dam is not None

    def owns(self, actual: CoverState) -> bool:
        """Spec §1.0: engine owns the current state."""
        return (
            self.owner is Owner.ENGINE
            and self.engine_target is not None
            and self.engine_target.matches(actual)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner.value if self.owner else None,
            "engine_target": self.engine_target.value if self.engine_target else None,
            "manual_move_at": _dt_to_str(self.manual_move_at),
            "dam": self.dam.value if self.dam else None,
            "dam_layer": self.dam_layer.value if self.dam_layer else None,
            "wind_active": self.wind_active,
            "enabled": self.enabled,
            "mode": self.mode.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverPersisted:
        def enum_or_none[E: StrEnum](enum: type[E], raw: Any) -> E | None:
            return enum(raw) if raw not in (None, "") else None

        return cls(
            owner=enum_or_none(Owner, data.get("owner")),
            engine_target=enum_or_none(Target, data.get("engine_target")),
            manual_move_at=_str_to_dt(data.get("manual_move_at")),
            dam=enum_or_none(Target, data.get("dam")),
            dam_layer=enum_or_none(DamLayer, data.get("dam_layer")),
            wind_active=bool(data.get("wind_active", False)),
            enabled=bool(data.get("enabled", True)),
            mode=Mode(data.get("mode", Mode.AUTO.value)),
        )


@dataclass(frozen=True, slots=True)
class HubSignals:
    """House-wide inputs for one evaluation."""

    now: datetime
    sun_elevation: float
    frost: bool | None  # None = unknown
    frost_near_freezing: bool  # last known outdoor temp <= threshold + release
    sunny: bool | None
    hot_day: bool | None
    shading_mode: ShadingMode = ShadingMode.AUTO
    reopening_mode: ReopeningMode = ReopeningMode.PASSIVE
    simulation: bool = False


@dataclass(frozen=True, slots=True)
class ScheduleView:
    """What the cover's profile says right now (schedule.view)."""

    quiet_active: bool = False
    desired: Desired = Desired.LEAVE_ALONE
    rule_fired_at: datetime | None = None
    rule_index: int | None = None
    released: bool = False
    open_rule_fired_at: datetime | None = None  # set while the last fired rule is an open rule


@dataclass(frozen=True, slots=True)
class CoverInputs:
    """Per-cover inputs for one evaluation."""

    actual: CoverState
    door: DoorState = DoorState.NONE
    door_last_changed: datetime | None = None
    sun_hits: bool = False
    room_cold: bool = False
    room_hot: bool = False
    room_degraded: bool = False
    wind_active: bool = False
    schedule: ScheduleView = field(default_factory=ScheduleView)


@dataclass(frozen=True, slots=True)
class Decision:
    desired: Desired
    layer: Layer
    reason: str
    wind_opinion: Desired = Desired.LEAVE_ALONE
    door_opinion: Desired = Desired.LEAVE_ALONE
    want_shade: bool | None = None


@dataclass(slots=True)
class Pending:
    target: Target
    layer: Layer
    sent_at: datetime
    last_progress_at: datetime


@dataclass(slots=True)
class CoverRuntime:
    """Per-cover state that is never persisted (spec §5)."""

    pending: Pending | None = None
    last_send_at: datetime | None = None
    backoff_until: datetime | None = None
    backoff_s: int = 0
    consecutive_failures: int = 0
    command_failed: bool = False
    unconfirmed: bool = False
    contrary_since: datetime | None = None
    override_dwell: ContinuousCondition = field(default_factory=lambda: ContinuousCondition(1800))
    last_simulated: tuple[Layer, Target] | None = None
    frost_conflict_notified: bool = False
    prev_wind_active: bool = False
    restoring_until: datetime | None = None
    open_rule_satisfied_at: datetime | None = None  # fire time of the open rule already satisfied
    last_evaluation: Decision | None = None


@dataclass(frozen=True, slots=True)
class Send:
    target: Target
    layer: Layer
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class Defer:
    until: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class Suppress:
    reason: str


type Action = Send | Defer | Suppress | None


@dataclass(frozen=True, slots=True)
class StepResult:
    decision: Decision
    action: Action
    status: Status
    notify_frost_conflict: bool
    next_check_at: datetime | None


@dataclass(frozen=True, slots=True)
class TransitionResult:
    kind: TransitionKind
    new_state: CoverState
