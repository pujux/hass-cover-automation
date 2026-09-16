"""Unit-safe reading of Home Assistant states (spec §2 "Units")."""

from __future__ import annotations

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import State
from homeassistant.util.unit_conversion import SpeedConverter, TemperatureConverter


def read_float(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, "", None):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def temperature_unit_of(state: State | None) -> str | None:
    if state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    return str(unit) if unit else None


# `temperature_unit_of` reads the generic `unit_of_measurement` attribute; it is unit-agnostic
# despite the name. This alias is the clearer spelling for non-temperature sensors (e.g. wind).
unit_of = temperature_unit_of


def to_celsius(value: float, unit: str | None) -> float:
    if unit in (None, UnitOfTemperature.CELSIUS) or unit not in TemperatureConverter.VALID_UNITS:
        return value
    return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)


def from_celsius(value: float, unit: str) -> float:
    if unit == UnitOfTemperature.CELSIUS or unit not in TemperatureConverter.VALID_UNITS:
        return value
    return TemperatureConverter.convert(value, UnitOfTemperature.CELSIUS, unit)


def speed_convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    if from_unit is None or to_unit is None or from_unit == to_unit:
        return value
    if from_unit not in SpeedConverter.VALID_UNITS or to_unit not in SpeedConverter.VALID_UNITS:
        return value
    return SpeedConverter.convert(value, from_unit, to_unit)
