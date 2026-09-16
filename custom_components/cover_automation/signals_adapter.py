"""Home Assistant states → engine signals (spec §2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.cover import ATTR_CURRENT_POSITION, CoverEntityFeature
from homeassistant.components.sun.const import STATE_ATTR_AZIMUTH, STATE_ATTR_ELEVATION
from homeassistant.components.weather.const import (
    ATTR_WEATHER_TEMPERATURE,
    ATTR_WEATHER_TEMPERATURE_UNIT,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .config_map import CoverBindings, HubConfig
from .engine import classify
from .engine.model import (
    CoverConfig,
    CoverInputs,
    CoverState,
    DoorState,
    HubSignals,
    ScheduleView,
    ShadingRule,
)
from .engine.signals import (
    DailyLatch,
    Debounce,
    FrostSignal,
    Graceful,
    RoomTemperature,
    WindProtection,
)
from .engine.sun import SunHits
from .forecast import TodayForecast
from .store import StoreData
from .units import read_float, speed_convert, temperature_unit_of, to_celsius, unit_of

SUN_ENTITY = "sun.sun"
_UNUSABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def classify_state(state: State | None, tolerance: float) -> CoverState:
    """Map a `cover.*` HA state onto the engine's `CoverState` (spec §1.0)."""
    if state is None:
        return CoverState.UNAVAILABLE
    raw = state.attributes.get(ATTR_CURRENT_POSITION)
    position = float(raw) if isinstance(raw, (int, float)) else None
    return classify.classify(state.state, position, tolerance)


def sun_position(hass: HomeAssistant) -> tuple[float, float] | None:
    """Current `(azimuth, elevation)` from `sun.sun`, or None if unavailable."""
    state = hass.states.get(SUN_ENTITY)
    if state is None:
        return None
    az = state.attributes.get(STATE_ATTR_AZIMUTH)
    el = state.attributes.get(STATE_ATTR_ELEVATION)
    if not isinstance(az, (int, float)) or not isinstance(el, (int, float)):
        return None
    return float(az), float(el)


def cover_supports(state: State | None) -> tuple[bool, bool]:
    """`(open_and_close, set_position)` from a cover state's `supported_features`."""
    features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) if state else 0
    open_and_close = bool(features & CoverEntityFeature.OPEN) and bool(
        features & CoverEntityFeature.CLOSE
    )
    return open_and_close, bool(features & CoverEntityFeature.SET_POSITION)


def _binary(state: State | None) -> bool | None:
    """`on`/`off` → True/False; missing or unavailable/unknown → None."""
    if state is None or state.state in _UNUSABLE:
        return None
    return state.state == STATE_ON


