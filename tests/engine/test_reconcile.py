from __future__ import annotations

from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    CoverState,
    DamLayer,
    Decision,
    Desired,
    Layer,
    Owner,
    Target,
)
from custom_components.cover_automation.engine.reconcile import reconcile

from tests.engine.conftest import at

T0 = at("2026-07-01", "08:00")
FIRST_OPEN = Decision(Desired.OPEN, Layer.SHADING, "no_shade")
FIRST_NIGHT = Decision(Desired.LEAVE_ALONE, Layer.NONE, "night")


def test_first_setup_assigns_engine_ownership():
    p = CoverPersisted()
    assert reconcile(p, CoverState.CLOSED, FIRST_OPEN, T0) == "first_setup"
    assert p.owner is Owner.ENGINE and p.engine_target is Target.CLOSED and p.dam is None
    p2 = CoverPersisted()
    reconcile(p2, CoverState.PARTIAL, FIRST_OPEN, T0)
    assert p2.engine_target is None


def test_completed_during_downtime_is_unchanged():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    assert reconcile(p, CoverState.CLOSED, FIRST_OPEN, T0) == "unchanged"
    assert p.owner is Owner.ENGINE and p.manual_move_at is None


def test_user_closed_then_restart_stays_user_owned():
    p = CoverPersisted(
        owner=Owner.USER,
        engine_target=Target.CLOSED,
        manual_move_at=at("2026-06-30", "15:00"),
        dam=Target.OPEN,
        dam_layer=DamLayer.SHADING,
    )
    assert reconcile(p, CoverState.CLOSED, FIRST_OPEN, T0) == "unchanged"
    assert (
        p.owner is Owner.USER
        and p.dam is Target.OPEN
        and p.manual_move_at == at("2026-06-30", "15:00")
    )


def test_manual_during_downtime_records_manual_move():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    assert reconcile(p, CoverState.OPEN, FIRST_OPEN, T0) == "manual_during_downtime"
    assert p.owner is Owner.USER and p.manual_move_at == T0
    assert (
        p.dam is Target.OPEN
    )  # recorded against the first evaluation's desired (harmless, ends by dwell)
    p2 = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    reconcile(p2, CoverState.OPEN, FIRST_NIGHT, T0)
    assert p2.dam is Target.CLOSED and p2.dam_layer is DamLayer.OTHER


def test_live_override_survives_restart_during_frost():
    p = CoverPersisted(
        owner=Owner.USER,
        engine_target=Target.CLOSED,
        manual_move_at=at("2026-06-30", "14:00"),
        dam=Target.CLOSED,
        dam_layer=DamLayer.SHADING,
    )
    frost = Decision(Desired.LEAVE_ALONE, Layer.FROST, "frost_active")
    assert reconcile(p, CoverState.OPEN, frost, T0) == "override_kept"
    assert p.dam is Target.CLOSED and p.manual_move_at == at("2026-06-30", "14:00")


def test_moving_or_unavailable_at_startup_changes_nothing():
    p = CoverPersisted(owner=Owner.ENGINE, engine_target=Target.CLOSED)
    assert reconcile(p, CoverState.MOVING, FIRST_OPEN, T0) == "unchanged"
    assert reconcile(p, CoverState.UNAVAILABLE, FIRST_OPEN, T0) == "unchanged"
    assert p.owner is Owner.ENGINE
