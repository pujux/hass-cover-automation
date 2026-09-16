from __future__ import annotations

import pytest
from custom_components.cover_automation.units import (
    from_celsius,
    read_float,
    speed_convert,
    temperature_unit_of,
    to_celsius,
)
from homeassistant.core import State


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, None),
        (State("sensor.x", "unavailable"), None),
        (State("sensor.x", "unknown"), None),
        (State("sensor.x", "abc"), None),
        (State("sensor.x", "21.5"), 21.5),
        (State("sensor.x", "-3"), -3.0),
    ],
)
def test_read_float(state, expected):
    assert read_float(state) == expected


def test_temperature_conversions():
    assert to_celsius(68.0, "°F") == pytest.approx(20.0)
    assert to_celsius(20.0, "°C") == 20.0
    assert to_celsius(20.0, None) == 20.0
    assert to_celsius(293.15, "K") == pytest.approx(20.0)
    assert from_celsius(20.0, "°F") == pytest.approx(68.0)
    assert temperature_unit_of(State("sensor.t", "1", {"unit_of_measurement": "°F"})) == "°F"
    assert temperature_unit_of(None) is None


def test_speed_conversions():
    assert speed_convert(36.0, "km/h", "m/s") == pytest.approx(10.0)
    assert speed_convert(10.0, "m/s", "km/h") == pytest.approx(36.0)
    assert speed_convert(12.0, None, "km/h") == 12.0
    assert speed_convert(12.0, "km/h", None) == 12.0
    assert speed_convert(12.0, "bogus", "km/h") == 12.0
