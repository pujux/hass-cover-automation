from __future__ import annotations

from datetime import timedelta

from custom_components.cover_automation.engine.gate import decide
from custom_components.cover_automation.engine.model import (
    CoverConfig,
    CoverInputs,
    CoverPersisted,
    CoverRuntime,
    CoverState,
    Decision,
    Defer,
    Desired,
    DoorState,
    HubSignals,
    Layer,
    Owner,
    Pending,
    ReopeningMode,
    Send,
    Suppress,
    Target,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "12:00")
CFG = CoverConfig("c", "c", 180.0, min_move_interval_s=600)


def sig(**kw) -> HubSignals:
    base = {
        "now": T0,
        "sun_elevation": 30.0,
        "frost": False,
        "frost_near_freezing": False,
        "sunny": True,
        "hot_day": True,
    }
    base.update(kw)
    return HubSignals(**base)


def d(desired: Desired, layer: Layer) -> Decision:
    return Decision(desired, layer, layer.value)


def test_nothing_to_do():
    rt = CoverRuntime()
    assert (
        decide(
            d(Desired.LEAVE_ALONE, Layer.NONE),
            CFG,
            CoverPersisted(),
            CoverInputs(CoverState.OPEN),
            sig(),
            rt,
            T0,
        )
        is None
    )
    assert (
        decide(
            d(Desired.OPEN, Layer.SHADING),
            CFG,
            CoverPersisted(),
            CoverInputs(CoverState.OPEN),
            sig(),
            rt,
            T0,
        )
        is None
    )


def test_disabled_suppresses_even_wind():
    p = CoverPersisted(enabled=False)
    a = decide(
        d(Desired.OPEN, Layer.WIND),
        CFG,
        p,
        CoverInputs(CoverState.CLOSED),
        sig(),
        CoverRuntime(),
        T0,
    )
    assert a == Suppress("disabled")


def test_wind_sends_immediately_despite_override_backoff_and_moving():
    p = CoverPersisted(dam=Target.OPEN)
    rt = CoverRuntime(backoff_until=T0 + timedelta(hours=1), last_send_at=T0)
    a = decide(d(Desired.OPEN, Layer.WIND), CFG, p, CoverInputs(CoverState.MOVING), sig(), rt, T0)
    assert a == Send(Target.OPEN, Layer.WIND)


def test_door_respects_override_only_if_manual_move_after_door_opened():
    door_open_at = T0 - timedelta(hours=1)
    p = CoverPersisted(dam=Target.OPEN, manual_move_at=T0 - timedelta(minutes=30))
    inputs = CoverInputs(CoverState.CLOSED, door=DoorState.OPEN, door_last_changed=door_open_at)
    assert decide(
        d(Desired.OPEN, Layer.DOOR), CFG, p, inputs, sig(), CoverRuntime(), T0
    ) == Suppress("override_after_door_opened")
    p2 = CoverPersisted(dam=Target.OPEN, manual_move_at=T0 - timedelta(hours=2))
    assert decide(d(Desired.OPEN, Layer.DOOR), CFG, p2, inputs, sig(), CoverRuntime(), T0) == Send(
        Target.OPEN, Layer.DOOR
    )


def test_door_ignores_min_interval_but_respects_backoff():
    inputs = CoverInputs(CoverState.CLOSED, door=DoorState.OPEN, door_last_changed=T0)
    rt = CoverRuntime(last_send_at=T0 - timedelta(seconds=10))
    assert decide(
        d(Desired.OPEN, Layer.DOOR), CFG, CoverPersisted(), inputs, sig(), rt, T0
    ) == Send(Target.OPEN, Layer.DOOR)
    rt2 = CoverRuntime(backoff_until=T0 + timedelta(minutes=5))
    assert decide(
        d(Desired.OPEN, Layer.DOOR), CFG, CoverPersisted(), inputs, sig(), rt2, T0
    ) == Defer(T0 + timedelta(minutes=5), "backoff")


def test_override_blocks_commands_toward_dam_only():
    p = CoverPersisted(dam=Target.CLOSED, owner=Owner.USER)
    assert decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        p,
        CoverInputs(CoverState.OPEN),
        sig(),
        CoverRuntime(),
        T0,
    ) == Suppress("manual_override")
    p2 = CoverPersisted(dam=Target.OPEN, owner=Owner.USER)
    assert decide(
        d(Desired.CLOSED, Layer.SCHEDULE),
        CFG,
        p2,
        CoverInputs(CoverState.OPEN),
        sig(),
        CoverRuntime(),
        T0,
    ) == Send(Target.CLOSED, Layer.SCHEDULE)


