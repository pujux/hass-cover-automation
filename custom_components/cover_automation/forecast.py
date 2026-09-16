"""Daily forecast fetch: weather.get_forecasts → today's max/min in °C (spec §2 "Hot day")."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Any

from homeassistant.components.weather import (
    ATTR_FORECAST_TEMP,
    ATTR_FORECAST_TEMP_LOW,
    ATTR_FORECAST_TIME,
    SERVICE_GET_FORECASTS,
)
from homeassistant.components.weather.const import ATTR_WEATHER_TEMPERATURE_UNIT
from homeassistant.components.weather.const import DOMAIN as WEATHER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .units import to_celsius

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TodayForecast:
    max_c: float | None
    min_c: float | None


def _entry_day(raw: Any, tz: tzinfo) -> date | None:
    parsed = dt_util.parse_datetime(str(raw)) if raw is not None else None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).date()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def async_fetch_today(
    hass: HomeAssistant, weather_entity: str, day: date, tz: tzinfo
) -> TodayForecast | None:
    try:
        response = await hass.services.async_call(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            {ATTR_ENTITY_ID: weather_entity, "type": "daily"},
            blocking=True,
            return_response=True,
        )
    except Exception as err:
        _LOGGER.warning("Daily forecast fetch from %s failed: %s", weather_entity, err)
        return None

    payload = response.get(weather_entity) if response else None
    forecast_raw = payload.get("forecast") if isinstance(payload, dict) else None
    entries: list[Any] = forecast_raw if isinstance(forecast_raw, list) else []

    state = hass.states.get(weather_entity)
    unit = (
        str(state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT))
        if state and state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
        else None
    )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if _entry_day(entry.get(ATTR_FORECAST_TIME), tz) != day:
            continue
        high = _as_float(entry.get(ATTR_FORECAST_TEMP))
        low = _as_float(entry.get(ATTR_FORECAST_TEMP_LOW))
        return TodayForecast(
            max_c=to_celsius(high, unit) if high is not None else None,
            min_c=to_celsius(low, unit) if low is not None else None,
        )
    return None
