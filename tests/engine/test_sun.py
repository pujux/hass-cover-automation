from __future__ import annotations

import pytest
from custom_components.cover_automation.engine.model import CoverConfig
from custom_components.cover_automation.engine.sun import (
    SunHits,
    inside_strict,
    signed_azimuth_diff,
)


def cfg(**kw) -> CoverConfig:
    base = {"cover_id": "c1", "name": "c1", "azimuth": 180.0}
    base.update(kw)
    return CoverConfig(**base)


@pytest.mark.parametrize(
    ("sun", "cover", "expected"),
    [(180, 180, 0.0), (200, 180, 20.0), (160, 180, -20.0), (10, 350, 20.0), (350, 10, -20.0)],
)
def test_signed_diff_wraps(sun, cover, expected):
    assert signed_azimuth_diff(sun, cover) == pytest.approx(expected)


def test_inside_strict_is_strict_on_azimuth_inclusive_on_elevation():
    c = cfg(tolerance_left=60, tolerance_right=60, elevation_min=10, elevation_max=50)
    assert inside_strict(c, 180, 10)  # elevation inclusive
    assert inside_strict(c, 180, 50)
    assert not inside_strict(c, 120, 30)  # azimuth strict
    assert not inside_strict(c, 240, 30)
    assert inside_strict(c, 239.9, 30)


def test_hysteresis_on_azimuth_edge():
    c = cfg(tolerance_left=60, tolerance_right=60)
    s = SunHits(c, margin=2.0)
    assert s.update(120.5, 30) is True  # strictly inside on the left edge
    assert s.update(119.0, 30) is True  # outside by 1 deg: still on
    assert s.update(117.9, 30) is False  # outside by more than 2: off
    assert s.update(119.0, 30) is False  # must be strictly inside to turn on again
    assert s.update(120.1, 30) is True


def test_elevation_release_never_below_horizon():
    c = cfg(elevation_min=0.0)
    s = SunHits(c, margin=2.0)
    assert s.update(180, 5) is True
    assert s.update(180, 0.5) is True
    assert s.update(180, 0.0) is False  # off at elevation <= 0 despite the 2 deg margin


def test_elevation_release_uses_margin_above_horizon():
    c = cfg(elevation_min=10.0, elevation_max=40.0)
    s = SunHits(c, margin=2.0)
    assert s.update(180, 20) is True
    assert s.update(180, 8.5) is True  # within margin below min
    assert s.update(180, 7.9) is False
    s.seed(180, 20)
    assert s.update(180, 41.5) is True
    assert s.update(180, 42.1) is False


def test_seed_uses_strict_test():
    c = cfg()
    s = SunHits(c, margin=2.0)
    assert s.state is None
    assert s.seed(119.0, 30) is False  # outside by 1 deg is NOT inside at seed time
    assert s.seed(181.0, 30) is True


def test_never_hits_when_elevation_range_inverted():
    c = cfg(elevation_min=50.0, elevation_max=10.0)
    assert not inside_strict(c, 180, 30)