def test_reopening_modes_for_shading_open():
    inputs = CoverInputs(CoverState.CLOSED)
    owned = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    user = CoverPersisted(owner=Owner.USER, engine_target=Target.CLOSED)
    assert decide(
        d(Desired.OPEN, Layer.SHADING), CFG, owned, inputs, sig(), CoverRuntime(), T0
    ) == Send(Target.OPEN, Layer.SHADING)
    assert decide(
        d(Desired.OPEN, Layer.SHADING), CFG, user, inputs, sig(), CoverRuntime(), T0
    ) == Suppress("reopening_passive")
    assert decide(
        d(Desired.OPEN, Layer.SHADING),
        CFG,
        user,
        inputs,
        sig(reopening_mode=ReopeningMode.ACTIVE),
        CoverRuntime(),
        T0,
    ) == Send(Target.OPEN, Layer.SHADING)
    assert decide(
        d(Desired.OPEN, Layer.SHADING),
        CFG,
        owned,
        inputs,
        sig(reopening_mode=ReopeningMode.OFF),
        CoverRuntime(),
        T0,
    ) == Suppress("reopening_off")
    # schedule opens are not subject to reopening mode
    assert decide(
        d(Desired.OPEN, Layer.SCHEDULE),
        CFG,
        user,
        inputs,
        sig(reopening_mode=ReopeningMode.OFF),
        CoverRuntime(),
        T0,
    ) == Send(Target.OPEN, Layer.SCHEDULE)


def test_moving_defers_non_protection_layers():
    a = decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.MOVING),
        sig(),
        CoverRuntime(),
        T0,
    )
    assert a == Suppress("moving")


def test_pending_command_for_the_same_target_suppresses_every_layer():
    """Gate 7: a duplicate of the command already in flight is pointless on any layer."""

    def rt() -> CoverRuntime:
        return CoverRuntime(
            pending=Pending(Target.CLOSED, Layer.SHADING, T0, T0),
            last_send_at=T0 - timedelta(hours=1),
        )

    inputs = CoverInputs(CoverState.OPEN, door=DoorState.OPEN, door_last_changed=T0)
    for layer in (Layer.SHADING, Layer.WIND, Layer.DOOR):
        assert decide(
            d(Desired.CLOSED, layer), CFG, CoverPersisted(), inputs, sig(), rt(), T0
        ) == Suppress("in_flight")
    # A pending close does not block the opposite target: wind still wins.
    assert decide(
        d(Desired.OPEN, Layer.WIND),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.MOVING),
        sig(),
        rt(),
        T0,
    ) == Send(Target.OPEN, Layer.WIND)


def test_min_interval_applies_to_shading_only_and_uses_any_send():
    rt = CoverRuntime(last_send_at=T0 - timedelta(seconds=100))
    a = decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.OPEN),
        sig(),
        rt,
        T0,
    )
    assert a == Defer(T0 + timedelta(seconds=500), "min_interval")
    b = decide(
        d(Desired.CLOSED, Layer.SCHEDULE),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.OPEN),
        sig(),
        rt,
        T0,
    )
    assert b == Send(Target.CLOSED, Layer.SCHEDULE)


def test_backoff_defers_a_shading_move_after_the_min_interval_expired():
    """I1/T8: the generic backoff path (gate 9), reachable now that a failed send rolls
    the interval clock back."""
    rt = CoverRuntime(
        last_send_at=T0 - timedelta(hours=2), backoff_until=T0 + timedelta(seconds=30)
    )
    a = decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.OPEN),
        sig(),
        rt,
        T0,
    )
    assert a == Defer(T0 + timedelta(seconds=30), "backoff")


def test_simulation_sends_once_per_layer_target():
    rt = CoverRuntime()
    a = decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.OPEN),
        sig(simulation=True),
        rt,
        T0,
    )
    assert a == Send(Target.CLOSED, Layer.SHADING, simulated=True)
    rt.last_simulated = (Layer.SHADING, Target.CLOSED)
    b = decide(
        d(Desired.CLOSED, Layer.SHADING),
        CFG,
        CoverPersisted(),
        CoverInputs(CoverState.OPEN),
        sig(simulation=True),
        rt,
        T0,
    )
    assert b == Suppress("simulated_duplicate")
