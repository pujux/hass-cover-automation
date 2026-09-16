"""Per-cover facade composing layers, override lifecycle, gate, commands and reconcile."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import classify, commands, gate, layers, override, reconcile
from .const import OVERRIDE_DWELL_S, RESTORING_WINDOW_S
from .model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverRuntime,
    CoverState,
    Decision,
    Defer,
    Desired,
    HubSignals,
    Layer,
    Send,
    Status,
    StepResult,
    TransitionResult,
)
from .signals import ContinuousCondition


class CoverEngine:
    """Per-cover facade over the layer stack, override lifecycle, gate and reconcile.

    Startup order (spec §5): `decide(inputs, signals)` produces the first decision from the
    persisted `owner`/`manual_move_at` without touching any state, `reconcile(actual,
    decision, now)` consumes it, and only then does `evaluate()` run and its action get
    acted on. Calling `evaluate()` first claims ownership for a command in flight (decision
    20) and `reconcile` would then record that command as a manual move.

    Persistence contract: the controller schedules a delayed Store save after every
    `evaluate` -- the dwell, §1.5(e) and a rule firing all mutate the persisted scalars --
    and saves immediately after `on_command_sent`, `reset`, `reconcile` and a transition
    classified as MANUAL.
    """

    def __init__(
        self,
        config: CoverConfig,
        persisted: CoverPersisted,
        runtime: CoverRuntime | None = None,
        *,
        override_dwell_s: int = OVERRIDE_DWELL_S,
    ) -> None:
        self.config = config
        self.p = persisted
        if runtime is not None:
            self.rt = runtime  # injected runtime keeps its own dwell
        else:
            # §5 persists wind_active so a wind episode survives a restart: without the seed
            # a release during downtime is never detected and no restoring window opens.
            self.rt = CoverRuntime(
                override_dwell=ContinuousCondition(override_dwell_s),
                prev_wind_active=persisted.wind_active,
            )

    # -- evaluation -----------------------------------------------------------------

    def decide(self, inputs: CoverInputs, signals: HubSignals) -> Decision:
        """The layer stack alone: no timers, no bookkeeping, no gate (spec §5 startup)."""
        rt = self.rt
        restoring = rt.restoring_until is not None and signals.now < rt.restoring_until
        return layers.evaluate(self.config, self.p, inputs, signals, restoring=restoring)

    def evaluate(
        self, inputs: CoverInputs, signals: HubSignals, *, rule_fired: bool = False
    ) -> StepResult:
        now = signals.now
        rt, p, cfg = self.rt, self.p, self.config
        if classify.is_settled(inputs.actual):
            rt.last_settled = inputs.actual  # baseline for C2, also right after a restart
        p.wind_active = inputs.wind_active
        if not p.enabled:
            # The wind edge detector keeps tracking while disabled: an episode that starts and
            # ends out of sight would otherwise look like a release on the next enabled
            # evaluation and open a restoring window for wind that is long gone.
            rt.prev_wind_active = inputs.wind_active
            # §1.1: the engine never commands a disabled cover and no timers run for it.
            disabled = Decision(Desired.LEAVE_ALONE, Layer.NONE, "disabled")
            return StepResult(disabled, None, self.status(disabled, inputs), False, None)
        if not signals.simulation:
            rt.last_simulated = None  # a later simulation session starts from a clean slate
        if rule_fired:
            override.on_rule_fired(p)
        if cfg.wind_enabled and rt.prev_wind_active and not inputs.wind_active:
            rt.restoring_until = now + timedelta(seconds=RESTORING_WINDOW_S)
        rt.prev_wind_active = inputs.wind_active
        if inputs.schedule.open_rule_fired_at is not None and inputs.actual is CoverState.OPEN:
            rt.open_rule_satisfied_at = inputs.schedule.open_rule_fired_at
        restoring = rt.restoring_until is not None and now < rt.restoring_until

        decision = layers.evaluate(cfg, p, inputs, signals, restoring=restoring)
        if restoring:
            target = decision.desired.as_target()
            below_agrees = decision.layer not in (Layer.FROST, Layer.WIND, Layer.DOOR) and (
                target is None or target.matches(inputs.actual)
            )
            if below_agrees:
                rt.restoring_until = None  # lower layers agree with actual: exemption lapses
                restoring = False

        dwell_at = override.update_dwell(p, rt, decision, inputs.sun_hits, now)

        frost_relevant = signals.frost is True or (
            signals.frost is None and signals.frost_near_freezing
        )
        notify = False
        if (
            decision.layer is Layer.FROST
            and frost_relevant
            and (decision.wind_opinion is Desired.OPEN or decision.door_opinion is Desired.OPEN)
        ):
            notify = not rt.frost_conflict_notified
            rt.frost_conflict_notified = True
        elif decision.layer is not Layer.FROST:
            rt.frost_conflict_notified = False

        action = gate.decide(decision, cfg, p, inputs, signals, rt, now)
        rt.last_evaluation = decision
        status = self.status(decision, inputs)

        candidates: list[datetime | None] = [
            dwell_at,
            classify.pending_deadline(rt, cfg),
            rt.restoring_until if restoring else None,
            action.until if isinstance(action, Defer) else None,
        ]
        next_check = min((c for c in candidates if c is not None), default=None)
        return StepResult(decision, action, status, notify, next_check)

    def status(self, decision: Decision, inputs: CoverInputs) -> Status:
        p, rt = self.p, self.rt
        if inputs.actual is CoverState.UNAVAILABLE:
            return Status.COVER_UNAVAILABLE
        if not p.enabled:
            return Status.DISABLED
        if rt.command_failed:
            return Status.COMMAND_FAILED
        if rt.unconfirmed:
            return Status.UNCONFIRMED
        if decision.layer is Layer.FROST:
            return Status.HELD_FROST
        if decision.layer is Layer.WIND:
            return Status.PROTECTED_WIND
        if decision.layer is Layer.DOOR:
            return Status.DOOR_OPEN
        if decision.layer is Layer.QUIET_HOURS:
            return Status.QUIET_HOURS
        if inputs.actual is CoverState.PARTIAL:
            return Status.PARTIAL
        if p.dam is not None:
            return Status.MANUAL_OVERRIDE
        if decision.layer is Layer.SCHEDULE:
            return Status.SCHEDULE_HOLD
        if decision.layer is Layer.SHADING and decision.desired is Desired.CLOSED:
            return Status.CLOSED_SHADING
        if decision.layer is Layer.SHADING and decision.desired is Desired.OPEN:
            return Status.OPEN_NO_SHADE
        return Status.IDLE

    # -- events ---------------------------------------------------------------------

    def on_transition(self, new_state: CoverState, now: datetime) -> TransitionResult:
        return classify.on_transition(self.p, self.rt, self.config, new_state, now)

    def check_pending(self, current_state: CoverState, now: datetime) -> str | None:
        return classify.check_pending(self.p, self.rt, self.config, current_state, now)

    def on_command_sent(self, send: Send, now: datetime) -> None:
        if send.simulated:
            commands.on_simulated(self.rt, send.target, send.layer)
            return
        commands.on_command_sent(self.p, self.rt, send.target, send.layer, now)

    def on_command_failed(self, now: datetime) -> datetime:
        return commands.on_command_failed(self.p, self.rt, self.config, now)

    def reset(self, actual: CoverState, now: datetime) -> None:
        override.reset(self.p, actual, now)
        self.rt.override_dwell.reset()

    def reconcile(self, actual: CoverState, first_decision: Decision | None, now: datetime) -> str:
        return reconcile.reconcile(self.p, actual, first_decision, now)

    @property
    def repair_needed(self) -> bool:
        return self.rt.consecutive_failures >= 3