class HubSignalSource:
    """Sunny, hot day and frost for the whole house (spec §2)."""

    def __init__(self, hass: HomeAssistant, hub: HubConfig, latch: DailyLatch) -> None:
        self.hass = hass
        self.hub = hub
        self.latch = latch
        self._condition: Graceful[str] = Graceful(hub.weather_grace_s)
        self._sunny = Debounce(hub.sunny_on_delay_s, hub.sunny_off_delay_s)
        self.frost = FrostSignal(
            threshold=to_celsius(hub.frost_threshold, hub.temperature_unit),
            grace_s=hub.weather_grace_s,
        )
        self._hot_high_c = to_celsius(hub.hot_high, hub.temperature_unit)
        self._hot_low_c = (
            to_celsius(hub.hot_low, hub.temperature_unit) if hub.hot_low is not None else None
        )
        self._weather_unavailable_since: datetime | None = None
        self._forecast_failed_since: datetime | None = None

    # -- raw readings ----------------------------------------------------------------
    def _weather(self) -> State | None:
        state = self.hass.states.get(self.hub.weather_entity)
        return None if state is None or state.state in _UNUSABLE else state

    def _raw_sunny(self, now: datetime) -> bool | None:
        # Weather availability is tracked unconditionally: even with a sunny override
        # configured, the weather entity remains the frost fallback and forecast source,
        # so `weather_unavailable_beyond_grace()` must still reflect its real state.
        weather = self._weather()
        if weather is None:
            self._weather_unavailable_since = self._weather_unavailable_since or now
        else:
            self._weather_unavailable_since = None
        if self.hub.sunny_override_entity:
            return _binary(self.hass.states.get(self.hub.sunny_override_entity))
        condition = self._condition.update(weather.state if weather else None, now)
        return None if condition is None else condition in self.hub.sunny_conditions

    def _outdoor_c(self) -> float | None:
        if self.hub.outdoor_temperature_sensor:
            state = self.hass.states.get(self.hub.outdoor_temperature_sensor)
            value = read_float(state)
            return None if value is None else to_celsius(value, temperature_unit_of(state))
        weather = self._weather()
        if weather is None:
            return None
        raw = weather.attributes.get(ATTR_WEATHER_TEMPERATURE)
        if not isinstance(raw, (int, float)):
            return None
        unit = weather.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
        return to_celsius(float(raw), str(unit) if unit else None)

    # -- lifecycle -------------------------------------------------------------------
    def seed(self, now: datetime) -> None:
        self._sunny.seed(self._raw_sunny(now), now)
        self.frost.update(self._outdoor_c(), now)

    def update(self, now: datetime) -> None:
        self._sunny.update(self._raw_sunny(now), now)
        self.frost.update(self._outdoor_c(), now)

    def apply_forecast(self, today: TodayForecast | None, day: date, now: datetime) -> None:
        if today is None:
            self._forecast_failed_since = self._forecast_failed_since or now
            return
        self._forecast_failed_since = None
        self.latch.update(day, today.max_c, today.min_c, self._hot_high_c, self._hot_low_c)

    def rollover(self, day: date) -> None:
        self.latch.rollover(day)

    def signals(self, now: datetime, store: StoreData, sun_elevation: float) -> HubSignals:
        return HubSignals(
            now=now,
            sun_elevation=sun_elevation,
            frost=self.frost.active,
            frost_near_freezing=self.frost.near_freezing_last_known,
            sunny=self.sunny_state,
            hot_day=self.hot_day,
            shading_mode=store.shading_mode,
            reopening_mode=store.reopening_mode,
            simulation=store.simulation,
        )

    def next_check_at(self, now: datetime) -> datetime | None:
        """When the hub signals would next change on their own, or None.

        A grace expiry that has already passed is dropped: `_weather_unavailable_since` and
        `_forecast_failed_since` stay set for the whole outage, so an elapsed expiry would
        otherwise remain a permanent "check again now" candidate and re-arm a timer every
        second for as long as the source is down.
        """
        candidates = [self._sunny.next_change_at()]
        grace = timedelta(seconds=self.hub.weather_grace_s)
        for since in (self._weather_unavailable_since, self._forecast_failed_since):
            if since is not None and since + grace > now:
                candidates.append(since + grace)
        return min((c for c in candidates if c is not None), default=None)

    # -- views -----------------------------------------------------------------------
    @property
    def sunny_state(self) -> bool | None:
        return self._sunny.state

    @property
    def hot_day(self) -> bool | None:
        if self.hub.hot_override_entity:
            return _binary(self.hass.states.get(self.hub.hot_override_entity))
        return self.latch.hot_day

    def weather_unavailable_beyond_grace(self, now: datetime) -> bool:
        since = self._weather_unavailable_since
        return since is not None and (now - since).total_seconds() >= self.hub.weather_grace_s

    def forecast_failed_beyond_grace(self, now: datetime) -> bool:
        since = self._forecast_failed_since
        return since is not None and (now - since).total_seconds() >= self.hub.weather_grace_s

    @property
    def frost_source_unavailable(self) -> bool:
        return self._outdoor_c() is None

    @property
    def wind_sensor_unavailable(self) -> bool:
        if not self.hub.wind_sensor:
            return False
        return read_float(self.hass.states.get(self.hub.wind_sensor)) is None


