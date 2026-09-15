from __future__ import annotations

from datetime import datetime

import pytest
from custom_components.cover_automation.engine.model import (
    CoverPersisted,
    CoverState,
    DamLayer,
    Desired,
    Mode,
    Owner,
    Status,
    Target,
)

from tests.engine.conftest import at


def test_target_inverse_and_matches():
    assert Target.OPEN.inverse is Target.CLOSED
    assert Target.CLOSED.inverse is Target.OPEN
    assert Target.OPEN.matches(CoverState.OPEN)
    assert not Target.OPEN.matches(CoverState.PARTIAL)
    assert Target.from_state(CoverState.CLOSED) is Target.CLOSED
    assert Target.from_state(CoverState.MOVING) is None


def test_desired_as_target():
    assert Desired.OPEN.as_target() is Target.OPEN
    assert Desired.LEAVE_ALONE.as_target() is None


def test_persisted_roundtrip():
    p = CoverPersisted(
        owner=Owner.USER,
        engine_target=Target.CLOSED,
        manual_move_at=at("2026-07-01", "14:00"),
        dam=Target.CLOSED,
        dam_layer=DamLayer.SHADING,
        wind_active=True,
        enabled=False,
        mode=Mode.DARK_ONLY,
    )
    d = p.to_dict()
    assert d["manual_move_at"] == "2026-07-01T14:00:00+02:00"
    assert CoverPersisted.from_dict(d) == p


def test_persisted_defaults_and_helpers():
    p = CoverPersisted()
    assert p.owner is None and p.enabled and p.mode is Mode.AUTO
    assert not p.override_active
    p.owner = Owner.ENGINE
    p.engine_target = Target.CLOSED
    assert p.owns(CoverState.CLOSED)
    assert not p.owns(CoverState.OPEN)
    p.dam = Target.OPEN
    assert p.override_active


def test_from_dict_tolerates_missing_keys():
    p = CoverPersisted.from_dict({"owner": "engine"})
    assert p.owner is Owner.ENGINE and p.manual_move_at is None and p.mode is Mode.AUTO


def test_status_precedence_order():
    order = list(Status)
    assert order[0] is Status.COVER_UNAVAILABLE
    assert order[-1] is Status.IDLE
    assert order.index(Status.PARTIAL) < order.index(Status.MANUAL_OVERRIDE)


def test_manual_move_at_parses_z_suffix():
    p = CoverPersisted.from_dict({"manual_move_at": "2026-07-01T12:00:00Z"})
    assert isinstance(p.manual_move_at, datetime)
    assert p.manual_move_at.tzinfo is not None


def test_from_dict_rejects_naive_datetime():
    with pytest.raises(ValueError):
        CoverPersisted.from_dict({"manual_move_at": "2026-07-01T12:00:00"})
