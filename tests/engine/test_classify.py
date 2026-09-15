from __future__ import annotations

import pytest
from custom_components.cover_automation.engine.classify import classify, is_contrary, is_settled
from custom_components.cover_automation.engine.model import CoverState, Target


@pytest.mark.parametrize(
    ("ha_state", "position", "expected"),
    [
        ("open", 100, CoverState.OPEN),
        ("open", 97, CoverState.OPEN),  # within tolerance
        ("open", 94, CoverState.PARTIAL),
        ("open", 50, CoverState.PARTIAL),
        ("open", 5, CoverState.CLOSED),  # position wins over HA state
        ("open", 0, CoverState.CLOSED),
        ("closed", 0, CoverState.CLOSED),
        ("closed", 40, CoverState.CLOSED),  # HA says closed -> closed
        ("open", None, CoverState.OPEN),  # binary cover
        ("closed", None, CoverState.CLOSED),
        ("opening", 30, CoverState.MOVING),
        ("closing", None, CoverState.MOVING),
        ("unavailable", 50, CoverState.UNAVAILABLE),
        ("unknown", None, CoverState.UNAVAILABLE),
        (None, None, CoverState.UNAVAILABLE),
        ("weird", 50, CoverState.UNAVAILABLE),
    ],
)
def test_classify(ha_state, position, expected):
    assert classify(ha_state, position, 5.0) is expected


def test_settled_and_contrary():
    assert is_settled(CoverState.OPEN) and is_settled(CoverState.PARTIAL)
    assert not is_settled(CoverState.MOVING) and not is_settled(CoverState.UNAVAILABLE)
    assert is_contrary(CoverState.OPEN, Target.CLOSED)
    assert not is_contrary(CoverState.PARTIAL, Target.CLOSED)
    assert not is_contrary(CoverState.MOVING, Target.CLOSED)
    assert not is_contrary(CoverState.CLOSED, Target.CLOSED)