class CoverSignalSet:
    """Sun hits, room temperature, wind and door for one cover (spec §2)."""

    def __init__(
        self,
        hass: HomeAssistant,
        cfg: CoverConfig,
        bind: CoverBindings,
        hub: HubConfig,
        *,
        wind_active: bool,
        sun_release_margin: float,
    ) -> None:
        self.hass = hass
        self.cfg = cfg
        self.bind = bind
        self.hub = hub
        self.sun_hits = SunHits(cfg, sun_release_margin)
        self.room: RoomTemperature | None = None
        if bind.room_sensor:
            self.room = RoomTemperature(
                to_celsius(cfg.comfort_floor, bind.temperature_unit),
                to_celsius(cfg.comfort_ceiling, bind.temperature_unit),
            )
        self.wind: WindProtection | None = None
        if cfg.wind_enabled and hub.wind_sensor:
            self.wind = WindProtection(
                cfg.wind_upper, cfg.wind_lower, cfg.wind_hold_s, active=wind_active
            )
        self._room_flags: tuple[bool, bool, bool] = (False, False, False)
        self.wind_unit_current: str | None = None

    def _room_temp_c(self) -> float | None:
        if not self.bind.room_sensor:
            return None
        state = self.hass.states.get(self.bind.room_sensor)
        value = read_float(state)
        return None if value is None else to_celsius(value, temperature_unit_of(state))

    def _wind_value(self) -> float | None:
        if not self.hub.wind_sensor:
            return None
        state = self.hass.states.get(self.hub.wind_sensor)
        value = read_float(state)
        self.wind_unit_current = unit_of(state)
        if value is None:
            return None
        return speed_convert(value, self.wind_unit_current, self.bind.wind_unit)

    def _apply(self, now: datetime, sun: tuple[float, float] | None, *, seed: bool) -> None:
        if sun is not None:
            az, el = sun
            if seed:
                self.sun_hits.seed(az, el)
            else:
                self.sun_hits.update(az, el)
        if self.room is not None:
            reading = self._room_temp_c()
            self._room_flags = (
                self.room.seed(reading, now) if seed else self.room.update(reading, now)
            )
        if self.wind is not None:
            self.wind.update(self._wind_value(), now)

    def seed(self, now: datetime, sun: tuple[float, float] | None) -> None:
        self._apply(now, sun, seed=True)

    def update(self, now: datetime, sun: tuple[float, float] | None) -> None:
        self._apply(now, sun, seed=False)

    def _door(self) -> tuple[DoorState, datetime | None]:
        if not self.bind.door_sensor:
            return DoorState.NONE, None
        state = self.hass.states.get(self.bind.door_sensor)
        if state is None or state.state in _UNUSABLE:
            return DoorState.UNAVAILABLE, None
        return (DoorState.OPEN if state.state == STATE_ON else DoorState.CLOSED), state.last_changed

    def inputs(self, actual: CoverState, schedule: ScheduleView) -> CoverInputs:
        door, changed = self._door()
        cold, hot, degraded = self._room_flags
        return CoverInputs(
            actual=actual,
            door=door,
            door_last_changed=changed,
            sun_hits=self.sun_hits_state,
            room_cold=cold,
            room_hot=hot,
            room_degraded=degraded,
            wind_active=self.wind_active,
            schedule=schedule,
        )

    def next_check_at(self) -> datetime | None:
        candidates = [
            c
            for c in (
                self.room.next_check_at() if self.room else None,
                self.wind.next_check_at() if self.wind else None,
            )
            if c is not None
        ]
        return min(candidates, default=None)

    @property
    def sun_hits_state(self) -> bool:
        return bool(self.sun_hits.state)

    @property
    def wind_active(self) -> bool:
        return self.wind.active if self.wind is not None else False

    @property
    def wind_state(self) -> str:
        if self.wind is None:
            return "disabled"
        if self.wind.unavailable:
            return "unavailable"
        return "active" if self.wind.active else "inactive"

    @property
    def wind_unit_mismatch(self) -> bool:
        return (
            self.wind is not None
            and self.wind_unit_current is not None
            and self.bind.wind_unit is not None
            and self.wind_unit_current != self.bind.wind_unit
        )

    @property
    def room_state(self) -> str:
        if self.room is None:
            return "none"
        cold, hot, degraded = self._room_flags
        if degraded:
            return "degraded"
        if cold:
            return "cold"
        return "hot" if hot else "comfortable"

    @property
    def room_unusable(self) -> bool:
        return self.cfg.shading_rule is ShadingRule.ROOM_ONLY and (
            self.room is None or self._room_flags[2]
        )

    @property
    def door_unavailable(self) -> bool:
        return self._door()[0] is DoorState.UNAVAILABLE
