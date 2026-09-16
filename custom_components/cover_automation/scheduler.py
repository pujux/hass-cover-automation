"""Schedule rule timers and views (spec §1.2 layer 5, §2 timers, decision 28)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .engine import schedule
from .engine.model import CoverState, ScheduleView, Target
from .engine.schedule import Profile, SunTimes, TimeMode

_LOGGER = logging.getLogger(__name__)


class HassSunTimes:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _event(self, event: str, day: date, fallback: time) -> datetime:
        result = get_astral_event_date(self.hass, event, day)
        if result is None:
            _LOGGER.debug("No %s on %s at this location; using %s local", event, day, fallback)
            return datetime.combine(day, fallback, tzinfo=dt_util.get_default_time_zone())
        return result

    def sunrise(self, day: date) -> datetime:
        return self._event(SUN_EVENT_SUNRISE, day, time(6, 0))

    def sunset(self, day: date) -> datetime:
        return self._event(SUN_EVENT_SUNSET, day, time(18, 0))


@dataclass(frozen=True, slots=True)
class NextEvent:
    at: datetime
    profile_id: str
    profile_name: str
    action: Target
    rule_index: int
    covers: tuple[str, ...]


class ScheduleTracker:
    def __init__(
        self,
        hass: HomeAssistant,
        profiles: Mapping[str, Profile],
        cover_profile: Mapping[str, str | None],
        on_fire: Callable[[frozenset[str], datetime], Awaitable[None]],
        *,
        sun: SunTimes | None = None,
    ) -> None:
        self.hass = hass
        self.profiles = dict(profiles)
        self.cover_profile = dict(cover_profile)
        self._on_fire = on_fire
        self.sun: SunTimes = sun or HassSunTimes(hass)
        self._unsub: CALLBACK_TYPE | None = None
        self._armed_at: datetime | None = None
        self._armed_covers: frozenset[str] = frozenset()
        self._generation = 0

    @property
    def tz(self) -> tzinfo:
        return dt_util.get_default_time_zone()

    def _covers_of(self, profile_id: str) -> tuple[str, ...]:
        return tuple(sorted(c for c, p in self.cover_profile.items() if p == profile_id))

    def _profile_for(self, cover_id: str) -> Profile | None:
        profile_id = self.cover_profile.get(cover_id)
        return self.profiles.get(profile_id) if profile_id else None

    def view(
        self,
        cover_id: str,
        now: datetime,
        actual: CoverState,
        manual_move_at: datetime | None,
        satisfied_fire_at: datetime | None,
    ) -> ScheduleView:
        profile = self._profile_for(cover_id)
        if profile is None:
            return ScheduleView()
        return schedule.view(
            profile, now, self.sun, actual, manual_move_at, satisfied_fire_at, tz=self.tz
        )

    def _events(self, now: datetime) -> list[NextEvent]:
        events: list[NextEvent] = []
        for profile_id, profile in self.profiles.items():
            covers = self._covers_of(profile_id)
            if not covers:
                continue
            nxt = schedule.next_fire(profile, now, self.sun, tz=self.tz)
            if nxt is None:
                continue
            at, index = nxt
            events.append(
                NextEvent(at, profile_id, profile.name, profile.rules[index].action, index, covers)
            )
        events.sort(key=lambda e: (e.at, e.profile_id))
        return events

    def next_event(self, now: datetime) -> NextEvent | None:
        events = self._events(now)
        return events[0] if events else None

    def next_event_for(self, cover_id: str, now: datetime) -> NextEvent | None:
        profile_id = self.cover_profile.get(cover_id)
        return next((e for e in self._events(now) if e.profile_id == profile_id), None)

    def active_rule_label(self, cover_id: str, now: datetime) -> str | None:
        profile = self._profile_for(cover_id)
        if profile is None:
            return None
        last = schedule.last_fired(profile, now, self.sun, tz=self.tz)
        if last is None:
            return None
        fired_at, index = last
        action = "close" if profile.rules[index].action is Target.CLOSED else "open"
        return f"{action} rule {index + 1} of {profile.name} ({fired_at.astimezone(self.tz):%H:%M})"

    def skipped_rules_today(self, now: datetime) -> list[tuple[str, int]]:
        day = now.astimezone(self.tz).date()
        skipped: list[tuple[str, int]] = []
        for profile_id, profile in self.profiles.items():
            for index, rule in enumerate(profile.rules):
                if rule.time_mode is TimeMode.FIXED:
                    continue
                if schedule.fire_time(rule, day, self.sun, profile.quiet_hours, self.tz) is None:
                    skipped.append((profile_id, index))
        return skipped

    @callback
    def async_arm(self, now: datetime) -> None:
        self.async_cancel()
        self._generation += 1
        events = self._events(now)
        if not events:
            self._armed_at = None
            self._armed_covers = frozenset()
            return
        at = events[0].at
        self._armed_at = at
        self._armed_covers = frozenset(
            cover for event in events if event.at == at for cover in event.covers
        )
        self._unsub = async_track_point_in_time(self.hass, self._fired, at)

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _fired(self, _now: datetime) -> None:
        self._unsub = None
        generation = self._generation
        at = self._armed_at
        covers = self._armed_covers
        try:
            if covers and at is not None:
                await self._on_fire(covers, at)
        except Exception:
            _LOGGER.exception("Schedule rule handler failed")
        finally:
            if generation == self._generation and at is not None:
                self.async_arm(at)
