"""Stateful signal primitives (spec §2): grace, debounce, dwell, latch, room, wind, frost."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .const import (
    FROST_RELEASE_K,
    FROST_THRESHOLD_C,
    ROOM_DWELL_S,
    ROOM_HYSTERESIS_K,
    WEATHER_GRACE_S,
)


class ContinuousCondition:
    """True once `cond` has been True for `duration_s` accumulated seconds.

    None pauses accumulation (leave_alone), False resets it.
    """

    def __init__(self, duration_s: float) -> None:
        self.duration_s = float(duration_s)
        self._accum_s = 0.0
        self._last_true_at: datetime | None = None

    def reset(self) -> None:
        self._accum_s = 0.0
        self._last_true_at = None

    def update(self, cond: bool | None, now: datetime) -> bool:
        if cond is None:
            self._last_true_at = None
        elif cond:
            if self._last_true_at is not None:
                self._accum_s += (now - self._last_true_at).total_seconds()
            self._last_true_at = now
        else:
            self.reset()
        return self._accum_s >= self.duration_s

    def remaining_s(self) -> float | None:
        if self._last_true_at is None:
            return None
        return max(0.0, self.duration_s - self._accum_s)


class Graceful[T]:
    """Hold the last known value while the source is unavailable, up to grace_s."""

    def __init__(self, grace_s: float = WEATHER_GRACE_S) -> None:
        self.grace_s = float(grace_s)
        self.last_known: T | None = None
        self.unavailable_since: datetime | None = None

    def update(self, value: T | None, now: datetime) -> T | None:
        if value is not None:
            self.last_known = value
            self.unavailable_since = None
            return value
        if self.last_known is None:
            return None
        if self.unavailable_since is None:
            self.unavailable_since = now
        if (now - self.unavailable_since).total_seconds() >= self.grace_s:
            return None
        return self.last_known


class Debounce:
    """Boolean debounce with separate on/off delays; None = unknown passes through."""

    def __init__(self, on_delay_s: float, off_delay_s: float) -> None:
        self.on_delay_s = float(on_delay_s)
        self.off_delay_s = float(off_delay_s)
        self.state: bool | None = None
        self._raw: bool | None = None
        self._raw_since: datetime | None = None

    def seed(self, raw: bool | None, now: datetime) -> bool | None:
        self.state = raw
        self._raw = raw
        self._raw_since = now
        return self.state

    def update(self, raw: bool | None, now: datetime) -> bool | None:
        if raw is None:
            self.state = None
            self._raw = None
            self._raw_since = None
            return None
        if self.state is None:
            return self.seed(raw, now)
        if raw != self._raw or self._raw_since is None:
            self._raw = raw
            self._raw_since = now
        if raw != self.state:
            delay = self.on_delay_s if raw else self.off_delay_s
            if (now - self._raw_since).total_seconds() >= delay:
                self.state = raw
        return self.state

    def next_change_at(self) -> datetime | None:
        if self._raw is None or self._raw_since is None or self._raw == self.state:
            return None
        delay = self.on_delay_s if self._raw else self.off_delay_s
        return self._raw_since + timedelta(seconds=delay)


@dataclass(slots=True)
class DailyLatch:
    """Today's forecast max only rises, min only falls; hot_day latches True until rollover."""

    date: date | None = None
    max: float | None = None
    min: float | None = None
    hot_day: bool | None = None

    def rollover(self, day: date) -> None:
        if day != self.date:
            self.date = day
            self.max = None
            self.min = None
            self.hot_day = None

    def update(
        self,
        day: date,
        forecast_max: float | None,
        forecast_min: float | None,
        hot_high: float,
        hot_low: float | None,
    ) -> bool | None:
        self.rollover(day)
        if forecast_max is not None:
            self.max = forecast_max if self.max is None else max(self.max, forecast_max)
        if forecast_min is not None:
            self.min = forecast_min if self.min is None else min(self.min, forecast_min)
        if self.max is None:
            return self.hot_day
        low_ok = hot_low is None or (self.min is not None and self.min >= hot_low)
        if self.max >= hot_high and low_ok:
            self.hot_day = True
        elif self.hot_day is not True:
            self.hot_day = False
        return self.hot_day

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat() if self.date else None,
            "max": self.max,
            "min": self.min,
            "hot_day": self.hot_day,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyLatch:
        raw_date = data.get("date")
        return cls(
            date=date.fromisoformat(raw_date) if raw_date else None,
            max=data.get("max"),
            min=data.get("min"),
            hot_day=data.get("hot_day"),
        )


