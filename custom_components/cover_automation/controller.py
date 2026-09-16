"""Controller: subscriptions, timers, evaluate → act → persist (spec §1.3-§1.5, §2, §5)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import const, repairs
from .config_map import CoverBindings, HubConfig
from .engine.cover import CoverEngine
from .engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverState,
    Decision,
    Defer,
    HubSignals,
    Layer,
    Mode,
    ReopeningMode,
    Send,
    ShadingMode,
    Status,
    StepResult,
    Target,
    TransitionKind,
)
from .engine.schedule import Profile
from .forecast import async_fetch_today
from .scheduler import ScheduleTracker
from .signals_adapter import (
    SUN_ENTITY,
    CoverSignalSet,
    HubSignalSource,
    classify_state,
    cover_supports,
    sun_position,
)
from .store import CoverAutomationStore
from .views import CoverView, HubView, signal_update

_LOGGER = logging.getLogger(__name__)
_INTEGRATION_LOGGER = logging.getLogger("custom_components.cover_automation")

EVENT_ACTION = f"{const.DOMAIN}_action"
FALLBACK_TICK = timedelta(minutes=5)
FORECAST_REFRESH = timedelta(hours=1)
FORECAST_THROTTLE = timedelta(minutes=10)


def _jsonable(value: Any) -> Any:
    """datetime → ISO string, StrEnum → value, recursively (diagnostics)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)


