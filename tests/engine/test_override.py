from __future__ import annotations

from datetime import timedelta

from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    CoverRuntime,
    CoverState,
    DamLayer,
    Decision,
    Desired,
    Layer,
    Owner,
    Target,
)
from custom_components.cover_automation.engine.override import (
    clear,
    on_manual_move,
    on_rule_fired,
    reset,
    update_dwell,
)

from tests.engine.conftest import at

T0 = at("2026-07-01", "14:00")
SHADE = Decision(Desired.CLOSED, Layer.SHADING, "shade")
NO_SHADE = Decision(Desired.OPEN, Layer.SHADING, "no_shade")
NIGHT = Decision(Desired.LEAVE_ALONE, Layer.NONE, "night")
HOLD = Decision(Desired.CLOSED, Layer.SCHEDULE, "schedule_close")


def test_manual_move_against_shading_records_dam_and_layer():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    on_manual_move(p, SHADE, CoverState.OPEN, T0)
    assert p.owner is Owner.USER and p.manual_move_at == T0
    assert p.dam is Target.CLOSED and p.dam_layer is DamLayer.SHADING
    assert p.engine_target is Target.CLOSED  # untouched


def test_manual_move_during_leave_alone_uses_inverse_of_new_actual():
    p = CoverPersisted(owner=Owner.ENGINE)
    on_manual_move(p, NIGHT, CoverState.OPEN, T0)
    assert p.dam is Target.CLOSED and p.dam_layer is DamLayer.OTHER


def test_manual_move_to_partial_during_leave_alone_has_no_dam():
    p = CoverPersisted(owner=Owner.ENGINE)
    on_manual_move(p, NIGHT, CoverState.PARTIAL, T0)
    assert p.dam is None and p.dam_layer is None and p.owner is Owner.USER


def test_manual_move_against_schedule_hold_is_other_layer():
    p = CoverPersisted(owner=Owner.ENGINE)
    on_manual_move(p, HOLD, CoverState.OPEN, T0)
    assert p.dam is Target.CLOSED and p.dam_layer is DamLayer.OTHER


def test_manual_move_while_disabled_records_no_override():
    p = CoverPersisted(owner=Owner.ENGINE, enabled=False)
    on_manual_move(p, SHADE, CoverState.OPEN, T0)
    assert p.owner is Owner.USER and p.dam is None


def test_rule_fired_and_reset():
    p = CoverPersisted(
        owner=Owner.USER, dam=Target.CLOSED, dam_layer=DamLayer.SHADING, manual_move_at=T0
    )
    on_rule_fired(p)
    assert p.dam is None and p.dam_layer is None and p.owner is Owner.USER
    p.dam = Target.OPEN
    reset(p, CoverState.CLOSED, T0)
    assert p.owner is Owner.ENGINE and p.engine_target is Target.CLOSED and p.dam is None
    assert p.manual_move_at == T0  # untouched so a released hold stays released
    reset(p, CoverState.PARTIAL, T0)
    assert p.engine_target is None
    clear(p)
    assert p.dam is None


def test_dwell_ends_override_after_continuous_difference():
    p = CoverPersisted(owner=Owner.USER, dam=Target.CLOSED, dam_layer=DamLayer.OTHER)
    rt = CoverRuntime()
    assert update_dwell(p, rt, NO_SHADE, True, T0) == T0 + timedelta(seconds=1800)
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(minutes=10))
    assert p.dam is Target.CLOSED
    update_dwell(p, rt, SHADE, True, T0 + timedelta(minutes=15))  # back to dam: reset
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(minutes=16))
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(minutes=45))
    assert p.dam is Target.CLOSED  # only 29 min since reset
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(minutes=46))
    assert p.dam is None


def test_dwell_pauses_on_leave_alone():
    p = CoverPersisted(owner=Owner.USER, dam=Target.CLOSED, dam_layer=DamLayer.OTHER)
    rt = CoverRuntime()
    update_dwell(p, rt, NO_SHADE, True, T0)
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(minutes=20))
    update_dwell(p, rt, NIGHT, True, T0 + timedelta(minutes=25))  # pause
    update_dwell(p, rt, NIGHT, True, T0 + timedelta(hours=8))
    assert p.dam is Target.CLOSED
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(hours=8, minutes=1))
    update_dwell(p, rt, NO_SHADE, True, T0 + timedelta(hours=8, minutes=11))
    assert p.dam is None  # 20 + 10 minutes


def test_shading_override_ends_when_sun_leaves():
    p = CoverPersisted(owner=Owner.USER, dam=Target.CLOSED, dam_layer=DamLayer.SHADING)
    rt = CoverRuntime()
    update_dwell(p, rt, SHADE, True, T0)
    assert p.dam is Target.CLOSED
    assert update_dwell(p, rt, NIGHT, False, T0 + timedelta(hours=3)) is None
    assert p.dam is None


def test_other_layer_override_survives_sun_leaving():
    p = CoverPersisted(owner=Owner.USER, dam=Target.CLOSED, dam_layer=DamLayer.OTHER)
    rt = CoverRuntime()
    update_dwell(p, rt, NIGHT, False, T0)
    assert p.dam is Target.CLOSED