class _HysteresisDwell:
    """Boolean with an on-test, an off-test and a dwell before either transition."""

    def __init__(self, dwell_s: float) -> None:
        self.state = False
        self._dwell = ContinuousCondition(dwell_s)

    def seed(self, state: bool) -> None:
        self.state = state
        self._dwell.reset()

    def update(self, on_raw: bool, off_raw: bool, now: datetime) -> bool:
        wants_change = (not self.state and on_raw) or (self.state and off_raw)
        if self._dwell.update(bool(wants_change), now):
            self.state = not self.state
            self._dwell.reset()
        return self.state

    def remaining_s(self) -> float | None:
        return self._dwell.remaining_s()


class RoomTemperature:
    """room_cold / room_hot with hysteresis band and dwell (spec §2)."""

    def __init__(
        self,
        floor: float,
        ceiling: float,
        band: float = ROOM_HYSTERESIS_K,
        dwell_s: float = ROOM_DWELL_S,
    ) -> None:
        self.floor = floor
        self.ceiling = ceiling
        self.band = band
        self._cold = _HysteresisDwell(dwell_s)
        self._hot = _HysteresisDwell(dwell_s)
        self.degraded = False
        self._last_now: datetime | None = None

    def seed(self, temp: float | None, now: datetime) -> tuple[bool, bool, bool]:
        self._last_now = now
        if temp is None:
            self._cold.seed(False)
            self._hot.seed(False)
            self.degraded = True
        else:
            self._cold.seed(temp < self.floor)
            self._hot.seed(temp >= self.ceiling)
            self.degraded = False
        return self._cold.state, self._hot.state, self.degraded

    def update(self, temp: float | None, now: datetime) -> tuple[bool, bool, bool]:
        self._last_now = now
        if temp is None:
            self.degraded = True
            self._cold.seed(False)
            self._hot.seed(False)
            return False, False, True
        self.degraded = False
        cold = self._cold.update(temp < self.floor, temp >= self.floor + self.band, now)
        hot = self._hot.update(temp >= self.ceiling, temp < self.ceiling - self.band, now)
        return cold, hot, False

    def next_check_at(self) -> datetime | None:
        if self._last_now is None:
            return None
        remaining = [
            r for r in (self._cold.remaining_s(), self._hot.remaining_s()) if r is not None
        ]
        if not remaining:
            return None
        return self._last_now + timedelta(seconds=min(remaining))


class WindProtection:
    """Per-cover wind protection: on at >= upper, off after < lower for hold_s."""

    def __init__(self, upper: float, lower: float, hold_s: float, active: bool = False) -> None:
        self.upper = upper
        self.lower = lower
        self.hold_s = float(hold_s)
        self.active = active
        self.unavailable = False
        self._below_since: datetime | None = None

    def update(self, value: float | None, now: datetime) -> bool:
        if value is None:
            self.unavailable = True
            self._below_since = None  # an unknown gap breaks the "continuously below" run
            return self.active
        self.unavailable = False
        if value >= self.upper:
            self.active = True
            self._below_since = None
        elif self.active and value < self.lower:
            if self._below_since is None:
                self._below_since = now
            elif (now - self._below_since).total_seconds() >= self.hold_s:
                self.active = False
                self._below_since = None
        else:
            self._below_since = None
        return self.active

    def next_check_at(self) -> datetime | None:
        if self._below_since is None:
            return None
        return self._below_since + timedelta(seconds=self.hold_s)


class FrostSignal:
    """frost_active with release hysteresis; unknown after the grace period."""

    def __init__(
        self,
        threshold: float = FROST_THRESHOLD_C,
        release_k: float = FROST_RELEASE_K,
        grace_s: float = WEATHER_GRACE_S,
    ) -> None:
        self.threshold = threshold
        self.release_k = release_k
        self._graceful: Graceful[float] = Graceful(grace_s)
        self.active: bool | None = None

    def update(self, temp: float | None, now: datetime) -> bool | None:
        value = self._graceful.update(temp, now)
        if value is None:
            self.active = None
            return None
        if self.active:
            if value > self.threshold + self.release_k:
                self.active = False
        elif value <= self.threshold:
            self.active = True
        else:
            self.active = False
        return self.active

    @property
    def near_freezing_last_known(self) -> bool:
        last = self._graceful.last_known
        return last is not None and last <= self.threshold + self.release_k
