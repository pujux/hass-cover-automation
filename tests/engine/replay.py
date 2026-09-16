"""Day-replay harness: drives one CoverEngine through synthetic time (spec §6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from custom_components.cover_automation.engine import schedule
from custom_components.cover_automation.engine.cover import CoverEngine
from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverState,
    DoorState,
    HubSignals,
    Layer,
    ReopeningMode,
    ScheduleView,
    Send,
    ShadingMode,
    Target,
)

from tests.engine.conftest import TZ


class FakeSun:
    def sunrise(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 5, 0, tzinfo=TZ)

    def sunset(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 21, 0, tzinfo=TZ)


@dataclass
class Sim:
    cfg: CoverConfig
    start: datetime
    profile: schedule.Profile | None = None
    persisted: CoverPersisted = field(default_factory=CoverPersisted)
    reopening: ReopeningMode = ReopeningMode.PASSIVE
    shading_mode: ShadingMode = ShadingMode.AUTO
    travel_s: int = 0  # 0 = instant transitions; >0 = PARTIAL while travelling
    engine: CoverEngine = field(init=False)
    now: datetime = field(init=False)
    actual: CoverState = CoverState.OPEN
    sun_hits: bool = False
    sun_elevation: float = 30.0
    sunny: bool | None = True
    hot_day: bool | None = True
    room_cold: bool = False
    room_hot: bool = False
    wind_active: bool = False
    frost: bool | None = False
    door: DoorState = DoorState.NONE
    door_changed: datetime | None = None
    simulation: bool = False
    commands: list[tuple[datetime, Target, Layer]] = field(default_factory=list)
    notifications: int = 0
    _arrival: tuple[datetime, CoverState] | None = None
    _last_rule_check: datetime = field(init=False)
    sun: FakeSun = field(default_factory=FakeSun)

    def __post_init__(self) -> None:
        self.engine = CoverEngine(self.cfg, self.persisted)
        self.now = self.start
        self._last_rule_check = self.start
        # Spec §5 setup order: reconcile runs once before the first evaluation.
        self.engine.reconcile(self.actual, None, self.now)

    # -- environment -------------------------------------------------------------

    def set_actual(self, state: CoverState) -> None:
        if state is not self.actual:
            self.actual = state
            self.engine.on_transition(state, self.now)

    def blip(self) -> None:
        """A connectivity dropout: the cover goes unavailable and returns in the same state.

        Instantaneous on purpose -- a controller skips an unavailable cover (spec §5), so no
        evaluation happens in between.
        """
        state = self.actual
        self.set_actual(CoverState.UNAVAILABLE)
        self.set_actual(state)

    def manual(self, state: CoverState) -> None:
        """The user moves the cover by hand (instantly)."""
        self._arrival = None
        self.set_actual(state)

    def open_door(self) -> None:
        self.door = DoorState.OPEN
        self.door_changed = self.now

    def close_door(self) -> None:
        self.door = DoorState.CLOSED
        self.door_changed = self.now

    def night(self) -> None:
        self.sun_elevation = -5.0
        self.sun_hits = False

    def day(self, hits: bool) -> None:
        self.sun_elevation = 30.0
        self.sun_hits = hits

    # -- time --------------------------------------------------------------------

    def tick(self) -> None:
        if self._arrival is not None and self.now >= self._arrival[0]:
            state = self._arrival[1]
            self._arrival = None
            self.set_actual(state)
        rule_fired = False
        view = ScheduleView()
        if self.profile is not None:
            fired = schedule.fired_between(self.profile, self._last_rule_check, self.now, self.sun)
            rule_fired = bool(fired)
            view = schedule.view(
                self.profile,
                self.now,
                self.sun,
                self.actual,
                self.engine.p.manual_move_at,
                satisfied_fire_at=self.engine.rt.open_rule_satisfied_at,
            )
        self._last_rule_check = self.now
        self.engine.check_pending(self.actual, self.now)
        inputs = CoverInputs(
            actual=self.actual,
            door=self.door,
            door_last_changed=self.door_changed,
            sun_hits=self.sun_hits,
            room_cold=self.room_cold,
            room_hot=self.room_hot,
            wind_active=self.wind_active,
            schedule=view,
        )
        signals = HubSignals(
            now=self.now,
            sun_elevation=self.sun_elevation,
            frost=self.frost,
            frost_near_freezing=False,
            sunny=self.sunny,
            hot_day=self.hot_day,
            shading_mode=self.shading_mode,
            reopening_mode=self.reopening,
            simulation=self.simulation,
        )
        result = self.engine.evaluate(inputs, signals, rule_fired=rule_fired)
        if result.notify_frost_conflict:
            self.notifications += 1
        if isinstance(result.action, Send):
            self.engine.on_command_sent(result.action, self.now)
            self.commands.append((self.now, result.action.target, result.action.layer))
            if not result.action.simulated:
                self._move(result.action.target)

    def _move(self, target: Target) -> None:
        end_state = CoverState.OPEN if target is Target.OPEN else CoverState.CLOSED
        if self.travel_s == 0:
            self.set_actual(end_state)
        else:
            self.set_actual(CoverState.PARTIAL)
            self._arrival = (self.now + timedelta(seconds=self.travel_s), end_state)

    def advance(self, minutes: int, step_s: int = 60) -> None:
        end = self.now + timedelta(minutes=minutes)
        while self.now < end:
            self.now = min(self.now + timedelta(seconds=step_s), end)
            self.tick()

    def until(self, day: str, clock: str) -> None:
        y, m, d = (int(x) for x in day.split("-"))
        hh, mm = (int(x) for x in clock.split(":"))
        target = datetime(y, m, d, hh, mm, tzinfo=TZ)
        minutes = int((target - self.now).total_seconds() // 60)
        assert minutes >= 0, "cannot go back in time"
        self.advance(minutes)

    def restart(self) -> None:
        """Simulate an HA restart: runtime is lost, persisted state kept, reconcile runs."""
        persisted = CoverPersisted.from_dict(self.engine.p.to_dict())
        self.engine = CoverEngine(self.cfg, persisted)
        view = ScheduleView()
        if self.profile is not None:
            view = schedule.view(
                self.profile, self.now, self.sun, self.actual, persisted.manual_move_at
            )
            # runtime is lost on restart: open_rule_satisfied_at starts as None on purpose
        first = self.engine.evaluate(
            CoverInputs(
                actual=self.actual,
                door=self.door,
                door_last_changed=self.door_changed,
                sun_hits=self.sun_hits,
                room_cold=self.room_cold,
                room_hot=self.room_hot,
                wind_active=self.wind_active,
                schedule=view,
            ),
            HubSignals(
                self.now,
                self.sun_elevation,
                self.frost,
                False,
                self.sunny,
                self.hot_day,
                self.shading_mode,
                self.reopening,
                self.simulation,
            ),
        )
        self.engine.reconcile(self.actual, first.decision, self.now)