class CoverAutomationController:
    """Owns the engines, every subscription and timer, and the write API (decision 17)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        hub: HubConfig,
        covers: Mapping[str, tuple[CoverConfig, CoverBindings]],
        profiles: Mapping[str, Profile],
        store: CoverAutomationStore,
        hub_device_id: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.hub = hub
        self._covers = dict(covers)
        self._profiles = dict(profiles)
        self._store = store
        self.hub_device_id = hub_device_id
        self.started = False
        self.cover_names: Mapping[str, str] = {cid: cfg.name for cid, (cfg, _b) in covers.items()}
        self._engines: dict[str, CoverEngine] = {}
        self._signals: dict[str, CoverSignalSet] = {}
        self._hub_signals = HubSignalSource(hass, hub, store.data.latch)
        self._schedule = ScheduleTracker(
            hass,
            self._profiles,
            {cid: cfg.profile_id for cid, (cfg, _b) in covers.items()},
            self._on_rule_fired,
        )
        self._actual: dict[str, CoverState] = {}
        self._reconciled: set[str] = set()
        self._retry_at: dict[str, datetime] = {}
        self._last_engine_move: dict[str, datetime] = {}
        self._cover_timers: dict[str, CALLBACK_TYPE] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_forecast_fetch: datetime | None = None
        self._watched: dict[str, set[str] | None] = {}
        self._problem = False
        # One lock per cover: an evaluation (and its blocking service call) only ever
        # serialises that cover, so a slow device cannot stall the others or `async_stop`.
        self._locks: dict[str, asyncio.Lock] = {cid: asyncio.Lock() for cid in covers}
        self.hub_view = HubView()
        # `cover_views` is the read-only face of `_cover_views` (same object): the entity-side
        # `ControllerProtocol` types it as an invariant `Mapping`, so the writable alias is
        # what the evaluation loop mutates.
        self._cover_views: dict[str, CoverView] = {
            cid: CoverView(name=cfg.name, cover_entity=bind.cover_entity)
            for cid, (cfg, bind) in covers.items()
        }
        self.cover_views: Mapping[str, CoverView] = self._cover_views

    # -- lifecycle ---------------------------------------------------------------------

    def engine(self, cover_id: str) -> CoverEngine:
        return self._engines[cover_id]

    async def async_start(self) -> None:
        now = dt_util.utcnow()
        for cover_id, (cfg, bind) in self._covers.items():
            persisted = self._store.data.covers.setdefault(cover_id, CoverPersisted())
            self._engines[cover_id] = CoverEngine(
                cfg, persisted, override_dwell_s=self.hub.override_dwell_s
            )
            self._signals[cover_id] = CoverSignalSet(
                self.hass,
                cfg,
                bind,
                self.hub,
                wind_active=persisted.wind_active,
                sun_release_margin=self.hub.sun_release_margin,
            )
        self._apply_verbose(self._store.data.verbose)
        self._hub_signals.seed(now)
        await self._async_refresh_forecast(now, force=True)
        self._subscribe()
        self._refresh_problem()
        self.started = True
        self._schedule.async_arm(now)
        await self.async_evaluate(now)

    async def async_start_job(self, hass: HomeAssistant) -> None:
        """Coroutine job for `async_at_started` (Task 9 wiring)."""
        del hass  # the controller already holds its own hass reference
        await self.async_start()

    async def async_stop(self) -> None:
        self.started = False
        self._async_cancel_all()
        # Drain every per-cover lock once: an evaluation that is already mid-flight finishes
        # here, and the `started` re-checks keep it from arming anything behind the sweep.
        for lock in self._locks.values():
            async with lock:
                pass
        await self._store.async_save()

    @callback
    def _async_cancel_all(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in self._cover_timers.values():
            cancel()
        self._cover_timers.clear()
        self._schedule.async_cancel()

    def _subscribe(self) -> None:
        hub, hass = self.hub, self.hass
        watched: dict[str, set[str] | None] = {}  # entity → affected cover ids (None = all)
        hub_entities = (
            hub.weather_entity,
            hub.wind_sensor,
            hub.outdoor_temperature_sensor,
            hub.sunny_override_entity,
            hub.hot_override_entity,
            SUN_ENTITY,
        )
        for entity_id in hub_entities:
            if entity_id:
                watched[entity_id] = None
        for cover_id, (_cfg, bind) in self._covers.items():
            for entity_id in (bind.cover_entity, bind.door_sensor, bind.room_sensor):
                if not entity_id:
                    continue
                affected = watched.setdefault(entity_id, set())
                if affected is not None:
                    affected.add(cover_id)
        self._watched = watched
        self._unsubs.append(
            async_track_state_change_event(hass, list(watched), self._on_state_change)
        )
        self._unsubs.append(
            async_track_time_change(hass, self._on_midnight, hour=0, minute=0, second=10)
        )
        self._unsubs.append(
            async_track_time_interval(hass, self._on_forecast_tick, FORECAST_REFRESH)
        )
        self._unsubs.append(async_track_time_interval(hass, self._on_fallback_tick, FALLBACK_TICK))
        self._unsubs.append(
            hass.bus.async_listen(ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, self._on_issue_event)
        )

    # -- triggers ----------------------------------------------------------------------

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        affected = self._watched.get(entity_id)
        if entity_id == self.hub.weather_entity:
            self.hass.async_create_task(self._async_weather_changed())
            return
        self.hass.async_create_task(self.async_evaluate(cover_ids=affected))

    async def _async_weather_changed(self) -> None:
        if not self.started:
            return
        now = dt_util.utcnow()
        await self._async_refresh_forecast(now, force=False)
        await self.async_evaluate(now)

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_midnight())

    async def _async_midnight(self) -> None:
        if not self.started:
            return
        now = dt_util.utcnow()
        self._hub_signals.rollover(dt_util.as_local(now).date())
        await self._async_refresh_forecast(now, force=True)
        if not self.started:  # `async_stop` may have run during the forecast fetch
            return
        self._schedule.async_arm(now)
        await self.async_evaluate(now)

    @callback
    def _on_forecast_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_forecast_tick())

    async def _async_forecast_tick(self) -> None:
        if not self.started:
            return
        now = dt_util.utcnow()
        await self._async_refresh_forecast(now, force=True)
        await self.async_evaluate(now)

    @callback
    def _on_fallback_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate())

    @callback
    def _on_issue_event(self, event: Event[Any]) -> None:
        if event.data.get("domain") != const.DOMAIN:
            return
        self._refresh_problem()
        self._publish()

    def _refresh_problem(self) -> None:
        registry = ir.async_get(self.hass)
        self._problem = any(
            domain == const.DOMAIN and issue.active and issue.dismissed_version is None
            for (domain, _issue_id), issue in registry.issues.items()
        )

    async def _on_rule_fired(self, covers: frozenset[str], at: datetime) -> None:
        await self.async_evaluate(at, cover_ids=covers, rule_fired=covers)

    async def _async_refresh_forecast(self, now: datetime, *, force: bool) -> None:
        last = self._last_forecast_fetch
        if not force and last is not None and now - last < FORECAST_THROTTLE:
            return
        self._last_forecast_fetch = now
        local_day = dt_util.as_local(now).date()
        today = await async_fetch_today(
            self.hass, self.hub.weather_entity, local_day, dt_util.get_default_time_zone()
        )
        self._hub_signals.apply_forecast(today, local_day, now)

    # -- evaluation --------------------------------------------------------------------

    async def async_evaluate(
        self,
        now: datetime | None = None,
        *,
        cover_ids: Iterable[str] | None = None,
        rule_fired: frozenset[str] = frozenset(),
    ) -> None:
        if not self.started:
            return
        at = now or dt_util.utcnow()
        ids = list(cover_ids) if cover_ids is not None else list(self._engines)
        sun = sun_position(self.hass)
        # Hub-level bookkeeping stays outside the per-cover locks.
        self._hub_signals.update(at)
        self._update_hub_repairs(at, sun)
        if sun is not None:
            hub_sig = self._hub_signals.signals(at, self._store.data, sun[1])
            for cover_id in ids:
                if cover_id not in self._engines:
                    continue
                try:
                    async with self._locks[cover_id]:
                        if not self.started:
                            # This task was queued on a contended lock before `async_stop`.
                            return
                        await self._evaluate_cover(
                            cover_id, at, sun, hub_sig, cover_id in rule_fired
                        )
                except Exception:  # per-cover isolation (spec §5)
                    _LOGGER.exception("Evaluation of cover %s failed", self.cover_names[cover_id])
        self._update_profile_repairs(at)
        self._publish()
        self._store.schedule_save()

    async def _sync_actual(self, cover_id: str, now: datetime) -> CoverState:
        _cfg, bind = self._covers[cover_id]
        actual = classify_state(self.hass.states.get(bind.cover_entity), self.hub.tolerance)
        previous = self._actual.get(cover_id)
        self._actual[cover_id] = actual
        if previous is not None and actual is not previous and cover_id in self._reconciled:
            result = self._engines[cover_id].on_transition(actual, now)
            _LOGGER.debug(
                "%s: %s → %s (%s)", self.cover_names[cover_id], previous, actual, result.kind
            )
            if result.kind is TransitionKind.MANUAL:
                await self._store.async_save()
        return actual

    async def _async_first_reconcile(
        self,
        cover_id: str,
        now: datetime,
        sun: tuple[float, float],
        hub_sig: HubSignals,
        actual: CoverState,
    ) -> None:
        """decide() → reconcile() before the first evaluate() ever runs (spec §5)."""
        engine, sig = self._engines[cover_id], self._signals[cover_id]
        sig.seed(now, sun)
        first = self._schedule.view(cover_id, now, actual, engine.p.manual_move_at, None)
        decision = engine.decide(sig.inputs(actual, first), hub_sig)
        before = engine.p.to_dict()
        message = engine.reconcile(actual, decision, now)
        _LOGGER.debug("%s: reconcile → %s", self.cover_names[cover_id], message)
        self._reconciled.add(cover_id)
        if engine.p.to_dict() != before:
            await self._store.async_save()

    async def _evaluate_cover(
        self,
        cover_id: str,
        now: datetime,
        sun: tuple[float, float],
        hub_sig: HubSignals,
        rule_fired: bool,
    ) -> None:
        engine, sig = self._engines[cover_id], self._signals[cover_id]
        actual = await self._sync_actual(cover_id, now)
        if cover_id not in self._reconciled:
            if actual is CoverState.UNAVAILABLE:
                # Nothing to reconcile against yet: wait for the cover's first settled state.
                self._cover_views[cover_id] = CoverView(
                    name=self.cover_names[cover_id],
                    cover_entity=self._covers[cover_id][1].cover_entity,
                    status=Status.COVER_UNAVAILABLE,
                    enabled=engine.p.enabled,
                    mode=engine.p.mode,
                )
                return
            await self._async_first_reconcile(cover_id, now, sun, hub_sig, actual)
        else:
            sig.update(now, sun)
        schedule = self._schedule.view(
            cover_id, now, actual, engine.p.manual_move_at, engine.rt.open_rule_satisfied_at
        )
        inputs = sig.inputs(actual, schedule)
        result = engine.evaluate(inputs, hub_sig, rule_fired=rule_fired)
        if inputs.actual is not CoverState.UNAVAILABLE:
            # A vanished entity can neither be commanded nor judged on its features.
            await self._act(cover_id, result, now)
        self._update_cover_repairs(cover_id, sig)
        self._cover_views[cover_id] = self._build_cover_view(cover_id, result, inputs, now)
        self._arm_cover_timer(cover_id, result, sig, now)

    # -- acting ------------------------------------------------------------------------

    async def _act(self, cover_id: str, result: StepResult, now: datetime) -> None:
        engine = self._engines[cover_id]
        cfg, bind = self._covers[cover_id]
        decision, action = result.decision, result.action
        self._update_frost_conflict(cover_id, result)
        if not isinstance(action, Send):
            return
        pending = engine.rt.pending
        if pending is not None and pending.target is action.target:
            # The gate only suppresses while the cover reports `moving`; a device that has not
            # reported yet would otherwise get the same command from every evaluation.
            _LOGGER.debug("%s: command already in flight (%s)", cfg.name, action.target.value)
            return
        verb = "open" if action.target is Target.OPEN else "close"
        if action.simulated:
            _LOGGER.info(
                "Simulation: would %s %s (%s: %s)",
                verb,
                cfg.name,
                decision.layer.value,
                decision.reason,
            )
            engine.on_command_sent(action, now)
            self._fire_action_event(cover_id, verb, decision, simulated=True)
            return
        open_close, set_position = cover_supports(self.hass.states.get(bind.cover_entity))
        unsupported = not open_close and not set_position
        repairs.set_issue(
            self.hass,
            repairs.cover_issue_id(repairs.ISSUE_COVER_UNSUPPORTED, cover_id),
            unsupported,
            translation_key="cover_unsupported",
            placeholders={"cover": cfg.name},
        )
        if unsupported:
            return
        if open_close:
            service = SERVICE_OPEN_COVER if verb == "open" else SERVICE_CLOSE_COVER
            data: dict[str, Any] = {ATTR_ENTITY_ID: bind.cover_entity}
        else:
            service = SERVICE_SET_COVER_POSITION
            data = {ATTR_ENTITY_ID: bind.cover_entity, ATTR_POSITION: 100 if verb == "open" else 0}
        engine.on_command_sent(action, now)
        try:
            await self.hass.services.async_call(COVER_DOMAIN, service, data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: %s failed: %s", cfg.name, service, err)
            self._retry_at[cover_id] = engine.on_command_failed(now)
            await self._store.async_save()
            return
        self._retry_at.pop(cover_id, None)
        self._last_engine_move[cover_id] = now
        await self._store.async_save()
        self._fire_action_event(cover_id, verb, decision, simulated=False)

    def _update_frost_conflict(self, cover_id: str, result: StepResult) -> None:
        cfg, _bind = self._covers[cover_id]
        issue_id = repairs.cover_issue_id(repairs.ISSUE_FROST_CONFLICT, cover_id)
        if result.notify_frost_conflict:
            persistent_notification.async_create(
                self.hass,
                f"{cfg.name} should open for wind or door protection, "
                "but frost protection keeps it in place.",
                title="Cover Automation",
                notification_id=f"{const.DOMAIN}_frost_{cover_id}",
            )
            repairs.set_issue(
                self.hass,
                issue_id,
                True,
                translation_key="frost_conflict",
                placeholders={"cover": cfg.name},
            )
        elif result.decision.layer is not Layer.FROST:
            repairs.set_issue(self.hass, issue_id, False, translation_key="frost_conflict")

    def _fire_action_event(
        self, cover_id: str, verb: str, decision: Decision, *, simulated: bool
    ) -> None:
        _cfg, bind = self._covers[cover_id]
        self.hass.bus.async_fire(
            EVENT_ACTION,
            {
                ATTR_ENTITY_ID: bind.cover_entity,
                "cover_name": self.cover_names[cover_id],
                "action": verb,
                "reason": decision.reason,
                "layer": decision.layer.value,
                "simulated": simulated,
            },
        )

    # -- timers ------------------------------------------------------------------------

    def _arm_cover_timer(
        self, cover_id: str, result: StepResult, sig: CoverSignalSet, now: datetime
    ) -> None:
        if cancel := self._cover_timers.pop(cover_id, None):
            cancel()
        if not self.started:
            return
        candidates = [
            c
            for c in (
                result.next_check_at,
                sig.next_check_at(),
                self._hub_signals.next_check_at(),
                self._retry_at.get(cover_id),
            )
            if c is not None
        ]
        if not candidates:
            return
        delay = max(1.0, (min(candidates) - now).total_seconds())
        self._cover_timers[cover_id] = async_call_later(
            self.hass, delay, partial(self._on_cover_timer, cover_id)
        )

    @callback
    def _on_cover_timer(self, cover_id: str, _now: datetime) -> None:
        self._cover_timers.pop(cover_id, None)
        self.hass.async_create_task(self._async_cover_timer(cover_id))

    async def _async_cover_timer(self, cover_id: str) -> None:
        if not self.started:
            return
        now = dt_util.utcnow()
        async with self._locks[cover_id]:
            await self._async_check_pending(cover_id, now)
        await self.async_evaluate(now, cover_ids={cover_id})

    async def _async_check_pending(self, cover_id: str, now: datetime) -> None:
        engine = self._engines[cover_id]
        if engine.rt.pending is None:
            return
        actual = await self._sync_actual(cover_id, now)
        before = engine.p.to_dict()
        if (message := engine.check_pending(actual, now)) is not None:
            _LOGGER.debug("%s: %s", self.cover_names[cover_id], message)
        if engine.p.to_dict() != before:
            await self._store.async_save()

    # -- repairs -----------------------------------------------------------------------

    def _update_hub_repairs(self, now: datetime, sun: tuple[float, float] | None) -> None:
        hs, hub, eid = self._hub_signals, self.hub, self.entry.entry_id
        any_wind = any(cfg.wind_enabled for cfg, _b in self._covers.values())
        repairs.set_issue(
            self.hass,
            repairs.hub_issue_id(eid, repairs.ISSUE_WEATHER_UNAVAILABLE),
            hs.weather_unavailable_beyond_grace(now),
            translation_key="weather_unavailable",
        )
        repairs.set_issue(
            self.hass,
            repairs.hub_issue_id(eid, repairs.ISSUE_FORECAST_FAILED),
            hs.forecast_failed_beyond_grace(now),
            translation_key="forecast_fetch_failed",
        )
        repairs.set_issue(
            self.hass,
            repairs.hub_issue_id(eid, repairs.ISSUE_SUN_MISSING),
            sun is None,
            translation_key="sun_missing",
        )
        if hub.outdoor_temperature_sensor:
            frost_source_missing = hs.frost_source_unavailable
        else:
            # The weather entity is the frost fallback: only flag it once the frost signal has
            # actually gone unknown and the weather entity itself is not already reported down.
            frost_source_missing = (
                hs.frost.active is None
                and hs.frost_source_unavailable
                and not hs.weather_unavailable_beyond_grace(now)
            )
        repairs.set_issue(
            self.hass,
            repairs.hub_issue_id(eid, repairs.ISSUE_FROST_SOURCE_UNAVAILABLE),
            frost_source_missing,
            translation_key="frost_source_unavailable",
        )
        repairs.set_issue(
            self.hass,
            repairs.hub_issue_id(eid, repairs.ISSUE_WIND_UNAVAILABLE),
            any_wind and hs.wind_sensor_unavailable,
            translation_key="wind_sensor_unavailable",
        )

    def _update_cover_repairs(self, cover_id: str, sig: CoverSignalSet) -> None:
        name, engine = self.cover_names[cover_id], self._engines[cover_id]
        placeholders = {"cover": name}
        repairs.set_issue(
            self.hass,
            repairs.cover_issue_id(repairs.ISSUE_WIND_UNIT_CHANGED, cover_id),
            sig.wind_unit_mismatch,
            translation_key="wind_unit_changed",
            placeholders={
                **placeholders,
                "stored": str(self._covers[cover_id][1].wind_unit),
                "current": str(sig.wind_unit_current),
            },
        )
        repairs.set_issue(
            self.hass,
            repairs.cover_issue_id(repairs.ISSUE_DOOR_UNAVAILABLE, cover_id),
            sig.door_unavailable,
            translation_key="door_sensor_unavailable",
            placeholders=placeholders,
        )
        repairs.set_issue(
            self.hass,
            repairs.cover_issue_id(repairs.ISSUE_ROOM_UNUSABLE, cover_id),
            sig.room_unusable,
            translation_key="room_sensor_unusable",
            placeholders=placeholders,
        )
        repairs.set_issue(
            self.hass,
            repairs.cover_issue_id(repairs.ISSUE_COMMAND_FAILURES, cover_id),
            engine.repair_needed,
            translation_key="command_failures",
            placeholders=placeholders,
        )

    def _update_profile_repairs(self, now: datetime) -> None:
        """One `rule_skipped` issue per profile; the stale sweep only knows profile-scoped ids."""
        skipped: dict[str, list[int]] = {}
        for profile_id, index in self._schedule.skipped_rules_today(now):
            skipped.setdefault(profile_id, []).append(index + 1)
        for profile_id, profile in self._profiles.items():
            numbers = skipped.get(profile_id, [])
            repairs.set_issue(
                self.hass,
                repairs.cover_issue_id(repairs.ISSUE_RULE_SKIPPED, profile_id),
                bool(numbers),
                translation_key="rule_skipped",
                placeholders={
                    "profile": profile.name,
                    "rule": ", ".join(str(n) for n in sorted(numbers)),
                },
            )

    # -- views -------------------------------------------------------------------------

    def _planned(
        self, cover_id: str, result: StepResult, now: datetime
    ) -> tuple[str | None, datetime | None]:
        engine = self._engines[cover_id]
        if isinstance(result.action, Defer):
            target = result.decision.desired.value
            return f"{target} ({result.action.reason})", result.action.until
        if engine.rt.pending is not None:
            return f"{engine.rt.pending.target.value} (in progress)", None
        event = self._schedule.next_event_for(cover_id, now)
        if event is not None:
            return f"{event.action.value} by schedule", event.at
        return None, None

    def _build_cover_view(
        self, cover_id: str, result: StepResult, inputs: CoverInputs, now: datetime
    ) -> CoverView:
        engine, sig = self._engines[cover_id], self._signals[cover_id]
        cfg, bind = self._covers[cover_id]
        p, d = engine.p, result.decision
        planned, planned_at = self._planned(cover_id, result, now)
        return CoverView(
            name=cfg.name,
            cover_entity=bind.cover_entity,
            # recomputed after acting: a failed or unconfirmed command only shows up here
            status=engine.status(d, inputs),
            desired_state=d.desired.value,
            actual_state=inputs.actual.value,
            winning_layer=d.layer.value,
            reason=d.reason,
            sun_hits=inputs.sun_hits,
            sunny=self._hub_signals.sunny_state,
            hot_day=self._hub_signals.hot_day,
            room_state=sig.room_state,
            wind_state=sig.wind_state,
            active_rule=self._schedule.active_rule_label(cover_id, now),
            next_planned_action=planned,
            next_planned_at=planned_at,
            last_engine_move=self._last_engine_move.get(cover_id),
            owner=p.owner.value if p.owner else None,
            degraded=inputs.room_degraded,
            enabled=p.enabled,
            mode=p.mode,
            override_active=p.dam is not None,
            override_since=p.manual_move_at if p.dam is not None else None,
            overridden_desired=p.dam.value if p.dam else None,
            wind_active=inputs.wind_active,
        )

    def _build_hub_view(self, now: datetime) -> HubView:
        hs, data = self._hub_signals, self._store.data
        event = self._schedule.next_event(now)
        return HubView(
            sunny=hs.sunny_state,
            hot_day=hs.hot_day,
            frost=hs.frost.active,
            any_wind_active=any(v.wind_active for v in self._cover_views.values()),
            forecast_max_c=hs.latch.max,
            forecast_min_c=hs.latch.min,
            next_event_at=event.at if event else None,
            next_event_profile=event.profile_name if event else None,
            next_event_action=event.action.value if event else None,
            next_event_covers=tuple(self.cover_names[c] for c in event.covers) if event else (),
            shading_mode=data.shading_mode,
            reopening_mode=data.reopening_mode,
            simulation=data.simulation,
            verbose=data.verbose,
            problem=self._problem,
        )

    def _publish(self) -> None:
        self.hub_view = self._build_hub_view(dt_util.utcnow())
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for cover_id, engine in self._engines.items():
            rt = engine.rt
            pending = rt.pending
            out[cover_id] = {
                "persisted": engine.p.to_dict(),
                "runtime": {
                    "pending": None
                    if pending is None
                    else {
                        "target": pending.target.value,
                        "layer": pending.layer.value,
                        "sent_at": pending.sent_at.isoformat(),
                    },
                    "last_send_at": rt.last_send_at.isoformat() if rt.last_send_at else None,
                    "backoff_until": rt.backoff_until.isoformat() if rt.backoff_until else None,
                    "consecutive_failures": rt.consecutive_failures,
                    "command_failed": rt.command_failed,
                    "unconfirmed": rt.unconfirmed,
                    "restoring_until": rt.restoring_until.isoformat()
                    if rt.restoring_until
                    else None,
                },
                "view": _jsonable(asdict(self._cover_views[cover_id])),
            }
        return out

    # -- write API (decision 17) -------------------------------------------------------

    def _apply_verbose(self, on: bool) -> None:
        _INTEGRATION_LOGGER.setLevel(logging.DEBUG if on else logging.NOTSET)

    async def async_set_enabled(self, cover_id: str, enabled: bool) -> None:
        async with self._locks[cover_id]:
            self._engines[cover_id].p.enabled = enabled
        await self._store.async_save()
        await self.async_evaluate(cover_ids={cover_id})

    async def async_set_mode(self, cover_id: str, mode: Mode) -> None:
        async with self._locks[cover_id]:
            self._engines[cover_id].p.mode = mode
        await self._store.async_save()
        await self.async_evaluate(cover_ids={cover_id})

    async def async_set_shading_mode(self, mode: ShadingMode) -> None:
        self._store.data.shading_mode = mode
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_reopening_mode(self, mode: ReopeningMode) -> None:
        self._store.data.reopening_mode = mode
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_simulation(self, on: bool) -> None:
        self._store.data.simulation = on
        await self._store.async_save()
        await self.async_evaluate()

    async def async_set_verbose(self, on: bool) -> None:
        self._store.data.verbose = on
        self._apply_verbose(on)
        await self._store.async_save()
        self._publish()

    async def async_reset_override(self, cover_id: str) -> None:
        now = dt_util.utcnow()
        async with self._locks[cover_id]:
            actual = await self._sync_actual(cover_id, now)
            self._engines[cover_id].reset(actual, now)
        await self._store.async_save()
        await self.async_evaluate(now, cover_ids={cover_id})

    async def async_evaluate_now(self, cover_ids: Iterable[str] | None = None) -> None:
        await self.async_evaluate(cover_ids=cover_ids)
